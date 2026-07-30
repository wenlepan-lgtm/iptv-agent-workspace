#!/usr/bin/env python3
"""JOCTV Agent V3.1 — P4 知识发布 Pipeline (Batch F3/F4 + G2).

对应 30日凌晨任务.md §十(F3 实际发布流程 11 步) + §十一(G2 发布 Job 状态机 12 态).

发布 (F3):
  1. 创建 Immutable Release Package (build_release_package)
  2. 导出版本化 JSON (落盘 knowledge.json)
  3. 构建 Embedding/index (emb_dir)
  4. 写候选 Runtime 目录 (releases/vNNNN)
  5. Runtime Shadow 加载 (RuntimeLoader.shadow_load)
  6. Runtime 返回 load ACK
  7. 执行测试查询 (RuntimeLoader.test_query)
  8. 测试通过后原子切换 active_release (atomic_activate)
  9. 更新 active_runtime_version
  10. 记录发布审计
  11. 发布失败保持旧版本不变

回滚 (F4):
  1. 选择历史 Release
  2. 校验包完整性 (manifest + knowledge.json 存在)
  3. Shadow 加载
  4. 测试查询
  5. 原子切换
  6. Runtime ACK
  7. 更新 active_runtime_version
  8. 写审计
  9. 保留被回滚版本, 不删

Job 状态机 (G2):
  DRAFT REVIEWING APPROVED BUILDING VALIDATING DEPLOYING_SHADOW
  SHADOW_TESTING ACTIVATING APPLIED FAILED ROLLING_BACK ROLLED_BACK

红线:
- 发布失败保持旧版本不变 (原子切换前不碰 current).
- 关键发布操作不能在无审计记录时静默成功.
- 禁止直接修改历史版本内容.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .db import gen_id, get_conn, now_ms
from . import runtime_store as rt
from .runtime_store import (RuntimeLoader, atomic_activate, build_knowledge_payload,
                            build_release_package, validate_release, ValidationError,
                            deactivate_runtime)

# 12 态 (G2)
JOB_STATES = {
    "DRAFT", "REVIEWING", "APPROVED", "BUILDING", "VALIDATING",
    "DEPLOYING_SHADOW", "SHADOW_TESTING", "ACTIVATING", "APPLIED",
    "FAILED", "ROLLING_BACK", "ROLLED_BACK",
}

# 合法 Job 状态转移
JOB_TRANSITIONS = {
    ("DRAFT", "BUILDING"), ("BUILDING", "VALIDATING"),
    ("VALIDATING", "DEPLOYING_SHADOW"), ("DEPLOYING_SHADOW", "SHADOW_TESTING"),
    ("SHADOW_TESTING", "ACTIVATING"), ("ACTIVATING", "APPLIED"),
    # 失败
    ("BUILDING", "FAILED"), ("VALIDATING", "FAILED"),
    ("DEPLOYING_SHADOW", "FAILED"), ("SHADOW_TESTING", "FAILED"),
    ("ACTIVATING", "FAILED"),
    # 回滚: 新建回滚 Job 起点是 DRAFT, 直接进入 ROLLING_BACK
    ("DRAFT", "ROLLING_BACK"), ("APPLIED", "ROLLING_BACK"),
    ("ROLLING_BACK", "ROLLED_BACK"), ("ROLLING_BACK", "FAILED"),
    # 失败后可重试
    ("FAILED", "BUILDING"),
}


def create_publish_job(*, release_id: str, hotel_id: str, kind: str,
                       desired_version: int, initiated_by: str) -> str:
    """创建发布 Job (DRAFT)."""
    job_id = gen_id("pj")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO publish_jobs("
            "job_id, release_id, hotel_id, kind, state, desired_version, "
            "initiated_by, started_at, last_transition_at, apply_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, release_id, hotel_id, kind, "DRAFT", desired_version,
             initiated_by, now_ms(), now_ms(), "idle"),
        )
    return job_id


def _transition_job(job_id: str, new_state: str, *,
                    error_message: Optional[str] = None,
                    extra_set: str = "", extra_vals: list = None,
                    audit_writer=None) -> None:
    """Job 状态转移 + 审计. FAILED 是兜底目标, 任意状态都可转 FAILED (失败不卡状态机)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state FROM publish_jobs WHERE job_id=?", (job_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"job {job_id} not found")
        old = row["state"]
        is_terminal_self = (old == new_state)
        is_fail_to_failed = (new_state == "FAILED")
        if (old, new_state) not in JOB_TRANSITIONS and not is_terminal_self \
                and not is_fail_to_failed:
            raise ValueError(f"非法 Job 状态转移: {old} → {new_state}")
        sets = ["state=?", "last_transition_at=?"]
        vals: list = [new_state, now_ms()]
        if new_state in ("APPLIED", "ROLLED_BACK", "FAILED"):
            sets.append("finished_at=?")
            vals.append(now_ms())
        if error_message is not None:
            sets.append("error_message=?")
            vals.append(error_message)
        if extra_set:
            sets.append(extra_set)
            vals.extend(extra_vals or [])
        vals.append(job_id)
        conn.execute(
            f"UPDATE publish_jobs SET {', '.join(sets)} WHERE job_id=?", vals,
        )
    if audit_writer:
        audit_writer(action="publish_job_transition",
                     resource="publish_job", resource_id=job_id,
                     before_value={"state": old}, after_value={"state": new_state})


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM publish_jobs WHERE job_id=?", (job_id,),
        ).fetchone()
    return dict(row) if row else None


