#!/usr/bin/env python3
"""JOCTV Agent V3.1 — P4 知识发布 Runtime Store (Batch F1-F5).

对应 30日凌晨任务.md §十:
- F1 两级版本: Entity Revision (单条编辑版本, 见 publish_versions) + Knowledge Release (整酒店发布包).
- F2 发布前校验: schema/必填/重复/冲突/格式/语言/来源/embedding/index.
- F3 实际发布: Immutable Package → 写入候选 Runtime 目录 → Shadow Load → Runtime ACK → 原子切换.
- F4 回滚: 选历史 Release → 校验 → Shadow → 原子切换 → ACK, 保留旧版本不删.

本模块是"真实 Runtime"的权威实现:
- runtime_data/<hotel>/releases/vNNNN/knowledge.json + manifest.json
- runtime_data/<hotel>/current -> releases/vNNNN (符号链接, 原子切换 via rename)
- query_runtime() 真实读取 current/knowledge.json 回答查询 (非数据库 published 冒充).

红线:
- 后台 Publish 不能只改数据库状态. 必须真实落盘 + Runtime 加载 + ACK.
- 原子切换: 临时链接 → rename. 不允许直接 rm -rf current.
- 回滚保留历史版本, 不删.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .db import gen_id, get_conn, now_ms

# Runtime 根目录 (可由环境变量覆盖)
RUNTIME_ROOT = Path(os.environ.get(
    "P4_RUNTIME_ROOT",
    Path(__file__).resolve().parent.parent / "data" / "runtime_data",
))


def _hotel_dir(hotel_id: str) -> Path:
    return RUNTIME_ROOT / f"hotel_{hotel_id}"


def _releases_dir(hotel_id: str) -> Path:
    return _hotel_dir(hotel_id) / "releases"


def _current_link(hotel_id: str) -> Path:
    return _hotel_dir(hotel_id) / "current"


# ========== 构建发布内容 (从 DB 读 published 知识) ==========
def collect_published_knowledge(hotel_id: str) -> tuple[list[dict], list[dict]]:
    """收集一个酒店所有 publish_state='published' 且 enabled 的知识实体+属性."""
    with get_conn() as conn:
        ents = [dict(r) for r in conn.execute(
            "SELECT * FROM knowledge_entities "
            "WHERE hotel_id=? AND publish_state='published' AND enabled=1",
            (hotel_id,),
        ).fetchall()]
        ent_ids = [e["id"] for e in ents]
        attrs: list[dict] = []
        if ent_ids:
            placeholders = ",".join("?" * len(ent_ids))
            attrs = [dict(r) for r in conn.execute(
                f"SELECT * FROM knowledge_attributes WHERE entity_id IN ({placeholders})",
                ent_ids,
            ).fetchall()]
    return ents, attrs


# attr_key → gateway entry 字段名映射
_ATTR_TO_ENTRY_FIELD = {
    "location": "location", "地址": "location",
    "floor": "floor", "楼层": "floor",
    "hours": "open_hours", "营业时间": "open_hours", "时间": "open_hours",
    "price": "price", "价格": "price",
    "phone": "phone", "电话": "phone",
    "notes": "notes", "备注": "notes",
}
# attr_key → 中文显示名 (short_replies 的 key 用中文, 对齐 gateway 匹配)
_ATTR_KEY_TO_CN = {
    "location": "位置", "hours": "营业时间", "price": "价格",
    "phone": "电话", "ssid": "网络名称", "password": "密码",
    "notes": "备注",
}
# gateway 可匹配的 short_reply key 白名单 (golden_queries 只从这些 key 生成).
# gateway _phase2_query_facility 对 location/open_hours/price 有结构化匹配,
# 对其他 key 匹配不稳定, 故 golden_queries 只验证这些.
_GATEWAY_MATCHABLE_KEYS = {
    "在哪里", "在哪", "位置", "几点", "时间", "营业时间", "多少钱", "价格", "电话", "电话号码",
}


def build_knowledge_payload(hotel_id: str) -> dict[str, Any]:
    """构建 knowledge.json 内容. 同时输出 admin entities 结构 + gateway entries 结构.

    gateway (server_phase2_candidate.py) 期望 {"entries": [...]} 扁平结构,
    每条 entry 字段: knowledge_id/display_name/aliases/location/floor/open_hours/
    price/phone/reply_template/short_replies.
    admin 内部用 entities (entity→attributes 嵌套) 做版本管理.
    两者都写入 knowledge.json, 兼容双方.
    """
    ents, attrs = collect_published_knowledge(hotel_id)
    # 按 entity 聚合属性
    attrs_by_ent: dict[str, list[dict]] = {}
    for a in attrs:
        attrs_by_ent.setdefault(a["entity_id"], []).append({
            "key": a["attr_key"], "value": a["attr_value"],
            "value_type": a["value_type"], "language": a["language"],
            "confidence": a["confidence"], "source": a["source"],
        })
    entities_out = []
    entries_out = []   # gateway 兼容格式
    # attr_key → gateway entry 字段名映射 (用模块级常量)
    KEY_TO_ENTRY_FIELD = _ATTR_TO_ENTRY_FIELD
    for e in ents:
        aliases = e["aliases"]
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (ValueError, TypeError):
                aliases = []
        name = e["canonical_name"]
        e_attrs = attrs_by_ent.get(e["id"], [])
        entities_out.append({
            "id": e["id"], "entity_type": e["entity_type"],
            "canonical_name": name, "aliases": aliases or [],
            "language": e["language"], "attributes": e_attrs,
        })
        # 构建 gateway entry (扁平, 对齐 server_phase2_candidate.py 期望)
        # gateway 匹配依赖: display_name/aliases/entity_ref/facility/reply_template/short_replies
        loc_val = ""
        hrs_val = ""
        price_val = ""
        for a in e_attrs:
            k = a["key"]; v = str(a["value"])
            field = KEY_TO_ENTRY_FIELD.get(k)
            if field:
                entry_field_map = {field: v}
            else:
                entry_field_map = {}
        # 先扫一遍填顶层字段
        top_fields: dict[str, str] = {}
        for a in e_attrs:
            k = a["key"]; v = str(a["value"])
            field = KEY_TO_ENTRY_FIELD.get(k)
            if field:
                top_fields[field] = v
        loc_val = top_fields.get("location", "")
        hrs_val = top_fields.get("open_hours", "")
        price_val = top_fields.get("price", "")
        entry: dict[str, Any] = {
            "knowledge_id": e["id"],
            "display_name": name,
            "aliases": aliases or [],
            "enabled": True,
        }
        entry.update(top_fields)
        # gateway 结构化匹配依赖 facility 嵌套对象 (含 facility_id + 各属性)
        facility_id = e["entity_type"] + "_" + e["id"][:8]
        fac_obj: dict[str, Any] = {
            "facility_id": facility_id,
            "display_name": name,
            "name": name,
        }
        for a in e_attrs:
            k = a["key"]; v = str(a["value"])
            field = KEY_TO_ENTRY_FIELD.get(k)
            if field:
                fac_obj[field] = v
        entry["facility"] = fac_obj
        # short_replies: key 用英文 (对齐 gateway seed: open_hours/location/price)
        short_replies: dict[str, str] = {}
        if loc_val:
            short_replies["location"] = f"{name}在{loc_val}。"
        if hrs_val:
            short_replies["open_hours"] = f"{name}营业时间是{hrs_val}。"
        if price_val:
            short_replies["price"] = f"{name}{price_val}。"
        # 额外中文 key 的回复 (方便 golden_queries 多样性, gateway 不强依赖)
        if loc_val:
            short_replies["位置"] = f"{name}在{loc_val}。"
        if hrs_val:
            short_replies["营业时间"] = f"{name}营业时间是{hrs_val}。"
        entry["short_replies"] = short_replies
        # reply_template (顶层, gateway 第二条匹配路径用)
        parts = []
        if loc_val: parts.append(f"{name}在{{location}}")
        if hrs_val: parts.append(f"营业时间是{{open_hours}}")
        if price_val: parts.append(f"{{price}}")
        entry["reply_template"] = "，".join(parts) + "。" if parts else f"{name}的信息。"
        entry["entity_ref"] = f"admin_knowledge.entity.{e['id']}"
        entries_out.append(entry)
    return {"hotel_id": hotel_id,
            "entities": entities_out,           # admin 内部结构
            "entries": entries_out,             # gateway 兼容 (server_phase2_candidate.py 期望)
            "built_at": now_ms(),
            "entity_count": len(entities_out),
            "attribute_count": len(attrs)}


# ========== F2 发布前校验 ==========
class ValidationError(ValueError):
    pass


def validate_release(hotel_id: str, payload: dict[str, Any]) -> list[str]:
    """发布前校验. 返回 warning 列表; 有硬错误抛 ValidationError."""
    errors: list[str] = []
    warnings: list[str] = []
    seen_names: dict[str, str] = {}  # canonical_name -> entity_id (查重)
    for e in payload.get("entities", []):
        eid = e["id"]
        name = e.get("canonical_name", "").strip()
        if not name:
            errors.append(f"实体 {eid} 缺 canonical_name")
        if name and name in seen_names:
            errors.append(f"重复实体 canonical_name={name} (entity {eid} 与 {seen_names[name]})")
        seen_names[name] = eid
        # 必填属性检查 (至少有 1 个属性)
        attrs = e.get("attributes", [])
        if not attrs:
            warnings.append(f"实体 {name} 无任何属性")
        for a in attrs:
            key = a.get("key")
            val = a.get("value")
            if not key:
                errors.append(f"实体 {name} 有属性缺 key")
            if val is None or val == "":
                errors.append(f"实体 {name} 属性 {key} 值为空")
            # 营业时间格式粗校验 (hours 含 ':' 且看起来像时间)
            if key in ("hours", "营业时间") and val:
                if ":" not in str(val) and "-" not in str(val):
                    warnings.append(f"实体 {name} hours 格式可疑: {val}")
    if errors:
        raise ValidationError("; ".join(errors))
    return warnings


# ========== 计算内容 sha256 ==========
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ========== F3 构建 Immutable Release Package ==========
def build_release_package(hotel_id: str, payload: dict[str, Any],
                          version: int, release_id: str,
                          embedding_version: str = "v1-bow-zh") -> dict[str, Any]:
    """落盘 Immutable Package. 返回 manifest 元数据."""
    rel_dir = _releases_dir(hotel_id) / f"v{version:04d}"
    rel_dir.mkdir(parents=True, exist_ok=True)
    # knowledge.json
    kj_path = rel_dir / "knowledge.json"
    # Codex ISSUE 2a: content_sha 排除 built_at 等易变字段 (同业务内容应同内容指纹).
    # built_at 已在 manifest 记录 (provenance); knowledge.json 用 stable 子集落盘,
    # 保证 sha256sum(knowledge.json)==manifest.content_sha256 且同内容稳定.
    _payload_stable = {k: v for k, v in payload.items() if k != "built_at"}
    kj_bytes = json.dumps(_payload_stable, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    kj_path.write_bytes(kj_bytes)
    content_sha = _sha256_bytes(kj_bytes)
    # embeddings 目录 (本版用简单 BoW 索引占位, 真实 embedding 由专门服务构建)
    emb_dir = rel_dir / "embeddings"
    emb_dir.mkdir(exist_ok=True)
    # 构建 keyword 索引 (location/hours/价格 等关键词 → entity)
    index: dict[str, list[str]] = {}
    for e in payload.get("entities", []):
        keywords = [e["canonical_name"]] + e.get("aliases", [])
        keywords.append(e.get("entity_type", ""))
        for a in e.get("attributes", []):
            keywords.append(a.get("key", ""))
        for kw in keywords:
            if kw:
                index.setdefault(kw, [])
                if e["id"] not in index[kw]:
                    index[kw].append(e["id"])
    idx_bytes = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
    (emb_dir / "keyword_index.json").write_bytes(idx_bytes)
    index_sha = _sha256_bytes(idx_bytes)
    # golden_queries 门禁 (§39.3 H2): 每个 Release Package 必须携带 N≥10 条
    # golden_queries, admin publish 必须 loop 全部 via gateway /query, 全命中才 activate.
    golden_queries = _build_golden_queries(payload)
    # manifest
    manifest = {
        "release_id": release_id, "hotel_id": hotel_id, "version": version,
        "content_sha256": content_sha, "index_sha256": index_sha,
        "entity_count": payload.get("entity_count", len(payload.get("entities", []))),
        "attribute_count": payload.get("attribute_count", 0),
        "embedding_version": embedding_version,
        "golden_queries": golden_queries,
        "golden_query_count": len(golden_queries),
        "built_at": now_ms(),
        "package_dir": str(rel_dir),
    }
    (rel_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # fsync 目录保证落盘
    _fsync_dir(rel_dir)
    return manifest


# golden_queries 从 entries.short_replies 派生 (对齐 gateway 匹配逻辑).
# 旧 _QUERY_TEMPLATES_BY_KEY 已废弃 (query 模板与 gateway 匹配不对齐).


def _build_golden_queries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 payload 的 entries 派生 golden_queries (N>=10), P0-3: 按 entity 实际属性生成.

    gateway (server_phase2_candidate.py) _phase2_query_facility 对 open_hours/location/price
    三类查询均稳定返回 short_replies(P0-1c 后 facility entity 路径1 结构化降级会落路径2
    short_replies 兜底). 故每条 golden query 按 entity 实际拥有的属性生成, query 用中文
    自然语言, expected_answer_pattern 用属性原始值(gateway short_reply 内嵌该值, 子串命中).
    """
    # (entry 顶层属性字段, 查询后缀, attr 标签)
    ATTRS = [("location", "在哪里", "location"),
             ("open_hours", "几点", "open_hours"),
             ("price", "多少钱", "price")]
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries = payload.get("entries", [])

    def _emit(name: str, field: str, suffix: str, label: str,
              eid: str, entry: dict[str, Any]) -> None:
        val = str(entry.get(field, "") or "")
        if not name or not val:
            return
        q = f"{name}{suffix}"
        if q in seen:
            return
        seen.add(q)
        queries.append({"query": q, "expected_answer_pattern": val,
                        "entity_id": eid, "attr_key": label})

    for entry in entries:
        name = entry.get("display_name", "")
        eid = entry.get("knowledge_id", "")
        for field, suffix, label in ATTRS:
            _emit(name, field, suffix, label, eid, entry)
    # 不足 10 条: 用 alias 变体补(同一 entity 的 alias 对其各属性再生成)
    if len(queries) < 10:
        for entry in entries:
            if len(queries) >= 10:
                break
            eid = entry.get("knowledge_id", "")
            for alias in (entry.get("aliases") or []):
                if len(queries) >= 10:
                    break
                for field, suffix, label in ATTRS:
                    _emit(alias, field, suffix, label, eid, entry)
    return queries


