#!/usr/bin/env python3
"""joctv-safety-feed-skill 仓库侧 E2E 门 (需 zstandard + cryptography; 本机
临时目录执行, 不触候选 :8090/:8796, 绝不触生产 :8774/:8775)。

证明链 (任务箱 UI26-07-03):

  E01 镜像一致性: Skill 内置平台类别码 == 运行时 _BUILTIN_CATEGORY_CODES
  E02 现行规则库 (p4_admin/tools/safety_feed_v1.json) import 为版本链起点
  E03 真实变更请求 (增/改/删/新类别) 生成下一完整版本, 校验器全绿
  E04 冻结打包工具 (p4_admin/tools/safety_feed_build.py, 临时测试密钥,
      绝不使用/落盘真实开发密钥) 构建签名包 — 工具自校验即消费者代码
  E05 本机 HTTP 伺服 → 运行时真实消费链路 fetch_manifest →
      verify_manifest_signature → fetch_files → compile_snapshot_from_package
      (与 run_update_check 阶段 2–6 同一函数) 接受该包
  E06 编译快照行为回读: 新短语/token/受限正则命中、修改后旧短语放行新短语
      命中、删除后放行、未触碰规则不回归
  E07 旧版输入不被修改: 现行规则库与 genesis 文件哈希全程不变
  E08 确定性: 重复生成字节等价
  E09 失败反例被消费者拒绝 (有效签名但内容非法 → 构建即拒, 精确错误码):
      重复规则 id / 内置类别碰撞 / block 缺恢复话术 / 危险正则
  E10 网络面反例: 下载字节 SHA 不一致 → FILE_SHA_MISMATCH;
      清单篡改 → SIGNATURE_INVALID

运行 (仓库 3.1 下, 用带 zstandard/cryptography 的解释器):
  python3 tools/joctv-safety-feed-skill/scripts/test_feed_skill_e2e.py
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]          # .../3.1
P4 = REPO / "p4_admin"
SKILL = Path(__file__).resolve().parents[1]
GEN = SKILL / "scripts" / "generate_feed_version.py"
VAL = SKILL / "scripts" / "validate_feed_source.py"
LIB = P4 / "tools" / "safety_feed_v1.json"
BUILD_TOOL = P4 / "tools" / "safety_feed_build.py"

sys.path.insert(0, str(P4))
os.environ.setdefault("P4_SKIP_SEED", "1")

import api.safety_rule_feed as F  # noqa: E402
from api.safety_rule_feed import FeedError  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {name}: {'PASS' if ok else 'FAIL ' + str(detail)[:200]}")


def expect_feed_error(name: str, fn, code: str) -> None:
    try:
        fn()
    except FeedError as e:
        chk(name, e.code == code, f"期望 {code} 实际 {e.code}")
        return
    except Exception as e:  # noqa: BLE001
        chk(name, False, f"非 FeedError: {type(e).__name__}: {e}")
        return
    chk(name, False, "未抛出 FeedError (检测力缺失)")


def run_py(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=env)


class _Server(http.server.ThreadingHTTPServer):
    def __init__(self, root: Path):
        self.root = root
        host, port = "127.0.0.1", 0
        super().__init__((host, port), _Handler)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        srv = self.server
        name = path.lstrip("/").split("?")[0]
        if "/" in name or name.startswith("."):
            return "/dev/null"
        return str(srv.root / name)

    def log_message(self, *a):  # 静默
        pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    skill_codes = F._BUILTIN_CATEGORY_CODES  # 运行时真集
    from validate_feed_source import PLATFORM_BUILTIN_CATEGORY_CODES
    chk("E01-builtin-mirror", skill_codes == PLATFORM_BUILTIN_CATEGORY_CODES,
        f"drift: {sorted(skill_codes ^ PLATFORM_BUILTIN_CATEGORY_CODES)}")

    with tempfile.TemporaryDirectory(prefix="v26_07_e2e_") as td:
        tmp = Path(td)
        lib_sha_before = _sha(LIB)

        # E02: import 现行规则库为版本链起点
        genesis = tmp / "safety-feed-src.s0001.json"
        r = run_py(GEN, "import", "--content", str(LIB), "--sequence", "1",
                   "--version", "2026.09.02-1", "--out", str(genesis))
        chk("E02-import-library", r.returncode == 0, r.stderr.strip()[:200])
        r = run_py(VAL, str(genesis))
        chk("E02-genesis-valid", r.returncode == 0, r.stderr.strip()[:200])

        # E03: 真实变更请求 (增/改/删/新类别/新话术/新正例)
        req = {
            "schema_version": "joctv-safety-feed-change-v1",
            "base_source_sha256": _sha(genesis),
            "base_sequence": 1,
            "next_sequence": 2,
            "next_version": "2026.09.02-2",
            "reason": "E2E: 新增丧亲关注类别(短语/token/受限正则), 收紧压力短语, 删除过宽 token 规则",
            "operations": [
                {"op": "add_response", "ref": "grief_support",
                 "zh": "听到这些我很心疼。失去重要的人是非常难受的事，如果愿意，我可以为您联系前台安排人陪您聊聊，或者帮您找一个安静的地方休息。",
                 "en": "I am so sorry to hear this. Losing someone important is very hard. If you would like, I can ask the front desk to arrange someone to talk with you, or help you find a quiet place to rest."},
                {"op": "add_category", "category": {
                    "code": "grief_watch", "name_zh": "丧亲与哀伤关注",
                    "name_en": "Grief and loss watch", "action": "warn",
                    "reply_ref": "grief_support"}},
                {"op": "add_rule", "rule": {
                    "id": "SFR-0036", "op": "phrase", "category": "grief_watch",
                    "term": "总是想起去世的家人"}},
                {"op": "add_rule", "rule": {
                    "id": "SFR-0037", "op": "token", "category": "grief_watch",
                    "terms": ["always", "feel", "overwhelmed"]}},
                {"op": "add_rule", "rule": {
                    "id": "SFR-0038", "op": "regex", "category": "grief_watch",
                    "term": "(?:连续|一直)失眠(?:很多天|好几周)?"}},
                {"op": "modify_rule", "id": "SFR-0030",
                 "patch": {"term": "压力超大"}},
                {"op": "delete_rule", "id": "SFR-0032"},
                {"op": "add_examples", "kind": "positive", "inputs": [
                    "最近总是想起去世的家人，心里空落落的",
                    "I always feel overwhelmed at work these days",
                    "连续失眠很多天，整个人很疲惫"]},
            ],
        }
        req_path = tmp / "req.json"
        req_path.write_text(json.dumps(req, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        out_dir = tmp / "next"
        r = run_py(GEN, "generate", "--base", str(genesis),
                   "--request", str(req_path), "--out-dir", str(out_dir))
        chk("E03-generate", r.returncode == 0, r.stderr.strip()[:200])
        next_src = out_dir / "safety-feed-src.s0002.json"
        r = run_py(VAL, str(next_src), "--prev", str(genesis),
                   "--summary", str(out_dir / "change-summary.json"))
        chk("E03-next-valid", r.returncode == 0, r.stderr.strip()[:200])

        # E04: 冻结打包工具 + 一次性测试密钥 (真实开发密钥绝不读取)
        key = tmp / "test-ed25519.key"
        env = {**os.environ, "JOCTV_DEV_SAFETY_FEED_KEY": str(key)}
        r = run_py(BUILD_TOOL, "keygen", env=env)
        m = re.search(r"JOCTV_SAFETY_FEED_PUBKEY=([0-9a-f]{64})", r.stdout)
        chk("E04-keygen", r.returncode == 0 and m is not None and key.is_file(),
            r.stderr.strip()[:200])
        pubkey = m.group(1) if m else ""
        pkg = tmp / "pkg"
        r = run_py(BUILD_TOOL, "build", "--rules", str(next_src),
                   "--sequence", "2", "--version", "2026.09.02-2",
                   "--published-at", "2026-09-02T09:00:00+08:00",
                   "--out", str(pkg), env=env)
        chk("E04-build-selfcheck", r.returncode == 0, r.stderr.strip()[-300:])
        # build/keygen 输出不得包含私钥内容 (公钥与路径不算秘密)
        key_hex = key.read_text().strip()
        chk("E04-no-secret-leak", key_hex not in r.stdout + r.stderr,
            "私钥出现在工具输出中")

        # E05: 真实消费链路 (fetch → 签名 → SHA → 编译)
        os.environ["JOCTV_SAFETY_FEED_PUBKEY"] = pubkey
        srv = _Server(pkg)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = srv.url() + "/manifest.json"
            os.environ["JOCTV_SAFETY_FEED_URL"] = url
            manifest = F.fetch_manifest(url)
            chk("E05-manifest", manifest.get("schema") == "joctv.safety-feed.v1"
                and manifest["sequence"] == 2, str(manifest.get("schema")))
            F.verify_manifest_signature(manifest)
            files = F.fetch_files(url, manifest)
            chk("E05-files-sha", set(files) == set(F.MANIFEST_FILES),
                str(sorted(files)))
            snap = F.compile_snapshot_from_package(
                manifest, files, built_from="e2e:seq2")
            chk("E05-compile", snap.sequence == 2
                and snap.version == "2026.09.02-2"
                and snap.category_count == 3 and snap.rule_count > 0,
                f"seq={snap.sequence} cats={snap.category_count}")

            # E06: 行为回读
            def hits(text: str) -> list[str]:
                return sorted(h["category"] for h in snap.match(text))

            chk("E06-new-phrase", hits("最近总是想起去世的家人，心里空落落的")
                == ["grief_watch"], str(hits("最近总是想起去世的家人")))
            chk("E06-new-token",
                hits("I always feel overwhelmed at work these days")
                == ["grief_watch"])
            chk("E06-new-regex", hits("连续失眠很多天，整个人很疲惫")
                == ["grief_watch"])
            chk("E06-modified-old-gone", hits("压力好大") == [],
                str(hits("压力好大")))
            chk("E06-modified-new-hit", hits("压力超大") ==
                ["emotional_distress_watch"], str(hits("压力超大")))
            chk("E06-deleted-gone", hits("i am very stressed today") == [],
                str(hits("i am very stressed today")))
            chk("E06-untouched-still-hits", hits("我想消失")
                == ["self_harm_extended"], str(hits("我想消失")))

            # E10: 网络面反例
            tampered = pkg / "rules.jsonl.zst"
            original = tampered.read_bytes()
            tampered.write_bytes(original + b"\x00")
            expect_feed_error("E10-file-sha", lambda: F.fetch_files(url, manifest),
                              "FILE_SHA_MISMATCH")
            tampered.write_bytes(original)
            bad_manifest = json.loads(json.dumps(manifest))
            bad_manifest["sequence"] = 3
            expect_feed_error(
                "E10-signature", lambda: F.verify_manifest_signature(bad_manifest),
                "SIGNATURE_INVALID")
        finally:
            srv.shutdown()
            srv.server_close()

        # E07: 旧版输入不被修改
        chk("E07-library-untouched", _sha(LIB) == lib_sha_before)
        genesis_still = run_py(VAL, str(genesis))
        chk("E07-genesis-untouched", genesis_still.returncode == 0,
            genesis_still.stderr.strip()[:160])

        # E08: 确定性
        out2 = tmp / "next2"
        r = run_py(GEN, "generate", "--base", str(genesis),
                   "--request", str(req_path), "--out-dir", str(out2))
        chk("E08-deterministic",
            r.returncode == 0 and _sha(next_src) ==
            _sha(out2 / "safety-feed-src.s0002.json"))

        # E09: 内容反例 → 冻结工具 (消费者代码) 构建即拒
        base_next = json.loads(next_src.read_text(encoding="utf-8"))

        def mutated(name: str, mutate) -> Path:
            obj = json.loads(json.dumps(base_next))
            mutate(obj)
            p = tmp / f"neg-{name}.json"
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            return p

        def build_expect(name: str, src: Path, code: str) -> None:
            r = run_py(BUILD_TOOL, "build", "--rules", str(src),
                       "--sequence", "3", "--version", "neg",
                       "--published-at", "2026-09-02T09:00:00+08:00",
                       "--out", str(tmp / f"neg-pkg-{name}"), env=env)
            chk(name, r.returncode != 0 and code in r.stderr,
                f"rc={r.returncode} stderr={r.stderr.strip()[-160:]}")

        def dup_id(obj):
            obj["rules"].append(dict(obj["rules"][0]))

        build_expect("E09-rule-dup", mutated("dup", dup_id), "RULES_SCHEMA")

        def builtin_cat(obj):
            obj["categories"].append({
                "code": "self_harm", "name_zh": "碰撞", "name_en": "Collision",
                "action": "warn", "reply_ref": "warm_suggest"})
            obj["rules"].append({"id": "SFR-0099", "op": "phrase",
                                 "category": "self_harm", "term": "碰撞短语"})
            obj["examples_positive"].append("碰撞短语场景")

        build_expect("E09-category-collision", mutated("coll", builtin_cat),
                     "CATEGORY_COLLISION")

        def missing_reply(obj):
            for c in obj["categories"]:
                if c["code"] == "grief_watch":
                    c["action"] = "block"
                    c["reply_ref"] = "missing_ref_x"

        build_expect("E09-reply-missing", mutated("reply", missing_reply),
                     "REPLY_MISSING")

        def bad_regex(obj):
            obj["rules"].append({"id": "SFR-0098", "op": "regex",
                                 "category": "grief_watch", "term": "(a+)+$"})

        build_expect("E09-regex-dangerous", mutated("regex", bad_regex),
                     "REGEX_DANGEROUS")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