# 模块级异常 (供 admin_routes 捕获). 发布/回滚失败时抛出, Job 已落库 FAILED.
class PublishError(Exception):
    """发布流程失败. Job 已标 FAILED, 旧版本保持不变."""
    pass


class RollbackError(Exception):
    """回滚流程失败. Job 已标 FAILED, 当前版本保持不变."""
    pass


# ========== golden_queries 门禁 (§39.3 H2) ==========
def _match_golden(answer: Optional[str], expected_pattern: str) -> bool:
    """answer 是否匹配 expected_pattern (子串, 大小写不敏感, 去空格)."""
    if answer is None:
        return False
    a = str(answer).replace(" ", "").lower()
    p = str(expected_pattern).replace(" ", "").lower()
    if not p:
        return bool(a)
    return p in a


def _run_golden_queries_gate(*, release_id: str, hotel_id: str,
                             golden_queries: list[dict]) -> dict:
    """loop manifest golden_queries via gateway /query (namespace=shadow).

    全命中 (answer 匹配 expected_answer_pattern) 才允许 activate.
    任一未命中记录到 missed. 返回 {all_hit, total, hit, missed, results}.
    """
    results = []
    hit = 0
    missed = []
    for gq in golden_queries:
        q = gq.get("query", "")
        expected = gq.get("expected_answer_pattern", "")
        # 调 gateway /query (shadow namespace)
        tr = RuntimeLoader.test_query(release_id, q, hotel_id=hotel_id)
        ok = False
        answer = None
        if tr.get("ok"):
            answer = tr.get("answer")
            # 先匹配 expected_answer_pattern; 不匹配再尝试 entity_id 命中
            if _match_golden(answer, expected):
                ok = True
            elif gq.get("entity_id") and answer and gq["entity_id"] in str(answer):
                ok = True
        if ok:
            hit += 1
        else:
            missed.append({"query": q, "expected_answer_pattern": expected,
                           "answer": answer, "entity_id": gq.get("entity_id"),
                           "gateway_ok": tr.get("ok"),
                           "gateway_error": tr.get("error") if not tr.get("ok") else None})
        results.append({"query": q, "expected": expected, "answer": answer, "hit": ok})
    return {"all_hit": hit == len(golden_queries) and len(golden_queries) > 0,
            "total": len(golden_queries), "hit": hit,
            "missed": missed, "results": results}