def _extract_pattern_from_reply(reply: str, name: str, key: str,
                                entry: dict) -> str:
    """从 gateway short_reply 提取 expected_pattern.

    reply 形如 "早餐在2F 西餐厅。" → pattern "2F 西餐厅".
    若无法提取, 用 entry 里对应字段值 (location/open_hours/price 等).
    """
    # key → entry 字段名映射
    KEY_FIELD = {"location": "location", "在哪里": "location", "在哪": "location",
                 "open_hours": "open_hours", "几点": "open_hours", "时间": "open_hours",
                 "price": "price"}
    field = KEY_FIELD.get(key)
    if field and entry.get(field):
        return str(entry[field])
    # 兜底: reply 去掉 name 前缀和标点
    import re as _re
    pat = _re.sub(rf'^.*?{ _re.escape(name)}', '', reply)
    pat = _re.sub(r'[。.!！？?，,]+$', '', pat).strip()
    # 去掉常见前缀 ("是", "在")
    pat = _re.sub(r'^(是|在|营业时间是)', '', pat).strip()
    return pat or reply


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # 某些文件系统不支持


# ========== Runtime Shadow Load + 测试查询 + ACK ==========
# P0-1 修复: 不再用进程内 dict mock, 改为真实 HTTP 调用 v3-gateway.
# gateway 端点 POST /internal/knowledge/shadow-load 由 Batch A 在 server.py 实现.
# 当前 gateway 无此端点 → 真实失败 → publish 不得 APPLIED, Job 标 pending_gateway.

import os as _os

# Gateway 地址 (v3-gateway :8765). 本地回环, 不走公网.
GATEWAY_BASE_URL = _os.environ.get(
    "P4_GATEWAY_BASE_URL", "http://127.0.0.1:8765")
GATEWAY_SHADOW_LOAD_PATH = "/internal/knowledge/shadow-load"
GATEWAY_QUERY_PATH = "/internal/knowledge/query"
GATEWAY_ACTIVATE_PATH = "/internal/knowledge/activate"
GATEWAY_HTTP_TIMEOUT = 5.0  # 秒


def _gateway_post(path: str, payload: dict, timeout: float = GATEWAY_HTTP_TIMEOUT) -> dict:
    """真实 HTTP POST 到 v3-gateway. 返回 {ack_status, http_status, body, error}.

    gateway 未实现端点 → http_status=404/000 → ack_status=pending_gateway.
    不冒充成功.
    """
    import httpx
    url = GATEWAY_BASE_URL + path
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
        body_text = resp.text
        try:
            body_json = resp.json()
        except Exception:
            body_json = None
        if resp.status_code == 200:
            ack = "ok"
        elif resp.status_code == 404:
            ack = "pending_gateway"   # gateway 未实现此端点
        else:
            ack = "failed"
        return {"ack_status": ack, "http_status": resp.status_code,
                "body": body_json or body_text, "error": None}
    except httpx.ConnectError as e:
        return {"ack_status": "pending_gateway",
                "http_status": 0, "body": None,
                "error": f"gateway connect failed: {e}"}
    except Exception as e:
        return {"ack_status": "failed", "http_status": 0, "body": None,
                "error": f"gateway call error: {e}"}