# ========== F3 完整发布流程 ==========
def run_publish_pipeline(*, hotel_id: str, initiated_by: str,
                         notes: Optional[str] = None,
                         test_query: str = "健身房在哪里",
                         audit_writer=None) -> dict[str, Any]:
    """执行 F3 完整发布 11 步. 任何步骤失败 → FAILED + 保持旧版本不变.

    成功返回: {release_id, version, job_id, active_runtime_version, test_answer}
    失败抛 PublishError (Job 已落库为 FAILED).
    """
    # Step 1: 创建 Release + Job
    release_id, version = rt.create_knowledge_release(
        hotel_id, created_by=initiated_by, notes=notes)
    job_id = create_publish_job(
        release_id=release_id, hotel_id=hotel_id, kind="publish",
        desired_version=version, initiated_by=initiated_by)

    try:
        # Step 2: 构建发布内容
        _transition_job(job_id, "BUILDING", audit_writer=audit_writer)
        payload = build_knowledge_payload(hotel_id)
        if payload["entity_count"] == 0:
            raise PublishError(f"hotel {hotel_id} 无 published 知识, 无法发布")

        # Step 3: 发布前校验 (F2)
        _transition_job(job_id, "VALIDATING", audit_writer=audit_writer)
        warnings = validate_release(hotel_id, payload)

        # Step 4: 构建 Immutable Package (落盘)
        manifest = build_release_package(
            hotel_id, payload, version, release_id)
        _update_release(release_id, content_sha256=manifest["content_sha256"],
                        index_sha256=manifest["index_sha256"],
                        entity_count=manifest["entity_count"],
                        attribute_count=manifest["attribute_count"],
                        package_dir=manifest["package_dir"],
                        embedding_version=manifest["embedding_version"])

        # Step 5: Runtime Shadow 加载 (P0-1: 真实 HTTP 调 gateway, 不冒充)
        _transition_job(job_id, "DEPLOYING_SHADOW", audit_writer=audit_writer)
        ack = RuntimeLoader.shadow_load(release_id, manifest["package_dir"],
                                        hotel_id=hotel_id)
        _record_ack(release_id, job_id, "shadow", ack)
        # P0-1: gateway 未 ACK (pending_gateway/failed) → 不得继续, Job FAILED
        if ack.get("ack_status") != "ok":
            raise PublishError(
                f"gateway shadow-load 未 ACK (ack_status={ack.get('ack_status')}, "
                f"http={ack.get('http_status')}, err={ack.get('error')}). "
                f"gateway 端点 /internal/knowledge/shadow-load 未实现或不可达. "
                f"publish 不得 APPLIED, 待 Batch A 实现 gateway 端点.")

        # Step 6+7: golden_queries 门禁 (§39.3 H2)
        # 不再用单 test_query, 改为 loop manifest 的 golden_queries (N≥10) via gateway /query.
        # 全命中 (answer 匹配 expected_answer_pattern 或 entity_id 命中) 才允许 activate.
        # 任一未命中 → PublishError, 不 atomic_activate.
        _transition_job(job_id, "SHADOW_TESTING", audit_writer=audit_writer)
        golden_queries = manifest.get("golden_queries", [])
        if len(golden_queries) < 10:
            raise PublishError(
                f"manifest golden_queries 不足 10 条 (got {len(golden_queries)}), "
                f"违反 §39.3 H2. 拒绝 activate.")
        golden_results = _run_golden_queries_gate(
            release_id=release_id, hotel_id=hotel_id,
            golden_queries=golden_queries)
        _record_ack(release_id, job_id, "golden_queries", {
            "ack_status": "ok" if golden_results["all_hit"] else "failed",
            "total": golden_results["total"],
            "hit": golden_results["hit"],
            "missed": golden_results["missed"][:5],   # 记前 5 条未命中
        }, query_test_result=golden_results)
        if not golden_results["all_hit"]:
            missed_summary = "; ".join(
                f"[{m['query']}→got:{m.get('answer')!r},期望:{m['expected_answer_pattern']!r}]"
                for m in golden_results["missed"][:3])
            raise PublishError(
                f"golden_queries 门禁未通过: {golden_results['hit']}/{golden_results['total']} 命中, "
                f"{len(golden_results['missed'])} 条未命中. 前 3 条: {missed_summary}. "
                f"拒绝 activate, runtime 保持旧版本.")

        # Step 8: admin 文件层原子切换 current + gateway activate ACK
        _transition_job(job_id, "ACTIVATING", audit_writer=audit_writer)
        current_link = atomic_activate(hotel_id, version)
        # P0-1: gateway activate 必须也 ACK (gateway 真实切换自己的 active runtime)
        gw_activate = RuntimeLoader.activate(release_id, hotel_id, version,
                                             manifest["package_dir"])
        _record_ack(release_id, job_id, "activate", gw_activate)
        if gw_activate.get("ack_status") != "ok":
            # gateway 未 ACK activate: 回滚 admin 文件层切换, 保持旧版本
            try:
                # 切回上一个 good 版本 (如果有)
                old_v = _previous_good_version(hotel_id, version)
                if old_v is not None:
                    atomic_activate(hotel_id, old_v)
                else:
                    deactivate_runtime(hotel_id)
            except Exception:
                pass
            raise PublishError(
                f"gateway activate 未 ACK (ack_status={gw_activate.get('ack_status')}, "
                f"http={gw_activate.get('http_status')}). 已回滚 admin 文件层.")

        # Step 9: 更新 active_runtime_version (数据库指针, 与 current 一致)
        _set_hotel_runtime_active(hotel_id, release_id, version, current_link)

        # Step 10: 完成
        _transition_job(job_id, "APPLIED", audit_writer=audit_writer,
                        extra_set="active_runtime_version=?, apply_status=?",
                        extra_vals=[version, "activated"])
        _update_release(release_id, status="applied", published_by=initiated_by,
                        published_at=now_ms())
        RuntimeLoader.drop_shadow(release_id, hotel_id=hotel_id)

        if audit_writer:
            audit_writer(action="knowledge_publish", resource="knowledge_release",
                         resource_id=release_id,
                         after_value={"version": version, "hotel_id": hotel_id},
                         message=f"published v{version}")

        return {"release_id": release_id, "version": version, "job_id": job_id,
                "active_runtime_version": version,
                "golden_queries_hit": f"{golden_results['hit']}/{golden_results['total']}",
                "warnings": warnings}

    except Exception as e:
        # Step 11: 失败保持旧版本不变
        _transition_job(job_id, "FAILED", error_message=str(e),
                        audit_writer=audit_writer)
        _update_release(release_id, status="failed")
        RuntimeLoader.drop_shadow(release_id, hotel_id=hotel_id)
        if audit_writer:
            audit_writer(action="system_error", resource="publish_job",
                         resource_id=job_id, result="error",
                         message=f"publish failed: {e}")
        raise PublishError(str(e)) from e