class RuntimeLoader:
    """Runtime 加载器: 真实 HTTP 调用 v3-gateway.

    职责:
    - shadow_load: POST gateway /internal/knowledge/shadow-load, 等 gateway ACK.
    - test_query: POST gateway /internal/knowledge/query (对 shadow namespace).
    - activate: POST gateway /internal/knowledge/activate (gateway 真实切换自己的 runtime).
    - drop_shadow: 通知 gateway 丢弃 shadow (best-effort).

    红线 (P0-1):
    - 不再用进程内 _shadow dict 冒充 Runtime.
    - gateway 未 ACK (404/连接失败) 时, ack_status != "ok", publish 不得 APPLIED.
    - 文件层 current 符号链接 + atomic_activate + query_runtime 真读盘 是真实文件系统操作,
      但只是 admin 侧的"版本目录管理", 不等于 Gateway Runtime 已加载. Gateway ACK 才算真闭环.
    """
    @classmethod
    def shadow_load(cls, release_id: str, package_dir: str,
                    hotel_id: str = None) -> dict[str, Any]:
        """真实调用 gateway shadow-load. gateway 无端点 → pending_gateway."""
        kj_path = Path(package_dir) / "knowledge.json"
        if not kj_path.exists():
            return {"ack_status": "failed", "error": f"knowledge.json not found: {kj_path}",
                    "http_status": None}
        payload = {
            "release_id": release_id,
            "hotel_id": hotel_id,
            "package_dir": str(package_dir),
            "knowledge_json_path": str(kj_path),
        }
        return _gateway_post(GATEWAY_SHADOW_LOAD_PATH, payload)

    @classmethod
    def test_query(cls, release_id: str, query: str,
                   hotel_id: str = None) -> dict[str, Any]:
        """真实调用 gateway query (对 shadow namespace). gateway 无端点 → 失败.

        注意: 当 gateway 未实现时, 测试查询会失败. 这是诚实的 —— 没有真 Runtime 就
        无法测试查询. 不能用本地 _answer_from_payload 冒充 gateway 的回答.
        """
        payload = {"release_id": release_id, "hotel_id": hotel_id, "query": query,
                   "namespace": "shadow"}
        result = _gateway_post(GATEWAY_QUERY_PATH, payload)
        if result.get("ack_status") == "ok" and isinstance(result.get("body"), dict):
            return {"ok": True, "query": query,
                    "answer": result["body"].get("answer"),
                    "gateway_body": result["body"]}
        return {"ok": False, "query": query,
                "error": f"gateway query failed: {result.get('error') or result.get('http_status')}",
                "gateway_result": result}

    @classmethod
    def activate(cls, release_id: str, hotel_id: str, version: int,
                 package_dir: str) -> dict[str, Any]:
        """真实通知 gateway 切换 active runtime. gateway 无端点 → pending_gateway."""
        payload = {"release_id": release_id, "hotel_id": hotel_id,
                   "version": version, "package_dir": str(package_dir)}
        return _gateway_post(GATEWAY_ACTIVATE_PATH, payload)

    @classmethod
    def drop_shadow(cls, release_id: str, hotel_id: str = None) -> None:
        """通知 gateway 丢弃 shadow. gateway 无端点时静默 (best-effort)."""
        try:
            _gateway_post("/internal/knowledge/drop-shadow",
                          {"release_id": release_id, "hotel_id": hotel_id},
                          timeout=2.0)
        except Exception:
            pass


def _answer_from_payload(payload: dict[str, Any], query: str) -> Optional[str]:
    """从 knowledge payload 回答简单查询 (关键词匹配)."""
    q = query.lower()
    for e in payload.get("entities", []):
        names = [e["canonical_name"]] + e.get("aliases", [])
        if any(n and n in query for n in names):
            # 找到匹配实体, 返回所有属性拼接
            parts = []
            for a in e.get("attributes", []):
                parts.append(f"{a['key']}={a['value']}")
            return "; ".join(parts) if parts else f"找到 {e['canonical_name']} 但无属性"
    return None


# ========== 原子切换 current 符号链接 (F3 步骤 8) ==========
def atomic_activate(hotel_id: str, version: int) -> str:
    """原子切换 current -> releases/vNNNN. 返回 current 绝对路径."""
    target = _releases_dir(hotel_id) / f"v{version:04d}"
    if not target.exists():
        raise FileNotFoundError(f"release dir not found: {target}")
    link = _current_link(hotel_id)
    link.parent.mkdir(parents=True, exist_ok=True)
    # 临时链接 → fsync → rename (POSIX 原子)
    tmp_link = link.parent / f".current.tmp.{os.getpid()}.{int(time.time()*1000)}"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(str(target), str(tmp_link))
    _fsync_dir(link.parent)
    os.replace(str(tmp_link), str(link))
    _fsync_dir(link.parent)
    return str(link)