# ========== F4 回滚流程 ==========
def run_rollback_pipeline(*, hotel_id: str, target_version: int,
                          initiated_by: str, reason: Optional[str] = None,
                          test_query: str = "健身房在哪里",
                          audit_writer=None) -> dict[str, Any]:
    """执行 F4 回滚 9 步. 失败保持当前版本不变."""
    with get_conn() as conn:
        target = conn.execute(
            "SELECT * FROM knowledge_releases "
            "WHERE hotel_id=? AND version=? AND status='applied'",
            (hotel_id, target_version),
        ).fetchone()
    if not target:
        raise RollbackError(f"target release v{target_version} not found or not applied")
    target = dict(target)
    # 被回滚目标必须包完整
    pkg_dir = Path(target["package_dir"])
    if not (pkg_dir / "knowledge.json").exists() or not (pkg_dir / "manifest.json").exists():
        raise RollbackError(f"target package broken: {pkg_dir}")

    # 创建新 Release 记录 (回滚也产新版本号, 不改历史)
    release_id, version = rt.create_knowledge_release(
        hotel_id, created_by=initiated_by,
        notes=f"rollback to v{target_version}: {reason or ''}")
    job_id = create_publish_job(
        release_id=release_id, hotel_id=hotel_id, kind="rollback",
        desired_version=target_version, initiated_by=initiated_by)
    _update_release(release_id, source_release_id=target["release_id"],
                    rollback_from_version=_current_active_version(hotel_id))

    try:
        _transition_job(job_id, "ROLLING_BACK", audit_writer=audit_writer)
        # 直接复用历史 Package 做 shadow (不重新构建内容, 保持历史不可变)
        # P0-1: 真实 HTTP 调 gateway
        ack = RuntimeLoader.shadow_load(release_id, str(pkg_dir), hotel_id=hotel_id)
        _record_ack(release_id, job_id, "rollback_shadow", ack)
        if ack.get("ack_status") != "ok":
            raise RollbackError(
                f"gateway shadow-load 未 ACK (rollback): ack={ack.get('ack_status')}, "
                f"http={ack.get('http_status')}. 待 gateway 端点实现.")

        # golden_queries 门禁 (§39.3 H2, 回滚也必须验证)
        target_manifest_path = pkg_dir / "manifest.json"
        target_golden = []
        if target_manifest_path.exists():
            try:
                tm = json.loads(target_manifest_path.read_text(encoding="utf-8"))
                target_golden = tm.get("golden_queries", [])
            except Exception:
                pass
        if len(target_golden) < 10:
            raise RollbackError(
                f"target v{target_version} manifest golden_queries 不足 10 条 "
                f"(got {len(target_golden)}), 违反 §39.3 H2. 拒绝 rollback activate.")
        rb_golden = _run_golden_queries_gate(
            release_id=release_id, hotel_id=hotel_id,
            golden_queries=target_golden)
        _record_ack(release_id, job_id, "rollback_golden_queries", {
            "ack_status": "ok" if rb_golden["all_hit"] else "failed",
            "total": rb_golden["total"], "hit": rb_golden["hit"],
            "missed": rb_golden["missed"][:5],
        }, query_test_result=rb_golden)
        if not rb_golden["all_hit"]:
            raise RollbackError(
                f"rollback golden_queries 门禁未通过: {rb_golden['hit']}/{rb_golden['total']}. "
                f"拒绝 activate, 保持当前版本.")

        # admin 文件层原子切回历史版本目录 + gateway activate ACK
        current_link = atomic_activate(hotel_id, target_version)
        # P0-1e: activate 必须用 shadow_load 同一个 release_id (gateway 按 release_id 查 shadow ns),
        # 不能用 target["release_id"] (历史 v 的原始 id, 未在本轮 shadow-load) -> ack=failed.
        gw_activate = RuntimeLoader.activate(
            release_id, hotel_id, target_version, str(pkg_dir))
        _record_ack(release_id, job_id, "rollback_activate", gw_activate)
        if gw_activate.get("ack_status") != "ok":
            # gateway 未 ACK: 切回回滚前的版本, 保持现状
            from_v = _current_active_version(hotel_id)
            if from_v is not None:
                try:
                    atomic_activate(hotel_id, from_v)
                except Exception:
                    pass
            raise RollbackError(
                f"gateway activate 未 ACK (rollback): ack={gw_activate.get('ack_status')}. "
                f"已回滚 admin 文件层.")
        _set_hotel_runtime_active(hotel_id, target["release_id"], target_version,
                                  current_link, is_rollback=True,
                                  rollback_release_id=release_id)
        _transition_job(job_id, "ROLLED_BACK", audit_writer=audit_writer,
                        extra_set="active_runtime_version=?, apply_status=?",
                        extra_vals=[target_version, "activated"])
        _update_release(release_id, status="rolled_back",
                        published_by=initiated_by, published_at=now_ms())
        RuntimeLoader.drop_shadow(release_id, hotel_id=hotel_id)

        if audit_writer:
            audit_writer(action="knowledge_rollback", resource="knowledge_release",
                         resource_id=release_id,
                         before_value={"from_version": _current_active_version(hotel_id)},
                         after_value={"rolled_back_to": target_version},
                         message=reason or "")
        return {"release_id": release_id, "job_id": job_id,
                "rolled_back_to_version": target_version,
                "golden_queries_hit": f"{rb_golden['hit']}/{rb_golden['total']}"}

    except Exception as e:
        _transition_job(job_id, "FAILED", error_message=str(e),
                        audit_writer=audit_writer)
        _update_release(release_id, status="failed")
        RuntimeLoader.drop_shadow(release_id, hotel_id=hotel_id)
        if audit_writer:
            audit_writer(action="system_error", resource="publish_job",
                         resource_id=job_id, result="error",
                         message=f"rollback failed: {e}")
        raise RollbackError(str(e)) from e