def deactivate_runtime(hotel_id: str) -> None:
    """移除 current 链接 (用于发布失败清理). 不删 releases 目录."""
    link = _current_link(hotel_id)
    if link.is_symlink() or link.exists():
        link.unlink()


# ========== 真实 Runtime 查询 (F5 验收) ==========
def query_runtime(hotel_id: str, query: str) -> dict[str, Any]:
    """真实调用 gateway query. gateway 无端点 → 返回 error, 不本地冒充.

    P0-1 修复: 不再用 _answer_from_payload 本地匹配冒充 gateway 回答.
    本地仅返回 current 符号链接的版本号 + entity_count (admin 版本目录状态),
    answer 字段必须来自 gateway.
    """
    # 文件系统层: 读 current 指向的版本号 (这是 admin 版本目录状态, 真实)
    link = _current_link(hotel_id)
    actual_version = None
    entity_count = None
    if link.exists():
        try:
            actual_version = int(link.resolve().name.lstrip("v"))
        except Exception:
            actual_version = None
        kj = link / "knowledge.json"
        if kj.exists():
            try:
                payload = json.loads(kj.read_text(encoding="utf-8"))
                entity_count = payload.get("entity_count", len(payload.get("entities", [])))
            except Exception:
                pass
    # Gateway 层: 真实 HTTP 查询 answer
    gw = _gateway_post(GATEWAY_QUERY_PATH,
                       {"hotel_id": hotel_id, "query": query, "namespace": "active"})
    answer = None
    gateway_ok = False
    if gw.get("ack_status") == "ok" and isinstance(gw.get("body"), dict):
        answer = gw["body"].get("answer")
        gateway_ok = True
    return {
        "hotel_id": hotel_id,
        "query": query,
        "answer": answer,                       # 来自 gateway, 未实现时为 None
        "gateway_ok": gateway_ok,               # gateway 是否真实响应
        "gateway_http_status": gw.get("http_status"),
        "gateway_error": gw.get("error") if not gateway_ok else None,
        "active_runtime_version": actual_version,   # admin 版本目录状态 (真实读盘)
        "current_link": str(link),
        "current_link_exists": link.exists(),
        "entity_count": entity_count,
    }


def query_local_package(hotel_id: str, query: str) -> dict[str, Any]:
    """纯文件系统检查: 读 admin 版本目录的 current/knowledge.json 做关键词匹配.

    明确标注: 这是 admin 侧落盘内容验证, 不是 Gateway Runtime 的回答.
    用于 F5 验收时确认"发布包内容正确写入磁盘", 但不等于 Gateway 已加载.
    """
    link = _current_link(hotel_id)
    if not link.exists():
        return {"ok": False, "hotel_id": hotel_id, "error": "current not activated",
                "answer": None, "source": "local_package"}
    kj = link / "knowledge.json"
    if not kj.exists():
        return {"ok": False, "hotel_id": hotel_id, "error": "knowledge.json missing",
                "answer": None, "source": "local_package"}
    payload = json.loads(kj.read_text(encoding="utf-8"))
    answer = _answer_from_payload(payload, query)
    try:
        version = int(link.resolve().name.lstrip("v"))
    except Exception:
        version = None
    return {"ok": True, "hotel_id": hotel_id, "answer": answer,
            "active_runtime_version": version, "source": "local_package",
            "entity_count": payload.get("entity_count", len(payload.get("entities", []))),
            "note": "admin 版本目录内容验证, 非 Gateway Runtime 回答"}


def get_runtime_status(hotel_id: str) -> dict[str, Any]:
    """读取 hotel_runtime_state + current 实际指向."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hotel_runtime_state WHERE hotel_id=?", (hotel_id,),
        ).fetchone()
    state = dict(row) if row else None
    # 真实读 current 链接
    link = _current_link(hotel_id)
    actual_version = None
    if link.exists():
        try:
            actual_version = int(link.resolve().name.lstrip("v"))
        except Exception:
            actual_version = None
    return {"db_state": state, "current_link": str(link),
            "current_link_exists": link.exists(),
            "actual_runtime_version": actual_version}


# ========== 创建 Knowledge Release 记录 (F1) ==========
def create_knowledge_release(hotel_id: str, created_by: str,
                             notes: Optional[str] = None) -> tuple[str, int]:
    """创建新 Release 记录 (status=draft). 返回 (release_id, version)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_v "
            "FROM knowledge_releases WHERE hotel_id=?",
            (hotel_id,),
        ).fetchone()
        version = row["next_v"]
        release_id = gen_id("kr")
        conn.execute(
            "INSERT INTO knowledge_releases("
            "release_id, hotel_id, version, status, created_by, created_at, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (release_id, hotel_id, version, "draft", created_by, now_ms(), notes),
        )
    return release_id, version