# ========== 辅助 ==========
def _update_release(release_id: str, **fields) -> None:
    if not fields:
        return
    sets = [f"{k}=?" for k in fields]
    vals = list(fields.values()) + [release_id]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE knowledge_releases SET {', '.join(sets)} WHERE release_id=?",
            vals,
        )


def _record_ack(release_id: str, job_id: str, load_kind: str,
                ack: dict[str, Any], query_test_result: Optional[dict] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runtime_load_acks(release_id, job_id, load_kind, "
            "ack_status, ack_payload, query_test_result, acked_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (release_id, job_id, load_kind, ack.get("ack_status", "unknown"),
             json.dumps(ack, ensure_ascii=False),
             json.dumps(query_test_result, ensure_ascii=False) if query_test_result else None,
             now_ms()),
        )
        if job_id and ack.get("ack_status") == "ok":
            conn.execute(
                "UPDATE publish_jobs SET shadow_ack=? WHERE job_id=?",
                (json.dumps(ack, ensure_ascii=False), job_id),
            )


def _set_hotel_runtime_active(hotel_id: str, release_id: str, version: int,
                              current_link: str, *,
                              is_rollback: bool = False,
                              rollback_release_id: Optional[str] = None) -> None:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT hotel_id FROM hotel_runtime_state WHERE hotel_id=?",
            (hotel_id,),
        ).fetchone()
        last_good = version
        if existing:
            conn.execute(
                "UPDATE hotel_runtime_state SET active_release_id=?, "
                "active_runtime_version=?, last_good_version=?, desired_version=?, "
                "apply_status='activated', current_symlink=?, updated_at=? "
                "WHERE hotel_id=?",
                (release_id, version, last_good, version, current_link,
                 now_ms(), hotel_id),
            )
        else:
            conn.execute(
                "INSERT INTO hotel_runtime_state("
                "hotel_id, active_release_id, active_runtime_version, "
                "last_good_version, desired_version, apply_status, "
                "current_symlink, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (hotel_id, release_id, version, last_good, version,
                 "activated", current_link, now_ms()),
            )


def _current_active_version(hotel_id: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT active_runtime_version FROM hotel_runtime_state WHERE hotel_id=?",
            (hotel_id,),
        ).fetchone()
    return row["active_runtime_version"] if row else None


def _previous_good_version(hotel_id: str, exclude_version: int) -> Optional[int]:
    """找上一个 applied 的历史版本 (publish 失败回滚用)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT version FROM knowledge_releases "
            "WHERE hotel_id=? AND status='applied' AND version<>? "
            "ORDER BY version DESC LIMIT 1",
            (hotel_id, exclude_version),
        ).fetchone()
    return row["version"] if row else None


def list_releases(hotel_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_releases WHERE hotel_id=? ORDER BY version DESC",
            (hotel_id,),
        ).fetchall()
    return [dict(r) for r in rows]
