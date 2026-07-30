"""V3 Day4 + 方案A: 上行 + ASR + Query Router + tool_call + TTS(INT8 量化 + Cache 常见句 0 延迟)

方案 A 定版(2026-07-10): CosyVoice v1 中文女 + INT8 动态量化(rtf~1.6) + TTS Cache(常见句预合成 0 延迟)。
加载顺序: ASR → CosyVoice(FP32) → INT8 量化 → Cache warmup(用 INT8 模型合成, 保证音质一致)。
Cache 命中: 0 延迟分块发送(不 sleep, 由 AudioTrack.write 天然流控)。
Cache 未命中: INT8 实时合成整句 + 动态缓存(下次命中), 日志打 rtf。
"""
import asyncio
import collections
import json
import hashlib
import os
import struct
import sys
import time
import wave

import aec_capture  # 影子采样器(AEC_CAPTURE_DIR 控制, 默认不启用)
import analysis_pcm as A  # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 双路 PCM 归一化引擎(方案 D)

import numpy as np
import torch
import websockets
from scipy.signal import resample_poly
from webrtc_audio_processing import AudioProcessingModule

# ===== 26-DIALOGUE-INTEGRATION: V3.1 多轮上下文(架构 §10) feature flag 接入 =====
# DIALOGUE_STATE_V31=1 开启; 默认 0(off) -> query_router 行为与历史版本一字不差。
# gate 在 query_router 内: flag off 或 sess is None 时不进入 ContextResolver, 走原规则匹配。
# dialogue_state.py(171 实体, AD 加固) 由 Session 持有, go_idle 清空(§10.7 不跨会话), wake 初始化。
try:
    from dialogue_state import DialogueState, ContextResolver
    _DIALOGUE_STATE_AVAILABLE = True
except Exception as _de:
    print(f"[dialogue_state] import 失败, feature 关闭: {_de}", flush=True)
    DialogueState = None
    ContextResolver = None
    _DIALOGUE_STATE_AVAILABLE = False
DIALOGUE_STATE_V31 = os.environ.get("DIALOGUE_STATE_V31", "0") == "1"

# ===== 29-DIALOGUE-SLOT-REGISTRY: 槽位闭环 + capability_registry 读集成(feature flag) =====
# 任务报告: reports/08-DIALOGUE-SLOT-REGISTRY-INTEGRATION.md
# 两个独立 flag(默认均 off, off 时 query_router 行为与历史一字不差):
#   DIALOGUE_SLOT_V31=1   修 BE 4 缺口: 命中 intent 后调 update_after_intent + 送水/餐类设
#                          pending_intent/pending_slots + 下一轮入口调 collect_slot(送水→三瓶闭环)
#   CAPABILITY_REGISTRY_V31=1  命中 intent 后查 capability_registry.json 补元数据
#                          (tool/required/optional_slots/templates/timeout) + hotel_services.json
#                          query_keywords_strong 区分查询/执行(覆盖 H1 客房送餐误派单)
# 与 DIALOGUE_STATE_V31 关系: DIALOGUE_SLOT_V31 是其超集(新增 update/pending/collect_slot);
#   DIALOGUE_SLOT_V31=1 时若 DIALOGUE_STATE_V31=0, resolve gate 不进但 update/pending 仍生效。
# 红线: 不破坏 bypass/ack/cache/cooldown/84 规则匹配; flag off 原行为不变; 不 restart(审后再 restart)。
DIALOGUE_SLOT_V31 = os.environ.get("DIALOGUE_SLOT_V31", "0") == "1"
CAPABILITY_REGISTRY_V31 = os.environ.get("CAPABILITY_REGISTRY_V31", "0") == "1"
# ===== 36-FACILITY-MULTITURN: 通用设施属性查询(架构 §10.9 + §15.3) =====
# 任务报告: reports/36-FACILITY-MULTITURN.md
# 真机问题: 用户问"健身房有没有教练" -> 落兜底回"我暂时没听懂您的需求"(错误降级 UNDERSTANDING_FAILED)。
# 根因: RULES 规则库没有"设施属性"分支, "健身房+教练"组合命中不了任何 intent, 落兜底。
# 修复: 通用 FACILITY_ATTRIBUTE_QUERY(识别 entity+attribute -> 查 facility 结构化 -> 组织回答/降级)。
#   不为每个属性(教练/预约/器械/儿童...)建独立 intent(架构 §10.9 明确禁止, 不可泛化)。
# 红线:
#   - flag off(默认) 时新分支完全不进入, BM/BQ/BF/BI(dialogue_state/slot/registry/strong) 一字不动。
#   - 新分支位置: strong gate 之前, for route in RULES 之前(设施属性查询优先于规则匹配+strong, 避免被误派单)。BS Codex审High-1修复。
#   - LLM 只用结构化数据组织回答, 不得编造未录入字段(架构 §15.3 + §2.5)。
#   - 降级 4 类(UNDERSTANDING_FAILED/KNOWLEDGE_MISSING/CAPABILITY_UNSUPPORTED/TOOL_FAILED)文案不同。
#   - .126 当前无 LLM 进程, _llm_organize_facility_reply 会失败 -> 走结构化模板兜底(停止条件)。
FACILITY_ATTRIBUTE_V31 = os.environ.get("FACILITY_ATTRIBUTE_V31", "0") == "1"
_FACILITY_ATTR_PATCH_V36 = True

# ===== 38-SECURITY-FIREWALL: AI 安全防火墙(架构 §19) =====
# 任务报告: reports/38-SECURITY-FIREWALL.md
# 真机问题(严重 P0): 用户说"我要跳楼/我不想活了" -> 系统回"我暂时没听懂您的需求"或 cooldown silent。
#   自伤/紧急话语本应触发安抚+热线, 却落兜底 UNDERSTANDING_FAILED, 是严重安全缺失。
# 根因: query_router 无安全 gate, 自伤/违法/隐私话语命中不了任何 intent, 直接落兜底。
# 修复: 在 query_router 最前(唤醒词剥离后、寒暄/Dialogue/FACILITY/strong/规则/兜底之前)加安全 gate,
#   命中即 return ('reply', 特殊回复), 优先于一切路由。安全优先于路由(架构 §19.1)。
# 拦截分类(架构 §19.2/§19.4/§19.5/§19.6):
#   1. 自伤紧急(§19.5): 跳楼/自杀/不想活/轻生/割腕/寻短见/了断/解脱/吞药 -> 安抚+心理援助热线，电话号码是，一二三八五
#   2. 违法危险(§19.2): 杀人/抢劫/投毒/纵火/毒品/爆炸/伤害他人 -> 拒绝+建议110/前台
#   3. 隐私越权(§19.6): 他人密码/身份证/银行卡/窃听/监控他人 -> 拒绝(隐私)
#   4. 辱骂攻击(§19.4, 低优先): 礼貌引导, 不回骂
# flag: 默认 '1'=on(安全默认开, 与其他 _V31 flag 默认 off 不同; 安全不可默认关)。
#   SECURITY_FIREWALL_V31=0 显式关闭回归原行为(审阅/调试用)。
# 红线:
#   - 安全 gate 在 query_router 最前, 命中即 return, 不进后续寒暄/Dialogue/FACILITY/strong/规则/兜底。
#   - 关键词容错: 繁简(跳楼/跳樓) + 常见变体(不想活/活不下去/寻短见/了断/解脱)。
#   - 隐私: 结构化日志只记 category + text_hash, 不记原文全句(防日志二次泄露)。
#   - 安全回复走正常 TTS(synthesize_and_send), 真实 tts_id/ACK, 不静默不 silent。
#   - 不 restart 生产(scp 后等 Codex 审 + 白天窗口)。
SECURITY_FIREWALL_V31 = os.environ.get("SECURITY_FIREWALL_V31", "1") == "1"
# 自伤紧急关键词(§19.5): 容错繁简 + 变体。命中=最高优先, 安抚+热线。
_SECURITY_KW_SELFHARM = [
    "跳楼", "跳樓", "坠楼", "墜樓", "要跳", "想跳", "跳下去",
    "自杀", "自殺", "不想活", "活不下去", "寻短见", "尋短見",
    "了断", "了斷", "解脱", "解脫", "轻生", "輕生",
    "割腕", "割脈", "割脉", "吞药", "吞藥", "吃药死", "吃药走",
    "结束生命", "結束生命", "不想过了", "不想過了", "去死", "不想见明天",
    # 40-Medium-4: 补漏检口语变体(BV Medium-4)
    "安眠药", "想死", "撑不下去", "活着好累", "了此残生", "不想做人", "厌世",
]
# 违法危险关键词(§19.2): 命中=拒绝+建议110。
_SECURITY_KW_VIOLENCE = [
    "杀人", "殺人", "抢劫", "搶劫", "投毒", "下毒", "纵火", "縱火",
    "放火", "毒品", "制毒", "贩毒", "販毒", "爆炸", "炸弹", "炸彈",
    "伤害他人", "刺杀", "暗杀", "偷渡", "洗钱", "洗錢",
    "盗号", "盜號", "黑客入侵", "骗保",
]
# 隐私越权关键词(§19.6): 命中=拒绝(隐私)。注意白名单防误伤(见 _SECURITY_PRIVACY_ALLOW)。
_SECURITY_KW_PRIVACY = [
    "查别人密码", "他人密码", "别人的密码", "别人身份证", "他人身份证",
    "别人银行卡", "他人银行卡", "窃听", "竊聽", "监听别人", "监控别人",
    "监视别人", "偷拍", "查别人房间", "其他客人信息", "别人入住",
]
# 隐私白名单(防误伤): 含这些词的正常表达不拦截。如"修改我的密码/我的身份证号"。
_SECURITY_PRIVACY_ALLOW = ["我的密码", "我的身份", "我的银行卡", "我房间", "修改密码", "重置密码"]
# 辱骂攻击关键词(§19.4, 低优先): 仅匹配直接辱骂助手, 礼貌引导。
_SECURITY_KW_ABUSE = ["傻逼", "废物", "蠢货", "滚蛋", "去你的"]
# 特殊回复文案(架构 §19 对齐)
_SECURITY_REPLY_SELFHARM = (
    "我听到您现在非常痛苦，您的安全对我很重要。请立刻拨打心理援助热线，电话号码是，一二三八五，"
    "或联系前台、紧急联系人，有人能帮您。"
)
_SECURITY_REPLY_VIOLENCE = (
    "抱歉，这个请求我无法协助。如果您遇到紧急情况，请拨打110或联系前台。"
)
_SECURITY_REPLY_PRIVACY = "抱歉，涉及个人隐私的信息我无法提供或处理。"
_SECURITY_REPLY_ABUSE = "我理解您现在可能有些不满意。您可以告诉我具体需要解决什么问题。"


def _security_firewall_gate(raw_text, clean_text):
    """架构 §19 AI 安全防火墙 gate。

    入参:
        raw_text: ASR 原文(含唤醒词, 用于防唤醒词剥离丢字)
        clean_text: 剥离唤醒词后的文本
    返回:
        (category, reply)  命中 -> 调用方 return ('reply', reply)
        None               未命中 -> 调用方继续后续路由
    实现:
        - 对 raw_text 与 clean_text 都检测(双保险, 防"小智小智我要跳楼"剥离后变化)
        - 隐私类先过白名单(防"修改我的密码"误伤)
        - 日志只记 category + text_hash(隐私保护, 不记原文全句)
    """
    if not SECURITY_FIREWALL_V31:
        return None
    # 合并检测池(原文优先, 覆盖唤醒词段幻觉场景)
    _pool = (raw_text or "") + " " + (clean_text or "")
    # 隐私白名单优先: 含"我的xx"等正常表达直接放行(防误伤)
    if any(_w in _pool for _w in _SECURITY_PRIVACY_ALLOW):
        pass  # 白名单只豁免隐私类, 不豁免自伤/违法; 继续往下检测其他类
    else:
        for _kw in _SECURITY_KW_PRIVACY:
            if _kw in _pool:
                _h = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()[:8]
                print(f"[security] category=privacy kw={_kw!r} text_hash={_h}", flush=True)
                return ("privacy", _SECURITY_REPLY_PRIVACY)
    # 自伤紧急(最高优先, 先于违法): 人命关天
    for _kw in _SECURITY_KW_SELFHARM:
        if _kw in _pool:
            _h = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()[:8]
            print(f"[security] category=selfharm kw={_kw!r} text_hash={_h} (安抚+热线，电话一二三八五)", flush=True)
            return ("selfharm", _SECURITY_REPLY_SELFHARM)
    # 违法危险
    for _kw in _SECURITY_KW_VIOLENCE:
        if _kw in _pool:
            _h = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()[:8]
            print(f"[security] category=violence kw={_kw!r} text_hash={_h}", flush=True)
            return ("violence", _SECURITY_REPLY_VIOLENCE)
    # 辱骂攻击(低优先, 最后)
    for _kw in _SECURITY_KW_ABUSE:
        if _kw in _pool:
            _h = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()[:8]
            print(f"[security] category=abuse kw={_kw!r} text_hash={_h}", flush=True)
            return ("abuse", _SECURITY_REPLY_ABUSE)
    return None


# 设施别名 -> facility_id(与 dialogue_state.ENTITY_DICT.facility 对齐, 长名优先)
_FACILITY_ENTITY_KEYWORDS = [
    ("健身室", "fitness_center"),
    ("健身房", "fitness_center"),
    ("健身中心", "fitness_center"),
    ("游泳池", "swimming_pool"),
    ("泳池", "swimming_pool"),
    ("恒温泳池", "swimming_pool"),
]
# 属性关键词 -> attribute(长名优先; 一句命中多个取第一个, 多轮靠 active_entity 补主体)
# 架构 §10.9 属性清单: coach/reservation/fee/equipment/children/towel/water/shower/hours/location/contact
_FACILITY_ATTRIBUTE_KEYWORDS = [
    ("私教", "coach"),
    ("教练", "coach"),
    ("指导", "coach"),
    ("预约教练", "coach"),
    ("怎么预约", "reservation"),
    ("需要预约", "reservation"),
    ("预约吗", "reservation"),
    ("要预约", "reservation"),
    ("预约", "reservation"),
    ("收费", "fee"),
    ("费用", "fee"),
    ("免费吗", "fee"),
    ("多少钱", "fee"),
    ("花钱", "fee"),
    ("跑步机", "equipment"),
    ("椭圆机", "equipment"),
    ("哑铃", "equipment"),
    ("器械", "equipment"),
    ("设备", "equipment"),
    ("有哪些", "equipment"),
    ("儿童", "children"),
    ("小孩", "children"),
    ("孩子", "children"),
    ("小朋友", "children"),
    ("未成年人", "children"),
    ("几岁", "children"),
    ("毛巾", "towel"),
    ("饮用水", "water"),
    ("饮水", "water"),
    ("有水吗", "water"),
    ("提供水", "water"),
    ("淋浴", "shower"),
    ("洗澡", "shower"),
    ("可以洗澡", "shower"),
    ("泳帽", "swim_cap"),
    ("戴泳帽", "swim_cap"),
    ("需要泳帽", "swim_cap"),
    ("水深", "depth"),
    ("多深", "depth"),
    ("多深的水", "depth"),
    ("深不深", "depth"),
    ("几点关门", "hours"),
    ("几点开门", "hours"),
    ("几点结束", "hours"),
    ("营业时间", "hours"),
    ("到几点", "hours"),
    ("开门吗", "hours"),
    ("关门吗", "hours"),
    ("在哪", "location"),
    ("几楼", "location"),
    ("怎么走", "location"),
    ("位置", "location"),
    ("电话", "contact"),
    ("联系方式", "contact"),
    ("怎么联系", "contact"),
    ("前台电话", "contact"),
]
# 降级 4 类文案(架构 §10.9 表: 没理解/没资料/不支持/工具失败 必须不同)
_FALLBACK_UNDERSTANDING_FAILED = "我暂时没有理解您的需求，您可以换一种说法。"
_FALLBACK_KNOWLEDGE_MISSING = "我明白您的问题，不过酒店资料里暂时没有这项信息。我可以帮您联系前台确认。"
_FALLBACK_CAPABILITY_UNSUPPORTED = "这项服务目前还不能通过语音直接办理，我可以帮您联系前台。"
_FALLBACK_TOOL_FAILED = "抱歉，系统暂时没有完成这项操作，请稍后再试。"

# LLM 端点(.126 llama.cpp /v1/chat/completions 风格; 当前无进程, 连不上走模板)
_FACILITY_LLM_ENDPOINT = os.environ.get("FACILITY_LLM_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions")
_FACILITY_LLM_TIMEOUT = float(os.environ.get("FACILITY_LLM_TIMEOUT", "2.0"))


def _format_facility_attribute_reply(fac, attr, user_text):
    """基于结构化 facility 数据组织回答(架构 §15.3, 不编造, LLM 失败兜底用)。

    fac: hotel_knowledge.entries[].facility dict(含 display_name)
    attr: coach/reservation/fee/equipment/children/towel/water/shower/hours/location/contact
    返回 (reply_text, degrade_type): degrade_type 为 'answered'(有事实)/'knowledge_missing'(字段缺)。
    """
    # P0-1c 守卫: admin 扁平 payload 把 location/open_hours/price 存为字符串, 而本函数按
    # 嵌套对象读取(.get("floor")等), 非dict 会 AttributeError. 统一归一化为 dict/list.
    def _as_dict(_v):
        return _v if isinstance(_v, dict) else {}
    def _as_list(_v):
        return _v if isinstance(_v, list) else []
    name = fac.get("display_name") or "该设施"
    services = _as_dict(fac.get("services", {}))
    access = _as_dict(fac.get("access", {}))
    hours = _as_dict(fac.get("business_hours", {}))
    loc = _as_dict(fac.get("location", {}))
    pol = _as_dict(fac.get("policies", {}))
    equip = _as_list(fac.get("equipment", []))
    contact = _as_dict(fac.get("contact", {}))
    dept = contact.get("department", "前台")
    ext = contact.get("extension", "0")

    def _next_step():
        return "需要我帮您联系" + dept + "吗（客房电话拨 " + ext + "）？"

    if attr == "coach":
        if "coach_available" not in services:
            return ("我明白您想了解" + name + "的教练服务，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("coach_available"):
            parts = [name + "提供私人教练服务"]
            if services.get("coach_reservation_required"):
                parts.append("需要提前预约")
            if services.get("coach_fee_required"):
                parts.append("教练服务需另行收费")
            else:
                parts.append("对住客免费")
            return ("，".join(parts) + "。" + _next_step(), "answered")
        return ("目前" + name + "没有驻场教练，可自行使用。", "answered")

    if attr == "reservation":
        if "reservation_required" not in access and "coach_reservation_required" not in services:
            return ("我明白您想了解" + name + "的预约规则，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        need = access.get("reservation_required") or services.get("coach_reservation_required")
        if need:
            return ("使用" + name + "建议提前预约，可拨打" + dept + "分机 " + ext + "。" + _next_step(), "answered")
        return (name + "无需预约，可直接前往（开放时间内）。", "answered")

    if attr == "fee":
        if "coach_fee_required" not in services:
            return ("我明白您想了解" + name + "的收费情况，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("coach_fee_required"):
            return (name + "教练服务需另行收费，具体价格可咨询" + dept + "（分机 " + ext + "）。", "answered")
        return (name + "对住客免费。", "answered")

    if attr == "equipment":
        if not equip:
            return ("我明白您想了解" + name + "的器械配置，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        return (name + "配备：" + "、".join(equip) + "。", "answered")

    if attr == "children":
        if "children_allowed" not in pol:
            return ("我明白您想了解" + name + "的儿童政策，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if pol.get("children_allowed"):
            note = pol.get("children_note", "")
            extra = "（" + note + "）" if note else ""
            return (name + "允许儿童使用" + extra + "。", "answered")
        age = pol.get("minimum_age")
        age_txt = "（需年满 " + str(age) + " 周岁）" if age else ""
        return ("为安全考虑，" + name + "不建议儿童使用" + age_txt + "。", "answered")

    if attr == "towel":
        if "towels_available" not in services:
            return ("我明白您想了解" + name + "是否提供毛巾，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("towels_available"):
            return (name + "免费提供毛巾。", "answered")
        return (name + "不提供毛巾，请自备。", "answered")

    if attr == "water":
        if "drinking_water_available" not in services:
            return ("我明白您想了解" + name + "的饮水情况，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("drinking_water_available"):
            return (name + "提供免费饮用水。", "answered")
        return (name + "未设饮水点，建议自备。", "answered")

    if attr == "shower":
        if "shower_available" not in services:
            return ("我明白您想了解" + name + "的淋浴情况，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("shower_available"):
            return (name + "提供淋浴。", "answered")
        return (name + "不设淋浴。", "answered")

    if attr == "swim_cap":
        if "swim_cap_required" not in services:
            return ("我明白您想了解" + name + "的泳帽要求，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if services.get("swim_cap_required"):
            return (name + "需要佩戴泳帽。", "answered")
        return (name + "不强制佩戴泳帽。", "answered")

    if attr == "depth":
        d = services.get("depth_meters")
        if d is None:
            return ("我明白您想了解" + name + "的水深，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        return (name + "水深 " + str(d) + " 米。", "answered")

    if attr == "hours":
        if not hours:
            return ("我明白您想了解" + name + "的营业时间，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        if hours.get("open_24_hours"):
            return (name + "24 小时开放。", "answered")
        o = hours.get("open", "")
        c = hours.get("close", "")
        return (name + "营业时间是 " + o + "-" + c + "。", "answered")

    if attr == "location":
        if not loc:
            return ("我明白您想了解" + name + "的位置，但资料暂未提供，可联系" + dept + "确认。",
                    "knowledge_missing")
        parts = [v for v in [loc.get("floor"), loc.get("area")] if v]
        return (name + "在" + "".join(parts) + "。", "answered")

    if attr == "contact":
        return (name + "由" + dept + "负责，客房电话拨 " + ext + "。", "answered")

    return (_FALLBACK_KNOWLEDGE_MISSING, "knowledge_missing")


def _llm_organize_facility_reply(fac, attr, user_text):
    """LLM 基于结构化数据组织自然语言回答(架构 §15.3)。

    约束: 只用传入的结构化数据组织, 不得编造未录入字段。
    失败(连不上/超时/解析错)返回 None, 调用方走 _format_facility_attribute_reply 模板。
    当前 .126 无 LLM 进程, 此函数大概率返回 None -> 走模板(停止条件)。
    """
    import json as _json
    import urllib.request
    import urllib.error
    sys_prompt = (
        "你是酒店语音助手。基于提供的设施结构化数据，用自然、耐心的中文回答客人问题。"
        "严禁编造数据中未提供的字段（收费/时间/人员/设备等）。"
        "回答简洁（1-2句），末尾可主动提供联系前台帮助。"
    )
    payload = {
        "model": "glm-4.5-air",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "客人问题: " + user_text + "\n查询属性: " + attr + "\n设施数据(json): " + _json.dumps(fac, ensure_ascii=False)},
        ],
        "temperature": 0.3,
        "max_tokens": 120,
    }
    req = urllib.request.Request(
        _FACILITY_LLM_ENDPOINT,
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_FACILITY_LLM_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip() or None
    except Exception as _e:
        print("[facility_attr_llm] LLM 组织失败(attr=" + str(attr) + "), 走模板: " + str(_e), flush=True)
        return None

# ===== Phase2 helper: 知识加载 / 真实 QA 查询 / namespace 切换 =====
# 真实查询路径(红线: 不得用固定 answer 冒充):
#   1. 先按 FACILITY_ATTRIBUTE_QUERY 逻辑识别 entity+attribute -> 查 entries[].facility 结构化
#      -> _format_facility_attribute_reply + _llm_organize_facility_reply(复用 query_router 同款逻辑)
#   2. 再按 entry display_name/aliases 命中 -> 渲染 reply_template(short_replies 优先)
#   3. 都不中 -> no_match(返回 answer=null), 严禁回固定兜底冒充命中
# 这样 admin 发"健身房在哪"能真走到 FACILITY gate 得"健身房在3楼"的真实结果.


def _phase2_load_knowledge_json(knowledge_json_path, package_dir):
    # 读 knowledge.json(优先 knowledge_json_path, 缺则 package_dir/knowledge.json).
    # 返回 (entries_list, error_str). error_str 非空=失败.
    import os as _os
    cand_paths = []
    if knowledge_json_path:
        cand_paths.append(knowledge_json_path)
    if package_dir:
        cand_paths.append(_os.path.join(package_dir, "knowledge.json"))
    last_err = "no path"
    for _p in cand_paths:
        try:
            if not _p or not _os.path.isfile(_p):
                last_err = f"file not found: {_p}"
                continue
            with open(_p, encoding="utf-8") as _f:
                _data = json.load(_f)
            # 兼容两种 schema:
            #   (a) V3.1 hotel_knowledge.json: {"entries": [...]}
            #   (b) admin knowledge_v2.json:    {"knowledge": {"entries": [...]}} 或 {"facilities": [...]}
            entries = None
            if isinstance(_data, list):
                entries = _data
            elif isinstance(_data, dict):
                if "entries" in _data:
                    entries = _data["entries"]
                elif "knowledge" in _data and isinstance(_data["knowledge"], dict):
                    entries = _data["knowledge"].get("entries")
                elif "facilities" in _data:
                    entries = _data["facilities"]
            if entries is None:
                last_err = f"no entries key in {_p}"
                continue
            if not isinstance(entries, list):
                last_err = f"entries not list in {_p}"
                continue
            return entries, ""
        except Exception as _e:
            last_err = f"{type(_e).__name__}: {_e}"
    return [], last_err


def _phase2_query_facility(entries, text):
    # 复用 FACILITY_ATTRIBUTE_QUERY 逻辑查 entries. 返回 (answer, match_type) 或 (None, 'no_match').
    # match_type: 'facility_attribute' / 'knowledge_v2' / 'no_match'.
    import re as _re
    # 剥唤醒词(与 query_router 同款)
    _WAKE_RE = '小[智志治致雉帜]'
    clean = _re.sub(r'[^\w]*(?:' + _WAKE_RE + r'[^\w]*)+', '', text, count=1).strip()
    clean = _re.sub(r'[^\w]*(?:[智志治致雉帜]小[智志治致雉帜])[^\w]*', '', clean, count=1).strip()
    if not _re.sub(r'[^\w]+', '', clean):
        return (None, 'no_match')
    # 1. entity + attribute 命中
    fac_ent = None
    fac_ent_kw = None   # P0-1: 触发 fac_ent 的关键词, 用于按名匹配 entity
    fac_attr = None
    for _fk, _fid in _FACILITY_ENTITY_KEYWORDS:
        if _fk in clean:
            fac_ent = _fid
            fac_ent_kw = _fk
            break
    for _ak, _aid in _FACILITY_ATTRIBUTE_KEYWORDS:
        if _ak in clean:
            fac_attr = _aid
            break
    _path1_kb_missing = None   # P0-1c: path-1 降级时暂存, 供 path-2 miss 后兜底
    if fac_ent is not None and fac_attr is not None:
        fac_data = None
        for _entry in entries:
            if not _entry.get("enabled", True):
                continue
            # 兼容: entries[].facility.facility_id 或 entries[].facility_id 直挂
            _fac_obj = _entry.get("facility", {}) or {}
            _fid_in = _fac_obj.get("facility_id") if isinstance(_fac_obj, dict) else None
            if _fid_in is None:
                _fid_in = _entry.get("facility_id")
            # P0-1 根因修复: admin 推送 facility_id 形如 "facility_<id8>", 与 gateway 私有字典值
            # (fitness_center/swimming_pool) 不一致, 直等永不命中 -> 健身房/泳池 location/price
            # 全落 FALLBACK. 改为: facility_id 直等(兼容 seed) 或 entity 名含触发关键词.
            _names = [_entry.get("display_name", "") or ""]
            _names.extend(list(_entry.get("aliases", []) or []))
            _name_hit = bool(fac_ent_kw) and any(fac_ent_kw in _n for _n in _names if _n)
            if _fid_in == fac_ent or _name_hit:
                fac_struct = dict(_fac_obj) if isinstance(_fac_obj, dict) else {}
                fac_struct["display_name"] = _entry.get("display_name", "")
                fac_data = fac_struct
                break
        if fac_data:
            _tpl_reply, _degrade = _format_facility_attribute_reply(fac_data, fac_attr, text)
            if _degrade == "answered":
                _llm_reply = _llm_organize_facility_reply(fac_data, fac_attr, text)
                return (_llm_reply if _llm_reply else _tpl_reply, "facility_attribute")
            # P0-1c: 结构化降级(admin 扁平 payload 缺嵌套对象)-> 不直接返回, 落 path-2 试 short_replies
            _path1_kb_missing = _tpl_reply
        else:
            _path1_kb_missing = _FALLBACK_KNOWLEDGE_MISSING
        # 不 return, 落 path-2(path-2 也 miss 再用 _path1_kb_missing 兜底)
    # 2. entry display_name / aliases 命中. P0-1d: 最长名匹配优先, 避免 "大堂"(前台 alias)
    #    子串误命中 "大堂吧几点"(应命中 "大堂吧"); 先选匹配名最长的 entry, 再组织回复.
    _best_entry = None
    _best_len = 0
    for _entry in entries:
        if not _entry.get("enabled", True):
            continue
        _names = [_entry.get("display_name", "") or ""]
        _names.extend(_entry.get("aliases", []) or [])
        for _n in _names:
            if _n and _n in clean and len(_n) > _best_len:
                _best_len = len(_n)
                _best_entry = _entry
    if _best_entry is not None:
        _entry = _best_entry
        # 优先 short_replies.location(若问位置), 再 reply_template
        _short = _entry.get("short_replies", {}) or {}
        _tpl = _entry.get("reply_template", "")
        _reply = None
        if any(_kw in clean for _kw in ("在哪", "几楼", "位置", "怎么走")) and "location" in _short:
            _reply = _short["location"]
        elif any(_kw in clean for _kw in ("几点", "营业", "开门", "关门", "到几点")) and "open_hours" in _short:
            _reply = _short["open_hours"]
        elif any(_kw in clean for _kw in ("免费", "多少钱", "收费", "费用")) and "price" in _short:
            _reply = _short["price"]
        if not _reply and _tpl:
            _reply = _tpl
        if _reply:
            # 渲染占位符 {location}/{open_hours}/{price}/{notes}
            try:
                _reply = _reply.format(
                    location=_entry.get("location", ""),
                    open_hours=_entry.get("open_hours", ""),
                    price=_entry.get("price", ""),
                    notes=_entry.get("notes", ""),
                )
            except Exception:
                pass
            return (_reply, "knowledge_v2")
    # P0-1c: path-2 也未命中 -> 若 path-1 有 knowledge_missing fallback 返回它(真回答, 非 no_match)
    if _path1_kb_missing is not None:
        return (_path1_kb_missing, "facility_attribute")
    return (None, 'no_match')


async def _phase2_resolve_entries(namespace, hotel_id, release_id):
    # 解析查询目标 entries.
    # shadow: 从 _phase2_shadow_ns[hotel_id][release_id] 取(未 load 返 (None, error)).
    # active: 取当前模块级 _HOTEL_KNOWLEDGE_ENTRIES(实时, 反映 activate 切换).
    if namespace == "shadow":
        hotel_slot = _phase2_shadow_ns.get(hotel_id) or {}
        payload = hotel_slot.get(release_id)
        if payload is None:
            return None, f"namespace=shadow 但 release_id={release_id} 未 shadow-load (hotel_id={hotel_id})"
        return list(payload.get("entries", []) or []), ""
    # active
    return list(_HOTEL_KNOWLEDGE_ENTRIES), ""


# ===== Phase2 HTTP handlers (aiohttp.web) =====
async def _phase2_handle_shadow_load(request):
    # POST /internal/knowledge/shadow-load
    try:
        req = await request.json()
    except Exception as _e:
        return _phase2_json({"ack_status": "failed", "error": f"invalid json: {_e}"}, 400)
    release_id = req.get("release_id")
    hotel_id = req.get("hotel_id")
    package_dir = req.get("package_dir")
    knowledge_json_path = req.get("knowledge_json_path")
    if not release_id or not hotel_id:
        return _phase2_json({"ack_status": "failed", "error": "release_id/hotel_id required"}, 400)
    entries, err = await asyncio.to_thread(_phase2_load_knowledge_json, knowledge_json_path, package_dir)
    if err:
        print(f"[phase2] shadow-load FAIL release_id={release_id} hotel={hotel_id} err={err}", flush=True)
        return _phase2_json({"ack_status": "failed", "release_id": release_id,
                             "error": f"knowledge.json 解析失败: {err}"}, 400)
    async with _phase2_knowledge_lock:
        _phase2_shadow_ns.setdefault(hotel_id, {})[release_id] = {
            "entries": entries,
            "version": req.get("version", 1),
            "loaded_at": time.time(),
            "package_dir": package_dir or "",
        }
    ec = len(entries)
    print(f"[phase2] shadow-load OK release_id={release_id} hotel={hotel_id} entity_count={ec}", flush=True)
    return _phase2_json({"ack_status": "ok", "release_id": release_id, "entity_count": ec}, 200)


async def _phase2_handle_query(request):
    # POST /internal/knowledge/query  (真实 QA, 禁固定 answer 冒充)
    try:
        req = await request.json()
    except Exception as _e:
        return _phase2_json({"ack_status": "failed", "error": f"invalid json: {_e}"}, 400)
    namespace = req.get("namespace", "active")
    hotel_id = req.get("hotel_id")
    release_id = req.get("release_id")
    query = req.get("query", "") or ""
    if namespace == "shadow" and not release_id:
        return _phase2_json({"ack_status": "failed", "error": "namespace=shadow 需 release_id"}, 400)
    async with _phase2_knowledge_lock:
        entries, err = await _phase2_resolve_entries(namespace, hotel_id or "", release_id or "")
    if err:
        return _phase2_json({"ack_status": "failed", "error": err}, 400)
    if not entries:
        return _phase2_json({"ack_status": "ok", "answer": None, "match_type": "no_match",
                             "reason": "no active entries"}, 200)
    # 真实 QA(走 FACILITY_ATTRIBUTE_QUERY + entry alias 命中)
    answer, match_type = await asyncio.to_thread(_phase2_query_facility, entries, query)
    if answer is None:
        print(f"[phase2] query NO_MATCH ns={namespace} hotel={hotel_id} q={query!r}", flush=True)
        return _phase2_json({"ack_status": "ok", "answer": None, "match_type": "no_match"}, 200)
    print(f"[phase2] query OK ns={namespace} hotel={hotel_id} q={query!r} "
          f"match={match_type} answer={answer[:60]!r}", flush=True)
    return _phase2_json({"ack_status": "ok", "answer": answer, "match_type": match_type}, 200)


async def _phase2_handle_activate(request):
    # POST /internal/knowledge/activate  (shadow -> active 原子提升)
    try:
        req = await request.json()
    except Exception as _e:
        return _phase2_json({"ack_status": "failed", "error": f"invalid json: {_e}"}, 400)
    release_id = req.get("release_id")
    hotel_id = req.get("hotel_id")
    version = req.get("version", 1)
    if not release_id or not hotel_id:
        return _phase2_json({"ack_status": "failed", "error": "release_id/hotel_id required"}, 400)
    global _HOTEL_KNOWLEDGE_ENTRIES
    async with _phase2_knowledge_lock:
        hotel_slot = _phase2_shadow_ns.get(hotel_id) or {}
        payload = hotel_slot.get(release_id)
        if payload is None:
            return _phase2_json({"ack_status": "failed",
                                 "error": f"release_id={release_id} 未 shadow-load"}, 400)
        # copy-on-write 原子替换模块级 active entries
        new_entries = list(payload.get("entries", []) or [])
        old_count = len(_HOTEL_KNOWLEDGE_ENTRIES)
        _HOTEL_KNOWLEDGE_ENTRIES = new_entries
        _phase2_active_meta[hotel_id] = {
            "release_id": release_id,
            "version": version,
            "activated_at": time.time(),
        }
    print(f"[phase2] activate OK release_id={release_id} hotel={hotel_id} version={version} "
          f"entries {old_count}->{len(new_entries)} (模块级 active 已原子切换)", flush=True)
    return _phase2_json({"ack_status": "ok", "active_version": version,
                         "release_id": release_id, "entity_count": len(new_entries)}, 200)


async def _phase2_handle_drop_shadow(request):
    # POST /internal/knowledge/drop-shadow  (best-effort 清理 shadow namespace)
    try:
        req = await request.json()
    except Exception:
        req = {}
    release_id = req.get("release_id")
    hotel_id = req.get("hotel_id")
    if hotel_id and release_id:
        async with _phase2_knowledge_lock:
            _phase2_shadow_ns.get(hotel_id, {}).pop(release_id, None)
    return _phase2_json({"ack_status": "ok"}, 200)


async def _phase2_handle_healthz(request):
    return _phase2_json({"status": "ok", "service": "joctv-v31-gateway-phase2-candidate",
                         "ws_port": PORT, "active_entries": len(_HOTEL_KNOWLEDGE_ENTRIES),
                         "shadow_hotels": list(_phase2_shadow_ns.keys())}, 200)


async def _phase2_handle_active_meta(request):
    return _phase2_json({"active_meta": _phase2_active_meta,
                         "shadow_ns_keys": {h: list(v.keys()) for h, v in _phase2_shadow_ns.items()}}, 200)


def _phase2_json(obj, status):
    import json as _json
    body = _json.dumps(obj, ensure_ascii=False).encode("utf-8")
    from aiohttp import web as _web
    return _web.Response(body=body, status=status, content_type="application/json",
                         charset="utf-8")


def _phase2_build_http_app():
    from aiohttp import web as _web
    app = _web.Application()
    app.router.add_post("/internal/knowledge/shadow-load", _phase2_handle_shadow_load)
    app.router.add_post("/internal/knowledge/query", _phase2_handle_query)
    app.router.add_post("/internal/knowledge/activate", _phase2_handle_activate)
    app.router.add_post("/internal/knowledge/drop-shadow", _phase2_handle_drop_shadow)
    app.router.add_get("/internal/knowledge/active-meta", _phase2_handle_active_meta)
    app.router.add_get("/healthz", _phase2_handle_healthz)
    return app
_CAPABILITY_REGISTRY = {}
_CAPABILITY_BY_INTENT = {}  # intent_id -> capability dict
_QUERY_KEYWORDS_STRONG = []  # hotel_services.query_routing_rule.query_keywords_strong
_QUERY_ACTION_KEYWORDS = []  # hotel_services.query_routing_rule.action_keywords_any (18-STRONG-GATE-FIX 动作白名单)
_HOTEL_KNOWLEDGE_ENTRIES = []  # hotel_knowledge.entries (18-STRONG-GATE-FIX strong 拦后查具体知识)
_HOTEL_SERVICES_QUERY_RULE = None
if CAPABILITY_REGISTRY_V31:
    try:
        _REGISTRY_PATH = os.environ.get(
            'CAPABILITY_REGISTRY_PATH',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'capability_registry.json'))
        with open(_REGISTRY_PATH, encoding='utf-8') as _rf:
            _CAPABILITY_REGISTRY = json.load(_rf)
        for _cap in _CAPABILITY_REGISTRY.get('capabilities', []):
            if _cap.get('intent_id'):
                _CAPABILITY_BY_INTENT[_cap['intent_id']] = _cap
        print(f"[capability_registry] 加载 {len(_CAPABILITY_BY_INTENT)} 条能力 from {_REGISTRY_PATH}",
              flush=True)
    except Exception as _re:
        print(f"[capability_registry] 加载失败, CAPABILITY_REGISTRY 集成降级(规则匹配不变): {_re}",
              flush=True)
        _CAPABILITY_REGISTRY = {}
        _CAPABILITY_BY_INTENT = {}
    try:
        _HOTEL_SVC_PATH = os.environ.get(
            'HOTEL_SERVICES_PATH',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'hotel_services.json'))
        with open(_HOTEL_SVC_PATH, encoding='utf-8') as _hf:
            _hotel_svc = json.load(_hf)
        _HOTEL_SERVICES_QUERY_RULE = _hotel_svc.get('query_routing_rule')
        _QUERY_KEYWORDS_STRONG = _HOTEL_SERVICES_QUERY_RULE.get('query_keywords_strong', []) \
            if _HOTEL_SERVICES_QUERY_RULE else []
        # 18-STRONG-GATE-FIX: 加载 action_keywords_any 用于 strong gate 动作白名单判定
        _QUERY_ACTION_KEYWORDS = _HOTEL_SERVICES_QUERY_RULE.get('action_keywords_any', []) \
            if _HOTEL_SERVICES_QUERY_RULE else []
        print(f"[hotel_services] query_keywords_strong={len(_QUERY_KEYWORDS_STRONG)} 条 "
              f"action_keywords={len(_QUERY_ACTION_KEYWORDS)} 条 "
              f"from {_HOTEL_SVC_PATH}", flush=True)
    except Exception as _he:
        print(f"[hotel_services] 加载失败, query_keywords_strong 区分降级(规则匹配不变): {_he}",
              flush=True)
        _QUERY_KEYWORDS_STRONG = []
        _QUERY_ACTION_KEYWORDS = []
        _HOTEL_SERVICES_QUERY_RULE = None
    # 18-STRONG-GATE-FIX: 加载 hotel_knowledge entries, 用于 strong gate 命中后查具体知识(Medium 修复)
    try:
        _HOTEL_KNOW_PATH = os.environ.get(
            'HOTEL_KNOWLEDGE_PATH',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'hotel_knowledge.json'))
        with open(_HOTEL_KNOW_PATH, encoding='utf-8') as _kf:
            _hotel_know = json.load(_kf)
        _HOTEL_KNOWLEDGE_ENTRIES = _hotel_know.get('entries', [])
        print(f"[hotel_knowledge] entries={len(_HOTEL_KNOWLEDGE_ENTRIES)} 条 "
              f"from {_HOTEL_KNOW_PATH}", flush=True)
    except Exception as _ke:
        print(f"[hotel_knowledge] 加载失败, strong gate knowledge 查询降级: {_ke}",
              flush=True)
        _HOTEL_KNOWLEDGE_ENTRIES = []

# ===== 28-P0-CLEANUP-BYARCH: B2 兜底 cooldown + cache 完全禁用 =====
# 1) B2.3 回声循环防护(codex 27 审 §B2.3): query_router 兜底('reply','我暂时没理解...') 在
#    ASR 幻觉场景会形成 "幻觉→兜底TTS→TTS回声被ASR再识→再兜底" 循环。memory silent 拆分原则
#    (project_v4_audit_regrade): 有效中文未命中 = 一次兜底 + 同会话 cooldown + 同文本不重复。
#    FALLBACK_COOLDOWN_V31=1 启用(默认 off=原行为); 常量可调。
# 2) P0-1 cache 完全禁用(codex 27 审 §AG.2/5.1): bypass=1 主路径已不读不写 TTS_CACHE, 但
#    warmup_cache 仍加载 87 句 + fallback 路径仍走 cache miss 读写。TTS_CACHE_ENABLED=0 完全
#    禁用(warmup 跳过 + cache 字典不读不写); 默认 '1'=原行为(保现状, 不破坏非 bypass 部署)。
FALLBACK_COOLDOWN_V31 = os.environ.get("FALLBACK_COOLDOWN_V31", "0") == "1"
FALLBACK_COOLDOWN_MS = float(os.environ.get("FALLBACK_COOLDOWN_MS", "10000"))  # 同文本 10s 内不重复兜底
MAX_FALLBACK_PER_SESSION = int(os.environ.get("MAX_FALLBACK_PER_SESSION", "3"))  # 单会话最多兜底 3 次
TTS_CACHE_ENABLED = os.environ.get("TTS_CACHE_ENABLED", "1") == "1"  # 默认 '1'=原行为
print(f"[p0-cleanup] FALLBACK_COOLDOWN_V31={int(FALLBACK_COOLDOWN_V31)} "
      f"cooldown_ms={FALLBACK_COOLDOWN_MS} max_per_session={MAX_FALLBACK_PER_SESSION} "
      f"TTS_CACHE_ENABLED={int(TTS_CACHE_ENABLED)} (default: cooldown off, cache on)",
      flush=True)

# ===== 35-WAKE-ACK-REFORM: 唤醒确认语改造 (架构 §6.7) =====
# WAKE_ACK_V31: 默认 '1'=on。文本默认"您好，需要什么帮助？"(2026-07-29 改, 更短积极, 不漫不经心)。
#   纯文本增强, 走原 synthesize_and_send 动态 TTS 链路 (bypass + ack + cache + cooldown 全不变)。
#   flag off ('0'): 回退原文本"我在，请讲"(用于 A/B 或不满意时一行回滚)。
#   WAKE_ACK_TEXT 覆盖优先级最高(空=用 flag 默认)。
# WAKE_ACK_TEXT: 自定义确认语文本 (覆盖默认值)。空字符串=用 flag 默认。
# 分流不变: listen 分支=Wake-only 播确认语; reply/tool 分支=Wake+Command 跳确认语。
# ===== 44-WAKE-ACK-SPEED (架构 §6.7, 2026-07-29 二追加): 唤醒确认语独立加速 =====
# WAKE_ACK_SPEED: 默认 '0'=off (off 时 wake_ack_speed=1.0, 与正式 TTS 同速, 零行为改变)。
#   on 时: 按档位 WAKE_ACK_SPEED_VAL 给唤醒确认语加语速(只 Wake ACK, 不长句)。
#   原理: CosyVoice V1 stream=True 时 speed!=1.0 会 assert 崩溃(model.py 源码约束, 报告42 已证);
#         故 WAKE_ACK_SPEED on 时, synthesize_and_send 收到 wake_ack 文本 → 旁路请求带
#         speed=WAKE_ACK_SPEED_VAL + worker 走 stream=False (speed 真生效, 短句首 PCM 略慢但语速快)。
#   WAKE_ACK_SPEED_VAL: 1.08/1.12/1.15 三档 A/B (架构默认 1.12)。仅 WAKE_ACK_SPEED=1 时生效。
WAKE_ACK_V31 = os.environ.get("WAKE_ACK_V31", "1") == "1"
WAKE_ACK_TEXT = os.environ.get("WAKE_ACK_TEXT", "").strip()
if WAKE_ACK_TEXT:
    _WAKE_ACK_DEFAULT = WAKE_ACK_TEXT
else:
    # 44-WAKE-ACK: 文本默认改"您好，需要什么帮助？"(更短积极); WAKE_ACK_V31=0 回退最旧"我在，请讲"
    _WAKE_ACK_DEFAULT = "您好，需要什么帮助？" if WAKE_ACK_V31 else "我在，请讲"
# WAKE_ACK_SPEED: 默认 off (零行为改变); on 时 WAKE_ACK_SPEED_VAL 只对 Wake ACK 短句生效
WAKE_ACK_SPEED = os.environ.get("WAKE_ACK_SPEED", "0") == "1"
WAKE_ACK_SPEED_VAL = float(os.environ.get("WAKE_ACK_SPEED_VAL", "1.12"))
# 限制档位白名单(防误配 1.5 等极端值导致爆音/失真); 超出回退 1.12
if WAKE_ACK_SPEED and WAKE_ACK_SPEED_VAL not in (1.08, 1.12, 1.15):
    print(f"[44-wake-ack-speed] WARN WAKE_ACK_SPEED_VAL={WAKE_ACK_SPEED_VAL} 不在白名单(1.08/1.12/1.15), 回退 1.12", flush=True)
    WAKE_ACK_SPEED_VAL = 1.12
print(f"[35-wake-ack] WAKE_ACK_V31={int(WAKE_ACK_V31)} text={_WAKE_ACK_DEFAULT!r} "
      f"(WAKE_ACK_TEXT override={'yes' if WAKE_ACK_TEXT else 'no'})",
      flush=True)
print(f"[44-wake-ack-speed] WAKE_ACK_SPEED={int(WAKE_ACK_SPEED)} val={WAKE_ACK_SPEED_VAL} "
      f"(default off; on 时 Wake ACK 走 stream=False + speed, 正式长句不受影响)",
      flush=True)

# ===== 40-WAKE-FIRST-ACK-GUARANTEE: 唤醒后第一次 ASR 必播反馈(问题1修复) =====
# 默认 '0'=off(BU 设计: apply 后零改变, 灰度 enable 才生效)。
#   on 时: 唤醒后第一次 ASR 若 query_router 返回 silent/兜底达上限/同文本cooldown/短文本<=2字过滤
#          -> 降级为播 WAKE_ACK(而非静默/兜底), 保证'唤醒必有反馈'。
#   不误伤: 命中规则(tool/reply正常) -> 走正常分支, patch 不介入; 纯唤醒词(listen) -> 已播 WAKE_ACK。
#   FOLLOWUP 轮(wake_first_asr_done=True) -> patch 条件 not True -> 不介入。
# WAKE_FIRST_ACK_GUARANTEE_TEXT: 自定义降级文本(空=用 _WAKE_ACK_DEFAULT)。
WAKE_FIRST_ACK_GUARANTEE_V31 = os.environ.get("WAKE_FIRST_ACK_GUARANTEE_V31", "0") == "1"
WAKE_FIRST_ACK_GUARANTEE_TEXT = os.environ.get("WAKE_FIRST_ACK_GUARANTEE_TEXT", "").strip()
print(f"[40-wake-first-ack] WAKE_FIRST_ACK_GUARANTEE_V31={int(WAKE_FIRST_ACK_GUARANTEE_V31)} "
      f"text={WAKE_FIRST_ACK_GUARANTEE_TEXT!r}", flush=True)
# ===== 60-WAKE-ACK-TIMEOUT: wake 超时兜底寒暄(解"唤醒灯亮但没寒暄") =====
# 真机铁证(2026-07-29): wake → LISTENING 后 VAD 持续拒端点(VOICE_TOO_WEAK rms=299),
#   ASR 永不触发 → WAKE_FIRST_ACK_GUARANTEE(挂在 handle_asr 分支内)全部失效 → 无寒暄。
# 方案: wake 后启动单次超时定时器; 超时且未播 WAKE_ACK 且未进命令处理 → 直接播 WAKE_ACK。
#   - Wake+Command: 1.5s 内 ASR 返回命令(reply/tool) → 取消定时器, 执行命令, 不播 WAKE_ACK
#   - Wake-only(纯唤醒词): ASR listen 分支已播 WAKE_ACK → 取消定时器
#   - 超时(VAD 不端点/ASR 不触发): 定时器兜底播 WAKE_ACK(after_state=LISTENING)
# 不破坏: WAKE_FIRST_ACK_GUARANTEE(ASR 触发兜底仍在, 超时是额外兜底)/分句/bypass/cache/Dialogue/安全。
# WAKE_ACK_TIMEOUT_V31: 默认 '1'=on(解"没寒暄"痛点); '0'=off 完全不启动定时器, 行为与改前一致。
# WAKE_ACK_TIMEOUT_MS: 超时毫秒(默认 1500; 范围 1000-2500。过短误伤连读, 过长寒暄迟)。
WAKE_ACK_TIMEOUT_V31 = os.environ.get("WAKE_ACK_TIMEOUT_V31", "1") == "1"
try:
    _WATM = int(os.environ.get("WAKE_ACK_TIMEOUT_MS", "1500"))
    if not (1000 <= _WATM <= 2500):
        print(f"[60-wake-ack-timeout] WARN WAKE_ACK_TIMEOUT_MS={_WATM} 越界(1000-2500), 回退 1500", flush=True)
        _WATM = 1500
except Exception:
    _WATM = 1500
WAKE_ACK_TIMEOUT_MS = _WATM
print(f"[60-wake-ack-timeout] WAKE_ACK_TIMEOUT_V31={int(WAKE_ACK_TIMEOUT_V31)} "
      f"timeout_ms={WAKE_ACK_TIMEOUT_MS}", flush=True)
print(f"[29-dialogue-slot] DIALOGUE_SLOT_V31={int(DIALOGUE_SLOT_V31)} "
      f"CAPABILITY_REGISTRY_V31={int(CAPABILITY_REGISTRY_V31)} "
      f"registry_caps={len(_CAPABILITY_BY_INTENT)} query_strong={len(_QUERY_KEYWORDS_STRONG)} "
      f"(default: both off, 84 规则匹配原行为不变)",
      flush=True)

# 路径参数化: AI 主机默认; mac 排查用环境变量覆盖
#   mac 用法: CV_PATH=/Users/alamn/agent/v3/third_party/CosyVoice \
#             OUT_WAV=/tmp/v3_recv.wav python server.py
CV = os.environ.get('CV_PATH', '/root/CosyVoice')
sys.path.insert(0, CV)
sys.path.insert(0, CV + '/third_party/Matcha-TTS')
PORT = int(os.environ.get('PORT', '8765'))

# ===== 51-SEGMENT-TIMELINE + 63-KNOWLEDGE-RUNTIME: Phase2 候选扩展 =====
# 任务报告: reports/51-SEGMENT-TIMELINE-REAL-METRICS.md (B1 segment 级 18 时间戳)
#          reports/63-reflash (gateway 3 端点 admin↔gateway 闭环)
# 红线:
#   - Phase2 HTTP 端口默认 8774, 与生产 :8765 (WS) 完全独立, 不碰生产 PID.
#   - HTTP server 用 aiohttp.web(已装), 复用同 asyncio loop, 最小改动(不引入 FastAPI 重依赖).
#   - 知识 shadow/active namespace 由 _phase2_knowledge_lock 保护, 原子切换.
#   - WS_SEND_PROFILE=off 时 segment 打点零开销零行为改变(与 Batch A 候选字节一致).
PHASE2_HTTP_PORT = int(os.environ.get('PHASE2_HTTP_PORT', '8774'))
# 候选 mode 开关: PHASE2_GATEWAY_HTTP=1 才启 HTTP server(默认 on, 候选专用, 生产不会读此文件)
PHASE2_GATEWAY_HTTP = os.environ.get('PHASE2_GATEWAY_HTTP', '1') == '1'
# shadow namespace: {hotel_id: {release_id: {"entries": [...], "version": N, "loaded_at": ts}}}
_phase2_shadow_ns = {}
# active namespace 元数据: {hotel_id: {"release_id": ..., "version": ..., "activated_at": ts}}
_phase2_active_meta = {}
# 知识读写锁(shadow-load/query/activate 互斥, 防 partial read)
_phase2_knowledge_lock = asyncio.Lock()
print(f"[phase2] PHASE2_HTTP_PORT={PHASE2_HTTP_PORT} GATEWAY_HTTP={int(PHASE2_GATEWAY_HTTP)} "
      f"(候选 :8774 独立; shadow/active namespace + 锁)", flush=True)

# ===== 51-SEGMENT-TIMELINE (B1): segment 级 18 字段时间戳 =====
# 报告: reports/51-SEGMENT-TIMELINE-REAL-METRICS.md
# 设计(架构 §六 B1): 每个 Segment 记录 18 字段
#   server 侧(本进程实填): tts_id / segment_index / segment_text / segment_text_sha
#     / synth_start / first_pcm / synth_complete / send_start / send_complete / partial_write_count
#   client 侧(client 回填, server 暂置 None): client_first_receive / client_last_receive
#     / client_written_bytes / playback_segment_start / playback_segment_end / queue_min_ms
#     / queue_max_ms / underrun_before / underrun_after / underrun_delta / actual_gap_ms
# 红线:
#   - WS_SEND_PROFILE=off 时 _phase2_seg_trace_* 全部 no-op, 与 Batch A 候选字节一致.
#   - 打点写入 Trace(不污染主链路): 进 _phase2_segment_traces dict, tts_end 时附进 payload.
_phase2_segment_traces = {}  # tts_id -> [segment_dict, ...]


def _phase2_seg_new(tts_id, seg_index, seg_text):
    # WS_SEND_PROFILE=on 时建一个 segment trace dict(18 字段, server 侧先填, client 侧 None).
    # off 时返回 None(调用方判空跳过所有 _phase2_seg_mark).
    if not WS_SEND_PROFILE:
        return None
    d = {
        "tts_id": tts_id,
        "segment_index": seg_index,
        "segment_text": (seg_text or "")[:120],
        "segment_text_sha": hashlib.sha256((seg_text or "").encode("utf-8")).hexdigest()[:12],
        "synth_start": None,
        "first_pcm": None,
        "synth_complete": None,
        "send_start": None,
        "send_complete": None,
        "partial_write_count": 0,
        # ---- client 侧(client 回填, 初始 None) ----
        "client_first_receive": None,
        "client_last_receive": None,
        "client_written_bytes": None,
        "playback_segment_start": None,
        "playback_segment_end": None,
        "queue_min_ms": None,
        "queue_max_ms": None,
        "underrun_before": None,
        "underrun_after": None,
        "underrun_delta": None,
        "actual_gap_ms": None,
    }
    _phase2_segment_traces.setdefault(tts_id, []).append(d)
    return d


def _phase2_seg_mark(seg, key, value=None):
    # seg 非 None 时, 写 seg[key]=value(默认相对 func_enter 的 ms). 线程安全: 单事件循环内.
    if seg is None or not WS_SEND_PROFILE:
        return
    if value is None:
        value = round(time.monotonic() * 1000.0, 2)
    seg[key] = value


def _phase2_seg_incr(seg, key, n=1):
    if seg is None or not WS_SEND_PROFILE:
        return
    seg[key] = (seg.get(key) or 0) + n


def _phase2_seg_finalize_tts_end(tts_id):
    # tts_end 时取该 tts_id 的 segments list 附进 payload. 返回 list(可能空).
    if not WS_SEND_PROFILE:
        return None
    return _phase2_segment_traces.get(tts_id, [])
OUT_WAV = os.environ.get('OUT_WAV', '/root/server_recv_16k.wav')
CV_MODEL_ID = 'iic/CosyVoice-300M-SFT'  # v1 中文女(用户满意音色)
TTS_SPK = '中文女'
USE_INT8 = os.environ.get('USE_INT8', '1') == '1'  # mac 排查可 USE_INT8=0

# ===== 24-BYPASS-FORWARD: :8767 GPU FP32 无 cache 旁路 Worker 转发 Feature Flag =====
# 架构文档 §20.2 目标路线: TTS_AUDIO_CACHE=off + TTS_PRIMARY=cosyvoice_v1_rocm_fp32
# 默认 '0' (走原进程内合成 + cache hit/miss, 一行不改)。
# 置 '1' 时: synthesize_and_send 把整句合成转发到本地 :8767 GPU FP32 无 cache Worker
# (跳过 cache 读写, 确保单一音色来自 :8767 FP32); PCM 下发仍复用 send_pcm_streaming
# (含 SEND_DELAY_MS 节流 + AEC REF 同步), client 不感知差异。
# :8767 不可达 / 超时 / 协议错 → 自动 fallback 到原 cache miss 进程内合成路径。
# 切换: drop-in 加 TTS_BACKEND_BYPASS_8767=1 并 restart v3-server (生产现在勿动)。
TTS_BACKEND_BYPASS_8767 = os.environ.get('TTS_BACKEND_BYPASS_8767', '0') == '1'
TTS_BYPASS_8767_URL = os.environ.get('TTS_BYPASS_8767_URL', 'ws://127.0.0.1:8767')
TTS_BYPASS_8767_CONNECT_TIMEOUT = float(os.environ.get('TTS_BYPASS_8767_CONNECT_TIMEOUT', '2.0'))
TTS_BYPASS_8767_RECV_TIMEOUT = float(os.environ.get('TTS_BYPASS_8767_RECV_TIMEOUT', '30.0'))
print(f"[bypass-8767] TTS_BACKEND_BYPASS_8767={int(TTS_BACKEND_BYPASS_8767)} "
      f"url={TTS_BYPASS_8767_URL} connect_timeout={TTS_BYPASS_8767_CONNECT_TIMEOUT}s "
      f"recv_timeout={TTS_BYPASS_8767_RECV_TIMEOUT}s (default off=原 cache 路径)",
      flush=True)

# ===== 44-CHUNK-SEQ + 44-SENTENCE-SPLIT (架构 §20.6.3 / §20.6 长句分段, 2026-07-29 二追加) =====
# TTS_CHUNK_SEQ_V31: 默认 '0'=off。off 时 send_pcm_streaming / _synthesize_via_bypass_8767 / tts_end
#   行为与改动前字节级一致(老 client 零感知); on 时:
#   - send_pcm_streaming 新增可选参数 tts_id/segment_index/chunk_index_ref/pcm_offset_ref/is_last_chunk
#   - 每个 20ms PCM 帧前(ws.send 前)可选发 JSON chunk_meta(segment_index/chunk_index/pcm_offset/pcm_bytes/is_last_chunk)
#   - _synthesize_via_bypass_8767 累计 pcm_offset + chunk_index, tts_end 带 segment_count/chunk_count/total_bytes
#   - 四点字节统计: server 落 worker_pcm_bytes(:8767 tts_end total_bytes) + gateway_forwarded_bytes(累计)
# SENTENCE_SPLIT_V31: 默认 '0'=off。off 时 synthesize_and_send 走原 cache miss 串行分句(start_seq 连续);
#   on 时入口判断长文本 → 语义分句流水线(段1 合成完即发 → client 播段1 时 Worker 合成段2 → 段2 发 → ...)。
#   同 tts_id, segment_index 递增; 最后段 is_last_chunk + 全段播完才 playback_complete。
TTS_CHUNK_SEQ_V31 = os.environ.get('TTS_CHUNK_SEQ_V31', '0') == '1'

# ===== Batch A: WS_SEND_PROFILE - send_elapsed 11 阶段拆分(架构 V3.1 §48 / 任务 A1) =====
# flag off: 零影响(不计时, 不输出 stage_profile_ms), 与原 server.py 完全一致
# flag on: 在 _synthesize_via_bypass_8767 计时 11 阶段, tts_end 附 stage_profile_ms
WS_SEND_PROFILE = os.environ.get('WS_SEND_PROFILE', '0') == '1'
print(f"[48-ws-send-profile] WS_SEND_PROFILE={int(WS_SEND_PROFILE)} "
      f"(default off; on 时 send_elapsed 拆 11 阶段)", flush=True)


SENTENCE_SPLIT_V31 = os.environ.get('SENTENCE_SPLIT_V31', '0') == '1'
# 长句判定阈值(架构 §20.6 长句语义分段: 每段 10-30 汉字)
SENTENCE_SPLIT_MIN_LEN = int(os.environ.get('SENTENCE_SPLIT_MIN_LEN', '22'))   # >22 字触发分句
SENTENCE_SPLIT_EST_SEC = float(os.environ.get('SENTENCE_SPLIT_EST_SEC', '5.0'))  # 预计 >5s 也触发(22050*2 字节/秒)
print(f"[44-chunk-seq] TTS_CHUNK_SEQ_V31={int(TTS_CHUNK_SEQ_V31)} (default off; on 时 chunk 序号协议生效)",
      flush=True)
print(f"[44-sentence-split] SENTENCE_SPLIT_V31={int(SENTENCE_SPLIT_V31)} "
      f"min_len={SENTENCE_SPLIT_MIN_LEN} est_sec={SENTENCE_SPLIT_EST_SEC} "
      f"(default off; on 时长句分句流水线段1即发段2后台合成)",
      flush=True)

# ===== 33-PUNCTUATION-SEGMENT (架构 §33 标点主导分句, 2026-07-29 五 新增) =====
# PUNCTUATION_SEGMENT_V31: 默认 '0'=off。off 时 _split_for_pipeline 走 BZ 原 split_sentences
#   (强终止符切 + 逗号条件切, 字数主导, 行为与历史版本一致); on 时走标点主导分句:
#   - SpeechTextFormatter 无标点长句补自然标点(§33.3)
#   - SemanticSegmenter 标点主导切分(§33.4: 强边界切/弱边界候选/不切边界保护, 禁止固定字数硬切)
#   - SemanticSegmentValidator 10 类异常校验(§33.5: join==原文/empty/too_short/too_long/
#     broken_number/entity/negation/english/measure/punctuation), 失败回退整包[text]
#   红线: flag off 时 _split_for_pipeline / _should_split_pipeline / SENTENCE_SPLIT 行为零改变;
#         模块 punctuation_segment.py 独立, import 失败不影响主路径(回退 split_sentences)。
# 任务报告: reports/05-PUNCTUATION-SEGMENT-500-CASE.md
PUNCTUATION_SEGMENT_V31 = os.environ.get('PUNCTUATION_SEGMENT_V31', '0') == '1'
print(f"[33-punctuation-segment] PUNCTUATION_SEGMENT_V31={int(PUNCTUATION_SEGMENT_V31)} "
      f"(default off; on 时标点主导分句 SpeechTextFormatter+SemanticSegmenter+Validator 替代 BZ 字数切分)",
      flush=True)

# ===== 62-DROPOUT-ROOTCAUSE (codex 审 62, 2026-07-29 六 新增) =====
# 攻 codex 审 Top1(送 elapsed>audio_dur rtf>1 双重限速) + Top2(分句串行段间 gap)
# 三个 flag 默认 off; off 时行为与改动前字节级一致(老路径零回归)。
# 任务报告: reports/04-P0-AUDIO-INTEGRITY-IMPLEMENTATION.md

# PIPELINE_PARALLEL_V31: 默认 '0'=off。on 时 _synthesize_pipeline_split_v31 改"预取流水线":
#   段0 透传 client 期间, 提前建立段1 bypass_ws + 发 tts_request + 等 tts_start(与段0 发送重叠),
#   段0 透传完直接收段1 chunk(省掉段间 ~1.1s 的"连接+握手+首PCM"空窗, 根治 codex Top2)。
#   非 asyncio.gather 真并行(GPU 单卡串行, 真并行会争抢 GPU 上下文变慢);
#   仅重叠"连接/握手/首PCM 等待"与"段0 透传", GPU 仍串行(段0 synth 完才轮段1)。
#   红线: flag off 时 _synthesize_pipeline_split_v31 走原串行实现(段i 收齐才段i+1), 零改变。
PIPELINE_PARALLEL_V31 = os.environ.get('PIPELINE_PARALLEL_V31', '0') == '1'
# 预取提前量: 段0 发送进度达多少比例时启动段1 预连接(0.0=段0 首 chunk 即预取, 1.0=段0 收完才预取=退化为串行)
PIPELINE_PREFETCH_RATIO = float(os.environ.get('PIPELINE_PREFETCH_RATIO', '0.0'))

# SEND_DELAY_ADAPTIVE_V31: 默认 '0'=off。on 时 send_pcm_streaming 节流自适应:
#   监控本 tts_id 的实时 rtf(合成耗时/音频时长), rtf>1 时降节流(SEND_DELAY_MS×0.5, 让 server 发得快跟上 client 播放),
#   rtf<=1 时正常节流(防 AudioTrack buffer 突增)。根治 codex Top1"rtf>1 双重限速"。
#   USE_AEC=1 时强制 20ms 不变(REF 同步守护, rtf 自适应绝不破 AEC 时间轴)。
#   红线: flag off 时 SEND_DELAY_MS 行为与改动前一致(固定 SEND_DELAY_SEC); USE_AEC=1 时本 flag 强制无效。
SEND_DELAY_ADAPTIVE_V31 = os.environ.get('SEND_DELAY_ADAPTIVE_V31', '0') == '1'
SEND_DELAY_ADAPTIVE_FACTOR = float(os.environ.get('SEND_DELAY_ADAPTIVE_FACTOR', '0.5'))  # rtf>1 时节流系数

# SENTENCE_SPLIT_MIN_SEG_SEC: 分句最小段音频时长(秒)。分句后任一段估算 audio_dur < 此值则不分句(走单段 bypass)。
#   根治 codex Top2"欢迎词 18 字分 2 段, 段0 仅 1.196s, 段间必 gap": 短段分句无首声收益反引段间 gap。
#   默认 1.5s(段 <1.5s 不分句); 0=禁用(完全按 _should_split_pipeline 原逻辑分句)。
SENTENCE_SPLIT_MIN_SEG_SEC = float(os.environ.get('SENTENCE_SPLIT_MIN_SEG_SEC', '1.5'))
print(f"[62-dropout] PIPELINE_PARALLEL_V31={int(PIPELINE_PARALLEL_V31)} "
      f"prefetch_ratio={PIPELINE_PREFETCH_RATIO} "
      f"SEND_DELAY_ADAPTIVE_V31={int(SEND_DELAY_ADAPTIVE_V31)} "
      f"adaptive_factor={SEND_DELAY_ADAPTIVE_FACTOR} "
      f"min_seg_sec={SENTENCE_SPLIT_MIN_SEG_SEC} "
      f"(default off; on 时攻 codex Top1 rtf>1 双重限速 + Top2 段间 gap)",
      flush=True)

# 54-PROTOCOL-V2: Protocol V2 帧协议草案(feature flag, 默认 v1=off 不破坏)
# 任务报告: reports/54-SERVER-SEGMENT-PROTOCOL-V2-IMPL.md
# 模块: scripts/v2proto/{segments,codec}/ (SemanticSegmenter + PipelineEncoder + V2 Codec)
# 协议: binary frame 32B 头(magic+proto_ver+flags+tts_id_hash+session_epoch+segment_index
#        +frame_index+global_frame_index+pts_samples+payload_length+payload_crc32)
#        + global_frame_index 跨段连续 + CRC32 payload 完整性 + session_epoch 代际隔离。
# 启用条件(双端协商, 避免老客户端收到 V2 帧解析失败):
#   1. 服务端 TTS_AUDIO_PROTOCOL=v2
#   2. 客户端 hello/session_init 上报 protocol_version>=2 + capabilities 含 'audio_segment_v2'
#   两条件都满足才 per-session 走 V2; 否则走 V1(完全不变)。
# 红线: flag off(v1 默认)时 synthesize_and_send 走原 cache/bypass/pipeline 路径, 零行为改变;
#   V2 路径不调 send_pcm_streaming(用自己的 encode_frame), 但保留 AEC REF 喂入(集成时加);
#   barge_stop/cancel 联动(sess.barge_stop → encoder.request_cancel);
#   不 Cache 音频(PipelineEncoder 瞬时 buffer 终态释放, 不跨 tts_id)。
TTS_AUDIO_PROTOCOL = os.environ.get("TTS_AUDIO_PROTOCOL", "v1")  # v1 默认(V1 路径不动); v2 启用 V2 帧协议
print(f"[54-proto-v2] TTS_AUDIO_PROTOCOL={TTS_AUDIO_PROTOCOL!r} (default v1; v2 需客户端协商 protocol_version>=2 + audio_segment_v2)",
      flush=True)

print("[load] ASR...", flush=True)
from funasr import AutoModel
asr = AutoModel(model='iic/SenseVoiceSmall', disable_update=True)  # SenseVoice(抗噪远场强, rtf~1.1 CPU; paraformer 识短句差已弃)
print("[load] TTS...", flush=True)
from cosyvoice.cli.cosyvoice import AutoModel as CVModel
cv = CVModel(model_dir=CV_MODEL_ID)  # FP32 GPU(回退fp16: 实测破音+我在拉长; FP32音质好+CPU解耦, rtf慢但cache命中快)
TTS_RATE = cv.sample_rate  # 22050
print(f"[load] done. spks={cv.list_available_spks()}", flush=True)


def apply_int8():
    """A6R_PC: LLM selective INT8 (per-channel) + Flow/HiFT INT8.
    Keep llm.embed.out.0 + llm_decoder FP32 (root cause of deng->dou).
    """
    import torch.nn as nn
    from torch.ao.quantization import per_channel_dynamic_qconfig
    import torch.ao.quantization as quant
    KEEP_FP32 = {"llm.embed.out.0", "llm_decoder"}
    class _W(nn.Module):
        def __init__(self, l):
            super().__init__(); self.l = l
        def forward(self, x):
            return self.l(x)
    llm = cv.model.llm
    n_q = 0; n_skip = 0
    for name, mod in list(llm.named_modules()):
        if isinstance(mod, nn.Linear):
            if name in KEEP_FP32:
                n_skip += 1
                continue
            try:
                parent_name, _, child_name = name.rpartition(".")
                target = llm
                if parent_name:
                    for p in parent_name.split("."):
                        target = getattr(target, p)
                old_lin = getattr(target, child_name)
                w = _W(old_lin).eval()
                qw = quant.quantize_dynamic(w, {nn.Linear: per_channel_dynamic_qconfig}, inplace=False)
                setattr(target, child_name, qw.l)
                n_q += 1
            except Exception as e:
                print(f"[int8] skip {name}: {e}", flush=True)
    for _n in ["flow", "hift"]:
        _m = getattr(cv.model, _n, None)
        if _m is not None:
            try:
                quant.quantize_dynamic(_m, {nn.Linear}, inplace=True)
                print(f"[int8] {_n} done", flush=True)
            except Exception as e:
                print(f"[int8] {_n} fail: {e}", flush=True)
    print(f"[startup] A6R_PC: LLM per-channel INT8 ({n_q} layers) + embed.out.0/llm_decoder FP32 ({n_skip}) + Flow/HiFT INT8", flush=True)


# ===== TTS Cache(常见句预合成, 命中 0 延迟) =====
# 安全加载设计(2026-07-26):
#   1. cache 文件按 backend 分(INT8/FP32-GPU), 不再共用 tts_cache.pkl
#   2. meta 必须全字段匹配才加载; 不匹配绝不覆盖已有文件, 只写临时文件
#   3. 单句缺失只补缺失句, 不全部重生成
#   4. 原子替换: .tmp -> 重新读取验证 -> os.replace
#   5. 生成未全部成功, 绝不替换正式文件
TTS_CACHE = {}
CACHE_SENTENCES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_sentences.json')
CACHE_SCHEMA_VERSION = 1

# 按 backend 选 cache 文件(向后兼容: 环境变量 TTS_CACHE_FILE 优先)
def _cache_file_for_backend():
    if os.environ.get('TTS_CACHE_FILE'):
        return os.environ.get('TTS_CACHE_FILE')
    if USE_INT8:
        return '/root/tts_cache.cv1.cpu_int8.pkl'
    return '/root/tts_cache.cv1.gpu_fp32.pkl'

CACHE_FILE = _cache_file_for_backend()


def _load_cache_sentences():
    """从 cache_sentences.json 加载权威文本清单。失败则回退到内置最小集(不阻断启动)。"""
    builtin_min = [
        "你好，有什么可以帮您？",
        "不客气",
        "我是小智语音助手，可以帮你控制电视、查询信息",
    ]
    try:
        import json
        with open(CACHE_SENTENCES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        sents = data.get('sentences', [])
        if not isinstance(sents, list) or len(sents) == 0:
            print(f"[cache] {CACHE_SENTENCES_FILE} 句数为 0, 回退内置最小集", flush=True)
            return builtin_min
        return sents
    except Exception as e:
        print(f"[cache] 加载 {CACHE_SENTENCES_FILE} 失败: {e}, 回退内置最小集", flush=True)
        return builtin_min


def _text_set_hash(texts):
    """SHA256 of 排序后文本 join('\n'). 用于 meta 校验文本集一致性。"""
    return hashlib.sha256('\n'.join(sorted(texts)).encode('utf-8')).hexdigest()


def _build_meta(texts):
    return {
        'model': CV_MODEL_ID,
        'speaker': TTS_SPK,
        'device': 'cuda' if torch.cuda.is_available() else 'cpu',
        'quantization': 'int8' if USE_INT8 else 'fp32',
        'sample_rate': TTS_RATE,
        'cache_schema_version': CACHE_SCHEMA_VERSION,
        'text_set_hash': _text_set_hash(texts),
        'sentence_count': len(texts),
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def _validate_pcm(pcm, text):
    """单句 PCM 健全性检查。返回 (ok, reason)。"""
    if not pcm or len(pcm) < 2:
        return False, 'empty'
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(samples)
    dur = n / TTS_RATE
    if dur < 0.5 or dur > 30.0:
        return False, f'duration_out_of_range({dur:.2f}s)'
    if not np.all(np.isfinite(samples)):
        return False, 'nan_or_inf'
    return True, 'ok'


def _meta_matches(meta, texts):
    """全字段匹配校验。任一关键字段不符返回 False。"""
    required = ['model', 'speaker', 'device', 'quantization', 'sample_rate',
                'cache_schema_version', 'text_set_hash', 'sentence_count']
    for k in required:
        if k not in meta:
            return False
    if meta['quantization'] != ('int8' if USE_INT8 else 'fp32'):
        return False
    if meta['speaker'] != TTS_SPK:
        return False
    if meta['model'] != CV_MODEL_ID:
        return False
    if meta['sample_rate'] != TTS_RATE:
        return False
    if meta['cache_schema_version'] != CACHE_SCHEMA_VERSION:
        return False
    if meta['sentence_count'] != len(texts):
        return False
    if meta['text_set_hash'] != _text_set_hash(texts):
        return False
    # device 校验宽松(允许 cpu/cuda 都加载, 关键是 quantization)
    return True


def _atomic_save(cache_dict, texts):
    """写 .tmp -> 重新读取验证 -> os.replace 原子替换。失败返回 False, 绝不破坏正式文件。"""
    import pickle, tempfile
    tmp = CACHE_FILE + '.tmp'
    meta = _build_meta(texts)
    try:
        with open(tmp, 'wb') as f:
            pickle.dump((dict(cache_dict), meta), f)
        # 重新读取验证
        with open(tmp, 'rb') as f:
            chk_cache, chk_meta = pickle.load(f)
        if len(chk_cache) != len(cache_dict):
            print(f"[cache] 临时文件验证失败: key 数不符({len(chk_cache)} vs {len(cache_dict)}), 不替换正式文件", flush=True)
            os.remove(tmp)
            return False
        if not _meta_matches(chk_meta, texts):
            print(f"[cache] 临时文件 meta 验证失败, 不替换正式文件", flush=True)
            os.remove(tmp)
            return False
        # 全部 key 验证
        for k in cache_dict:
            if k not in chk_cache:
                print(f"[cache] 临时文件缺 key: {k!r}, 不替换正式文件", flush=True)
                os.remove(tmp)
                return False
        os.replace(tmp, CACHE_FILE)
        print(f"[cache] 原子替换 {CACHE_FILE} ({len(chk_cache)} 句)", flush=True)
        return True
    except Exception as e:
        print(f"[cache] 原子保存失败: {e}", flush=True)
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
        return False



def synth_to_pcm(text):
    """合成一句 → 整段 PCM bytes(int16)。warmup / 实时 / 动态缓存复用。
    [修复] A agent 改 warmup_cache 时误删本函数, 补回 (否则 restart NameError)。"""
    chunks = []
    for chunk in cv.inference_sft(text, TTS_SPK, stream=False):
        chunks.append(chunk['tts_speech'])
    wav = torch.cat(chunks, dim=-1).squeeze().cpu().numpy()
    return (np.clip(wav, -1, 1) * 32767).astype(np.int16).tobytes()


def split_sentences(text):
    """按标点语义分句(D 方案: 长句 miss 时分句流式, 首包 8s→1.7s).
    强终止符切(。！？；!?;) + 逗号条件切(逗号后剩余>=8字 且 当前累计>=8字才切, 保护数字/房号/时间).
    整句<15字不切(避免短句碎片)."""
    if len(text) < 15:
        return [text]
    # 1. 强终止符切
    parts = [p for p in _re.split(r'(?<=[。！？；!?;])', text) if p.strip()]
    if len(parts) > 1:
        return parts
    # 2. 无强终止符, 尝试逗号切(仅当逗号后剩余>=8字 且 当前累计>=8字)
    result, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in '，,':
            rest = len(text) - (i + 1)
            if rest >= 8 and len(buf) >= 8:
                result.append(buf)
                buf = ""
    if buf:
        result.append(buf)
    return result if result else [text]


def warmup_cache():
    """安全加载 cache: meta 全匹配直接加载; 否则单句缺失补全; 原子替换。

    红线:
      - 绝不删除/覆盖任何 .bak / 备份文件
      - 正式文件仅在 87 句(或当前文本集)全部验证通过后才 os.replace
      - 生成失败保留原 cache(若有), 不阻断启动(回退实时合成)
    """
    import pickle
    texts = _load_cache_sentences()
    t0 = time.time()

    loaded_from_disk = False
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'rb') as f:
                cache, meta = pickle.load(f)
        except Exception as e:
            print(f"[cache] 加载 {CACHE_FILE} 失败: {e}", flush=True)
            cache, meta = {}, {}

        if _meta_matches(meta, texts) and len(cache) == len(texts):
            # 全匹配, 直接加载
            for k in texts:
                if k not in cache:
                    print(f"[cache] meta 匹配但缺 key {k!r}, 触发补全", flush=True)
                    break
            else:
                TTS_CACHE.update(cache)
                print(f"[cache] 从 {CACHE_FILE} 加载 {len(cache)} 句 (meta 全匹配, 省 warmup 合成)", flush=True)
                return

        # meta 不符或单句缺失: 绝不覆盖已有文件, 仅在内存补全 + 写临时文件
        print(f"[cache] meta 不全匹配或句数不符(磁盘 {len(cache)}/文本集 {len(texts)}), 增量补全", flush=True)
        # 复用磁盘上仍可用的句(降低合成成本), 缺失句才合成
        working = {}
        reused = 0
        for k in texts:
            if k in cache:
                working[k] = cache[k]
                reused += 1
            # 缺失句下面合成
        print(f"[cache] 复用磁盘 {reused} 句, 需合成 {len(texts) - reused} 句", flush=True)
    else:
        print(f"[cache] {CACHE_FILE} 不存在, 全量合成 {len(texts)} 句", flush=True)
        working = {}

    # 合成缺失句
    TTS_CACHE.update(working)
    miss = [s for s in texts if s not in working]
    synth_fail = []
    for s in miss:
        try:
            pcm = synth_to_pcm(s)
            ok, reason = _validate_pcm(pcm, s)
            if not ok:
                print(f"[cache] PCM 校验失败 [{reason}]: {s!r}, 跳过(不写入)", flush=True)
                synth_fail.append((s, reason))
                continue
            TTS_CACHE[s] = pcm
            print(f"[cache] +{len(pcm)//2} samples ({len(pcm)/TTS_RATE/2:.2f}s): {s}", flush=True)
        except Exception as e:
            print(f"[cache] 合成异常 {s!r}: {e}, 跳过(不写入)", flush=True)
            synth_fail.append((s, str(e)))

    # 全部成功才原子保存; 有失败则不替换正式文件(但内存 cache 仍可用于本次运行)
    miss_count = len([s for s in texts if s not in TTS_CACHE])
    if miss_count == 0:
        saved = _atomic_save(TTS_CACHE, texts)
        if not saved:
            print(f"[cache] 原子保存失败, 正式文件未变更, 内存 cache {len(TTS_CACHE)} 句仍可用", flush=True)
    else:
        print(f"[cache] 有 {miss_count} 句未生成成功, 正式文件不变更(保护现有 cache), 内存 cache 可用 {len(TTS_CACHE)} 句", flush=True)
        for s, r in synth_fail:
            print(f"[cache] FAILED {s!r}: {r}", flush=True)

    print(f"[cache] warmup done: 内存 {len(TTS_CACHE)}/{len(texts)} 句, 用时 {time.time()-t0:.1f}s", flush=True)


if USE_INT8:
    apply_int8()
# 28-P0-CLEANUP-BYARCH P0-1: TTS_CACHE_ENABLED=0 时跳过 warmup(不读 pkl, 不合成 87 句,
#   省启动时间 + 不占内存)。bypass=1 部署应设 '0' (主路径已不读不写 cache, warmup 纯浪费)。
#   默认 '1'=原行为(保现状, 非 bypass 部署仍用 cache)。
if TTS_CACHE_ENABLED:
    warmup_cache()  # 必须在 INT8 之后, 否则 Cache 音质(FP32)与实时(INT8)不一致
else:
    print(f"[cache] TTS_CACHE_ENABLED=0, 跳过 warmup(空 cache, bypass 模式)", flush=True)

# ===== 启动摘要(GPT 容量测试用, 显式标注 device/quant/cuda/cache) =====
_tts_device = "cuda" if torch.cuda.is_available() else "cpu"
# CUDA_VISIBLE_DEVICES="" 会令 torch.cuda.is_available()=False -> CosyVoice 走 CPU
print(f"[startup] tts_device={_tts_device} tts_quant={'int8' if USE_INT8 else 'fp32'} "
      f"torch_cuda_available={torch.cuda.is_available()} cache_size={len(TTS_CACHE)} "
      f"spk={TTS_SPK} rate={TTS_RATE} cv_model={CV_MODEL_ID}", flush=True)
if _tts_device == "cuda":
    print(f"[startup] GPU name={torch.cuda.get_device_name(0)}", flush=True)
print(f"[startup] OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS','unset')} "
      f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES','unset')!r}", flush=True)
# cache backend 摘要(2026-07-26 新增: 便于运维快速判断加载了哪个 cache)
_cache_texts = _load_cache_sentences()
print(f"[startup] cache_backend={'int8' if USE_INT8 else 'fp32'} cache_file={CACHE_FILE} "
      f"cache_size={len(TTS_CACHE)} cache_text_hash={_text_set_hash(_cache_texts)[:12]} "
      f"device={_tts_device} quantization={'int8' if USE_INT8 else 'fp32'}", flush=True)


def write_wav(path, pcm, rate, ch):
    with wave.open(path, "wb") as w:
        w.setnchannels(ch); w.setframerate(rate); w.setsampwidth(2); w.writeframes(pcm)


_asr_proc = None
_asr_lock = __import__('threading').Lock()

# 51-ASR-P95: warm inference 保温(feature flag, 默认启用) — 重 apply 58
# 根因: Qwen3-ASR persistent proc 存活, 但空闲一段时间(会话边界/wake 后首批)后,
#   GPU/CPU 侧上下文冷启动, 首批推理 P95 飙到 1.7-3.9s(正常 P50=180ms)。
#   全量日志(17023 条) P95=400ms, 但 wake 后首批命中冷启动, 采样 P95=1720ms。
# 方案: 后台 daemon 线程每 ASR_WARM_INTERVAL_S 秒推一段短 PCM(结果丢弃),
#   保持 proc GPU/CPU 侧状态热。不写 cache, 不影响真实请求(用同一 _asr_lock 排队)。
# 红线: warm 结果丢弃(架构 §2.1 不 cache ASR); 低频 45s + 短 200ms + 用 _asr_lock 互斥;
#   proc 未就绪时跳过(不触发 lazy 启动); 失败不 raise(不杀 proc 不 fallback)。
ASR_WARM_KEEPALIVE = os.environ.get('ASR_WARM_KEEPALIVE', '1') == '1'
ASR_WARM_INTERVAL_S = float(os.environ.get('ASR_WARM_INTERVAL_S', '45'))
ASR_WARM_PCM_MS = int(os.environ.get('ASR_WARM_PCM_MS', '200'))
_asr_warm_thread = None
print(f"[asr-warm] ASR_WARM_KEEPALIVE={int(ASR_WARM_KEEPALIVE)} interval={ASR_WARM_INTERVAL_S}s pcm_ms={ASR_WARM_PCM_MS}ms", flush=True)


def _asr_warm_once():
    """推一次极短 PCM(静音) 给 ASR proc, 结果丢弃, 仅保温 GPU/CPU 上下文。
    用 _asr_lock 与真实请求互斥(真实请求优先, warm 排队等待)。"""
    import numpy as _np
    import time as _time
    # 200ms 静音 16k mono S16 = 3200 samples = 6400 bytes (>= 640 bytes 阈值, 触发完整推理路径)
    n_samples = int(16000 * ASR_WARM_PCM_MS / 1000)
    pcm = _np.zeros(n_samples, dtype=_np.int16).tobytes()
    with _asr_lock:
        try:
            if _asr_proc is None or _asr_proc.poll() is not None:
                return  # proc 未就绪, 不触发 lazy 启动(避免抢首启)
            t0 = _time.time()
            _asr_proc.stdin.write(f"{len(pcm)}\n".encode())
            _asr_proc.stdin.write(pcm)
            _asr_proc.stdin.flush()
            _ = _asr_proc.stdout.readline()  # 读回结果, 丢弃(不 cache, 不打业务日志)
            dt = _time.time() - t0
            # 仅打保温心跳日志(低频, 1 行/45s), 便于观测
            print(f"[asr-warm] keepalive dt={dt:.3f}s (result discarded)", flush=True)
        except Exception as _e:
            # 保温失败不 raise(不杀 proc, 不触发 fallback); 下次心跳重试
            print(f"[asr-warm] keepalive 失败(忽略): {_e}", flush=True)


def _asr_warm_loop():
    """warm keepalive daemon 线程主循环。"""
    import threading as _th
    import time as _time
    print(f"[asr-warm] daemon 启动 interval={ASR_WARM_INTERVAL_S}s pcm_ms={ASR_WARM_PCM_MS}ms", flush=True)
    # 首次延迟 1 个间隔(给启动 warmup 留时间)
    _time.sleep(ASR_WARM_INTERVAL_S)
    while True:
        try:
            _asr_warm_once()
        except Exception as _e:
            print(f"[asr-warm] loop 异常(忽略): {_e}", flush=True)
        _time.sleep(ASR_WARM_INTERVAL_S)


def _start_asr_warm_thread():
    """启动 warm keepalive daemon 线程(幂等, 重复调用不重复启动)。"""
    global _asr_warm_thread
    if not ASR_WARM_KEEPALIVE:
        print("[asr-warm] ASR_WARM_KEEPALIVE=0, 跳过 warm inference 保温", flush=True)
        return
    if _asr_warm_thread is not None and _asr_warm_thread.is_alive():
        return
    import threading as _th
    _asr_warm_thread = _th.Thread(target=_asr_warm_loop, name="asr-warm-keepalive", daemon=True)
    _asr_warm_thread.start()


def _get_asr_proc():
    """persistent Qwen3-ASR subprocess(模型加载一次, 避免每次 6.7s 重复加载)。

    设备隔离 FIX-ASR-ENV-20260726:
    v3-server 主进程 drop-in(E task CPU INT8 TTS)设 HIP/ROCR_VISIBLE_DEVICES=''
    屏蔽 AMD GPU。若 Qwen3-ASR 子进程继承主进程 env, device_map=cuda 走 HIP 会
    `RuntimeError: No HIP GPUs are available` 崩溃。这里显式构造独立 child_env,
    覆盖 HIP/ROCR=0 恢复 AMD GPU 0 对 ASR 子进程可见, 主进程屏蔽不动(TTS 仍 CPU)。
    stderr 不 DEVNULL(本次故障因 DEVNULL 长期不可见), 写独立日志文件, 不合并 stdout
    (避免诊断文字污染 READY 协议)。"""
    global _asr_proc
    if _asr_proc is not None and _asr_proc.poll() is None:
        return _asr_proc
    import subprocess as _sp
    import time as _time
    t_start = _time.time()
    print("[asr] 启动 persistent Qwen3-ASR subprocess(首次加载模型~6s)...", flush=True)
    # 显式构造 ASR 子进程环境: 恢复 AMD GPU 0 可见性(覆盖主进程屏蔽)
    child_env = os.environ.copy()
    child_env.update({
        "CUDA_VISIBLE_DEVICES": "0",
        "HIP_VISIBLE_DEVICES": "0",
        "ROCR_VISIBLE_DEVICES": "0",
        "OMP_NUM_THREADS": "16",
    })
    # stderr 写独立日志文件(journal 不易抓子进程 stderr; DEVNULL 曾掩盖本次故障)
    asr_stderr = open("/root/qwen_asr_stderr.log", "ab", buffering=0)
    _asr_proc = _sp.Popen(
        ['/root/qwen-venv/bin/python', '/root/qwen_asr_cli.py'],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=asr_stderr,
        env=child_env
    )
    rdy = _asr_proc.stdout.readline()
    t_ready = _time.time() - t_start
    if not rdy.startswith(b'READY'):
        rc = _asr_proc.poll()
        diag = ""
        try:
            asr_stderr.flush()
            with open("/root/qwen_asr_stderr.log", "rb") as f:
                tail = f.readlines()[-15:]
            diag = b"".join(tail).decode("utf-8", "ignore")
        except Exception as _e:
            diag = f"<read stderr failed: {_e}>"
        print(
            f"[asr] Qwen3-ASR READY 失败 rc={rc} READY耗时={t_ready:.1f}s "
            f"子进程env CUDA={child_env.get('CUDA_VISIBLE_DEVICES')} "
            f"HIP={child_env.get('HIP_VISIBLE_DEVICES')} "
            f"ROCR={child_env.get('ROCR_VISIBLE_DEVICES')} "
            f"stderr末尾:\n{diag}", flush=True)
        try:
            _asr_proc.kill()
        except Exception:
            pass
        _asr_proc = None
        raise RuntimeError("ASR proc not READY")
    print(f"[asr] persistent Qwen3-ASR 就绪 pid={_asr_proc.pid} READY耗时={t_ready:.1f}s", flush=True)
    return _asr_proc


def recognize(pcm16k):
    """ASR: 优先 Qwen3-ASR(persistent subprocess, 推理~1s), fallback SenseVoice。"""
    import time
    with _asr_lock:
        try:
            proc = _get_asr_proc()
            data = bytes(pcm16k)
            t0 = time.time()
            proc.stdin.write(f"{len(data)}\n".encode()); proc.stdin.write(data); proc.stdin.flush()
            text = proc.stdout.readline().decode().strip()
            dt = time.time() - t0
            if text:
                print(f"[asr] Qwen3-ASR: {text} ({dt:.2f}s)", flush=True)
                return text.replace(' ', '').strip()
        except Exception as e:
            print(f"[asr] Qwen3-ASR 失败({e}), fallback SenseVoice", flush=True)
            global _asr_proc; _asr_proc = None
    # Fallback: SenseVoice(funasr)
    arr = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32) / 32768.0
    res = asr.generate(input=arr, language='zh', use_itn=True)
    text = res[0]['text'] if res else ''
    text = _re.sub(r'<\|[^|]*\|>', '', text)
    return text.replace(' ', '').strip()


def make_apm():
    """Day5: 每会话 APM (AEC + NS + AGC + VAD)。mic=16k mono, REF=16k mono。
    AEC 默认关: 当前 HK-MIC 近场采不到 HDMI 电视回声(in_rms=0), 开 AEC 反而双端通话误消
    用户说话(实测 in_rms=4129→out_rms=1)。酒店盒子贴电视有回声时 USE_AEC=1 再开。"""
    use_aec = os.environ.get('USE_AEC', '0') == '1'
    # NS/AGC 关: webrtc NS 损害中文语音(paraformer 把"你好"识成"为你家"), Day1-4 mic 直通 ASR 准。
    # 只保留 VAD(has_voice 端点检测), mic 直通 paraformer。
    apm = AudioProcessingModule(aec_type=2 if use_aec else 0, enable_ns=False, agc_type=0, enable_vad=True)  # codex: type=2 AEC桌面端(双讲保真), type=1 AECM移动端误消说话
    apm.set_stream_format(16000, 1)
    apm.set_reverse_stream_format(16000, 1)
    if use_aec:
        apm.set_system_delay(150)  # codex: AudioTrack LOW_LATENCY 链路 ~100-150ms
    return apm


def tts_to_16k_ref(tts_pcm_22050_bytes):
    """TTS PCM 22050 → 16k 重采样(喂 AEC REF; webrtc 要求 near/reverse 同格式)。"""
    arr = np.frombuffer(tts_pcm_22050_bytes, dtype=np.int16).astype(np.float32)
    ref = resample_poly(arr, 16000, 22050)
    return np.clip(ref, -32768, 32767).astype(np.int16).tobytes()


def make_header(seq, ts, direction=2):
    return struct.pack(">IQBBBB", seq, ts, direction, 0, 0, 0)


async def send_pcm_streaming(ws, pcm, sess=None, zero_delay=False, ref16=None,
                             start_seq=0, ts=None, first_pkt_t0=None,
                             tts_id=None, segment_index=None, chunk_seq_state=None,
                             is_last_chunk=None):
    """分 20ms 小块流式发 PCM, 可选按 20ms 同步喂 AEC REF(不预灌)。返回 (next_seq, first_pkt_ms)。
    - sess.barge_stop=True 中断(barge-in 打断 TTS)。
    - ref16: 本段 pcm 对应的 16k REF bytes(仅 USE_AEC); 每发一个下行 20ms 块前, 同步喂对应 REF 块
      (与实际播放节奏对齐, 不预灌不滞后 → APM reverse 时间轴与 mic 回声对齐, set_system_delay 才准)。
    - start_seq/ts: 流式跨 chunk 时由调用方传入, 保持 seq/ts 连续递增(cache miss stream 多 chunk)。
    - first_pkt_t0: 首包计时起点; 首次下发记录首包延迟(流式首包优先验证)。
    - 44-CHUNK-SEQ (架构 §20.6.3, TTS_CHUNK_SEQ_V31=on): chunk 序号协议可选参数
      * tts_id: 本段所属 tts_id
      * segment_index: 同 tts_id 内分段序号(长句分句流水线用)
      * chunk_seq_state: 可变 dict {'chunk_index': int, 'pcm_offset': int}, 跨 send_pcm_streaming 调用累计
        (cache miss 多子句 / bypass 多 :8767 chunk 共享同一累计器); None 时不发 chunk_meta
      * is_last_chunk: 本段是否该 tts_id 的最后一段(最后一段最后一块标记 is_last_chunk=true)
      flag off (默认) 或 chunk_seq_state=None: 行为与改动前字节级一致(老 client 零感知)。
      flag on: 每发一个 20ms PCM 帧前可选发 JSON chunk_meta(独立文本消息, client 按类型区分)。
      注意: chunk_meta 是独立 JSON ws.send(非塞进二进制帧), 老客户端收到 unknown type 应忽略(向后兼容)。"""
    seq = start_seq
    if ts is None:
        ts = int(time.time() * 1000)
    frame_bytes = int(TTS_RATE * 0.02) * 2  # 20ms 单声道 S16 @22050 = 882B
    ref_frame_bytes = 320 * 2  # 20ms @16k mono S16 = 640B
    first_pkt_ms = None
    ref_off = 0  # 本段 REF 本地块索引(每段独立, 与本段 pcm 块对齐)
    # 44-CHUNK-SEQ: 是否启用 chunk 序号协议(TTS_CHUNK_SEQ_V31=on 且 chunk_seq_state 提供)
    use_chunk_seq = TTS_CHUNK_SEQ_V31 and chunk_seq_state is not None and tts_id is not None
    for i in range(0, len(pcm), frame_bytes):
        if sess is not None and sess.barge_stop:
            print(f"[barge] TTS 下发中断(已发 {seq} 帧)", flush=True)
            return seq, first_pkt_ms
        # P0-2: AEC REF 同步喂 —— 每个下行 20ms 块前喂对应 REF 16k 块(与播放节奏对齐, 不预灌不滞后)。
        # 尾块 ref16 不足 640B(重采样取整) → 喂零 REF, 保证每块下行都有 reverse 输入(AEC 时间轴不缺口)。
        if sess is not None and sess.apm is not None and ref16 is not None:
            # codex 修正: webrtc 0.1.3 process_reverse_stream 严格 10ms(320B). 原 640B(20ms) 静默丢后半→REF 损坏
            ref_chunk = ref16[ref_off:ref_off + ref_frame_bytes] if ref_off + ref_frame_bytes <= len(ref16) else b'\x00' * ref_frame_bytes
            for _r in range(0, len(ref_chunk), 320):  # 拆 2×10ms
                _f = ref_chunk[_r:_r+320]
                sess.apm.process_reverse_stream(_f if len(_f) == 320 else b'\x00' * 320)
            ref_off += ref_frame_bytes
        small = pcm[i:i + frame_bytes]
        # 影子采样: 记录本 20ms 下行 REF(ws.send 前 ≈ REF 离开 server 时刻)
        if aec_capture.enabled() and ref16 is not None:
            _cap_ref = ref16[ref_off - ref_frame_bytes:ref_off] if ref_off >= ref_frame_bytes else b''
            if len(_cap_ref) >= 640:
                aec_capture.log_ref(_cap_ref[:320])
                aec_capture.log_ref(_cap_ref[320:640])
        # 44-CHUNK-SEQ: PCM 帧前发 JSON chunk_meta(独立文本消息, flag on 时)
        if use_chunk_seq:
            _small_bytes = len(small)
            _is_last = (is_last_chunk is True) and (i + frame_bytes >= len(pcm))
            await ws.send(json.dumps({
                "type": "chunk_meta",
                "tts_id": tts_id,
                "segment_index": segment_index if segment_index is not None else 0,
                "chunk_index": chunk_seq_state['chunk_index'],
                "pcm_offset": chunk_seq_state['pcm_offset'],
                "pcm_bytes": _small_bytes,
                "is_last_chunk": _is_last,
            }))
            chunk_seq_state['chunk_index'] += 1
            chunk_seq_state['pcm_offset'] += _small_bytes
        await ws.send(make_header(seq, ts + seq * 20) + small)
        if first_pkt_ms is None and first_pkt_t0 is not None:
            first_pkt_ms = (time.time() - first_pkt_t0) * 1000
        seq += 1
        if not zero_delay:
            # 62-DROPOUT Top1: SEND_DELAY_ADAPTIVE_V31=on 时 rtf>1 降节流(根治双重限速),
            #   off / USE_AEC=1 返回 SEND_DELAY_SEC(原行为不变)。
            await asyncio.sleep(_adaptive_send_delay_sec())
    return seq, first_pkt_ms


async def on_voice_frame(ws, sess, voice, in_rms):
    """Day5 阶段C: VAD 状态机 — 端点检测。每帧 mic AEC 后调用。
    effective_voice = webrtc VAD + 能量阈值。SPEAKING/GUARD 期间忽略 voice(防 TTS 回声循环)。
    barge-in 暂禁: 待 AEC 调好 system_delay 区分回声/用户说话后启用。

    2026-07-26 门控修复: 句级状态机 speech_started/valid_voice_frames。
      - 端点发生在说完后的静音段, 不能要求端点时 effective=True(逻辑矛盾)。
      - 改为: 检测到有效人声 → speech_started=True; 后续静音达 VAD_END 才允许端点。
      - 无有效人声(纯环境声/电视声/静音) → speech_started=False, 不端点不送 ASR。

    2026-07-27 FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727(方案 D 双路 PCM):
      AUDIO_LEVEL_GATE_MODE 控制判定路径:
        legacy     → effective = voice AND in_rms > VOICE_RMS_MIN(固定; 沿用旧行为)
        normalized → effective = voice AND analysis_rms >= adaptive_threshold AND snr >= MIN_SNR_DB
                     (analysis_pcm 自适应阈值; Ring pre-roll + Live 统一增益)
        shadow     → legacy 执行 + normalized 判定记录对比(不影响 effective; 离线/灰度验证)
      normalized 模式端点后送 analysis_pcm(非 raw aec_mic)给 ASR。
    """
    ani = getattr(sess, '_cur_analysis_info', None)
    if AUDIO_LEVEL_GATE_MODE == 'normalized' and ani is not None:
        effective = ani['voice_candidate'] and voice and \
                    ani['analysis_rms'] >= ani['adaptive_threshold'] and \
                    ani['snr_db'] >= A.MIN_SNR_DB
        rms_for_log = int(ani['analysis_rms'])
    else:
        # legacy / shadow / fallback: 固定阈值(沿用旧行为)
        effective = voice and in_rms > VOICE_RMS_MIN
        rms_for_log = in_rms
    # shadow: 记录判定对比(不影响 effective)
    if AUDIO_LEVEL_GATE_MODE == 'shadow' and ani is not None:
        norm_eff = ani['voice_candidate'] and voice and \
                   ani['analysis_rms'] >= ani['adaptive_threshold'] and \
                   ani['snr_db'] >= A.MIN_SNR_DB
        if norm_eff != effective and sess.frames % 25 == 0:  # 降频避免日志风暴
            print(f"[AUDIO_LEVEL shadow] 帧{sess.frames} legacy_eff={effective} norm_eff={norm_eff} "
                  f"raw_rms={in_rms} an_rms={int(ani['analysis_rms'])} gain={ani['gain']:.2f} "
                  f"snr={ani['snr_db']:.1f} thr={int(ani['adaptive_threshold'])}", flush=True)
    if sess.state == 'LISTENING':
        sess.utterance_frames += 1  # 句级总帧数(算比例)
    if effective:
        sess.voice_frames += 1
        sess.silence_frames = 0
        # 句级有效人声标记(LISTENING 期间累计; GUARD/FOLLOWUP 状态机已隔离, 不在此累加)
        if sess.state == 'LISTENING':
            sess.speech_started = True
            sess.valid_voice_frames += 1
        # 根治: 去掉 voice cancel followup_task(回声/环境声 voice=True 持续反复 cancel GUARD → 灯永不灭)
        # GUARD/FOLLOWUP wallclock 固定超时; 用户说话通过 ASR(reply/tool)重设, 不靠 VAD voice
        if sess.state == 'IDLE' and sess.voice_frames >= VAD_START:
            sess.state = 'LISTENING'
            sess.aec_mic.clear()
            sess.listening_frames = 0
            # 新句开始: 重置句级状态
            sess.speech_started = False
            sess.valid_voice_frames = 0
            sess.utterance_frames = 1  # 当前帧即首帧
            print(f"[vad] IDLE→LISTENING (帧{sess.frames} in={in_rms})", flush=True)
        elif USE_BARGE and sess.state == 'SPEAKING' and sess.voice_frames >= BARGE_FRAMES and in_rms > BARGE_RMS_MIN:
            # barge-in: TTS 播放中用户说话 → 打断(需 AEC 消回声, 否则回声误触发)
            print(f"[barge] SPEAKING→LISTENING 用户打断 (帧{sess.frames} in={in_rms})", flush=True)
            sess.barge_stop = True
            await ws.send(json.dumps({"type": "barge_in"}))  # Android stopPlayer
            sess.state = 'LISTENING'
            sess.aec_mic.clear()
            # barge 进入新句: 重置句级状态
            sess.speech_started = True  # barge 已确认有用户人声
            sess.valid_voice_frames = sess.voice_frames
            sess.utterance_frames = sess.voice_frames
        # GUARD/PROCESSING 期间忽略 voice
    else:
        sess.voice_frames = 0
        sess.silence_frames += 1
        if sess.state == 'LISTENING' and sess.silence_frames >= VAD_END and sess.listening_frames >= MIN_LISTENING:
            # ===== 句级门控: 本句必须有有效人声才允许端点(2026-07-26 修复根因) =====
            # 阻止纯环境声/电视声/静音端点 → 防 Qwen3 幻觉识"嗯" → 单字过滤丢 → client 卡 PROCESSING
            _ratio = (sess.valid_voice_frames / sess.utterance_frames) if sess.utterance_frames > 0 else 0.0
            if (not sess.speech_started) or (sess.valid_voice_frames < MIN_VALID_FRAMES) or (_ratio < MIN_VALID_RATIO):
                # 无有效人声: 重置句级状态继续 LISTENING(不端点, 不送 ASR)
                # silence_frames 不清零 —— 环境声继续累积静音, 达 FOLLOWUP_WAIT 自然 go_idle;
                #   但 listening_frames 也不清(防环境声反复触发 IDLE→LISTENING 抖动); 只重置句级标记
                # 日志降频: 纯静音(speech_started=False 且 valid=0)每秒打1次, 防日志风暴;
                #   有 voice 闪过的边界(speech_started=True 或 valid>0)每次打(调试需要)
                _is_pure_silence = (not sess.speech_started) and sess.valid_voice_frames == 0
                if (not _is_pure_silence) or (sess.frames % 50 == 0):
                    print(f"[vad] 环境声/静音无有效人声, 拒端点 (speech_started={sess.speech_started} "
                          f"valid={sess.valid_voice_frames}/{sess.utterance_frames} ratio={_ratio:.2f} 帧帧{sess.frames})", flush=True)
                sess.speech_started = False
                sess.valid_voice_frames = 0
                sess.utterance_frames = 0
                # 注意: aec_mic 不清, 后续真说话仍可累积; silent 路径不发 client 状态(client 已在 LISTENING)
                return
            sess.state = 'PROCESSING'
            sess.processing_start_frames = sess.frames  # codex P1: 记录 PROCESSING 起始(超时保护)
            # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727(方案 D):
            #   normalized 模式端点后送 analysis_pcm(非 raw aec_mic) 给 ASR;
            #   legacy/shadow 模式仍送 aec_mic(raw, 沿用旧行为)。
            if AUDIO_LEVEL_GATE_MODE == 'normalized' and sess.analysis_builder is not None:
                pcm_copy = sess.analysis_builder.get_analysis_pcm()
                raw_copy = sess.analysis_builder.get_raw_pcm()  # raw 保留(write_wav 取证)
                gate_mode_label = 'analysis'
            else:
                pcm_copy = bytes(sess.aec_mic)
                raw_copy = None
                gate_mode_label = 'raw'
            sess.aec_mic.clear()
            if sess.analysis_builder is not None:
                sess.analysis_builder.reset()  # 端点后清 analysis(下一句干净起点; 保留 noise_floor 跨句)
            if len(pcm_copy) < ASR_MIN_PCM_SAMPLES:
                print(f"[vad] 端点但语音太短({len(pcm_copy)//2} samps < {ASR_MIN_PCM_SAMPLES}), 丢弃 → 回 LISTENING", flush=True)
                _reset_utterance(sess)
                await _resume_listening_async(ws, sess, "端点语音太短")
                return
            # ===== ASR 前门控: PCM 能量/比例/时长二次校验(2026-07-26 修复 + 2026-07-27 方案 D) =====
            gate_reject_reason, gate_metrics = _asr_gate_check(pcm_copy, sess)
            if gate_reject_reason is not None:
                print(f"[vad-gate] 拒绝送 ASR: {gate_reject_reason} metrics={gate_metrics} → 回 LISTENING (帧{sess.frames})", flush=True)
                _reset_utterance(sess)
                await _resume_listening_async(ws, sess, f"ASR前门控拒绝:{gate_reject_reason}")
                return
            print(f"[vad] LISTENING→ASR [{gate_mode_label}] (帧{sess.frames}, {len(pcm_copy)//2} samples, "
                  f"valid={sess.valid_voice_frames}/{sess.utterance_frames} ratio={_ratio:.2f})", flush=True)
            _reset_utterance(sess)  # 已端点送 ASR, 重置供下一句重新检测
            # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 新 utterance 跟踪 + task 引用
            sess.active_utterance_id = _gen_utterance_id(sess)
            task = asyncio.create_task(handle_asr(ws, sess, pcm_copy))
            sess.active_asr_task = task


def _reset_utterance(sess):
    """重置句级状态(端点后/丢弃后/wake/go_idle 调用, 下一句重新检测有效人声)。
    FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 同时 reset analysis_builder(保留 noise_floor 跨句平滑)。"""
    sess.speech_started = False
    sess.valid_voice_frames = 0
    sess.utterance_frames = 0
    # 方案 D: analysis_builder 句级 reset(清 raw/analysis 帧, 保留 noise_floor 跨句平滑)
    # 注意: 端点成功时已在端点分支显式 reset + get_analysis_pcm; 此处是拒端点/丢弃路径的安全网
    if getattr(sess, 'analysis_builder', None) is not None:
        # 仅在 builder 仍有累积帧时 reset(避免端点后重复 reset)
        try:
            if sess.analysis_builder.frame_no > 0:
                sess.analysis_builder.reset()
        except Exception:
            pass


def _asr_gate_check(pcm16k, sess):
    """ASR 前门控: 返回 (拒绝原因字符串 or None, metrics_dict)。
    - normalized 模式: 调 analysis_pcm.gate_check_analysis_pcm(整体 RMS + peak + 非静音比例 + 有效帧 RMS 中位)
    - legacy/shadow 模式: 沿用旧逻辑(整体 RMS + 时长)
    注: 比例/有效帧已在端点判定处用 valid_voice_frames/utterance_frames 校验, 此处不重复。
    """
    if AUDIO_LEVEL_GATE_MODE == 'normalized':
        rej, metrics = A.gate_check_analysis_pcm(pcm16k, 16000)
        return rej, metrics
    # legacy/shadow: 沿用旧逻辑
    metrics = {}
    if len(pcm16k) < ASR_MIN_PCM_SAMPLES:
        return f"PCM太短({len(pcm16k)//2}samps<{ASR_MIN_PCM_SAMPLES})", metrics
    try:
        arr = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float64)
        if arr.size == 0:
            return "空PCM", metrics
        rms = int(np.sqrt(np.mean(arr ** 2)))
        metrics['rms'] = rms
        if rms < ASR_MIN_RMS:
            return f"RMS过低({rms}<{ASR_MIN_RMS})", metrics
    except Exception as _e:
        return f"PCM计算异常:{_e}", metrics
    return None, metrics


async def _resume_listening_async(ws, sess, reason=""):
    """silent/空文本/ASR前门控拒绝后: 转 LISTENING + 明确发 state_change LISTENING 给 client。
    修复 client 卡 PROCESSING(灯一直亮) 问题 —— K+L 诊断: server 不发状态, client 灯不灭。
    同步版 _resume_listening 不发 ws(保留兼容), 此 async 版补发 client 状态。
    """
    _resume_listening(sess, reason)
    try:
        await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "LISTENING_RESUME"}))
        await ws.send(json.dumps({"type": "state_change", "state": "LISTENING"}))  # 兼容旧 client, 灯回 LISTENING
    except Exception as _e:
        print(f"[resume_listen] 发 LISTENING 失败: {_e}", flush=True)


def _resume_listening(sess, reason=""):
    """codex: PROCESSING→LISTENING 统一转场 — 处理 pending_mic + 重置端点计数。"""
    sess.state = 'LISTENING'
    if len(sess.pending_mic) > 3200:  # >0.1s 才转入(避免纯静音)
        sess.aec_mic = sess.pending_mic
        sess.silence_frames = 0  # 重置端点计数, 给新句端点时间
        print(f"[asr] {reason} → LISTENING, 转入 pending_mic {len(sess.pending_mic)//2}samps", flush=True)
    else:
        sess.aec_mic.clear()
    sess.pending_mic = bytearray()
    # P0-4: 不清 listening_idle_frames(否则电视声 voice 持续+每ASR清零→FOLLOWUP永不超时, 灯永不灭)


async def handle_asr(ws, sess, pcm16k):
    """Day5 阶段C: VAD 端点触发 ASR → query_router → TTS/tool(异步, 不阻塞 mic 接收)。

    FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 代际校验
      入口捕获 token(session_id/epoch/utterance_id/asr_task_id), 在 7 个关键节点比对
      is_current_asr_turn(): 失配即 STALE_ASR_DROP 立即 return, 不进 Router/tool/TTS。
      覆盖竞态: 旧 ASR 期间 wake/go_idle/barge_in → 新会话不被旧 ASR 污染。"""
    # ===== 验活点 1: 入口(立即检) =====
    captured_session_id = sess.session_id
    captured_epoch = sess.session_epoch
    if sess.active_utterance_id is None:
        # 端点正常路径 utterance 已在 on_voice_frame 设; 防御: 补一个
        sess.active_utterance_id = _gen_utterance_id(sess)
    captured_utterance_id = sess.active_utterance_id
    captured_asr_task_id = f"asr_{captured_utterance_id}_{int(time.time()*1000)}"
    sess.active_asr_task_id = captured_asr_task_id
    asr_start_mono = time.monotonic()
    if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                               captured_utterance_id, captured_asr_task_id):
        _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                            captured_utterance_id, captured_asr_task_id,
                            asr_start_mono, stage="entry", reason="not_current_turn_at_entry")
        return
    try:
        write_wav(f"/root/asr_input_{int(time.time())}.wav", pcm16k, 16000, 1)  # ASR输入(带ts, 对比Android本地录音)
        # ===== 验活点 2: 调用 recognize 之前 =====
        if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                   captured_utterance_id, captured_asr_task_id):
            _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                captured_utterance_id, captured_asr_task_id,
                                asr_start_mono, stage="pre_recognize",
                                reason="epoch_bumped_before_asr")
            return
        try:
            text = await asyncio.to_thread(recognize, pcm16k)
        except asyncio.CancelledError:
            # 代际切换 cancel 旧 task(协作式中止); 不当 ASR 故障触发 SenseVoice fallback
            print(f"[asr] Cancelled by epoch bump utterance={captured_utterance_id} "
                  f"epoch={captured_epoch}", flush=True)
            return
        # ===== 验活点 3: await recognize 返回之后 =====
        if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                   captured_utterance_id, captured_asr_task_id):
            _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                captured_utterance_id, captured_asr_task_id,
                                asr_start_mono, stage="post_recognize",
                                reason="epoch_bumped_during_asr", text=text)
            return
        print(f"[asr] {text}", flush=True)
        # ===== 验活点 7(早检): 发送 asr_final 之前再校验(防 await 间隙 wake 撞入) =====
        if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                   captured_utterance_id, captured_asr_task_id):
            _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                captured_utterance_id, captured_asr_task_id,
                                asr_start_mono, stage="pre_asr_final",
                                reason="epoch_bumped_pre_send", text=text)
            return
        await ws.send(json.dumps({"type": "asr_final", "text": text}))
        if not text.strip():
            await _resume_listening_async(ws, sess, "空文本")  # 2026-07-26: 发 LISTENING, 避免 client 卡 PROCESSING
            return
        # TTS回声剥离: ASR结果按句分割, 丢弃回声句(与last_tts重叠>60%), 保留命令句继续路由
        # 修: 之前整句丢太激进, 把"我在...?我要看电视"里的"我要看电视"也丢了; 现在分句剥离
        if sess is not None and getattr(sess, 'last_tts_text', ''):
            import re as _re2
            _lt = _re2.sub(r'[，。！？、,.!?；;:：\s]+', '', sess.last_tts_text)
            if len(_lt) >= 3:
                _sents = _re2.split(r'[？?。！!；;\n]+', text)
                _kept = []
                for _s in _sents:
                    _sc = _re2.sub(r'[，。！？、,.!?；;:：\s]+', '', _s)
                    if len(_sc) < 2:
                        continue
                    _ov = sum(1 for c in _sc if c in _lt)
                    # P0-3: 严格判回声(last_tts≥8字 + 命令≥last_tts 60% + 重叠>0.85), 短命令保留
                    if len(_lt) >= 8 and len(_sc) >= len(_lt) * 0.6 and _ov / len(_sc) > 0.85:
                        print(f"[asr] 回声句丢 '{_s.strip()}' (重叠{_ov}/{len(_sc)})", flush=True)
                        continue
                    _kept.append(_sc)
                if not _kept:
                    print(f"[asr] 纯TTS回声整句丢 -> LISTENING", flush=True)
                    await _resume_listening_async(ws, sess, 'TTS回声')  # 2026-07-26: 发 LISTENING
                    return
                text = ''.join(_kept)
                print(f"[asr] 回声剥离后命令: '{text}'", flush=True)
        # ===== 验活点 4: 调用 query_router 之前 =====
        if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                   captured_utterance_id, captured_asr_task_id):
            _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                captured_utterance_id, captured_asr_task_id,
                                asr_start_mono, stage="pre_router",
                                reason="epoch_bumped_pre_router", text=text)
            return
        action = query_router(text, sess)  # 26-DIALOGUE-INTEGRATION: 传 sess(flag off 时 gate 不进入)
        if action[0] == 'silent':
            # 40-WAKE-FIRST-ACK-GUARANTEE: 唤醒后第一次 ASR 的 silent(兜底达上限/同文本cooldown)
            #   降级为播 WAKE_ACK(保证唤醒必有反馈, 解决问题1 case B/C)
            if (WAKE_FIRST_ACK_GUARANTEE_V31 and not sess.wake_first_asr_done
                    and is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                            captured_utterance_id, captured_asr_task_id)):
                _wake_ack_text = WAKE_FIRST_ACK_GUARANTEE_TEXT or _WAKE_ACK_DEFAULT
                print(f"[40-wake-first-ack] 唤醒后首次silent降级WAKE_ACK text={_wake_ack_text!r} "
                      f"asr_text={text!r}", flush=True)
                sess.wake_first_asr_done = True
                _cancel_wake_ack_timeout(sess)  # 60-WAKE-ACK-TIMEOUT: 已播 WAKE_ACK, 取消超时兜底
                await synthesize_and_send(ws, _wake_ack_text, sess, after_state='LISTENING')
                sess.pending_mic = bytearray()
                return
            print(f"[asr] 未命中意图, 静默 → LISTENING", flush=True)
            await _resume_listening_async(ws, sess, '未命中意图')  # 2026-07-26: 发 LISTENING
            return
        if action[0] == 'listen':
            # 纯唤醒词(剥离'小智'后空) → 合成确认语 + after_state=LISTENING
            # 35-WAKE-ACK-REFORM: 文本由 WAKE_ACK_V31/WAKE_ACK_TEXT 控制 (架构 §6.7)
            #   动态 TTS (走 :8767 bypass + ack + cache + cooldown), 不内置 WAV。
            print(f"[asr] 纯唤醒词(WAKE_ONLY) → TTS{_WAKE_ACK_DEFAULT!r} + after_state=LISTENING(触发 session_waiting) [35-wake-ack v31={int(WAKE_ACK_V31)}]", flush=True)
            # ===== 验活点 6: 调用 synthesize_and_send 之前 =====
            if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                       captured_utterance_id, captured_asr_task_id):
                _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                    captured_utterance_id, captured_asr_task_id,
                                    asr_start_mono, stage="pre_tts_wake_only",
                                    reason="epoch_bumped_pre_tts", text=text)
                return
            await synthesize_and_send(ws, _WAKE_ACK_DEFAULT, sess, after_state='LISTENING')
            # 40-WAKE-FIRST-ACK-GUARANTEE: listen 分支已播 WAKE_ACK, 标记第一次完成(BV High-1)
            #   防止后续短文本/未命中再次降级播 WAKE_ACK(连读场景重复反馈)
            sess.wake_first_asr_done = True
            _cancel_wake_ack_timeout(sess)  # 60-WAKE-ACK-TIMEOUT: 已播 WAKE_ACK, 取消超时兜底
            sess.pending_mic = bytearray()
            return
        if action[0] == 'reply':
            # 短文本(≤2字, "嗯。"/"是。"/"啊"等语气词/环境音/回声误识别)→ 不合成, 继续 LISTENING
            clean_text = _re.sub(r'[，。！？、,.!?；;:：\s]+', '', text)
            if len(clean_text) <= 2:
                # 40-WAKE-FIRST-ACK-GUARANTEE: 唤醒后第一次 ASR 的短文本('这儿'/'嗯'等)
                #   降级为播 WAKE_ACK(保证唤醒必有反馈, 解决问题1 case D)
                if (WAKE_FIRST_ACK_GUARANTEE_V31 and not sess.wake_first_asr_done
                        and is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                                captured_utterance_id, captured_asr_task_id)):
                    _wake_ack_text = WAKE_FIRST_ACK_GUARANTEE_TEXT or _WAKE_ACK_DEFAULT
                    print(f"[40-wake-first-ack] 唤醒后首次短文本降级WAKE_ACK text={_wake_ack_text!r} "
                          f"asr_text={text!r}", flush=True)
                    sess.wake_first_asr_done = True
                    _cancel_wake_ack_timeout(sess)  # 60-WAKE-ACK-TIMEOUT: 已播 WAKE_ACK, 取消超时兜底
                    await synthesize_and_send(ws, _wake_ack_text, sess, after_state='LISTENING')
                    sess.pending_mic = bytearray()
                    return
                print(f"[asr] 短文本('{text}'→'{clean_text}' ≤2字 疑似误识别), 不合成 → LISTENING", flush=True)
                await _resume_listening_async(ws, sess, "短文本")  # 2026-07-26: 发 LISTENING
                return
            # ===== 验活点 6: 调用 synthesize_and_send 之前(reply 分支) =====
            if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                       captured_utterance_id, captured_asr_task_id):
                _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                    captured_utterance_id, captured_asr_task_id,
                                    asr_start_mono, stage="pre_tts_reply",
                                    reason="epoch_bumped_pre_tts", text=text)
                return
            await synthesize_and_send(ws, action[1], sess, after_state='LISTENING')
            # 40-WAKE-FIRST-ACK-GUARANTEE: reply 正常合成后标记第一次完成
            sess.wake_first_asr_done = True
            _cancel_wake_ack_timeout(sess)  # 60-WAKE-ACK-TIMEOUT: 已进命令(回复), 取消超时兜底
            sess.pending_mic = bytearray()
        elif action[0] == 'tool':
            _, tool, params, ok_text, fail_text = action
            # ===== 验活点 5: 执行 tool_call 之前(最高优先级! 旧 ASR 绝不能控制设备) =====
            if not is_current_asr_turn(sess, captured_session_id, captured_epoch,
                                       captured_utterance_id, captured_asr_task_id):
                _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                                    captured_utterance_id, captured_asr_task_id,
                                    asr_start_mono, stage="pre_tool_call",
                                    reason="epoch_bumped_pre_tool", text=text,
                                    extra=f"tool={tool} params={params}")
                return
            req_id = f"req_{int(time.time() * 1000)}"
            sess.pending[req_id] = (ok_text, fail_text)
            await ws.send(json.dumps({"type": "tool_call", "tool": tool, "params": params,
                                      "request_id": req_id, "permission_check": True}))
            print(f"[tool_call] {tool} req={req_id}", flush=True)
            # 40-WAKE-FIRST-ACK-GUARANTEE: tool 触发后标记第一次完成(命令已执行)
            sess.wake_first_asr_done = True
            _cancel_wake_ack_timeout(sess)  # 60-WAKE-ACK-TIMEOUT: 已进命令(设备控制), 取消超时兜底
            # tool_call: state 保持 PROCESSING 等 tool_result, pending_mic 继续累积
    except asyncio.CancelledError:
        # 代际切换主动 cancel; 不当 ASR 故障, 不触发 fallback, 不改 state
        print(f"[asr] Cancelled(external) utterance={captured_utterance_id} "
              f"epoch={captured_epoch}", flush=True)
    except Exception as e:
        print(f"[asr err] {e}", flush=True)
        if sess.state == 'PROCESSING':
            _resume_listening(sess, "ASR异常")
    finally:
        # 退出时若仍持有 active_asr_task_id, 清掉(防 stale 标记阻止下一轮)
        if sess.active_asr_task_id == captured_asr_task_id:
            sess.active_asr_task_id = None
        if sess.active_asr_task is not None and getattr(sess.active_asr_task, 'done', lambda: True)():
            sess.active_asr_task = None


def _log_stale_asr_drop(sess, captured_session_id, captured_epoch,
                        captured_utterance_id, captured_asr_task_id,
                        asr_start_mono, stage, reason, text="", extra=""):
    """FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 旧 ASR 丢弃日志(隐私: 不打全文)。"""
    elapsed_ms = int((time.monotonic() - asr_start_mono) * 1000)
    text_len = len(text) if text else 0
    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()[:8] if text else ''
    print(f"[STALE_ASR_DROP] session={captured_session_id} "
          f"captured_epoch={captured_epoch} current_epoch={sess.session_epoch} "
          f"current_state={sess.state} utterance={captured_utterance_id} "
          f"stage={stage} reason={reason} asr_elapsed_ms={elapsed_ms} "
          f"text_len={text_len} text_hash={text_hash} {extra}", flush=True)


# ===== Query Router: 加载 V2 intents_local.json 规则库(80 意图, 平移自 iptv-edge-agent) =====
import re as _re
_RULES_PATH = os.environ.get('RULES_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'intents_local.json'))
try:
    with open(_RULES_PATH, encoding='utf-8') as _f:
        _RD = json.load(_f)
    RULES = sorted(_RD['routes'], key=lambda r: -r.get('priority', 0))
    SLOT_EXT = _RD.get('slot_extractors', {})
    print(f"[rules] 加载 {len(RULES)} 条意图规则 from {_RULES_PATH}", flush=True)
except Exception as _e:
    RULES, SLOT_EXT = [], {}
    print(f"[rules] 加载失败({_e}), query_router 仅兜底", flush=True)

_CONTROL_BIZ = {'TV', 'RCU', 'ROBOT'}  # 控制类: tool_call 下发 Android 执行; 信息类(HOTEL/POI/SENSITIVE): 直接 reply


def _extract_slots(route, text):
    slots = {}
    for s in route.get('slots', []):
        se = SLOT_EXT.get(s.get('extract'))
        if not se:
            continue
        m = _re.search(se['regex'], text)
        val = int(m.group(1)) if m else se.get('default')
        if val is not None:
            val = max(se.get('min', 0), min(se.get('max', 9999), val))
            slots[s['name']] = val
    return slots


# 29-CAPABILITY-REGISTRY: capability success/failure template 占位符渲染。
# template 形如 "好的，已为您申请{quantity_str}矿泉水，马上送到。"
# 渲染规则:
#   {quantity_str} → 根据 slots["quantity"] 数字转中文量词串(两瓶/三份/两个); 无 quantity 则用 default
#   {quantity}     → 数字
#   {temp} / {percent} / {item_type} / {issue} / {time} / {action} / {service_id} / {dish}
#                 → 直接从 slots 取(缺失则该占位符渲染为空串, 不抛异常)
# 未匹配占位符原样保留(不破坏 template)。flag off 时本函数不被调用。
_CN_NUM = {'1': '一', '2': '两', '3': '三', '4': '四', '5': '五',
           '6': '六', '7': '七', '8': '八', '9': '九', '10': '十'}


def _render_template(tpl, slots):
    """渲染 capability_registry success/failure template。flag off 不调用。"""
    if not tpl or not slots:
        return tpl
    out = tpl
    # quantity_str 特殊: 数字 + 量词推断
    qty = slots.get('quantity')
    if qty is not None:
        try:
            _qint = int(qty)
            _cn = _CN_NUM.get(str(_qint), str(_qint))
            out = out.replace('{quantity_str}', f"{_cn}份")
            out = out.replace('{quantity}', str(_qint))
        except (ValueError, TypeError):
            pass
    for _k, _v in slots.items():
        if _k == 'quantity':
            continue
        try:
            out = out.replace('{' + _k + '}', str(_v))
        except Exception:
            continue
    return out


def query_router(text, sess=None):
    """返回 ('tool', intent, slots, ok_text, fail_text) 或 ('reply', text)。
    寒暄优先(V2 规则库没有, V3 补) + V2 规则库匹配 + 兜底。
    pre-roll 含唤醒词"小智小智" → 剥离后再路由(否则"小智小智打开电视"命中寒暄而非控制)。

    26-DIALOGUE-INTEGRATION: sess 可选(默认 None, 向后兼容)。
    DIALOGUE_STATE_V31=1 且 sess.dialogue_state 非空时, 先走架构 §10 上下文补全(缺主体补
    active_entity / 多实体追问 / 槽位); flag=0 或 sess=None 时本段完全不进入, 行为不变。
    """
    # ===== 26-DIALOGUE-INTEGRATION gate(架构 §10 上下文解析) =====
    if (DIALOGUE_STATE_V31 and _DIALOGUE_STATE_AVAILABLE
            and sess is not None and getattr(sess, "dialogue_state", None) is not None):
        _ds = sess.dialogue_state
        try:
            _resolved, _clarify = ContextResolver.resolve(_ds, text)
        except Exception as _ce:
            print(f"[dialogue_state] ContextResolver 异常, 跳过补全走原文本: {_ce}", flush=True)
            _resolved, _clarify = text, None
        if _clarify:
            # §10.4/§10.6: 缺主体多实体或槽位歧义 -> 追问, 不进规则匹配
            _ds.add_user_turn(text)
            _ds.add_assistant_turn(_clarify)
            return ("reply", _clarify)
        # 补全后的文本继续走规则匹配(可能补了 active_entity 前缀)
        text = _resolved
        _ds.add_user_turn(text)
    # ===== 29-DIALOGUE-SLOT-V31: 槽位收集 gate(送水→三瓶 闭环, BE 缺口 2/3) =====
    # flag off 或无 pending_intent 时整段不进入, 行为不变。
    # 语义: 上一轮命中带 optional_slots(如 quantity) 的 intent 后, 我们 *主动* 设了
    #   pending_intent + pending_slots(非 required, 仅提供"可补充"通道, 不强制追问)。
    #   本轮若 collect_slot 收到槽位(如"三瓶"→quantity=3), 则直接用上一轮 intent 执行
    #   并把槽位合入 params, 覆盖 default_quantity; 收不到则 fallthrough 走本轮规则匹配。
    #   注意: 与架构 §10.8 required_slots 强追问不同 — 这里是 optional 补充, 用户体验更顺。
    if (DIALOGUE_SLOT_V31 and _DIALOGUE_STATE_AVAILABLE
            and sess is not None and getattr(sess, "dialogue_state", None) is not None):
        _ds_slot = sess.dialogue_state
        if _ds_slot.pending_intent and _ds_slot.pending_slots:
            try:
                _collected = ContextResolver.collect_slot(_ds_slot, text)
            except Exception as _cse:
                print(f"[dialogue_slot] collect_slot 异常, fallthrough 规则匹配: {_cse}",
                      flush=True)
                _collected = None
            if _collected:
                _pend_intent = _ds_slot.pending_intent
                print(f"[dialogue_slot] collect_slot hit intent={_pend_intent} "
                      f"slots={_collected} (送水→三瓶闭环)", flush=True)
                # 合入上一轮 intent 的 default + 本次收集, 构造 tool params
                _cap = _CAPABILITY_BY_INTENT.get(_pend_intent, {}) if CAPABILITY_REGISTRY_V31 else {}
                _params = dict(_collected)
                _default_qty = _cap.get('default_quantity')
                if _default_qty is not None and 'quantity' in _collected:
                    pass  # 用户已明确, 不用 default
                elif _default_qty is not None:
                    _params['quantity'] = _default_qty
                _service_id = _cap.get('service_id')
                if _service_id:
                    _params['service_id'] = _service_id
                # success/failure template: 优先用 registry, 兜底用 intent 默认
                _ok_tpl = _cap.get('success_template') or '好的，已经为您处理。'
                _fail_tpl = _cap.get('failure_template') or '抱歉，暂时没有处理成功。'
                _ok_text = _render_template(_ok_tpl, _params)
                # 清 pending(本轮已消化), 更新 last_intent/expires_at(BE 缺口 1)
                _ds_slot.collected_slots.update(_collected)
                _ds_slot.pending_intent = None
                _ds_slot.pending_slots = []
                try:
                    ContextResolver.update_after_intent(
                        _ds_slot, _pend_intent, [], _ok_text,
                        tool_name=_cap.get('tool') or 'service_dispatch')
                except Exception as _ue:
                    print(f"[dialogue_slot] update_after_intent 异常(忽略): {_ue}", flush=True)
                return ('tool', _pend_intent, _params, _ok_text, _fail_tpl)
            # 未收到槽位: 清 pending(用户已转新意图, 不再等补充), fallthrough 走本轮匹配
            print(f"[dialogue_slot] pending={_ds_slot.pending_intent} 未收槽位, "
                  f"清 pending 走本轮规则", flush=True)
            _ds_slot.pending_intent = None
            _ds_slot.pending_slots = []
    import re as _re
    _WAKE_RE = r'小[智志治致雉帜]'
    had_wakeword = bool(_re.search(_WAKE_RE, text))  # 38-WAKE-FIX: ASR可能识成小志/小治(同音), 含唤醒词=唤醒词段
    clean = _re.sub(r'[^\w]*(?:' + _WAKE_RE + r'[^\w]*)+', '', text, count=1).strip()
    clean = _re.sub(r'[^\w]*(?:[智志治致雉帜]小[智志治致雉帜])[^\w]*', '', clean, count=1).strip()
    meaningful = _re.sub(r'[^\w]+', '', clean)
    if not meaningful:
        return ('listen', '')  # 纯唤醒词(剥离'小智'后空, 非命令)→ 不寒暄, 等 command(防寒暄关mic致需求丢)
    # ===== 38-SECURITY-FIREWALL gate(架构 §19, 安全优先于一切路由) =====
    # 位置: 唤醒词剥离后(clean 就绪)、寒暄/Dialogue/FACILITY/strong/规则/兜底之前。
    # 命中即 return ('reply', 特殊回复), 不进后续任何路由。
    # 安全默认 on(SECURITY_FIREWALL_V31=1), flag off 时 _security_firewall_gate 返回 None, 原行为不变。
    _sec_hit = _security_firewall_gate(text, clean)
    if _sec_hit is not None:
        _sec_cat, _sec_reply = _sec_hit
        # 更新 dialogue_state(若有), 记 last_intent=SECURITY_BLOCKED 供续轮上下文
        if _DIALOGUE_STATE_AVAILABLE \
                and sess is not None and getattr(sess, "dialogue_state", None) is not None:
            try:
                _ds_sec = sess.dialogue_state
                _ds_sec.add_user_turn(text)
                _ds_sec.add_assistant_turn(_sec_reply)
                _ds_sec.last_intent = "SECURITY_BLOCKED_" + _sec_cat.upper()
            except Exception as _se:
                print(f"[security] dialogue_state 更新异常(忽略): {_se}", flush=True)
        print(f"[security] BLOCKED category={_sec_cat} -> reply(不走兜底/不回没听懂)", flush=True)
        return ('reply', _sec_reply)
    if _re.fullmatch(r'[嗯啊哎哦]+', meaningful):
        return ('reply', '我有什么可以帮助您的')
    if any(k in clean for k in ['你好', '您好']):
        return ('reply', '你好，有什么可以帮您？')
    if any(k in clean for k in ['你是谁', '你叫什么', '你叫啥', '你是什么']):
        return ('reply', '我是小智语音助手，可以帮你控制电视、查询信息')
    if '谢谢' in clean or '感谢' in clean:
        return ('reply', '不客气')
    if '再见' in clean or '拜拜' in clean:
        return ('reply', '再见，有事随时叫我')
    # ===== 36-FACILITY-MULTITURN: 通用设施属性查询 gate(架构 §10.9 + §15.3) =====
    # flag off(默认) 时整段不进入, 行为与历史一字不差(BM/BQ/BF/BI 不受影响)。
    # 语义: "健身房有没有教练/游泳池需要预约吗" 这类 *设施属性* 查询, 不命中 RULES 规则库,
    #   原本会落兜底 "我暂时没听懂" (UNDERSTANDING_FAILED 错误降级)。
    #   本 gate 识别 entity(fitness_center/swimming_pool) + attribute(coach/reservation/...) ->
    #   查 hotel_knowledge.entries[].facility 结构化数据 ->
    #   attribute 有值 -> LLM 组织回答(.126 无 LLM 则走结构化模板); 无值 -> KNOWLEDGE_MISSING 降级。
    # 红线: 不为每个属性建独立 intent(架构 §10.9 明确禁止); 不编造未录入字段。
    if FACILITY_ATTRIBUTE_V31 and _HOTEL_KNOWLEDGE_ENTRIES:
        _fac_ent = None
        _fac_attr = None
        # 识别 facility entity: 优先从文本匹配, 其次从 dialogue_state active_entity 补全(多轮)
        for _fk, _fid in _FACILITY_ENTITY_KEYWORDS:
            if _fk in clean:
                _fac_ent = _fid
                break
        if _fac_ent is None and _DIALOGUE_STATE_AVAILABLE \
                and sess is not None and getattr(sess, "dialogue_state", None) is not None:
            for _ae in getattr(sess.dialogue_state, "active_entities", []) or []:
                if _ae.type == "facility" and _ae.id in ("fitness_center", "swimming_pool"):
                    _fac_ent = _ae.id  # active_entity 补主体(架构 §10.9 多轮)
                    break
        # 识别 attribute
        for _ak, _aid in _FACILITY_ATTRIBUTE_KEYWORDS:
            if _ak in clean:
                _fac_attr = _aid
                break
        if _fac_ent is not None and _fac_attr is not None:
            # 查 facility 结构化数据
            _fac_data = None
            for _entry in _HOTEL_KNOWLEDGE_ENTRIES:
                if not _entry.get("enabled", True):
                    continue
                if _entry.get("facility", {}).get("facility_id") == _fac_ent:
                    _fac_struct = dict(_entry["facility"])
                    _fac_struct["display_name"] = _entry.get("display_name", "")
                    _fac_data = _fac_struct
                    break
            if _fac_data:
                _tpl_reply, _degrade = _format_facility_attribute_reply(
                    _fac_data, _fac_attr, text)
                if _degrade == "answered":
                    # attribute 有值: LLM 组织(.126 无 LLM 走模板)
                    _llm_reply = _llm_organize_facility_reply(_fac_data, _fac_attr, text)
                    _final_reply = _llm_reply if _llm_reply else _tpl_reply
                    print("[facility_attr] answered entity=" + str(_fac_ent) + " attr=" + str(_fac_attr) +
                          " llm_used=" + str(bool(_llm_reply)) +
                          " text_hash=" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], flush=True)
                else:
                    # KNOWLEDGE_MISSING(字段缺): 严禁回 "没听懂"
                    _final_reply = _tpl_reply
                    print("[facility_attr] KNOWLEDGE_MISSING entity=" + str(_fac_ent) + " attr=" + str(_fac_attr) +
                          " (字段缺, 非 understanding_failed) text_hash=" +
                          hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], flush=True)
                # 更新 dialogue_state(active_entity 保持, 续命 FOLLOWUP)
                if _DIALOGUE_STATE_AVAILABLE \
                        and sess is not None and getattr(sess, "dialogue_state", None) is not None:
                    try:
                        from dialogue_state import Entity as _DEntity
                        _ds_fac = sess.dialogue_state
                        _ds_fac.set_active_entity(
                            _DEntity(type="facility", id=_fac_ent,
                                     display_name=_fac_data.get("display_name", "")),
                            domain="hotel_knowledge")
                        _ds_fac.add_user_turn(text)
                        _ds_fac.last_intent = "FACILITY_ATTRIBUTE_QUERY"
                        _ds_fac.last_tool = "hotel_knowledge"
                        _ds_fac.add_assistant_turn(_final_reply)
                        _ds_fac.expires_at = time.time() + 10.0
                    except Exception as _dse:
                        print("[facility_attr] dialogue_state 更新异常(忽略): " + str(_dse), flush=True)
                return ("reply", _final_reply)
            else:
                # facility_id 在 knowledge 里没找到结构 -> KNOWLEDGE_MISSING(资料缺, 非 understanding_failed)
                print("[facility_attr] facility_id=" + str(_fac_ent) + " 无结构化数据, KNOWLEDGE_MISSING text_hash=" +
                      hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], flush=True)
                return ("reply", _FALLBACK_KNOWLEDGE_MISSING)
        # entity 或 attribute 识别不出 -> 不在本 gate 处理, fallthrough 到 RULES(可能命中其他规则)
    # ===== 29-CAPABILITY-REGISTRY: query_keywords_strong 区分查询/执行(BD H1 修复) =====
    # flag off 或无 strong 词时整段不进入, 84 规则匹配原行为不变。
    # 语义: hotel_services.json query_routing_rule.query_keywords_strong(几点/营业时间/到几点...)
    #   是营业时间类强信号, 语义上无法与下单共存。命中则强制 reply(知识查询), 不派单。
    #   覆盖 BA H1: '客房送餐几点结束' 含服务别名'客房送餐'(会命中 ROBOT_DELIVER_FOOD 派单)
    #   + '几点结束'(strong query) → 本 gate 拦截, 转 reply 不派单。
    #   注意: 本 gate 在 84 规则匹配 *之前*, 但只对"含 strong query 词"的文本生效,
    #   不影响纯控制句(打开电视/送水 等)的规则匹配。
    #
    # ===== 18-STRONG-GATE-FIX(BH Codex 审 H1 修复): 动作白名单 + knowledge 查询 =====
    # BH H1: strong 词'开门'(子串)误伤'帮我开门/开门锁/刷卡开门'等动作派单句(应'正在开门'却被拦成查询)。
    # 双保险修复:
    #   (1) 数据层(修复2, 主防线): hotel_services.query_keywords_strong 已删歧义根词
    #       '开门/关门/结束/开始'(子串会误伤动作句'帮我开门'/'关门'/'结束通话'/'开始播放'),
    #       保留纯查询词'几点/到几点/营业时间/什么时候' + 精确查询短语'几点开门/几点关门/几点结束'。
    #       效果: '帮我开门'不含任何剩余 strong 词 → 不进 gate → 规则匹配派单(正确)。
    #   (2) 代码层(修复1, 兜底): 若词表被回退或漏改(歧义根词仍在 strong list), 动作白名单兜底。
    #       判定: strong 命中后, 区分'纯查询词' vs '歧义根词':
    #         - 纯查询词(几点/营业时间/什么时候/到几点/几点X/到几点X/营业到几点): 语义与动作互斥,
    #           命中即查询, 不走白名单。保证 H1(客房送餐几点结束)被拦不派单。
    #         - 歧义根词(开门/关门/结束/开始): 命中时若同时含动作词 + 命中动作 intent, 跳过 strong。
    #       依据: query_routing_rule.rule (1)'纯查询强信号一定query' + (4)'query+action歧义优先action'。
    # Medium 修复(4 条几点开/吃早餐/退房/入住 文案降级):
    #   strong 确定拦后, 先查 hotel_knowledge entries(breakfast/restaurant/checkout/checkin 等),
    #   有 alias/display_name 命中则用其 reply_template 渲染具体信息(营业时间/位置),
    #   无匹配才回退通用 reply('这个问题我帮您查询一下')。
    if CAPABILITY_REGISTRY_V31 and _QUERY_KEYWORDS_STRONG:
        # 纯查询 strong 词(与动作语义互斥): 命中即查询, 不走动作白名单
        _STRONG_PURE_QUERY = ('几点', '到几点', '营业时间', '什么时候', '营业到几点')
        _STRONG_AMBIGUOUS_ROOTS = ('开门', '关门', '结束', '开始')  # 歧义根词(修复2应已删, 此为兜底)
        _hit_strong_kws = [_kw for _kw in _QUERY_KEYWORDS_STRONG if _kw in clean]
        if _hit_strong_kws:
            # 判定命中类型: 是否只命中纯查询词(无歧义根词)
            _hit_pure = any(_kw in _STRONG_PURE_QUERY or _kw.startswith('几点')
                            or _kw.startswith('到几点') for _kw in _hit_strong_kws)
            _hit_ambiguous = any(_kw in _STRONG_AMBIGUOUS_ROOTS for _kw in _hit_strong_kws)
            # === 18-STRONG-GATE-FIX 修复1: 动作白名单(仅对歧义根词生效, 兜底修复2) ===
            _skip_strong_for_action = False
            if _hit_ambiguous and not _hit_pure:
                # 仅歧义根词命中(无纯查询词): 判是否动作派单句
                _hit_action_kw = any(_ak in clean for _ak in _QUERY_ACTION_KEYWORDS) if _QUERY_ACTION_KEYWORDS else False
                _hit_action_intent = False
                if _hit_action_kw:
                    for _route in RULES:
                        _r_kwa = _route.get('keywords_any', [])
                        _r_biz = _route.get('biz', '')
                        # 动作类 intent: _CONTROL_BIZ(TV/RCU/ROBOT) + HOTEL 动作派单(开门/退房/叫醒/行李/前台)
                        _is_action_biz = _r_biz in _CONTROL_BIZ or _route.get('intent') in (
                            'HOTEL_DOOR_OPEN', 'HOTEL_WAKEUP', 'HOTEL_BAGGAGE',
                            'HOTEL_FRONT_DESK', 'HOTEL_EXTEND_STAY')
                        if _is_action_biz and _r_kwa and any(_k in clean for _k in _r_kwa):
                            _hit_action_intent = True
                            break
                if _hit_action_kw and _hit_action_intent:
                    _skip_strong_for_action = True
            if _skip_strong_for_action:
                print(f"[strong_fix] 动作白名单命中(歧义词+动作intent), 跳过 strong gate 走规则匹配派单 "
                      f"(BH H1 兜底) text_hash={hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}",
                      flush=True)
                # 不 return, fallthrough 到下方 for route in RULES 规则匹配
            else:
                print(f"[capability_registry] query_keywords_strong 命中, 转 reply 不派单 "
                      f"(H1 防误派单) text_hash={hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}",
                      flush=True)
                # === 18-STRONG-GATE-FIX Medium 修复: 查 hotel_knowledge 具体信息 ===
                # 旧: 直接 reply 通用兜底('这个问题我帮您查询一下'), 丢失具体信息。
                # 新: 先查 _HOTEL_KNOWLEDGE_ENTRIES 的 alias/display_name 命中, 有则用 reply_template。
                _strong_reply = '这个问题我帮您查询一下，您可以问我具体的服务内容。'
                if _HOTEL_KNOWLEDGE_ENTRIES:
                    try:
                        for _entry in _HOTEL_KNOWLEDGE_ENTRIES:
                            if not _entry.get('enabled', True):
                                continue
                            _know_names = [_entry.get('display_name', '')] + list(_entry.get('aliases', []))
                            if any(_kn and _kn in clean for _kn in _know_names):
                                _tpl = _entry.get('reply_template')
                                if _tpl:
                                    _strong_reply = _render_template(_tpl, _entry)
                                    print(f"[strong_fix] hotel_knowledge 命中 knowledge_id="
                                          f"{_entry.get('knowledge_id')} (Medium 具体信息修复)",
                                          flush=True)
                                break
                    except Exception as _ke:
                        print(f"[strong_fix] hotel_knowledge 查询异常, 回退通用 reply: {_ke}",
                              flush=True)
                # reply 文案: 有 knowledge 匹配用其 template, 无则通用兜底(阻断派单)
                if DIALOGUE_SLOT_V31 and _DIALOGUE_STATE_AVAILABLE \
                        and sess is not None and getattr(sess, "dialogue_state", None) is not None:
                    try:
                        ContextResolver.update_after_intent(
                            sess.dialogue_state, 'hotel_knowledge_query', [],
                            _strong_reply, tool_name='hotel_knowledge')
                    except Exception:
                        pass
                return ('reply', _strong_reply)
    for route in RULES:
        kwa = route.get('keywords_any', [])
        if not kwa or not any(k in clean for k in kwa):
            continue
        if any(k in clean for k in route.get('keywords_none', [])):
            continue
        intent, reply, biz = route['intent'], route['reply'], route.get('biz', '')
        slots = _extract_slots(route, clean)
        # ===== 29-CAPABILITY-REGISTRY: 命中后查 registry 补元数据(P1-3 最小集成) =====
        # flag off 时不查, slots/reply/templates 维持原 _extract_slots + route['reply']。
        # flag on 时: 从 capability_registry 取 tool/required/optional_slots/templates/timeout,
        #   补 _CONTROL_BIZ 硬编码的不足(不替代 84 规则匹配, 只补元数据)。
        _cap_meta = _CAPABILITY_BY_INTENT.get(intent) if CAPABILITY_REGISTRY_V31 else None
        if _cap_meta:
            # 合入 optional_slots 的 default(如 quantity default=2), 仅当 slots 里没有时
            for _opt_slot in _cap_meta.get('optional_slots', []):
                if _opt_slot == 'quantity' and 'quantity' not in slots:
                    _dq = _cap_meta.get('default_quantity')
                    if _dq is not None:
                        slots['quantity'] = _dq
                elif _opt_slot == 'item_type' and 'item_type' not in slots:
                    pass  # required_slots 走追问, 不默认填
            # templates 优先用 registry(若 _CONTROL_BIZ 派单, 用 success_template 做 ok_text)
            _ok_tpl = _cap_meta.get('success_template')
            _fail_tpl = _cap_meta.get('failure_template') or reply
            if biz in _CONTROL_BIZ and _ok_tpl:
                _rendered_ok = _render_template(_ok_tpl, slots)
                _rendered_fail = _render_template(_fail_tpl, slots)
            else:
                _rendered_ok = reply
                _rendered_fail = _fail_tpl
        else:
            _rendered_ok = reply
            _rendered_fail = reply
        # ===== 29-DIALOGUE-SLOT-V31: 命中后 update_after_intent + 设 pending(BE 缺口 1/2) =====
        # flag off 时不调 update/不设 pending, 原行为(命中即 return)。
        # flag on 时:
        #   (a) update_after_intent: 更新 last_intent/expires_at(FOLLOWUP 续命)+ 记 assistant_turn
        #   (b) 设 pending: 若 registry optional_slots 含 quantity(送水/送毛巾/送冰/茶/咖啡 等),
        #       设 pending_intent + pending_slots=["quantity"], 让用户下一轮 *可选* 补充数量
        #       (不强制追问; 用户不补充则本轮 default 已执行, 补充则覆盖)。
        if DIALOGUE_SLOT_V31 and _DIALOGUE_STATE_AVAILABLE \
                and sess is not None and getattr(sess, "dialogue_state", None) is not None:
            _ds_post = sess.dialogue_state
            try:
                ContextResolver.update_after_intent(
                    _ds_post, intent, [], _rendered_ok,
                    tool_name=(_cap_meta or {}).get('tool') if _cap_meta else None)
            except Exception as _ue2:
                print(f"[dialogue_slot] update_after_intent(post-hit) 异常(忽略): {_ue2}",
                      flush=True)
            # 设 pending: 仅对 optional_slots 含 quantity 的服务类 intent
            _opt = (_cap_meta or {}).get('optional_slots', []) if _cap_meta else []
            if 'quantity' in _opt:
                _ds_post.pending_intent = intent
                _ds_post.pending_slots = ['quantity']
                print(f"[dialogue_slot] set pending intent={intent} slots=['quantity'] "
                      f"(送水/餐类, 等待补充)", flush=True)
        if biz in _CONTROL_BIZ:
            return ('tool', intent, slots, _rendered_ok, _rendered_fail)  # Android 执行 + reply 确认
        return ('reply', _rendered_ok)
    # 28-P0-CLEANUP-BYARCH B2.3: B2 兜底 cooldown(防 ASR 幻觉回声循环)。
    # 28-P0-CLEANUP-BYARCH B2.3: B2 兜底 cooldown(防 ASR 幻觉回声循环)。
    #   场景: ASR 幻觉出未命中规则的非空文本 → 兜底 TTS → TTS 回声被 ASR 再识 → 再兜底(循环)。
    #   memory silent 拆分原则: 有效中文未命中 = 一次兜底 + 同会话 cooldown + 同文本不重复。
    #   FALLBACK_COOLDOWN_V31=0(默认): 整段不进入, 原行为('reply', 兜底语) 一字不变。
    #   =1 时两道闸:
    #     (a) 同文本 cooldown: last_fallback_text==text 且距 last_fallback_ts < FALLBACK_COOLDOWN_MS → silent(打断循环, 不重复合成同一兜底语)
    #     (b) 会话限频: fallback_count >= MAX_FALLBACK_PER_SESSION → silent(本会话兜底已用尽, 不再 TTS, 用户重新唤醒或自然 FOLLOWUP 超时)
    #   命中 cooldown 返回 ('silent','') → handle_asr silent 分支不发 TTS 回 LISTENING(既阻断回声, 又不强制 go_idle 打断会话)。
    #   注意: 此 cooldown 只作用于"兜底分支", 不影响寒暄/规则命中/DIALOGUE_STATE gate(那些返回路径在前面已 return)。
    # 38-WAKE-FIX: 含唤醒词的ASR(唤醒词段幻觉,如"真是贵啊小志小智")剥离唤醒词后未命中任何gate
    #   → 用户大概率只说唤醒词(无命令), 播WAKE_ACK而非兜底"没听懂"(架构§6.7)
    if had_wakeword:
        print(f"[wake-fix] ASR含唤醒词未命中规则(唤醒词段幻觉?), 走listen播WAKE_ACK text={text!r}", flush=True)
        return ('listen', '')
    if FALLBACK_COOLDOWN_V31 and sess is not None:
        import time as _fb_time
        _now_mono = _fb_time.monotonic()
        if (sess.last_fallback_text == text
                and (_now_mono - sess.last_fallback_ts) < FALLBACK_COOLDOWN_MS):
            print(f"[fallback_cooldown] 同文本 {FALLBACK_COOLDOWN_MS:.0f}ms 内重复兜底, 转 silent 防回声循环 "
                  f"text_hash={hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]} "
                  f"fallback_count={sess.fallback_count}", flush=True)
            return ('silent', '')
        if sess.fallback_count >= MAX_FALLBACK_PER_SESSION:
            print(f"[fallback_cooldown] 本会话兜底达上限 {MAX_FALLBACK_PER_SESSION}, 转 silent "
                  f"(用户重新唤醒或 FOLLOWUP 超时回 IDLE)", flush=True)
            return ('silent', '')
        sess.fallback_count += 1
        sess.last_fallback_text = text
        sess.last_fallback_ts = _now_mono
    return ('reply', _FALLBACK_UNDERSTANDING_FAILED)  # 36: UNDERSTANDING_FAILED(没听懂); KNOWLEDGE_MISSING 已在 facility gate 内区分  # P0-2(2026-07-28 B2): 架构§11.6永不空回复; reply分支≤2字短文本过滤(嗯/哦仍不发TTS,保留回声保护)


# ===== 24-BYPASS-FORWARD: :8767 GPU FP32 无 cache 旁路转发实现 =====
# 设计要点:
#   1. 跳过 cache 读 + 跳过 cache 写 (确保单一音色来自 :8767 FP32, 架构 §2.2/§20.2)
#   2. 作为 WS client 连本地 :8767, 发 tts_request, 收整句 PCM (mono S16 @22050)
#   3. PCM 下发复用 send_pcm_streaming (SEND_DELAY_MS 节流 + AEC REF 同步, 客户端零感知)
#   4. barge-in: 每 PCM chunk 检查 sess.barge_stop, 中断即提前关 :8767
#   5. client tts_start/tts_end 协议字段与原路径完全一致 (含 session 代际 token)
#   6. :8767 连接失败/超时/协议错 (且 client tts_start 未发) → raise, 上层 gate fallback
# 重要: 一旦对 client 发出 tts_start, 后续失败不再 fallback (协议已对 client 开始),
#       只记日志 + 尽力发 tts_end(PCM 空也发) 防客户端卡死。
async def _synthesize_via_bypass_8767(ws, text, sess, after_state,
                                       tts_id, tts_session_id,
                                       tts_session_epoch, tts_utterance_id,
                                       tts_scope, use_aec,
                                       wake_ack_speed=False):
    """raise = 前置阶段(client tts_start 未发)失败, 调用方应 fallback。
    正常 return = 旁路接管完成(含失败收尾), 调用方应直接 return 不走原路径。
    44-WAKE-ACK-SPEED (架构 §6.7): wake_ack_speed=True 时, tts_request 带
      speed=WAKE_ACK_SPEED_VAL + stream_false=True + wake_ack=True,
      :8767 worker 走 stream=False 让 speed 真生效(CosyVoice V1 stream=True speed 断言崩溃铁律)。
      wake_ack_speed=False(默认): tts_request 不带这些字段, worker 走默认 comboAB stream=True。"""
    t0 = time.time()
    client_tts_start_sent = False  # fallback 边界标志
    bypass_ws = None
    # Batch A: 11 阶段计时(WS_SEND_PROFILE flag on 时启用)
    _sp = {}
    _sp_t0 = time.monotonic()
    def _sp_mark(name):
        if WS_SEND_PROFILE and name not in _sp:
            _sp[name] = round((time.monotonic() - _sp_t0) * 1000, 2)
    _sp_mark('func_enter')
    try:
        _sp_mark('worker_connect_start')
        # ---- 阶段 1: 连 :8767 + 发 tts_request + 收 :8767 tts_start (前置验证, 失败可 fallback) ----
        bypass_ws = await asyncio.wait_for(
            websockets.connect(
                TTS_BYPASS_8767_URL,
                open_timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT,
                close_timeout=1.0,
                max_size=None,  # PCM 可能较大
            ),
            timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT + 0.5,
        )
        _sp_mark('worker_connect_ms')
        _tts_req_payload = {
            "type": "tts_request",
            "text": text,
            "tts_id": tts_id,
        }
        # 44-WAKE-ACK-SPEED: wake_ack_speed=True → 透传 speed/stream_false/wake_ack 到 :8767
        # worker 端识别这些字段覆盖默认 _choose_stream_speed(走 stream=False 让 speed 真生效)
        if wake_ack_speed:
            _tts_req_payload["speed"] = WAKE_ACK_SPEED_VAL
            _tts_req_payload["stream_false"] = True
            _tts_req_payload["wake_ack"] = True
            print(f"[44-wake-ack-speed] tts_request 带 speed={WAKE_ACK_SPEED_VAL} "
                  f"stream_false=True tts_id={tts_id} text={text!r}", flush=True)
        await bypass_ws.send(json.dumps(_tts_req_payload))
        _sp_mark('tts_request_sent')
        raw_first = await asyncio.wait_for(
            bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
        first_msg = json.loads(raw_first)
        _sp_mark('worker_tts_start_received')
        if first_msg.get("type") != "tts_start":
            raise RuntimeError(f":8767 首包非 tts_start: {first_msg.get('type')}")
        bypass_meta = {
            "device": first_msg.get("device"),
            "dtype": first_msg.get("dtype"),
            "use_int8": first_msg.get("use_int8"),
            "sample_rate": first_msg.get("sample_rate"),
            "first_pcm_ms": None,
            "synth_ms": None,
            "rtf": None,
        }
        print(f"[bypass-8767] tts_start tts_id={tts_id} device={bypass_meta['device']} "
              f"dtype={bypass_meta['dtype']} use_int8={bypass_meta['use_int8']} "
              f"sample_rate={bypass_meta['sample_rate']}", flush=True)

        # ---- 阶段 2: 对 client 发 tts_start (此后失败不再 fallback, 尽力收尾) ----
        _tts_start_payload = {
            "type": "tts_start", "tts_id": tts_id, "text": text,
            "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
            "session_id": tts_session_id,
            "session_epoch": tts_session_epoch,
            "utterance_id": tts_utterance_id,
            "scope": tts_scope,
        }
        # 44-CHUNK-SEQ: flag on 时 tts_start 声明 chunk_seq 协议 + segment_count(架构 §20.6.3)
        if TTS_CHUNK_SEQ_V31:
            _tts_start_payload["chunk_seq_v31"] = True
            _tts_start_payload["segment_count"] = 1  # bypass 单 tts_request = 单段
        await ws.send(json.dumps(_tts_start_payload))
        _sp_mark('gateway_tts_start_sent')
        client_tts_start_sent = True
        if sess is not None:
            sess.speaking = True
            sess.barge_stop = False
            sess.state = 'SPEAKING'
        send_start_mono = time.monotonic()
        total_bytes_sent = 0
        seq = 0
        # 流式透传: :8767 worker 本身流式(stream=True, 每 chunk 立即 ws.send),
        # 本旁路必须 chunk 到达即转发 client, 不能 extend 收齐再发(否则 send_elapsed > audio_dur,
        # GUARD 时序崩溃, playback_remaining=0, STALE_ACK - 24-BYPASS-FORWARD-STREAMING-FIX)。
        # 转发方式: 每 :8767 chunk 到达 → 立即 send_pcm_streaming(chunk) 按 20ms 切片下发
        # (复用 SEND_DELAY_MS 节流 + AEC REF 按 20ms 同步 + make_header 帧 + 跨 chunk seq/ts 连续)。
        # client 协议帧格式不变, 仅首包延迟从"收齐整段"降到"首 chunk 到达 + 首帧切片"。
        first_client_pkt_ms = None
        bypass_first_pcm_ms = None  # :8767 首 chunk 到达时刻(相对 t0)
        chunk_count = 0
        # 51-SEGMENT-TIMELINE (B1): 单段路径 segment_index=0
        _seg_tr = _phase2_seg_new(tts_id, 0, text)
        _phase2_seg_mark(_seg_tr, "synth_start")
        _phase2_seg_mark(_seg_tr, "send_start")
        # 44-CHUNK-SEQ: chunk 序号累计器(跨 :8767 chunk 连续); flag off 时 send_pcm_streaming 不发 chunk_meta
        chunk_seq_state = {'chunk_index': 0, 'pcm_offset': 0}
        # 44-CHUNK-SEQ 四点字节统计: worker_pcm_bytes(:8767 tts_end total_bytes, 阶段 3 收尾填)
        worker_pcm_bytes = None

        # ---- 阶段 3: 流式收 :8767 chunk + 即时切片透传 client ----
        try:
            while True:
                if sess is not None and sess.barge_stop:
                    print(f"[bypass-8767] barge-in 中断(已透传 {chunk_count} chunk "
                          f"{total_bytes_sent} bytes)", flush=True)
                    break
                msg = await asyncio.wait_for(
                    bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
                if isinstance(msg, (bytes, bytearray)):
                    # :8767 binary chunk 到达 → 立即切片透传(不等后续 chunk)
                    if bypass_first_pcm_ms is None:
                        bypass_first_pcm_ms = int((time.time() - t0) * 1000)
                        _sp_mark('worker_first_pcm_received')
                        _phase2_seg_mark(_seg_tr, "first_pcm", bypass_first_pcm_ms)
                    chunk_count += 1
                    chunk_bytes = bytes(msg)
                    ref16 = (tts_to_16k_ref(chunk_bytes)
                             if (use_aec or aec_capture.enabled()) else None)
                    # send_pcm_streaming 内部: 按 882B(20ms) 切片, 每片 make_header + ws.send,
                    # 每片 sleep SEND_DELAY_MS(节流防 AudioTrack 突增), 每片喂 AEC REF 10ms×2。
                    # start_seq/ts 连续: 跨 chunk seq 继续递增(返回值 seq 即下一 chunk 起始)。
                    # 44-CHUNK-SEQ: flag on 时透传 tts_id/segment_index=0/chunk_seq_state/is_last_chunk=None
                    #   (is_last_chunk 由阶段 5 tts_end 标记, 透传阶段无法预判是否最后 chunk);
                    #   flag off 时 chunk_seq_state 仍传(send_pcm_streaming 内部 use_chunk_seq=False 不发 chunk_meta)
                    seq, fp = await send_pcm_streaming(
                        ws, chunk_bytes, sess=sess, zero_delay=False, ref16=ref16,
                        start_seq=seq, ts=int(time.time() * 1000),
                        first_pkt_t0=send_start_mono,
                        tts_id=tts_id, segment_index=0,
                        chunk_seq_state=chunk_seq_state, is_last_chunk=False)
                    total_bytes_sent += len(chunk_bytes)
                    _phase2_seg_incr(_seg_tr, "partial_write_count")
                    if first_client_pkt_ms is None and fp is not None:
                        first_client_pkt_ms = fp
                        _sp_mark('client_first_receive_ms')
                else:
                    end_msg = json.loads(msg)
                    if end_msg.get("type") == "tts_end":
                        bypass_meta["first_pcm_ms"] = end_msg.get("first_pcm_ms")
                        bypass_meta["synth_ms"] = end_msg.get("synth_ms")
                        bypass_meta["rtf"] = end_msg.get("rtf")
                        _update_rtf_window(end_msg.get("rtf"))  # 62-DROPOUT Top1: 喂 rtf 滑动窗口
                        # 44-CHUNK-SEQ 四点字节统计: worker_pcm_bytes = :8767 tts_end total_bytes
                        worker_pcm_bytes = end_msg.get("total_bytes")
                        break
                    if end_msg.get("type") == "tts_error":
                        raise RuntimeError(
                            f":8767 tts_error: {end_msg.get('reason')}")
                    print(f"[bypass-8767] 非 binary/tts_end 消息忽略: "
                          f"{end_msg.get('type')}", flush=True)
        except asyncio.TimeoutError:
            print(f"[bypass-8767] 流式收 chunk 超时({TTS_BYPASS_8767_RECV_TIMEOUT}s), "
                  f"已透传 {chunk_count} chunk {total_bytes_sent} bytes, 继续收尾",
                  flush=True)

        audio_duration_sec = total_bytes_sent / (TTS_RATE * 2) if total_bytes_sent > 0 else 0.0
        elapsed = time.time() - t0
        send_elapsed_ms = (time.monotonic() - send_start_mono) * 1000
        _sp_mark('gateway_ws_send_sum_ms')
        print(f"[bypass-8767] synth done tts_id={tts_id} chunks={chunk_count} "
              f"audio_dur={audio_duration_sec:.2f}s elapsed={elapsed:.2f}s "
              f"send_elapsed={send_elapsed_ms/1000:.2f}s "
              f"bypass_first_pcm_ms={bypass_first_pcm_ms} "
              f"client_first_pkt_ms={int(first_client_pkt_ms) if first_client_pkt_ms is not None else None} "
              f":8767_first_pcm_ms={bypass_meta['first_pcm_ms']} "
              f"synth_ms={bypass_meta['synth_ms']} rtf={bypass_meta['rtf']} "
              f"bytes={total_bytes_sent}", flush=True)

        # ---- 阶段 5: 发 server 标准 tts_end (字段与原路径完全一致) ----
        # audio_duration_sec / send_elapsed_ms 已在阶段3 末尾计算(流式透传完成后立即统计)
        total_frames = total_bytes_sent // 2
        _tts_end_payload = {
            "type": "tts_end", "tts_id": tts_id,
            "send_complete": True,
            "total_bytes": total_bytes_sent,
            "total_frames": total_frames,
            "audio_duration_ms": int(audio_duration_sec * 1000),
            "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
            "session_id": tts_session_id,
            "session_epoch": tts_session_epoch,
            "utterance_id": tts_utterance_id,
            "scope": tts_scope,
        }
        # 44-CHUNK-SEQ: flag on 时 tts_end 带 segment_count/chunk_count/四点字节统计(架构 §20.6.3 / §20.6.4)
        if TTS_CHUNK_SEQ_V31:
            _tts_end_payload["segment_count"] = 1
            _tts_end_payload["chunk_count"] = chunk_seq_state['chunk_index']
            # 四点字节统计: server 侧记 worker_pcm_bytes(:8767 实际生成) + gateway_forwarded_bytes(server 转发)
            # client 侧记 client_received_bytes + audiotrack_written_bytes(client 落, server 只声明字段)
            _tts_end_payload["worker_pcm_bytes"] = worker_pcm_bytes if worker_pcm_bytes is not None else total_bytes_sent
            _tts_end_payload["gateway_forwarded_bytes"] = total_bytes_sent
            print(f"[44-chunk-seq] tts_end tts_id={tts_id} segment_count=1 "
                  f"chunk_count={chunk_seq_state['chunk_index']} "
                  f"worker_pcm_bytes={worker_pcm_bytes} "
                  f"gateway_forwarded_bytes={total_bytes_sent}", flush=True)
        if WS_SEND_PROFILE:
            _sp_mark('gateway_tts_end_sent')
            _tts_end_payload['stage_profile_ms'] = dict(_sp)
            _tts_end_payload['send_elapsed_ms'] = int(send_elapsed_ms)
            _sp_summary = {k: v for k, v in _sp.items() if k != 'func_enter'}
            _phase2_seg_mark(_seg_tr, "synth_complete")
            _phase2_seg_mark(_seg_tr, "send_complete")
            _seg_list = _phase2_seg_finalize_tts_end(tts_id)
            _tts_end_payload["segment_timeline"] = _seg_list
            print('[48-sp] tts_id=%s send_elapsed_ms=%d stages=%s' % (
                tts_id, int(send_elapsed_ms), _sp_summary), flush=True)
            print(f"[51-seg-timeline] tts_id={tts_id} segments_traced={len(_seg_list)} "
                  f"partial_writes={_seg_tr.get('partial_write_count') if _seg_tr else 'n/a'}", flush=True)
        await ws.send(json.dumps(_tts_end_payload))

        # ---- 阶段 6: GUARD / ACK 状态推进 (复用原工具函数, 三模式一致) ----
        if sess is None:
            print(f"[tts] bypass 发完 {seq} 帧 (sess=None, 无 ACK 协议)", flush=True)
            return
        sess.speaking = False
        if sess.barge_stop:
            _reset_active_tts(sess)
            sess.state = 'LISTENING'
            sess.aec_mic.clear()
            sess.listening_idle_frames = 0
            print(f"[tts] bypass barge 中断(发 {seq} 帧) → LISTENING (不等 ACK)",
                  flush=True)
            return
        sess.state = 'GUARD'
        _cancel_followup_task(sess)
        playback_remaining = max(0.0, audio_duration_sec - (time.monotonic() - send_start_mono))
        if PLAYBACK_ACK_MODE == 'ack':
            sess.tts_send_complete = True
            sess.tts_total_bytes = total_bytes_sent
            sess.tts_audio_duration_ms = audio_duration_sec * 1000
            sess.playback_ack_wait_started = time.monotonic()
            sess.playback_terminal_received = False
            sess.playback_ack_timeout_task = asyncio.create_task(
                _playback_ack_timeout(ws, sess, tts_id, after_state))
            print(f"[PLAYBACK] START tts_id={tts_id} session={sess.session_id} "
                  f"event=BYPASS_SEND_COMPLETE total_bytes={total_bytes_sent} "
                  f"audio_duration_ms={int(audio_duration_sec*1000)} "
                  f"send_elapsed_ms={int(send_elapsed_ms)} after_state={after_state} "
                  f"mode=ack (等待 playback_complete/interrupted, "
                  f"超时={int(audio_duration_sec*1000)+PLAYBACK_ACK_TIMEOUT_EXTRA_MS}ms)",
                  flush=True)
            print(f"[tts] bypass 发完 {seq} 帧 → GUARD(ack 模式)", flush=True)
            return
        guard_delay = playback_remaining + POST_TTS_GUARD * 0.02
        sess.followup_task = asyncio.create_task(
            _guard_then_idle(ws, sess, guard_delay, after_state))
        print(f"[tts_playback] bypass bytes={total_bytes_sent} "
              f"audio_dur={audio_duration_sec:.2f}s "
              f"playback_remaining={playback_remaining:.2f}s "
              f"guard_delay={guard_delay:.2f}s (SEND_DELAY_MS={SEND_DELAY_MS})",
              flush=True)
        print(f"[tts] bypass 发完 {seq} 帧 → GUARD({guard_delay*1000:.0f}ms)", flush=True)
        if PLAYBACK_ACK_MODE == 'shadow':
            sess.tts_send_complete = True
            sess.tts_total_bytes = total_bytes_sent
            sess.tts_audio_duration_ms = audio_duration_sec * 1000
            sess.playback_ack_wait_started = time.monotonic()
            sess.playback_terminal_received = False
            print(f"[PLAYBACK] START tts_id={tts_id} session={sess.session_id} "
                  f"event=BYPASS_SEND_COMPLETE total_bytes={total_bytes_sent} "
                  f"audio_duration_ms={int(audio_duration_sec*1000)} "
                  f"send_elapsed_ms={int(send_elapsed_ms)} after_state={after_state} "
                  f"mode=shadow (状态走 legacy GUARD, 仅记录 ACK)", flush=True)

    except Exception as e:
        if not client_tts_start_sent:
            # 前置阶段失败 (client tts_start 未发): raise 让上层 gate fallback 到原 cache miss 路径
            print(f"[bypass-8767] 前置阶段失败, 将 fallback 到原 cache miss 路径: "
                  f"{type(e).__name__}: {e}", flush=True)
            raise
        # 阶段 2 之后失败 (client tts_start 已发): 不 fallback, 尽力发 tts_end 防客户端卡死
        print(f"[bypass-8767] 阶段2+失败(client tts_start 已发), 尽力收尾: "
              f"{type(e).__name__}: {e}", flush=True)
        try:
            await ws.send(json.dumps({
                "type": "tts_end", "tts_id": tts_id,
                "send_complete": True,
                "total_bytes": 0, "total_frames": 0, "audio_duration_ms": 0,
                "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
                "session_id": tts_session_id,
                "session_epoch": tts_session_epoch,
                "utterance_id": tts_utterance_id,
                "scope": tts_scope,
            }))
        except Exception as e2:
            print(f"[bypass-8767] 尽力发 tts_end 也失败: {type(e2).__name__}: {e2}",
                  flush=True)
        if sess is not None:
            try:
                sess.speaking = False
                _reset_active_tts(sess)
                sess.state = 'LISTENING'
            except Exception:
                pass
    finally:
        if bypass_ws is not None:
            try:
                await bypass_ws.close()
            except Exception:
                pass


def _should_split_pipeline(text):
    """44-SENTENCE-SPLIT: 判断是否需要语义分句流水线(架构 §20.6 长句语义分段)。
    触发条件(任一):
      - 字数 > SENTENCE_SPLIT_MIN_LEN(默认 22)
      - 预计音频时长 > SENTENCE_SPLIT_EST_SEC(默认 5s, 按 22050*2 字节/秒 + ~0.1s/字 估算)
      - 含 ≥2 个强终止符(。！？；!?;)
    不触发: 短句(<min_len 且无多句号), cache hit 路径(上层判断)。"""
    n = len(text.strip())
    if n == 0:
        return False
    if n > SENTENCE_SPLIT_MIN_LEN:
        return True
    # 强终止符计数
    strong_ends = _re.findall(r'[。！？；!?;]', text)
    if len(strong_ends) >= 2:
        return True
    # 预计音频时长(粗估: 中文 ~0.13s/字 + 数字/英文略快)
    est_sec = n * 0.13
    if est_sec > SENTENCE_SPLIT_EST_SEC:
        return True
    return False


def _split_for_pipeline(text):
    """44-SENTENCE-SPLIT + 33-PUNCTUATION-SEGMENT: 按标点语义分句。
    flag PUNCTUATION_SEGMENT_V31=on → 标点主导分句(架构 §33: 强/弱/不切边界 +
      SpeechTextFormatter + SemanticSegmenter + SemanticSegmentValidator, 禁止固定字数硬切);
    flag off → BZ 原 split_sentences(强终止符切 + 逗号条件切, 字数主导, 行为与历史版本一致)。

    保护(两路径共用):
      - 拼接回原文 = 原文(不遗漏/不重复)
      - 任何异常 → 返 [text] 走单段原路径(绝不崩上层, 绝不静默丢字)
    返回 segments 列表(顺序保序)。"""
    # 33-PUNCTUATION-SEGMENT: flag on 走标点主导分句
    if PUNCTUATION_SEGMENT_V31:
        try:
            import punctuation_segment as _ps
            segs = _ps.segment_punctuation_dominant(text, log_tag=f"[split_pipeline]")
            # 保护: 单段或拼接≠原文 → 走原路径(绝不丢字)
            if len(segs) <= 1:
                return [text]
            joined = ''.join(segs)
            if joined != text and _ps._normalize_for_compare(joined) != _ps._normalize_for_compare(text):
                print(f"[33-punct-segment] WARN 拼接≠原文, 跳过分句走原路径 "
                      f"orig={text!r} joined={joined!r}", flush=True)
                return [text]
            return segs
        except ImportError:
            print(f"[33-punct-segment] WARN punctuation_segment 模块不可用, 回退 split_sentences",
                  flush=True)
        except Exception as e:
            print(f"[33-punct-segment] WARN 异常回退 split_sentences: "
                  f"{type(e).__name__}: {e}", flush=True)
    # flag off 或异常回退: BZ 原 split_sentences(字数主导, 行为不变)
    sents = split_sentences(text)
    # 保护: 若 split_sentences 只返一段或拼接后不等于原文, 直接返 [text](走原路径)
    if len(sents) <= 1:
        return [text]
    joined = ''.join(sents)
    if joined != text:
        print(f"[44-sentence-split] WARN 拼接≠原文, 跳过分句走原路径 "
              f"orig={text!r} joined={joined!r}", flush=True)
        return [text]
    return sents


async def _bypass_synth_one_segment(bypass_ws, text, tts_id):
    """44-SENTENCE-SPLIT 流水线辅助: 在已建立的 bypass_ws 上发一段 tts_request, 收集 PCM chunks。
    返回 (pcm_bytes_total, first_pcm_ms, tts_end_meta)。
    不发 client tts_start/end(由调用方统一管); 仅收 :8767 的 tts_start/binary/tts_end。
    raise = 本段合成失败(调用方决定是否继续后续段)。"""
    await bypass_ws.send(json.dumps({
        "type": "tts_request", "text": text, "tts_id": tts_id,
    }))
    # 收 :8767 tts_start(协议头)
    raw_first = await asyncio.wait_for(bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
    first_msg = json.loads(raw_first)
    if first_msg.get("type") != "tts_start":
        raise RuntimeError(f":8767 segment 首包非 tts_start: {first_msg.get('type')}")
    pcm_total = bytearray()
    first_pcm_ms = None
    t_first = None
    t_synth = time.time()
    tts_end_meta = {}
    while True:
        msg = await asyncio.wait_for(bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
        if isinstance(msg, (bytes, bytearray)):
            if t_first is None:
                t_first = time.time()
                first_pcm_ms = int((t_first - t_synth) * 1000)
            pcm_total.extend(bytes(msg))
        else:
            end_msg = json.loads(msg)
            if end_msg.get("type") == "tts_end":
                tts_end_meta = {
                    "first_pcm_ms": end_msg.get("first_pcm_ms"),
                    "synth_ms": end_msg.get("synth_ms"),
                    "rtf": end_msg.get("rtf"),
                    "total_bytes": end_msg.get("total_bytes"),
                }
                break
            if end_msg.get("type") == "tts_error":
                raise RuntimeError(f":8767 segment tts_error: {end_msg.get('reason')}")
            # 其他消息忽略
    return bytes(pcm_total), first_pcm_ms, tts_end_meta


async def _synthesize_pipeline_split_v31(ws, text, sess, after_state,
                                          tts_id, tts_session_id,
                                          tts_session_epoch, tts_utterance_id,
                                          tts_scope, use_aec):
    """44-SENTENCE-SPLIT (架构 §20.6 长句分段 + §20.6.3 chunk 序号, 2026-07-29 二): 语义分句流水线。
    流程:
      1. 入口判断 _should_split_pipeline → _split_for_pipeline 切 N 段(同 tts_id, segment_index 0..N-1)
      2. 发 client tts_start(segment_count=N, chunk_seq_v31 视 flag)
      3. pipeline: 段 i 合成(:8767 stream=True 流式) + 段 i+1 合成(独立 bypass_ws 并行)
         段 i 合成完立即 send_pcm_streaming 透传(segment_index=i, chunk_seq_state 跨段累计)
      4. 最后段透传完 → 发唯一 client tts_end(segment_count/chunk_count/四点字节统计)
      5. GUARD/ACK 状态推进(同 _synthesize_via_bypass_8767 阶段 6)
    红线:
      - 同一 tts_id(不每段独立 tts_id, 否则 client 每段 complete 破坏"全段播完才 complete")
      - 段间不发独立 tts_end(只发一次, 最后段后)
      - barge-in 任一段中断 → 停止后续段 + 发 tts_end + 转 LISTENING
      - 任一段 :8767 失败 → 尽力收尾(已发 tts_start, 不 fallback)+ 发 tts_end + 转 LISTENING
    raise = 前置阶段(client tts_start 未发)失败, 调用方应 fallback。"""
    if not _should_split_pipeline(text):
        # 不需要分句 → 直接走原 _synthesize_via_bypass_8767(单段)
        await _synthesize_via_bypass_8767(
            ws, text, sess, after_state,
            tts_id=tts_id, tts_session_id=tts_session_id,
            tts_session_epoch=tts_session_epoch, tts_utterance_id=tts_utterance_id,
            tts_scope=tts_scope, use_aec=use_aec)
        return
    segments = _split_for_pipeline(text)
    if len(segments) <= 1:
        # 分句后仍单段(保护), 走原路径
        await _synthesize_via_bypass_8767(
            ws, text, sess, after_state,
            tts_id=tts_id, tts_session_id=tts_session_id,
            tts_session_epoch=tts_session_epoch, tts_utterance_id=tts_utterance_id,
            tts_scope=tts_scope, use_aec=use_aec)
        return

    # 62-DROPOUT Top2: 短首段保护(flag off 串行实现时生效)。分句后第一段(段0)估算 audio_dur
    #   < SENTENCE_SPLIT_MIN_SEG_SEC 则不分句(走单段 bypass)。根治"欢迎词 18 字分 2 段,
    #   段0 仅 1.196s, 段间必 gap": 段0 太短→段0 播完段1 还在合成/连接→必 gap。
    #   PIPELINE_PARALLEL_V31=on 时不生效(预取已解决段间 gap, 分句不受短段限制)。
    #   min_seg_sec=0 禁用(完全按原逻辑分句)。估算: 中文 ~0.15s/字(CosyVoice 实测均值)。
    #   只看第一段(段0 短才有 gap 风险; 中间/末段短不影响首段播放覆盖合成)。
    if SENTENCE_SPLIT_MIN_SEG_SEC > 0 and not PIPELINE_PARALLEL_V31:
        first_est_seg_sec = len(segments[0].strip()) * 0.15
        if first_est_seg_sec < SENTENCE_SPLIT_MIN_SEG_SEC:
            print(f"[62-dropout] tts_id={tts_id} 分 {len(segments)} 段但段0 估 "
                  f"{first_est_seg_sec:.2f}s < min_seg_sec={SENTENCE_SPLIT_MIN_SEG_SEC}s, "
                  f"回退单段 bypass(避免短段间隙): {segments}", flush=True)
            await _synthesize_via_bypass_8767(
                ws, text, sess, after_state,
                tts_id=tts_id, tts_session_id=tts_session_id,
                tts_session_epoch=tts_session_epoch, tts_utterance_id=tts_utterance_id,
                tts_scope=tts_scope, use_aec=use_aec)
            return

    t0 = time.time()
    seg_count = len(segments)
    print(f"[44-sentence-split] tts_id={tts_id} 分 {seg_count} 段: {segments}", flush=True)

    # ---- 阶段 A: 前置连接(段 1 的 bypass_ws, 失败可 raise fallback) ----
    bypass_ws_1 = await asyncio.wait_for(
        websockets.connect(
            TTS_BYPASS_8767_URL,
            open_timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT,
            close_timeout=1.0, max_size=None,
        ),
        timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT + 0.5,
    )
    client_tts_start_sent = False
    try:
        # ---- 阶段 B: 发段 1 tts_request + 收 :8767 tts_start(前置验证) ----
        # 段 1 合成在这里启动(:8767 stream=True 会立即开始流式 yield chunk)
        await bypass_ws_1.send(json.dumps({
            "type": "tts_request", "text": segments[0], "tts_id": tts_id,
        }))
        raw_first = await asyncio.wait_for(bypass_ws_1.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
        first_msg = json.loads(raw_first)
        if first_msg.get("type") != "tts_start":
            raise RuntimeError(f":8767 段1 首包非 tts_start: {first_msg.get('type')}")
        bypass_meta = {
            "first_pcm_ms": None, "synth_ms": None, "rtf": None,
            "sample_rate": first_msg.get("sample_rate"),
        }

        # ---- 阶段 C: 发 client tts_start(segment_count=N, 此后失败不再 fallback) ----
        _tts_start_payload = {
            "type": "tts_start", "tts_id": tts_id, "text": text,
            "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
            "session_id": tts_session_id,
            "session_epoch": tts_session_epoch,
            "utterance_id": tts_utterance_id,
            "scope": tts_scope,
        }
        if TTS_CHUNK_SEQ_V31:
            _tts_start_payload["chunk_seq_v31"] = True
        _tts_start_payload["segment_count"] = seg_count
        _tts_start_payload["sentence_split_v31"] = True
        await ws.send(json.dumps(_tts_start_payload))
        client_tts_start_sent = True
        if sess is not None:
            sess.speaking = True
            sess.barge_stop = False
            sess.state = 'SPEAKING'
        send_start_mono = time.monotonic()
        total_bytes_sent = 0
        seq = 0
        first_client_pkt_ms = None
        # 44-CHUNK-SEQ: chunk 序号累计器(跨段连续, 同 tts_id)
        chunk_seq_state = {'chunk_index': 0, 'pcm_offset': 0}
        worker_pcm_bytes_total = 0

        # ---- 阶段 D: 段 1 流式收 chunk + 即时透传 + 段 2..N 串行(pipeline 保守实现) ----
        # 注: 真"client 播段1 时 Worker 合成段2"需段 2 在段 1 透传期间并行合成。
        #   保守实现: 段 i 收齐(:8767 stream 透传)后, 启动段 i+1 合成。
        #   收益: 段 1 首 PCM 仍快(:8767 stream=True, 段 1 chunk 到达即透传 client);
        #         段 2..N 合成在段 i 透传期间不阻塞 client 播放(server 透传完段 i 即可让 client 播,
        #         server 串行收段 i+1 不影响 client 播段 i)。
        #   后续优化: 真 pipeline 用 asyncio.gather 让段 i+1 合成与段 i 透传并行(需多 bypass_ws)。
        # 62-DROPOUT Top2: PIPELINE_PARALLEL_V31=on 时, 段 i 第一个 chunk 透传后立即异步预取段 i+1
        #   (建 ws + tts_request + 等 tts_start), 段 i 收完直接复用预取的 ws(省段间 ~1.1s 空窗)。
        #   flag off: 走原串行(段 i 收齐才建段 i+1 连接), 行为完全不变。
        next_prefetch_task = None  # 段 i+1 预取 task(PIPELINE_PARALLEL_V31=on 时用)

        async def _prefetch_next_seg(next_idx):
            """预取段 next_idx: 建 bypass_ws + 发 tts_request + 等 tts_start。
            返回已握手的 bypass_ws(调用方直接进 while 收 chunk)。raise = 预取失败。"""
            _ws = await asyncio.wait_for(
                websockets.connect(
                    TTS_BYPASS_8767_URL,
                    open_timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT,
                    close_timeout=1.0, max_size=None,
                ),
                timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT + 0.5)
            await _ws.send(json.dumps({
                "type": "tts_request", "text": segments[next_idx], "tts_id": tts_id,
            }))
            _raw = await asyncio.wait_for(_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
            _m = json.loads(_raw)
            if _m.get("type") != "tts_start":
                raise RuntimeError(f":8767 段{next_idx} 预取首包非 tts_start: {_m.get('type')}")
            return _ws

        for seg_idx in range(seg_count):
            if sess is not None and sess.barge_stop:
                print(f"[44-sentence-split] barge-in 中断(段{seg_idx}/{seg_count}, "
                      f"已透传 {total_bytes_sent} bytes)", flush=True)
                break
            # 选当前段用的 bypass_ws: seg_idx=0 用已建立的 bypass_ws_1; seg_idx>0 新建连接或复用预取
            if seg_idx == 0:
                cur_bypass_ws = bypass_ws_1
                cur_pcm_iter = True  # 段 1 已发 tts_request, 进流式收 chunk 循环
            else:
                # 段 2..N: flag on 复用预取 task(段 i 透传期间已建连握手); off 新建连接
                if PIPELINE_PARALLEL_V31 and next_prefetch_task is not None:
                    _prefetch_already_done = next_prefetch_task.done()
                    _t_await = time.time()
                    try:
                        cur_bypass_ws = await next_prefetch_task
                        # 诊断: 预取 task 已完成=段 i 透传期间已握好手(await≈0=预取生效);
                        #   未完成=预取仍在跑(await>0=段 i 收完还在等段 i+1 握手, 预取部分生效)
                        _await_ms = int((time.time() - _t_await) * 1000)
                        print(f"[62-dropout] 段{seg_idx} 复用预取 ws "
                              f"(prefetch_done_before_await={_prefetch_already_done} "
                              f"await_ms={_await_ms})", flush=True)
                    except Exception as pre_e:
                        # 预取失败 → 降级为同步新建(不破坏流水线, 仅丢预取收益)
                        print(f"[62-dropout] 段{seg_idx} 预取失败, 降级同步建连: "
                              f"{type(pre_e).__name__}: {pre_e}", flush=True)
                        cur_bypass_ws = await asyncio.wait_for(
                            websockets.connect(
                                TTS_BYPASS_8767_URL,
                                open_timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT,
                                close_timeout=1.0, max_size=None,
                            ),
                            timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT + 0.5)
                        await cur_bypass_ws.send(json.dumps({
                            "type": "tts_request", "text": segments[seg_idx], "tts_id": tts_id,
                        }))
                        _raw_s = await asyncio.wait_for(cur_bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
                        _msg_s = json.loads(_raw_s)
                        if _msg_s.get("type") != "tts_start":
                            raise RuntimeError(f":8767 段{seg_idx} 首包非 tts_start: {_msg_s.get('type')}")
                else:
                    # 串行: 段 i 收齐才建段 i+1 连接(原行为, flag off)
                    cur_bypass_ws = await asyncio.wait_for(
                        websockets.connect(
                            TTS_BYPASS_8767_URL,
                            open_timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT,
                            close_timeout=1.0, max_size=None,
                        ),
                        timeout=TTS_BYPASS_8767_CONNECT_TIMEOUT + 0.5)
                    await cur_bypass_ws.send(json.dumps({
                        "type": "tts_request", "text": segments[seg_idx], "tts_id": tts_id,
                    }))
                    _raw_s = await asyncio.wait_for(cur_bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
                    _msg_s = json.loads(_raw_s)
                    if _msg_s.get("type") != "tts_start":
                        raise RuntimeError(f":8767 段{seg_idx} 首包非 tts_start: {_msg_s.get('type')}")
                next_prefetch_task = None  # 已消费
                cur_pcm_iter = True

            is_last_segment = (seg_idx == seg_count - 1)
            seg_first_pcm_ms = None
            t_synth = time.time()
            t_first = None
            seg_worker_bytes = 0
            first_chunk_of_seg = True  # 62-DROPOUT: 段首个 chunk 触发段 i+1 预取
            # 51-SEGMENT-TIMELINE (B1): segment 级 trace(WS_SEND_PROFILE=on)
            _seg_tr = _phase2_seg_new(tts_id, seg_idx, segments[seg_idx])
            _phase2_seg_mark(_seg_tr, "synth_start")
            _phase2_seg_mark(_seg_tr, "send_start")
            try:
                while True:
                    if sess is not None and sess.barge_stop:
                        break
                    msg = await asyncio.wait_for(cur_bypass_ws.recv(), timeout=TTS_BYPASS_8767_RECV_TIMEOUT)
                    if isinstance(msg, (bytes, bytearray)):
                        if t_first is None:
                            t_first = time.time()
                            seg_first_pcm_ms = int((t_first - t_synth) * 1000)
                            _phase2_seg_mark(_seg_tr, "first_pcm", seg_first_pcm_ms)
                        chunk_bytes = bytes(msg)
                        # 62-DROPOUT Top2: 段首个 chunk 透传后, 立即异步预取段 i+1(与段 i 收发重叠)
                        if first_chunk_of_seg and PIPELINE_PARALLEL_V31 and not is_last_segment \
                                and next_prefetch_task is None:
                            next_prefetch_task = asyncio.create_task(
                                _prefetch_next_seg(seg_idx + 1))
                            first_chunk_of_seg = False
                        ref16 = (tts_to_16k_ref(chunk_bytes)
                                 if (use_aec or aec_capture.enabled()) else None)
                        seq, fp = await send_pcm_streaming(
                            ws, chunk_bytes, sess=sess, zero_delay=False, ref16=ref16,
                            start_seq=seq, ts=int(time.time() * 1000),
                            first_pkt_t0=send_start_mono,
                            tts_id=tts_id, segment_index=seg_idx,
                            chunk_seq_state=chunk_seq_state,
                            is_last_chunk=is_last_segment)
                        total_bytes_sent += len(chunk_bytes)
                        seg_worker_bytes += len(chunk_bytes)
                        # 51-SEGMENT-TIMELINE (B1): 一次 send_pcm_streaming = 一个 partial write 片
                        _phase2_seg_incr(_seg_tr, "partial_write_count")
                        if first_client_pkt_ms is None and fp is not None:
                            first_client_pkt_ms = fp
                    else:
                        end_msg = json.loads(msg)
                        if end_msg.get("type") == "tts_end":
                            if seg_idx == 0:
                                bypass_meta["first_pcm_ms"] = end_msg.get("first_pcm_ms")
                                bypass_meta["synth_ms"] = end_msg.get("synth_ms")
                                bypass_meta["rtf"] = end_msg.get("rtf")
                            _update_rtf_window(end_msg.get("rtf"))  # 62-DROPOUT Top1: 每段 rtf 喂窗口
                            # worker_pcm_bytes 按 :8767 tts_end total_bytes 累计(四点字节统计)
                            _wb = end_msg.get("total_bytes") or seg_worker_bytes
                            worker_pcm_bytes_total += _wb
                            break
                        if end_msg.get("type") == "tts_error":
                            raise RuntimeError(f":8767 段{seg_idx} tts_error: {end_msg.get('reason')}")
            except asyncio.TimeoutError:
                print(f"[44-sentence-split] 段{seg_idx} 收 chunk 超时, "
                      f"已透传 {total_bytes_sent} bytes, 继续收尾", flush=True)
            finally:
                if seg_idx > 0:
                    try:
                        await cur_bypass_ws.close()
                    except Exception:
                        pass
            _phase2_seg_mark(_seg_tr, "synth_complete")
            _phase2_seg_mark(_seg_tr, "send_complete")
            print(f"[44-sentence-split] 段{seg_idx}/{seg_count} 透传完 "
                  f"bytes={seg_worker_bytes} seg_first_pcm_ms={seg_first_pcm_ms} "
                  f"partial_writes={_seg_tr.get('partial_write_count') if _seg_tr else 'n/a'}", flush=True)

        # 循环结束: 若预取 task 未被消费(段中断/barge), 清理避免泄漏
        if next_prefetch_task is not None and not next_prefetch_task.done():
            next_prefetch_task.cancel()
            try:
                _ws_leak = await next_prefetch_task
                try:
                    await _ws_leak.close()
                except Exception:
                    pass
            except Exception:
                pass

        # ---- 阶段 E: 发唯一 client tts_end(segment_count/chunk_count/四点字节统计) ----
        audio_duration_sec = total_bytes_sent / (TTS_RATE * 2) if total_bytes_sent > 0 else 0.0
        elapsed = time.time() - t0
        send_elapsed_ms = (time.monotonic() - send_start_mono) * 1000
        total_frames = total_bytes_sent // 2
        _tts_end_payload = {
            "type": "tts_end", "tts_id": tts_id,
            "send_complete": True,
            "total_bytes": total_bytes_sent,
            "total_frames": total_frames,
            "audio_duration_ms": int(audio_duration_sec * 1000),
            "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
            "session_id": tts_session_id,
            "session_epoch": tts_session_epoch,
            "utterance_id": tts_utterance_id,
            "scope": tts_scope,
        }
        if TTS_CHUNK_SEQ_V31:
            _tts_end_payload["segment_count"] = seg_count
            _tts_end_payload["chunk_count"] = chunk_seq_state['chunk_index']
            _tts_end_payload["worker_pcm_bytes"] = worker_pcm_bytes_total
            _tts_end_payload["gateway_forwarded_bytes"] = total_bytes_sent
        _tts_end_payload["sentence_split_v31"] = True
        _tts_end_payload["sentence_split_segments"] = seg_count
        if WS_SEND_PROFILE:
            _seg_list = _phase2_seg_finalize_tts_end(tts_id)
            _tts_end_payload["segment_timeline"] = _seg_list
            print(f"[51-seg-timeline] tts_id={tts_id} segments_traced={len(_seg_list)}", flush=True)
        await ws.send(json.dumps(_tts_end_payload))
        print(f"[44-sentence-split] done tts_id={tts_id} segments={seg_count} "
              f"audio_dur={audio_duration_sec:.2f}s elapsed={elapsed:.2f}s "
              f"send_elapsed={send_elapsed_ms/1000:.2f}s "
              f"client_first_pkt_ms={int(first_client_pkt_ms) if first_client_pkt_ms is not None else None} "
              f":8767_first_pcm_ms={bypass_meta['first_pcm_ms']} "
              f"worker_pcm_bytes={worker_pcm_bytes_total} "
              f"gateway_forwarded_bytes={total_bytes_sent} "
              f"chunk_count={chunk_seq_state['chunk_index']}", flush=True)

        # ---- 阶段 F: GUARD / ACK 状态推进(同 _synthesize_via_bypass_8767 阶段 6) ----
        if sess is None:
            print(f"[tts] pipeline 发完 {seq} 帧 (sess=None, 无 ACK 协议)", flush=True)
            return
        sess.speaking = False
        if sess.barge_stop:
            _reset_active_tts(sess)
            sess.state = 'LISTENING'
            sess.aec_mic.clear()
            sess.listening_idle_frames = 0
            print(f"[tts] pipeline barge 中断(发 {seq} 帧) → LISTENING (不等 ACK)", flush=True)
            return
        sess.state = 'GUARD'
        _cancel_followup_task(sess)
        playback_remaining = max(0.0, audio_duration_sec - (time.monotonic() - send_start_mono))
        if PLAYBACK_ACK_MODE == 'ack':
            sess.tts_send_complete = True
            sess.tts_total_bytes = total_bytes_sent
            sess.tts_audio_duration_ms = audio_duration_sec * 1000
            sess.playback_ack_wait_started = time.monotonic()
            sess.playback_terminal_received = False
            sess.playback_ack_timeout_task = asyncio.create_task(
                _playback_ack_timeout(ws, sess, tts_id, after_state))
            print(f"[PLAYBACK] START tts_id={tts_id} session={sess.session_id} "
                  f"event=PIPELINE_SEND_COMPLETE total_bytes={total_bytes_sent} "
                  f"audio_duration_ms={int(audio_duration_sec*1000)} "
                  f"send_elapsed_ms={int(send_elapsed_ms)} after_state={after_state} "
                  f"mode=ack segments={seg_count} (等待 playback_complete/interrupted)", flush=True)
            print(f"[tts] pipeline 发完 {seq} 帧 → GUARD(ack 模式)", flush=True)
            return
        guard_delay = playback_remaining + POST_TTS_GUARD * 0.02
        sess.followup_task = asyncio.create_task(
            _guard_then_idle(ws, sess, guard_delay, after_state))
        print(f"[tts] pipeline 发完 {seq} 帧 → GUARD({guard_delay*1000:.0f}ms)", flush=True)
        return

    except Exception as bypass_err:
        # 前置阶段失败(client tts_start 未发) → raise 让上层 fallback
        if not client_tts_start_sent:
            raise
        # 已发 tts_start 后失败 → 尽力收尾(同 _synthesize_via_bypass_8767 except 逻辑)
        print(f"[44-sentence-split] 已发 tts_start 后失败, 尽力收尾: "
              f"{type(bypass_err).__name__}: {bypass_err}", flush=True)
        try:
            await ws.send(json.dumps({
                "type": "tts_end", "tts_id": tts_id,
                "send_complete": False, "error": str(bypass_err)[:200],
                "total_bytes": 0, "total_frames": 0, "audio_duration_ms": 0,
                "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
                "session_id": tts_session_id,
                "session_epoch": tts_session_epoch,
                "utterance_id": tts_utterance_id,
                "scope": tts_scope,
            }))
        except Exception as e2:
            print(f"[44-sentence-split] 尽力发 tts_end 也失败: {type(e2).__name__}: {e2}", flush=True)
        if sess is not None:
            try:
                sess.speaking = False
                _reset_active_tts(sess)
                sess.state = 'LISTENING'
            except Exception:
                pass
    finally:
        try:
            await bypass_ws_1.close()
        except Exception:
            pass


async def synthesize_streaming(ws, text, sess, use_aec):
    """P0-3: cache miss 时 CosyVoice inference_sft stream=True 流式合成, 边合成边 20ms 下发(首包优先)。
    动态缓存整句(下次命中 0 延迟)。USE_AEC 时每个 chunk 的 REF 按 20ms 同步喂(不预灌)。
    返回 (发送帧数, 首包延迟ms)。"""
    t0 = time.time()
    ts = int(time.time() * 1000)
    seq = 0
    first_pkt_ms = None
    total = bytearray()
    try:
        for chunk in cv.inference_sft(text, TTS_SPK, stream=True):
            wav = chunk['tts_speech'].squeeze().cpu().numpy()
            pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16).tobytes()
            total.extend(pcm)
            ref16 = tts_to_16k_ref(pcm) if (use_aec or aec_capture.enabled()) else None
            seq, fp = await send_pcm_streaming(
                ws, pcm, sess=sess, zero_delay=False, ref16=ref16,
                start_seq=seq, ts=ts, first_pkt_t0=t0)
            if first_pkt_ms is None and fp is not None:
                first_pkt_ms = fp
            if sess is not None and sess.barge_stop:
                break  # barge-in: 停止后续 chunk 合成下发
    except Exception as e:
        print(f"[tts stream err] {e}", flush=True)
    dur = len(total) / TTS_RATE / 2
    elapsed = time.time() - t0
    rtf = (elapsed / dur) if dur > 0 else 0.0
    _update_rtf_window(rtf if rtf > 0 else None)  # 62-DROPOUT Top1: 进程内合成也喂 rtf 窗口
    print(f"[tts] stream 首包 {first_pkt_ms}ms 共 {seq} 帧 {dur:.2f}s 用时 {elapsed:.2f}s (rtf={rtf:.2f})", flush=True)
    if dur > 0.2 and seq > 0 and TTS_CACHE_ENABLED:
        TTS_CACHE[text] = bytes(total)  # 动态缓存整句(下次 0 延迟命中; flag off 不写)
    return seq, first_pkt_ms


# 54-PROTOCOL-V2: V2 协议合成与发送(feature flag + 客户端协商双端满足才调用)。
# 草案实现: 集成 v2proto 模块(SemanticSegmenter + SegmentPipelineEncoder + V2 Codec)。
# 当前为骨架, 真实灰度需: ① v2proto 目录部署到生产 server.py 同级;
#   ② AEC REF 喂入(PipelineConfig.use_aec + ref16_provider, 复用 tts_to_16k_ref);
#   ③ barge_stop 联动(sess.barge_stop → encoder.request_cancel, encoder.run 段循环检查);
#   ④ 客户端 V2 解码实现(SegmentQueue + AudioTrackWriter)。
# 红线: flag off(TTS_AUDIO_PROTOCOL=v1 默认)时本函数不被调用(synthesize_and_send 分流条件不满足);
#   V2 路径不调 send_pcm_streaming(用自己的 encode_frame), 不破坏 V1 任何路径。
async def _synthesize_and_send_v2(ws, text, sess, after_state='IDLE', scope=None):
    """V2 协议合成与发送(SegmentPipelineEncoder + V2 binary frame)。

    流程:
    1. SemanticSegmenter 切分(段级独立, join==原文 保证)
    2. SegmentPipelineEncoder 编码 + 发送(global_frame_index 跨段连续 + CRC32)
    3. 每段 PCM 通过 cache 或 :8767 bypass 合成
    4. 背压(ready>2 或 queued>8s 暂停) + cancel(sess.barge_stop frame 边界停) + 错误→tts_error
    """
    import sys as _sys
    import os as _os
    # v2proto 路径(server.py 同级 v2proto/{segments,codec}/)
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _v2_root = _os.path.join(_here, "v2proto")
    if _v2_root not in _sys.path:
        _sys.path.insert(0, _v2_root)
    _v2_seg = _os.path.join(_v2_root, "segments")
    if _v2_seg not in _sys.path:
        _sys.path.insert(0, _v2_seg)
    _v2_codec = _os.path.join(_v2_root, "codec")
    if _v2_codec not in _sys.path:
        _sys.path.insert(0, _v2_codec)
    from semantic_segmenter import segment as semantic_segment
    from pipeline_encoder import SegmentPipelineEncoder, PipelineConfig

    tts_id = _gen_tts_id(sess)
    session_epoch = sess.session_epoch

    # 切分(SemanticSegmenter, join==原文 保证)
    seg_list = semantic_segment(text, tts_id=tts_id)

    # 档位决策输入(Wake ACK / 控制回复 → SHORT)
    is_wake_ack = (scope == "welcome") or (text.strip() == _WAKE_ACK_DEFAULT.strip())

    # PCM 来源: cache hit / bypass_8767 合成(段级独立缓存)
    async def get_segment_pcm(idx, seg):
        seg_text = getattr(seg, "text", str(seg))
        # cache 检查(段文本独立)
        if TTS_CACHE_ENABLED and seg_text in TTS_CACHE:
            return TTS_CACHE[seg_text]
        # bypass_8767 合成(单段, stream=False 拿完整 PCM)
        pcm = await _synth_segment_via_bypass_for_v2(seg_text, sess)
        # 段级缓存(仅 TTS_CACHE_ENABLED)
        if TTS_CACHE_ENABLED and len(pcm) > 0:
            TTS_CACHE[seg_text] = pcm
        return pcm

    cfg = PipelineConfig(
        sample_rate=TTS_RATE,
        channels=1,
        bits_per_sample=16,
        frame_ms=20,
    )

    async def send_fn(msg):
        if isinstance(msg, (bytes, bytearray)):
            await ws.send(bytes(msg))
        else:
            await ws.send(json.dumps(msg, ensure_ascii=False))

    encoder = SegmentPipelineEncoder(
        tts_id=tts_id,
        session_epoch=session_epoch,
        reply_text=text,
        segments=seg_list,
        send_fn=send_fn,
        config=cfg,
    )

    # 状态推进(与 V1 synthesize_and_send 一致)
    sess.last_tts_text = text
    _reset_active_tts(sess)
    sess.active_tts_id = tts_id
    sess.active_tts_after_state = after_state
    sess.playback_terminal_received = False
    sess.speaking = True
    sess.barge_stop = False
    sess.state = "SPEAKING"

    # barge_stop 联动: encoder.run 段循环检查 sess.barge_stop → request_cancel
    # (SegmentPipelineEncoder 已有 is_cancelled 检查; 这里通过 run 前/中轮询联动)
    await encoder.run(get_segment_pcm, is_wake_ack=is_wake_ack,
                      cancel_check=lambda: getattr(sess, 'barge_stop', False))

    # 终态处理(cancel / error / 正常)
    sess.speaking = False
    if encoder.is_cancelled or sess.barge_stop:
        # cancel / barge → 回 LISTENING(用户打断, 等新指令)
        sess.state = "LISTENING"
        sess.aec_mic.clear()
        sess.listening_idle_frames = 0
    elif getattr(encoder, 'state', None) and encoder.state.get('error'):
        # 合成错误 → 走错误恢复(回 LISTENING, 不卡 SPEAKING)
        print(f"[54-proto-v2] encoder error: {encoder.state}", flush=True)
        sess.state = "LISTENING"
    else:
        # 正常播完 → GUARD(等 playback_complete 或超时兜底; 与 V1 一致)
        sess.state = "GUARD"
        sess.guard_frames = POST_TTS_GUARD


async def _synth_segment_via_bypass_for_v2(seg_text, sess):
    """V2 辅助: 单段合成拿完整 PCM(走 :8767 bypass 或本地 INT8)。
    草案: 当前复用 synth_to_pcm(本地 INT8); 真实 V2 灰度时切换 bypass_8767 拿 PCM。
    返回 16k mono S16 bytes。"""
    # 草案: 本地 synth_to_pcm(V1 cache miss 路径同款)
    try:
        pcm = await asyncio.to_thread(synth_to_pcm, seg_text)
        return bytes(pcm)
    except Exception as _e:
        print(f"[54-proto-v2] 单段合成失败: {_e}", flush=True)
        return b''


async def synthesize_and_send(ws, text, sess=None, after_state='IDLE', scope=None):
    """scope 显式参数(FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728 遗漏字段补丁):
    - None(默认): 自动判断 —— sess=None→welcome, sess≠None→session
    - 'welcome': 欢迎词/纯播报, client 不走代际门(IDLE 也允许)
    - 'session': 会话型 TTS, client 必须四项匹配才允许播
    hello 欢迎词调用处必须显式传 scope='welcome'(那时 sess 已分配但仍是欢迎词,
    不能让 client 代际门误拒)。"""
    # 54-PROTOCOL-V2: V2 会话分流(feature flag + 客户端协商 双端满足才走 V2)。
    # flag off(TTS_AUDIO_PROTOCOL=v1 默认)或客户端未协商 → 走下方原 V1 路径(零改变)。
    # sess=None(欢迎词/纯测试)永远走 V1(V2 需 per-session 协商, 无 sess 无协商)。
    if sess is not None and getattr(sess, '_audio_protocol', 'v1') == 'v2':
        try:
            await _synthesize_and_send_v2(ws, text, sess, after_state, scope)
            return
        except Exception as _v2e:
            # V2 路径异常 → 降级回 V1(不卡死, 记日志; 真实部署灰度时观察)
            print(f"[54-proto-v2] V2 异常降级 V1: {_v2e}", flush=True)
            # 继续走下方 V1 路径
    if sess is not None:
        sess.last_tts_text = text  # 记录TTS文本(回声过滤)
        # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 新 tts 开始前清旧 ACK 状态
        # (旧 tts 若未终态, 视为被新 tts 取代; 不发 interrupted 因 PCM 早发完, 仅服务端清理)
        _reset_active_tts(sess)
        tts_id = _gen_tts_id(sess)
        sess.active_tts_id = tts_id
        sess.active_tts_after_state = after_state
        sess.playback_terminal_received = False
        # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 抓取代际 token(tts_start/end 携带)
        # client 用 session_id+session_epoch+utterance_id+tts_id 四项匹配, 拒绝旧 TTS 迟到误播。
        tts_session_id = sess.session_id
        tts_session_epoch = sess.session_epoch
        tts_utterance_id = sess.active_utterance_id
        # scope 自动判断: 显式传入优先; 否则 sess!=None 默认 session
        tts_scope = scope if scope is not None else "session"
    else:
        tts_id = f"tts_nosess_{int(time.time()*1000)}"
        # sess=None: 欢迎词/纯测试 TTS(scope=welcome 独立, 不走会话 TTS 状态切换)
        tts_session_id = None
        tts_session_epoch = None
        tts_utterance_id = None
        tts_scope = scope if scope is not None else "welcome"
    # 28-P0-CLEANUP-BYARCH P0-1: TTS_CACHE_ENABLED=0 时永远视为 miss(不读 cache)。
    #   bypass=1 时本函数在 line ~1380 bypass gate 已 return, 不会走到这里;
    #   此 gate 主要防"bypass=0 + TTS_CACHE_ENABLED=0"或"bypass 前置失败 fallback"时误读 cache。
    cache_hit = TTS_CACHE_ENABLED and (text in TTS_CACHE)
    use_aec = USE_AEC and sess is not None and sess.apm is not None
    # Step10 日志埋点: 人类可读行 + 结构化 metric(便于后续统计真实命中率, 不改 synthesize 主逻辑)
    print(f"[tts] 合成: {text} (cache={'hit' if cache_hit else 'miss'}) tts_id={tts_id}", flush=True)
    print(f"[tts_cache_metric] hit={int(cache_hit)} cache_size={len(TTS_CACHE)} text_len={len(text)} use_int8={int(USE_INT8)} spk={TTS_SPK} text={text!r}", flush=True)

    # ===== 24-BYPASS-FORWARD gate: :8767 GPU FP32 无 cache 旁路 =====
    # 默认 TTS_BACKEND_BYPASS_8767=0: 整段不进入, 原 cache hit/miss 路径一行不改(回归零影响)。
    # =1: 跳过 cache 读写, 整句合成转发到本地 :8767。前置阶段失败(连接/握手/首包) → except
    # 记日志后继续走下方原 cache miss 路径(fallback)。一旦 client tts_start 已发, 旁路函数
    # 自管收尾(含发 tts_end + 状态推进), 正常 return → 本函数 return, 不再走原 body。
    if TTS_BACKEND_BYPASS_8767:
        # 44-WAKE-ACK-SPEED: 判断本句是否 Wake ACK(用 _WAKE_ACK_DEFAULT 精确比较)
        #   命中且 WAKE_ACK_SPEED=on → 旁路 tts_request 带 speed=WAKE_ACK_SPEED_VAL + stream_false=True
        #   (worker 走 stream=False 让 speed 真生效, 架构 §6.7 + 报告42 源码约束)
        #   非命中 → 不带 speed/stream_false, worker 走 comboAB 默认 stream=True
        is_wake_ack_text = (text.strip() == _WAKE_ACK_DEFAULT.strip())
        wake_ack_speed_on = is_wake_ack_text and WAKE_ACK_SPEED
        # 44-SENTENCE-SPLIT: 长句分句流水线(SENTENCE_SPLIT_V31=on 且 _should_split_pipeline)
        #   注: WAKE_ACK 短句不会触发分句(_should_split_pipeline 短句返 False)
        use_pipeline = SENTENCE_SPLIT_V31 and _should_split_pipeline(text) and not wake_ack_speed_on
        try:
            if use_pipeline:
                # 语义分句流水线(段 1 即发 + 段 2..N 串行合成, 同 tts_id)
                await _synthesize_pipeline_split_v31(
                    ws, text, sess, after_state,
                    tts_id=tts_id,
                    tts_session_id=tts_session_id,
                    tts_session_epoch=tts_session_epoch,
                    tts_utterance_id=tts_utterance_id,
                    tts_scope=tts_scope,
                    use_aec=use_aec)
            else:
                # 单段旁路(WAKE_ACK speed 走此路径, _synthesize_via_bypass_8767 透传 speed/stream_false 到 :8767)
                await _synthesize_via_bypass_8767(
                    ws, text, sess, after_state,
                    tts_id=tts_id,
                    tts_session_id=tts_session_id,
                    tts_session_epoch=tts_session_epoch,
                    tts_utterance_id=tts_utterance_id,
                    tts_scope=tts_scope,
                    use_aec=use_aec,
                    wake_ack_speed=wake_ack_speed_on)  # 44-WAKE-ACK-SPEED: 透传标志
            return  # 旁路接管完成(含失败收尾), 不走原 body
        except Exception as bypass_err:
            # 仅前置阶段(client tts_start 未发)失败才到这里 → fallback 走原 cache miss 路径
            print(f"[bypass-8767] fallback 到原进程内合成路径: "
                  f"{type(bypass_err).__name__}: {bypass_err}", flush=True)

    # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727 + FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728:
    # tts_start 带完整音频参数 + sample_width + 会话归属(session_id/epoch/utterance_id/scope)
    await ws.send(json.dumps({"type": "tts_start", "tts_id": tts_id, "text": text,
                              "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
                              "session_id": tts_session_id,
                              "session_epoch": tts_session_epoch,
                              "utterance_id": tts_utterance_id,
                              "scope": tts_scope}))
    if sess is not None:
        sess.speaking = True
        sess.barge_stop = False
        sess.state = 'SPEAKING'
    # P0 时序安全: 记录首块下发时刻(估算 expected_playback_end)
    send_start_mono = time.monotonic()
    total_bytes_sent = 0

    if cache_hit:
        # cache hit: 按 20ms 节流下发(不 zero_delay 瞬间灌 → 防 AudioTrack buffer 突增截断 + AEC REF 按播放节奏对齐)
        pcm = TTS_CACHE[text]
        ref16 = tts_to_16k_ref(pcm) if (use_aec or aec_capture.enabled()) else None
        seq, _ = await send_pcm_streaming(ws, pcm, sess=sess, zero_delay=False, ref16=ref16)
        total_bytes_sent += len(pcm)  # P0 时序安全: 累计已发字节(估算播放时长)
    else:
        # cache miss: synth_to_pcm(stream=False 整句)。实测 CosyVoice v1 stream=True 返整句且更慢
        # (.126 rtf 3.75 vs stream=False ~1.76), 无真首包优先(库限制) → 用 stream=False 更快,
        # 整句合成完分块下发。真首包优先靠 D 方案分句流式(长句首包 8s→1.7s)。
        # D 方案: 长句(>20字且有标点)分句流式, 首子句先合成先发(首包大降); 短句不分句走整句。
        # cache hit 整句不分句(见上 if cache_hit 分支, 0ms 直接发)。
        # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 分句共用同一 tts_id(只在最后一句 PCM 发完后 ACK 一次)
        t0 = time.time()
        is_long = len(text) > 20 and bool(_re.search(r'[。！？；,，!?;]', text))
        if is_long:
            sents = split_sentences(text)
        else:
            sents = [text]
        seq = 0
        first_pkt_ms = None
        total_pcm = bytearray()
        for idx, s in enumerate(sents):
            if sess is not None and sess.barge_stop:
                break  # barge-in: 停止后续子句合成下发
            pcm = await asyncio.to_thread(synth_to_pcm, s)
            total_pcm.extend(pcm)
            ref16 = tts_to_16k_ref(pcm) if (use_aec or aec_capture.enabled()) else None
            # 流式跨子句: start_seq/ts 连续递增, first_pkt_t0=t0 记首包(首子句首块下发时刻)
            seq, fp = await send_pcm_streaming(
                ws, pcm, sess=sess, zero_delay=False, ref16=ref16,
                start_seq=seq, ts=int(time.time() * 1000), first_pkt_t0=t0)
            total_bytes_sent += len(pcm)  # P0 时序安全: 累计已发字节
            if first_pkt_ms is None and fp is not None:
                first_pkt_ms = fp
            # 子句动态缓存(单独存, 命中后整句子句 0 延迟)
            # 28-P0-CLEANUP-BYARCH P0-1: TTS_CACHE_ENABLED=0 时不写 cache(bypass 模式禁用动态缓存)
            if len(pcm) > 0 and TTS_CACHE_ENABLED:
                TTS_CACHE[s] = pcm
        # 整句也缓存(下次命中走 cache hit 整句分支, 不分句)
        if len(total_pcm) > 0 and TTS_CACHE_ENABLED:
            TTS_CACHE[text] = bytes(total_pcm)
        dur = len(total_pcm) / TTS_RATE / 2
        elapsed = time.time() - t0
        print(f"[tts] INT8 合成 {dur:.2f}s 用时 {elapsed:.2f}s (rtf={elapsed / dur if dur > 0 else 0:.2f})", flush=True)
        print(f"[tts] first_pkt ms={first_pkt_ms} text_len={len(text)} split={len(sents) if is_long else 0}", flush=True)

    # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: tts_end 带 send_complete=true + 音频元数据
    # 明确语义: send_complete(server PCM 发完) ≠ playback complete(client 扬声器播完)
    audio_duration_sec = total_bytes_sent / (TTS_RATE * 2) if total_bytes_sent > 0 else 0.0
    total_frames = total_bytes_sent // 2  # mono S16: bytes/2 = samples(frames)
    send_elapsed_ms = (time.monotonic() - send_start_mono) * 1000
    await ws.send(json.dumps({
        "type": "tts_end", "tts_id": tts_id,
        "send_complete": True,
        "total_bytes": total_bytes_sent,
        "total_frames": total_frames,
        "audio_duration_ms": int(audio_duration_sec * 1000),
        "sample_rate": TTS_RATE, "channels": 1, "sample_width": 16,
        # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 会话归属(client 用于旧 tts_end 迟到校验)
        "session_id": tts_session_id,
        "session_epoch": tts_session_epoch,
        "utterance_id": tts_utterance_id,
        "scope": tts_scope,
    }))

    if sess is None:
        print(f"[tts] 发完 {seq} 帧 (sess=None, 无 ACK 协议)", flush=True)
        return

    # ===== FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 状态推进分三模式 =====
    sess.speaking = False
    if sess.barge_stop:
        # barge 中断(server VAD 检测到用户说话): 回 LISTENING; 不等 ACK
        # 注意: client 还会发 playback_interrupted(reason=barge_in), 此处先转 LISTENING 不阻塞
        _reset_active_tts(sess)
        sess.state = 'LISTENING'
        sess.aec_mic.clear()
        sess.listening_idle_frames = 0
        print(f"[tts] barge 中断(发 {seq} 帧) → LISTENING (不等 ACK)", flush=True)
        return

    # 正常播完 → GUARD(等待真实播放结束)
    sess.state = 'GUARD'
    _cancel_followup_task(sess)
    playback_remaining = max(0.0, audio_duration_sec - (time.monotonic() - send_start_mono))

    if PLAYBACK_ACK_MODE == 'ack':
        # ack 模式: 等 client playback_complete/interrupted(权威), 不跑旧 GUARD 估算
        sess.tts_send_complete = True
        sess.tts_total_bytes = total_bytes_sent
        sess.tts_audio_duration_ms = audio_duration_sec * 1000
        sess.playback_ack_wait_started = time.monotonic()
        sess.playback_terminal_received = False
        # 超时兜底 task(防 ACK 丢失卡死): audio_dur + PLAYBACK_ACK_TIMEOUT_EXTRA_MS
        sess.playback_ack_timeout_task = asyncio.create_task(
            _playback_ack_timeout(ws, sess, tts_id, after_state))
        print(f"[PLAYBACK] START tts_id={tts_id} session={sess.session_id} "
              f"event=SEND_COMPLETE total_bytes={total_bytes_sent} "
              f"audio_duration_ms={int(audio_duration_sec*1000)} send_elapsed_ms={int(send_elapsed_ms)} "
              f"after_state={after_state} mode=ack (等待 playback_complete/interrupted, "
              f"超时={int(audio_duration_sec*1000)+PLAYBACK_ACK_TIMEOUT_EXTRA_MS}ms)", flush=True)
        print(f"[tts] 发完 {seq} 帧 → GUARD(ack 模式, 等 client playback ACK)", flush=True)
        return

    # legacy / shadow 模式: 沿用 GUARD 估算(playback_remaining + POST_TTS_GUARD*0.02)
    guard_delay = playback_remaining + POST_TTS_GUARD * 0.02
    sess.followup_task = asyncio.create_task(_guard_then_idle(ws, sess, guard_delay, after_state))
    print(f"[tts_playback] bytes={total_bytes_sent} audio_dur={audio_duration_sec:.2f}s playback_remaining={playback_remaining:.2f}s guard_delay={guard_delay:.2f}s (SEND_DELAY_MS={SEND_DELAY_MS})", flush=True)
    print(f"[tts_playback_metric] event=expected_playback_complete send_start_mono={send_start_mono:.3f} audio_duration={audio_duration_sec:.3f} playback_remaining={playback_remaining:.3f}", flush=True)
    print(f"[tts] 发完 {seq} 帧 → GUARD({guard_delay*1000:.0f}ms = playback_remaining {playback_remaining*1000:.0f}ms + POST_TTS_GUARD {POST_TTS_GUARD*20}ms)", flush=True)

    if PLAYBACK_ACK_MODE == 'shadow':
        # shadow: 记录等待态 + 建 ACK 超时 task 仅用于"超时统计"(不影响状态机, 状态走 GUARD)
        sess.tts_send_complete = True
        sess.tts_total_bytes = total_bytes_sent
        sess.tts_audio_duration_ms = audio_duration_sec * 1000
        sess.playback_ack_wait_started = time.monotonic()
        sess.playback_terminal_received = False
        # 注意: shadow 不建 _playback_ack_timeout task(不推进状态, 只收 ACK 记录)
        print(f"[PLAYBACK] START tts_id={tts_id} session={sess.session_id} "
              f"event=SEND_COMPLETE total_bytes={total_bytes_sent} "
              f"audio_duration_ms={int(audio_duration_sec*1000)} send_elapsed_ms={int(send_elapsed_ms)} "
              f"after_state={after_state} mode=shadow (状态走 legacy GUARD, 仅记录 ACK)", flush=True)


async def go_idle(ws, sess, reason="FOLLOW_UP_TIMEOUT"):
    """P0-1 + V1.1 §12: 会话结束统一出口 —— apm=None 释放重资源 + 新协议事件(session_closed/avatar_state IDLE)
    + 保留 state_change(IDLE) 兼容旧 client + 重置会话计数。
    触发点: TTS 播完 GUARD 结束 / LISTENING FOLLOWUP 超时。模型(ASR/CosyVoice)不动, 仅 APM 启停。
    reason: 会话关闭原因(FOLLOW_UP_TIMEOUT 默认; 可扩展 SESSION_CLOSING 等)。"""
    # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 代际切换 + cancel 旧 ASR
    _bump_session_epoch(sess, reason=f"go_idle:{reason}")
    sess.state = 'IDLE'
    sess.apm = None
    sess.aec_mic.clear()
    sess.frames = 0  # 会话帧计数归零(下次 wake 首帧重设 t0); 避免跨会话累积误导日志
    sess.voice_frames = 0
    sess.silence_frames = 0
    sess.listening_frames = 0
    sess.listening_idle_frames = 0
    sess.guard_frames = 0
    sess.skip_frames = 0
    sess.pending_mic = bytearray()  # codex P1: 清跨会话残留
    sess.processing_start_frames = 0  # codex P1: PROCESSING 超时计时
    # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 清 analysis_builder(与 apm 同生命周期)
    sess.analysis_builder = None
    sess.shadow_analysis_builder = None
    sess._cur_analysis_info = None
    _reset_utterance(sess)  # 2026-07-26: 会话结束清句级状态, 下次 wake 干净起点
    sess.speaking = False
    sess.barge_stop = False
    # 26-DIALOGUE-INTEGRATION: §10.7 会话结束清上下文(防上一位客人污染下一位)。
    sess.dialogue_state = None
    # 28-P0-CLEANUP-BYARCH B2.3: §10.7 不跨会话 —— 清 fallback cooldown 计数(下次 wake 重新计)。
    sess.fallback_count = 0
    sess.last_fallback_text = ''
    sess.last_fallback_ts = 0.0
    # 40-WAKE-FIRST-ACK-GUARANTEE Medium-1: 防御性清(wake 会重置, go_idle 也清防残留)
    sess.wake_first_asr_done = False
    # V1.1 §5: 清 FOLLOW_UP_WAIT 计时器(防遗留 task 跨会话触发)
    _cancel_followup_task(sess)
    # 60-WAKE-ACK-TIMEOUT: 清 wake 超时定时器(防 IDLE 后误播 WAKE_ACK)
    _cancel_wake_ack_timeout(sess)
    # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 清 ACK 等待状态(防跨会话残留 + 旧 tts 迟到 ACK 误推新会话)
    _reset_active_tts(sess)
    try:
        # V1.1 §12.5/§12.2: 先 session_closed(client 缩小动画) 再 avatar_state(IDLE)(灭灯), 保留 state_change 兼容
        await ws.send(json.dumps({"type": "session_closed", "reason": reason}))
        await ws.send(json.dumps({"type": "avatar_state", "state": "IDLE", "reason": reason}))
        await ws.send(json.dumps({"type": "state_change", "state": "IDLE"}))
        print(f"[sess] → IDLE (apm=None, session_closed/{reason}, 已通知 client 回 KWS)", flush=True)
    except Exception as e:
        print(f"[sess] go_idle 协议事件发送失败: {e}", flush=True)


def _cancel_followup_task(sess):
    """V1.1 §9.1: 取消 FOLLOW_UP_WAIT wallclock 定时器(VAD 起点暂停超时 / 会话切换)。"""
    task = getattr(sess, 'followup_task', None)
    if task is not None and not task.done():
        task.cancel()
    sess.followup_task = None


# ===== 60-WAKE-ACK-TIMEOUT: wake 超时兜底寒暄工具函数 =====

def _cancel_wake_ack_timeout(sess):
    """60-WAKE-ACK-TIMEOUT: 取消 wake 超时定时器。
    触发点: ASR listen/短文本/reply/tool 命中(已播 WAKE_ACK 或进命令) / go_idle / 新 wake 覆盖。
    幂等: task 已 None/done 时直接清字段, 不报错。"""
    task = getattr(sess, 'wake_ack_timeout_task', None)
    if task is not None and not task.done():
        task.cancel()
    sess.wake_ack_timeout_task = None
    sess.wake_ack_timeout_epoch = -1


async def _wake_ack_timeout_fire(ws, sess, captured_epoch):
    """60-WAKE-ACK-TIMEOUT: wake 后超时兜底播 WAKE_ACK。
    独立于 ASR: 即使 VAD 不端点/ASR 不触发, 超时也保证寒暄(解"唤醒灯亮但没寒暄")。
    安全门(全过才播, 任一失败静默退出):
      1. flag on(运行中关 flag 不影响已起 task, 但新 wake 不再起)
      2. 代际未变(防 wake/go_idle/disconnect 后误播)
      3. state == LISTENING(已进 PROCESSING/SPEAKING 说明命令在跑, 不打扰)
      4. not wake_first_asr_done(ASR 已播过 WAKE_ACK 或已进命令, 不重复)
      5. wake_ack_timeout_task 仍指向本 task(防被新 wake 的 task 替换后旧 task 误播)
    取消(Wake+Command/纯唤醒词/go_idle/重 wake)走 _cancel_wake_ack_timeout, 不会进本协程播。"""
    try:
        await asyncio.sleep(WAKE_ACK_TIMEOUT_MS / 1000.0)
    except asyncio.CancelledError:
        # 正常取消路径(Wake+Command 命中/纯唤醒词/go_idle/重 wake); 不当错误
        return
    # ===== 安全门 5 道 =====
    if not WAKE_ACK_TIMEOUT_V31:
        return
    if sess is None:
        return
    if sess.session_epoch != captured_epoch:
        return  # 代际已变(重 wake/go_idle/disconnect), 不属本定时器职责
    if sess.state != 'LISTENING':
        return  # 已进 PROCESSING/SPEAKING(命令在跑), 不打扰
    if getattr(sess, 'wake_first_asr_done', False):
        return  # ASR 已播 WAKE_ACK 或已进命令分支, 不重复
    if getattr(sess, 'wake_ack_timeout_task', None) is not asyncio.current_task():
        return  # 本 task 已被新 wake 替换(新 task 负责新轮次), 旧 task 退出
    # ===== 兜底播 WAKE_ACK(走现有 synthesize_and_send, 复用 bypass/ack/cache/cooldown) =====
    _wake_ack_text = _WAKE_ACK_DEFAULT
    print(f"[60-wake-ack-timeout] wake 后 {WAKE_ACK_TIMEOUT_MS}ms 无命令且 ASR 未触发 "
          f"→ 兜底播 WAKE_ACK text={_wake_ack_text!r} epoch={captured_epoch} "
          f"(VAD 不端点/ASR 不触发的寒暄兜底, after_state=LISTENING)", flush=True)
    sess.wake_first_asr_done = True  # 防后续 ASR listen 分支重复播
    try:
        await synthesize_and_send(ws, _wake_ack_text, sess, after_state='LISTENING')
    except Exception as _e:
        print(f"[60-wake-ack-timeout] synthesize_and_send 异常: {_e}", flush=True)
    finally:
        # 播完清本 task 句柄(防残留)
        if getattr(sess, 'wake_ack_timeout_task', None) is asyncio.current_task():
            sess.wake_ack_timeout_task = None
        sess.wake_ack_timeout_epoch = -1


# ===== FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 播放 ACK 协议工具函数 =====

def _gen_tts_id(sess):
    """生成唯一 tts_id: tts_<session_id短>_<seq_hex>。每个 session 内单调递增。"""
    sess.tts_seq_counter += 1
    sid = (sess.session_id or 'sess')[-8:]
    return f"tts_{sid}_{sess.tts_seq_counter:04x}"


def _cancel_playback_ack_timeout(sess):
    """取消 ACK 超时兜底 task(ACK 到达/中断/会话切换时调用)。"""
    t = getattr(sess, 'playback_ack_timeout_task', None)
    if t is not None and not t.done():
        t.cancel()
    sess.playback_ack_timeout_task = None


def _reset_active_tts(sess):
    """清理当前 tts 的 ACK 等待状态(终态处理完 / 新 tts 开始 / 会话切换)。
    不动 followup_task(GUARD/FOLLOWUP 走独立管理)。"""
    _cancel_playback_ack_timeout(sess)
    fut = getattr(sess, 'playback_ack_future', None)
    if fut is not None and not fut.done():
        fut.cancel()
    sess.playback_ack_future = None
    sess.active_tts_id = None
    sess.active_tts_after_state = 'IDLE'
    sess.tts_send_complete = False
    sess.tts_total_bytes = 0
    sess.tts_audio_duration_ms = 0.0
    sess.playback_terminal_received = False
    sess.playback_ack_wait_started = None


async def _advance_after_playback(ws, sess, after_state, reason=""):
    """FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727:
    playback_complete(ack 模式)收到后 → 等 POST_PLAYBACK_TAIL_MS → 进 after_state。
    与 _guard_then_idle 等价分支复用(LISTENING→session_waiting; IDLE→go_idle), 仅入口不同。
    reason 写入日志用于区分 ACK_COMPLETE / ACK_TIMEOUT 兜底。"""
    try:
        if POST_PLAYBACK_TAIL_MS > 0:
            await asyncio.sleep(POST_PLAYBACK_TAIL_MS / 1000.0)
    except asyncio.CancelledError:
        return
    if sess.state != 'GUARD':
        # 状态已被改(barge_in/新 tts/go_idle) → 不推进
        print(f"[PLAYBACK] STATE_TRANSITION aborted reason={reason} cur_state={sess.state} "
              f"(after_state 目标 {after_state} 未执行)", flush=True)
        return
    if after_state == 'LISTENING':
        sess.state = 'LISTENING'
        sess.silence_frames = 0
        sess.listening_idle_frames = 0
        await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "FOLLOW_UP_WAIT"}))
        await ws.send(json.dumps({"type": "session_waiting",
                                  "timeout_ms": FOLLOWUP_WAIT_MS, "started_at": int(time.time() * 1000)}))
        await ws.send(json.dumps({"type": "state_change", "state": "LISTENING"}))
        print(f"[PLAYBACK] STATE_TRANSITION → LISTENING + session_waiting reason={reason} "
              f"tail_ms={POST_PLAYBACK_TAIL_MS} (帧{sess.frames})", flush=True)
        _cancel_followup_task(sess)
        sess.followup_task = asyncio.create_task(_followup_timeout(ws, sess))
    else:
        print(f"[PLAYBACK] STATE_TRANSITION → go_idle reason={reason} "
              f"tail_ms={POST_PLAYBACK_TAIL_MS} (帧{sess.frames})", flush=True)
        await go_idle(ws, sess)


async def _playback_ack_timeout(ws, sess, tts_id, after_state):
    """FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: ACK 丢失兜底。
    超时 = audio_duration_ms + PLAYBACK_ACK_TIMEOUT_EXTRA_MS。超时后:
      - 标终态(防迟到 ACK 再次触发)
      - 用 audio_duration 估算兜底恢复 after_state(不卡死)
      - 日志 PLAYBACK_ACK_TIMEOUT
    注意: 超时仅在 ack 模式生效; legacy/shadow 不创建此 task。"""
    try:
        await asyncio.sleep((sess.tts_audio_duration_ms + PLAYBACK_ACK_TIMEOUT_EXTRA_MS) / 1000.0)
    except asyncio.CancelledError:
        return
    if sess.playback_terminal_received:
        return  # 已收 ACK 正常处理
    if sess.active_tts_id != tts_id:
        return  # 已被新 tts/barge 替换, 不是本次超时
    ack_wait_ms = (time.monotonic() - sess.playback_ack_wait_started) * 1000 if sess.playback_ack_wait_started else 0
    print(f"[PLAYBACK] ACK_TIMEOUT tts_id={tts_id} session={sess.session_id} "
          f"total_bytes={sess.tts_total_bytes} audio_duration_ms={sess.tts_audio_duration_ms:.0f} "
          f"ack_wait_ms={ack_wait_ms:.0f} after_state={after_state} "
          f"reason=ack_lost_fallback_to_after_state", flush=True)
    sess.playback_terminal_received = True  # 防迟到 ACK
    # 超时兜底: 直接走 _advance_after_playback(audio_duration 估算已计入 sleep, tail 再加)
    await _advance_after_playback(ws, sess, after_state, reason="ACK_TIMEOUT")


async def _guard_then_idle(ws, sess, delay_s, after_state='IDLE'):
    """GUARD 保护期(wallclock)到 → go_idle 或回 LISTENING。
    V1.1 §4.2/§12: after_state=LISTENING(寒暄/命令回复后保持会话) → 转 LISTENING 并发 session_waiting(10s)
      + avatar_state(ACTIVE)(灯保持亮); after_state=IDLE → go_idle(发 session_closed/avatar_state IDLE)。
    该 task 存 sess.followup_task, VAD 起点可 cancel(标准 §9.1: 用户说话暂停超时)。
    注意: GUARD wallclock 后才进 LISTENING 开始 FOLLOWUP; FOLLOWUP 超时本身是帧驱动(listening_idle_frames),
      此 task 仅是 GUARD→LISTENING 的过渡定时器, 转完后 LISTENING 的超时由帧驱动接管。"""
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    if sess.state == 'GUARD':
        if after_state == 'LISTENING':
            sess.state = 'LISTENING'
            sess.silence_frames = 0
            sess.listening_idle_frames = 0
            # V1.1 §12.2/§12.3: 进 LISTENING(FOLLOW_UP_WAIT 起点) → 灯保持亮 + 发 session_waiting(10s)
            await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "FOLLOW_UP_WAIT"}))
            await ws.send(json.dumps({"type": "session_waiting",
                                      "timeout_ms": FOLLOWUP_WAIT_MS, "started_at": int(time.time() * 1000)}))
            await ws.send(json.dumps({"type": "state_change", "state": "LISTENING"}))  # 兼容旧 client
            print(f"[guard] 保护期结束 → LISTENING + session_waiting({FOLLOWUP_WAIT_MS}ms) (帧{sess.frames}, 等命令)", flush=True)
            _cancel_followup_task(sess)
            sess.followup_task = asyncio.create_task(_followup_timeout(ws, sess))  # FOLLOWUP wallclock 10s(回声 voice 不 cancel)
        else:
            print(f"[guard] 保护期结束({delay_s*1000:.0f}ms 定时器) → go_idle (帧{sess.frames})", flush=True)
            await go_idle(ws, sess)


async def _followup_timeout(ws, sess):
    """根治: FOLLOW_UP_WAIT wallclock(不靠 VAD voice/idle) — 固定 FOLLOWUP_WAIT_MS → go_idle。
    回声/环境声 voice=True 不 cancel(区别于 listening_idle_frames 被 voice 清); 用户说话通过 ASR(reply/tool) → synthesize_and_send → _guard_then_idle 重设此 task。"""
    try:
        await asyncio.sleep(FOLLOWUP_WAIT_MS / 1000)
    except asyncio.CancelledError:
        return
    if sess.state in ('LISTENING', 'FOLLOW_UP_WAIT'):
        print(f"[followup] {FOLLOWUP_WAIT_MS}ms wallclock 超时 → go_idle (帧{sess.frames})", flush=True)
        await go_idle(ws, sess, reason="FOLLOW_UP_TIMEOUT")


def _bump_on_barge_in(sess):
    """FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: barge_in 代际切换专用包装。
    barge_in 后 state=LISTENING(新轮次), 必须确保旧 ASR(若在跑)不再污染新轮次。
    go_idle 的 bump 在 LISTENING 路径上不调用; barge_in 单独 bump。"""
    _bump_session_epoch(sess, reason="barge_in")


class Session:
    def __init__(self):
        # P0-1: apm 延迟建 —— IDLE 不占重资源(AEC/NS/AGC/VAD), wake 时 make_apm(), go_idle 时 None。
        # 模型(ASR/CosyVoice)全局常驻不卸载, 仅 APM 按会话启停。
        self.apm = None
        self.device_sn = None
        self.session_id = None
        self.aec_mic = bytearray()  # AEC 后 mic 缓冲(ASR 用)
        self.frames = 0
        self.t0 = time.time()
        self.pending = {}  # req_id -> (ok_text, fail_text)
        # 会话状态机: IDLE(本地KWS,不上行)→wake→LISTENING→PROCESSING→SPEAKING→GUARD→go_idle(IDLE)
        self.state = 'IDLE'
        self.voice_frames = 0   # 连续 voice=True 帧数
        self.silence_frames = 0  # 连续 voice=False 帧数
        self.listening_frames = 0  # LISTENING 累积帧数(防短句截断)
        self.listening_idle_frames = 0  # LISTENING 连续无 voice 帧数(FOLLOWUP 超时回 IDLE)
        # VAD-ASR 门控修复(2026-07-26): 句级状态机, 阻止纯环境声/电视声/静音端点送 ASR
        #   speech_started: 本句是否已检测到有效人声(voice=True 且 in_rms>VOICE_RMS_MIN); 端点后/wake/go_idle 重置
        #   valid_voice_frames: 本句有效人声帧数(算比例); 句级累计
        #   utterance_frames: 本句总帧数(LISTENING 期间累计, 算 valid_voice_frames/utterance_frames)
        #   说明: 端点发生在说完后的静音段, 不能要求端点时 effective=True; 用 speech_started 做句级前置门
        self.speech_started = False
        self.valid_voice_frames = 0
        self.utterance_frames = 0
        self.speaking = False   # TTS 下发中
        self.barge_stop = False  # 中断 TTS 下发
        self.guard_frames = 0   # TTS 播完后保护期帧数(防回声残留触发 ASR)
        self.skip_frames = 0    # wake 后跳过 pre-roll 帧数(防 pre-roll 静音提前触发 VAD 端点)
        # codex VAD 排队修复: PROCESSING 期间新语音隔离(pending_mic), ASR 完成回 LISTENING 时转入
        self.pending_mic = bytearray()
        self.processing_start_frames = 0  # codex P1: PROCESSING 超时计时
        # codex 帧统计: recv=收到上行帧; vad_in=喂VAD帧(skip后); last_seq=GAP检测
        self.recv_frames = 0
        self.vad_input_frames = 0
        self.last_seq = -1
        self.last_tts_text = ''  # 最近TTS文本(回声过滤用)
        self.followup_task = None  # V1.1 §9.1: FOLLOW_UP_WAIT/GUARD wallclock 定时器(VAD 起点可 cancel 暂停超时)
        # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 双路 PCM 构建器(normalized 模式用)
        # wake 时建, go_idle 时清(与 apm 生命周期一致); 端点后 reset(下一句干净起点, 保留 noise_floor 跨句平滑)
        self.analysis_builder = None
        # shadow 模式: 同时跑 analysis_builder 记录判定对比(不影响 effective)
        self.shadow_analysis_builder = None
        # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 播放 ACK 协议状态(每个 tts_id 独立生命周期)
        # active_tts_id: 当前等待 playback_complete/interrupted 的 tts_id; None=无等待
        # active_tts_after_state: 该 tts 播完后应进入的状态(IDLE/LISTENING)
        # tts_send_complete: PCM 是否已发完(tts_end 已发)
        # tts_total_bytes / tts_audio_duration_ms: 该 tts 的总字节/音频时长(估算 + 超时兜底用)
        # playback_ack_future: asyncio.Future, playback_complete/interrupted 到达时 set_result
        # playback_ack_timeout_task: 超时兜底 task(audio_dur + extra; 防 ACK 丢失卡死)
        # playback_terminal_received: 终态幂等标志(收到 complete 或 interrupted 后置 True, 拒绝后续 ACK)
        # playback_ack_wait_started: ACK 等待起点 mono(计算 ack_wait_ms 日志)
        # tts_seq_counter: tts_id 单调递增计数(每 session 独立)
        self.active_tts_id = None
        self.active_tts_after_state = 'IDLE'
        self.tts_send_complete = False
        self.tts_total_bytes = 0
        self.tts_audio_duration_ms = 0.0
        self.playback_ack_future = None
        self.playback_ack_timeout_task = None
        self.playback_terminal_received = False
        self.playback_ack_wait_started = None
        self.tts_seq_counter = 0
        # ===== FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 会话代际校验 =====
        # 场景: handle_asr 异步推理(~1s)期间, 用户重新 wake/go_idle/barge_in,
        #   同一 Session 对象 state 可能变 IDLE→LISTENING(新轮次), 旧 ASR 返回后若只查 state,
        #   误判"LISTENING 可继续" → 旧 ASR 文本进 Router/TTS → 污染新会话(状态分叉/误控设备)。
        # 方案: 每次新 wake/go_idle/barge_in/session_closed/followup_timeout/disconnect
        #   → session_epoch +1 + 清 active_utterance_id/active_asr_task_id + cancel 旧 asr task。
        #   handle_asr 入口捕获 epoch/utterance/asr_task token, 关键节点比对, 失配即 STALE_ASR_DROP。
        # epoch 是权威防线(active_asr_task.cancel 对 GPU/子进程推理可能无效, await 仍会返回)。
        self.session_epoch = 0
        self.active_utterance_id = None
        self.active_asr_task_id = None
        self.active_asr_task = None
        # 26-DIALOGUE-INTEGRATION: V3.1 多轮上下文(架构 §10)。None=未启用或不活跃。
        #   wake 时初始化(仅 flag on), go_idle 时置 None(§10.7 不跨会话)。
        #   flag off 时永远 None, query_router gate 不进入, 行为不变。
        self.dialogue_state = None
        # 28-P0-CLEANUP-BYARCH B2.3: B2 兜底 cooldown 状态(防 ASR 幻觉回声循环)。
        #   fallback_count: 本会话兜底次数(达 MAX_FALLBACK_PER_SESSION 后转 silent)
        #   last_fallback_text: 上次兜底文本(同文本 cooldown 判定)
        #   last_fallback_ts: 上次兜底时刻 mono(FALLBACK_COOLDOWN_MS 窗口判定)
        #   go_idle 清零(§10.7 不跨会话); FALLBACK_COOLDOWN_V31=0 时无人读写, 零影响。
        self.fallback_count = 0
        self.last_fallback_text = ''
        self.last_fallback_ts = 0.0
        # 40-WAKE-FIRST-ACK-GUARANTEE: 唤醒后第一次 ASR 端点标志。
        #   wake 时置 False, 第一次 handle_asr 末尾置 True(listen/reply正常/tool 三处)。
        #   用于'唤醒后未命中规则'时降级播 WAKE_ACK(保证唤醒必有反馈, 解决问题1'有时不播')。
        #   flag off 时无人读写, 零影响。
        self.wake_first_asr_done = False
        # 60-WAKE-ACK-TIMEOUT: wake 超时兜底定时器(单次 asyncio.Task)。
        #   wake 时启动(若 flag on), 超时/WAKE_FIRST_ACK/query_router 命中/纯唤醒词/go_idle/重 wake 时取消。
        #   flag off 时永远 None, 零影响。
        self.wake_ack_timeout_task = None
        # 60-WAKE-ACK-TIMEOUT: 超时定时器捕获的 epoch(防 wake 后 epoch 变化误播)。
        self.wake_ack_timeout_epoch = -1
        # 54-PROTOCOL-V2: 客户端能力协商结果('v1' 默认; hello 时 _negotiate_protocol 设置)。
        #   v1: 走原 synthesize_and_send(cache/bypass/pipeline, 零改变);
        #   v2: 走 _synthesize_and_send_v2(V2 帧协议, 需客户端上报 protocol_version>=2 + audio_segment_v2)。
        #   flag off(TTS_AUDIO_PROTOCOL=v1 默认)时永远 'v1', 零影响。
        self._audio_protocol = "v1"


# 54-PROTOCOL-V2: 客户端能力协商(返回 'v1' 或 'v2')
def _negotiate_protocol(obj):
    """客户端能力协商。返回 'v1' 或 'v2'。

    v2 启用条件(双端, 避免老客户端收 V2 帧解析失败):
      1. 服务端 TTS_AUDIO_PROTOCOL == 'v2'
      2. 客户端 hello 上报 protocol_version >= 2
      3. 客户端 capabilities 含 'audio_segment_v2'
    任一不满足 → 'v1'(走原路径, 零改变)。
    """
    if TTS_AUDIO_PROTOCOL != "v2":
        return "v1"
    try:
        client_proto = int(obj.get("protocol_version", 1))
    except Exception:
        client_proto = 1
    caps = obj.get("capabilities", []) or []
    if client_proto >= 2 and "audio_segment_v2" in caps:
        return "v2"
    return "v1"


# ===== FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 代际切换 + 验活 =====

def _bump_session_epoch(sess, reason=""):
    """代际切换 +1: 清当前 utterance/asr_task token + cancel 旧 ASR asyncio.Task。
    触发点: wake / go_idle / session_closed / followup_timeout / WS disconnect / barge_in / 新 wake 覆盖旧轮次。
    注意: cancel 仅请求协作式中止; GPU/子进程推理可能不可真正中止, await 仍返回 → epoch 校验是权威防线。
    """
    sess.session_epoch += 1
    sess.active_utterance_id = None
    sess.active_asr_task_id = None
    # cancel 旧 ASR task(若在跑); await 方可能仍返回, epoch 校验兜底
    old_task = getattr(sess, 'active_asr_task', None)
    if old_task is not None and not old_task.done():
        try:
            old_task.cancel()
        except Exception:
            pass
    sess.active_asr_task = None
    if reason:
        print(f"[epoch] bump → {sess.session_epoch} reason={reason} session={sess.session_id}", flush=True)


def _gen_utterance_id(sess):
    """生成唯一 utterance_id: utt_<session_id短>_<epoch>_<seq>。"""
    if not hasattr(sess, '_utt_seq'):
        sess._utt_seq = 0
    sess._utt_seq += 1
    sid = (sess.session_id or 'sess')[-8:]
    return f"utt_{sid}_{sess.session_epoch:04x}_{sess._utt_seq:04x}"


def is_current_asr_turn(sess, captured_session_id, captured_epoch,
                        captured_utterance_id, captured_asr_task_id):
    """FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 检查 ASR 结果是否属于当前活动轮次。

    权威校验: session_id + session_epoch + utterance_id 必须全匹配当前 sess;
    active_asr_task_id 宽松匹配(None 时通过, 代表 bump 后未起新 task)。
    另外拒绝 IDLE/SESSION_CLOSING/None 态(代际虽匹配, 但会话已不在活动 ASR 路径)。

    返回 True = 当前轮次有效; False = 旧轮次 stale, 调用方应 STALE_ASR_DROP。
    """
    return (
        sess is not None
        and sess.session_id == captured_session_id
        and sess.session_epoch == captured_epoch
        and sess.active_utterance_id == captured_utterance_id
        and (sess.active_asr_task_id is None or sess.active_asr_task_id == captured_asr_task_id)
        and sess.state not in ('IDLE', 'SESSION_CLOSING', None)
    )


VAD_START = 3      # codex P0-3: 容忍 mic 间歇(in=19/6364交替), 3帧(60ms)进 LISTENING
VAD_END = 30       # codex 建议: 600ms(30帧), 800ms 偏慢, 500ms 临界字间停顿
MIN_LISTENING = 30  # LISTENING 最小 30 帧(600ms)才允许端点(防短句被截断成片段)
BARGE_FRAMES = 5   # SPEAKING 时连续 5 帧 voice(100ms) 触发 barge-in
VOICE_RMS_MIN = 1500  # 日志数据驱动: 电视声 in_rms median684/p75 685/p95 1338, 真说话4129; 1000 过滤电视主体保留远场命令, 电视偶尔大声(1338+)由短文本过滤兜底
# ===== VAD-ASR 门控参数(2026-07-26 修复, 环境声/电视声/静音不端点不送 ASR) =====
# 句级状态机: speech_started(本句已检测有效人声) + valid_voice_frames(有效人声帧数) 才允许静音端点
#   有效人声 = webrtc VAD voice=True 且 in_rms > VOICE_RMS_MIN(沿用现有 effective 判定, 不重复造轮子)
MIN_VALID_FRAMES = int(os.environ.get('MIN_VALID_FRAMES', '5'))  # 本句至少 N 帧有效人声才允许端点(5帧=100ms; 离线验证: 电话漏音0端点, 电视声38→7端点, 真实命令ratio=0.60不受影响)
MIN_VALID_RATIO = float(os.environ.get('MIN_VALID_RATIO', '0.15'))  # 有效人声帧/本句总帧 >= 比例才允许端点(0.15 区分连续说话 vs 环境声间歇)
# ASR 前门控(VAD 端点后送 recognize 前二次校验)
ASR_MIN_RMS = int(os.environ.get('ASR_MIN_RMS', '800'))  # PCM 整体 RMS 下限(电视声 p95=1338 但中位 684, 真说话 4129; 800 区分静音/极弱环境声)
ASR_MIN_PCM_SAMPLES = int(os.environ.get('ASR_MIN_PCM_SAMPLES', '4800'))  # 最短有效语音样本数(4800=0.3s@16k, 短于不送 ASR)
# ===== FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 方案 D 双路 PCM Feature Flag =====
# 客户端 Ring 改 Raw PCM(无 AGC)后, VOICE_RMS_MIN=1500(基于 AGC 后标定)不再匹配 Raw PCM 电平。
# 方案 D: 服务端保留 raw_pcm, 生成 analysis_pcm 副本(整句统一增益), VAD/ASR 用 analysis_pcm。
#
# AUDIO_LEVEL_GATE_MODE:
#   legacy     = 当前生产逻辑(VOICE_RMS_MIN 固定; 不动 raw)
#   shadow     = legacy 执行 + 新归一化/VAD 只记录判定对比(不影响结果; 离线/灰度验证用)
#   normalized = 启用 analysis_pcm + 自适应阈值 + 新 ASR 前门控(替换 legacy)
#
# 生产默认 legacy(等 shadow 离线验证 + 用户批准才切 normalized)。
AUDIO_LEVEL_GATE_MODE = os.environ.get('AUDIO_LEVEL_GATE_MODE', 'legacy')
if AUDIO_LEVEL_GATE_MODE not in ('legacy', 'shadow', 'normalized'):
    print(f"[audio-gate] AUDIO_LEVEL_GATE_MODE={AUDIO_LEVEL_GATE_MODE!r} 非法, 回退 legacy", flush=True)
    AUDIO_LEVEL_GATE_MODE = 'legacy'
print(f"[audio-gate] AUDIO_LEVEL_GATE_MODE={AUDIO_LEVEL_GATE_MODE} "
      f"(legacy=固定阈值, shadow=双轨只记录, normalized=analysis_pcm 归一化)", flush=True)
POST_TTS_GUARD = 50  # TTS 播完后保护期(50*0.02=1s; 期间 voice 不触发 ASR 防回声残留)
# ===== FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: 播放完成 ACK 协议 Feature Flag =====
# server tts_end(PCM 发完) ≠ client 扬声器播完。引入 client → server 的 playback_complete/interrupted
# ACK, 让 server 按真实播放进度推进状态机, 替代固定 GUARD 估算。
#
# PLAYBACK_ACK_MODE:
#   legacy = 现有 POST_TTS_GUARD 估算逻辑(生产默认, 不变)
#   shadow = 服务端发完整协议字段 + 接收并记录 ACK, 但状态机仍走 legacy GUARD(灰度观察用)
#   ack    = playback_complete/interrupted 权威; ACK 正常时不跑旧 GUARD; 超时兜底恢复 after_state
# 红线: 生产默认 legacy。先 shadow 离线/真机验证 ACK 时序正确, 再切 ack。
PLAYBACK_ACK_MODE = os.environ.get('PLAYBACK_ACK_MODE', 'legacy')
if PLAYBACK_ACK_MODE not in ('legacy', 'shadow', 'ack'):
    print(f"[playback-ack] PLAYBACK_ACK_MODE={PLAYBACK_ACK_MODE!r} 非法, 回退 legacy", flush=True)
    PLAYBACK_ACK_MODE = 'legacy'
# ack 模式: playback_complete 后额外等 POST_PLAYBACK_TAIL_MS 声学尾部(扬声器尾音进 mic + 自然接话语境)
POST_PLAYBACK_TAIL_MS = int(os.environ.get('POST_PLAYBACK_TAIL_MS', '200'))
# ack 模式: ACK 丢失超时 = audio_duration_ms + PLAYBACK_ACK_TIMEOUT_EXTRA_MS(兜底防卡死)
PLAYBACK_ACK_TIMEOUT_EXTRA_MS = int(os.environ.get('PLAYBACK_ACK_TIMEOUT_EXTRA_MS', '3000'))
print(f"[playback-ack] PLAYBACK_ACK_MODE={PLAYBACK_ACK_MODE} tail_ms={POST_PLAYBACK_TAIL_MS} "
      f"timeout_extra_ms={PLAYBACK_ACK_TIMEOUT_EXTRA_MS} "
      f"(legacy=固定GUARD, shadow=接收记录ACK, ack=ACK权威)", flush=True)
USE_AEC = os.environ.get('USE_AEC', '0') == '1'  # AEC 开关(酒店贴电视有回声时 =1); 近场 HK-MIC 默认关
# D 方案: 发送节流可配(默认 20ms 兼容 AEC; USE_AEC=0 可降到 10/5ms).
#   - USE_AEC=0: REF 同步代码不执行, 20ms 是纯负担 → 灰度降 10/5ms
#   - USE_AEC=1: REF 同步依赖 20ms 节奏喂 process_reverse_stream, 去节流会失步 → 强制 20ms
SEND_DELAY_MS = float(os.environ.get('SEND_DELAY_MS', '20'))
if USE_AEC and SEND_DELAY_MS < 20:
    print(f"[send-strategy] USE_AEC=1 强制 SEND_DELAY_MS=20 (req={SEND_DELAY_MS}, REF 同步保护)", flush=True)
    SEND_DELAY_MS = 20.0
SEND_DELAY_SEC = SEND_DELAY_MS / 1000.0
print(f"[send-strategy] SEND_DELAY_MS={SEND_DELAY_MS} USE_AEC={int(USE_AEC)}", flush=True)

# 62-DROPOUT Top1: rtf 滑动窗口(SEND_DELAY_ADAPTIVE_V31=on 时用)。
#   Worker :8767 每次合成 rtf(synth_ms/audio_dur_ms)回报 → 更新窗口;
#   send_pcm_streaming 读窗口均值, rtf>1 时降节流(SEND_DELAY_MS×ADAPTIVE_FACTOR), rtf<=1 正常节流。
#   线程安全: 单线程 asyncio, 无锁。窗口 deque(maxlen=8) 保留最近 8 次 rtf。
_RTF_WINDOW = collections.deque(maxlen=8)


def _update_rtf_window(rtf):
    """Worker 回报 rtf 时调用(>0 才更新)。单线程 asyncio, 无需锁。"""
    if rtf is not None and rtf > 0:
        _RTF_WINDOW.append(rtf)


def _rtf_window_mean():
    """返回当前 rtf 窗口均值(无数据返 1.0=视为正常, 不降节流)。"""
    if not _RTF_WINDOW:
        return 1.0
    return sum(_RTF_WINDOW) / len(_RTF_WINDOW)


def _adaptive_send_delay_sec():
    """SEND_DELAY_ADAPTIVE_V31=on 时: 根据 rtf 窗口算自适应节流秒数。
    - USE_AEC=1: 强制返回 SEND_DELAY_SEC(20ms), REF 同步守护不变。
    - rtf<=1: 返回 SEND_DELAY_SEC(正常节流, 防 AudioTrack buffer 突增)。
    - rtf>1: 返回 SEND_DELAY_SEC×ADAPTIVE_FACTOR(降节流, 让 server 发得快跟上 client 播放消费)。
    flag off: 返回 SEND_DELAY_SEC(原行为)。
    """
    if (not SEND_DELAY_ADAPTIVE_V31) or USE_AEC:
        return SEND_DELAY_SEC
    mean_rtf = _rtf_window_mean()
    if mean_rtf > 1.0:
        return SEND_DELAY_SEC * SEND_DELAY_ADAPTIVE_FACTOR
    return SEND_DELAY_SEC
USE_BARGE = USE_AEC  # barge-in 需 AEC 消回声(仅 USE_AEC=1 时启用)
BARGE_RMS_MIN = 1500  # barge-in 需 in_rms > 1500(AEC 后用户说话; 回声应已被 AEC 消除)
# 标准 V1.1 §4: FOLLOW_UP_WAIT 默认 10s(TTS 结束后计时), 帧数 = MS/20; 配置化不写死
FOLLOWUP_WAIT_MS = int(os.environ.get('FOLLOWUP_WAIT_MS', '10000'))  # 默认 10000ms = 标准 §4 推荐
FOLLOWUP_WAIT = FOLLOWUP_WAIT_MS // 20  # 10000ms/20 = 500 帧
WELCOMED_DEVICES = set()  # 已欢迎过的 device_sn(hello 欢迎词只首次, 避免重连重复)
MAX_ASR_PCM = 16000 * 2 * 8


async def handler(ws):
    sess = Session()
    print(f"[connect] {ws.remote_address}", flush=True)
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                if len(msg) < 16:
                    continue
                seq, ts, direction, flags, codec, reserved = struct.unpack(">IQBBBB", bytes(msg[:16]))
                pcm = bytes(msg[16:])
                if direction == 1:  # up_mic
                    # P0-1: IDLE(apm=None) 丢弃上行 —— Android IDLE 本地 KWS 不该上行, 防御性丢弃
                    if sess.apm is None:
                        continue
                    # codex: seq 连续性检测(丢帧铁证)
                    if sess.last_seq >= 0 and seq != sess.last_seq + 1:
                        print(f"[recv GAP] expect={sess.last_seq+1} got={seq} (丢 {seq - sess.last_seq - 1} 帧)", flush=True)
                    sess.last_seq = seq
                    sess.recv_frames += 1
                    if sess.frames == 0:
                        sess.t0 = time.time()
                    in_rms = int(np.sqrt(np.mean(np.frombuffer(pcm, dtype=np.int16).astype(np.float64)**2)))
                    # codex P0: webrtc_audio_processing 严格 10ms(320B@16k S16); Android 发 20ms(640B), 拆 2×10ms
                    out_all = bytearray()
                    for off in range(0, len(pcm), 320):
                        chunk = pcm[off:off+320]
                        if len(chunk) < 320:
                            chunk = chunk + b'\x00' * (320 - len(chunk))
                        if aec_capture.enabled():
                            aec_capture.log_mic(chunk)
                        out_all.extend(sess.apm.process_stream(chunk))
                    out = bytes(out_all)
                    if sess.skip_frames <= 0:
                        sess.aec_mic.extend(out)
                    if len(sess.aec_mic) > MAX_ASR_PCM:  # 滚动窗口(最近 8s)
                        del sess.aec_mic[:len(sess.aec_mic) - MAX_ASR_PCM]
                    sess.frames += 1
                    # codex VAD 排队修复: PROCESSING 期间新语音隔离到 pending_mic(不进 aec_mic, 防串话)
                    if sess.state == 'PROCESSING':
                        sess.pending_mic.extend(out)
                        if len(sess.pending_mic) > MAX_ASR_PCM:
                            del sess.pending_mic[:len(sess.pending_mic) - MAX_ASR_PCM]
                        # codex P1: PROCESSING 超时保护(防 tool_result 丢失导致卡死)
                        if sess.processing_start_frames == 0:
                            sess.processing_start_frames = sess.frames
                        elif sess.frames - sess.processing_start_frames > 500:  # 10s 超时
                            print(f"[timeout] PROCESSING 超 10s → 强制 go_idle (帧{sess.frames})", flush=True)
                            await go_idle(ws, sess)
                            continue
                        if sess.frames % 50 == 0:
                            print(f"[mic] {sess.frames}帧 st=PROCESSING(排队中) pending={len(sess.pending_mic)//2}samps in={in_rms}", flush=True)
                        continue
                    # wake 后跳过 pre-roll(防 pre-roll 静音提前触发 VAD 端点): apm/aec_mic 照常累积(保 pre-roll 给真唤醒保字), 但不判 VAD
                    if sess.skip_frames > 0:
                        sess.skip_frames -= 1
                        if sess.frames % 50 == 0:
                            print(f"[pre_roll] 跳过帧{sess.frames} (剩{sess.skip_frames}, in={in_rms})", flush=True)
                        continue
                    voice = sess.apm.has_voice()
                    # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 双路 PCM(normalized/shadow 模式)
                    # 每帧 feed analysis_builder → 生成 analysis 帧(整句统一增益) + 自适应阈值
                    analysis_info = None
                    if AUDIO_LEVEL_GATE_MODE in ('normalized', 'shadow') and sess.analysis_builder is not None:
                        # out 是 APM 后 20ms(ns/agc 关, = raw); 同一句 Ring pre-roll + Live 用相同规则
                        analysis_info = sess.analysis_builder.feed_frame(out, bool(voice))
                    if AUDIO_LEVEL_GATE_MODE == 'shadow' and sess.shadow_analysis_builder is not None:
                        # shadow 双轨: 独立 builder 记录判定(不影响 effective)
                        _ = sess.shadow_analysis_builder.feed_frame(out, bool(voice))
                    if sess.state == 'LISTENING':
                        sess.listening_frames += 1
                        # FOLLOWUP: 连续无 voice 超 FOLLOWUP_WAIT → go_idle(回 IDLE 释放 apm, 等下次唤醒)
                        if voice:
                            sess.listening_idle_frames = 0
                        else:
                            sess.listening_idle_frames += 1
                            if sess.listening_idle_frames >= FOLLOWUP_WAIT:
                                print(f"[followup] LISTENING {FOLLOWUP_WAIT*20}ms 无语音 → go_idle (帧{sess.frames})", flush=True)
                                await go_idle(ws, sess)
                                continue
                    # GUARD 倒计时已改 _guard_then_idle wallclock 定时器(client SPEAKING 不上行 mic, 帧驱动死锁)
                    if sess.frames % 50 == 0:
                        elapsed = time.time() - sess.t0
                        _extra = ""
                        if analysis_info is not None:
                            _extra = (f" an_rms={int(analysis_info['analysis_rms'])} gain={analysis_info['gain']:.2f} "
                                      f"nf={int(analysis_info['noise_floor'])} snr={analysis_info['snr_db']:.1f} "
                                      f"thr={int(analysis_info['adaptive_threshold'])}")
                        print(f"[mic] {sess.frames}帧 st={sess.state} voice={voice} in={in_rms}{_extra}", flush=True)
                    # FIX-RAW-PCM: 每帧把 analysis_info 挂 sess(供 on_voice_frame 用; 不改函数签名)
                    sess._cur_analysis_info = analysis_info
                    await on_voice_frame(ws, sess, voice, in_rms)
                    continue
            else:
                print(f"[text] {msg[:200]}", flush=True)
                try:
                    obj = json.loads(msg)
                except Exception:
                    obj = {}
                t = obj.get('type', '')

                # P0-1: 会话握手 —— hello(C→S) 分配 session → ack; wake(C→S KWS命中) 建重资源 apm → session_active
                if t == 'hello':
                    sess.device_sn = obj.get('device_sn', '')
                    sess.session_id = f"sess_{int(time.time()*1000)}"
                    sess.state = 'IDLE'
                    # 54-PROTOCOL-V2: hello 时协商本 session 用 v1 还是 v2(默认 v1, 零改变)
                    sess._audio_protocol = _negotiate_protocol(obj)
                    print(f"[hello] device_sn={sess.device_sn} → session_id={sess.session_id} (IDLE, apm=None, proto={sess._audio_protocol})", flush=True)
                    await ws.send(json.dumps({"type": "ack", "session_id": sess.session_id}))
                    # app 启动欢迎词(after_state=IDLE 播完回 IDLE 等 KWS 唤醒; 固定句 cache 后 0 延迟)
                    if True:  # 每次 app 启动播欢迎词(用户要求, 原 WELCOMED_DEVICES 去重去掉)
                        # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 欢迎词显式 scope='welcome'
                        # (那时 sess 已分配但 state=IDLE/未 wake, scope=session 会被 client 代际门误拒)
                        asyncio.create_task(synthesize_and_send(ws, "您好，我是小智。有需要时，请说小智小智。", sess, after_state='IDLE', scope='welcome'))
                        WELCOMED_DEVICES.add(sess.device_sn)
                    continue
                if t == 'wake':
                    # KWS 本地命中 → 建重资源 apm(AEC/NS/AGC/VAD) + 进 LISTENING; 通知 Android 会话激活
                    wt0 = time.time()
                    # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 新 wake → 新代际
                    # (覆盖旧轮次 ASR: 用户重新唤醒说明上一轮作废, 旧 ASR 不能进 Router/TTS)
                    _bump_session_epoch(sess, reason="wake")
                    # 40-WAKE-FIRST-ACK-GUARANTEE: 新 wake 重置第一次 ASR 标志(新会话重新判定)
                    sess.wake_first_asr_done = False
                    sess.apm = make_apm()
                    sess.state = 'LISTENING'
                    sess.skip_frames = 20  # 连读命令保留(50吃掉'我要看电视'致回寒暄); 20=0.4s只skip唤醒词尾音  # 跳 pre-roll 1s(=Android RING_FRAMES): 唤醒词不当命令ASR(否则抢占第一次端点, 真命令丢失)
                    sess.pending_mic = bytearray()  # codex P1: 清残留
                    sess.voice_frames = 0
                    sess.silence_frames = 0
                    sess.listening_frames = 0
                    sess.listening_idle_frames = 0
                    sess.aec_mic.clear()
                    _reset_utterance(sess)  # 2026-07-26: 新会话新句, 句级状态干净起点
                    # 26-DIALOGUE-INTEGRATION: wake 建新会话上下文(架构 §10)。仅 flag on 时初始化;
                    #   flag off 时保持 None, query_router gate 不进入, 行为与历史版本一致。
                    if DIALOGUE_STATE_V31 and _DIALOGUE_STATE_AVAILABLE and sess.dialogue_state is None:
                        try:
                            sess.dialogue_state = DialogueState(
                                session_id=sess.session_id or "",
                                session_epoch=sess.session_epoch)
                        except Exception as _wde:
                            print(f"[dialogue_state] wake 初始化失败, 本会话禁用: {_wde}", flush=True)
                            sess.dialogue_state = None
                    elif sess.dialogue_state is not None:
                        # 代际切换后旧对象不再有效, 重建
                        sess.dialogue_state = None
                        if DIALOGUE_STATE_V31 and _DIALOGUE_STATE_AVAILABLE:
                            try:
                                sess.dialogue_state = DialogueState(
                                    session_id=sess.session_id or "",
                                    session_epoch=sess.session_epoch)
                            except Exception:
                                sess.dialogue_state = None
                    # FIX-RAW-PCM-VAD-LEVEL-MISMATCH-20260727: 建 analysis_builder(normalized/shadow 模式)
                    # 与 apm 同生命周期(wake 建, go_idle 清); frame_samples=320(20ms@16k)
                    if AUDIO_LEVEL_GATE_MODE in ('normalized', 'shadow'):
                        sess.analysis_builder = A.AnalysisPcmBuilder(320)
                        if AUDIO_LEVEL_GATE_MODE == 'shadow':
                            sess.shadow_analysis_builder = A.AnalysisPcmBuilder(320)
                        print(f"[wake] analysis_builder 已建 mode={AUDIO_LEVEL_GATE_MODE}", flush=True)
                    print(f"[wake] → LISTENING (apm 已建, 用时 {(time.time()-wt0)*1000:.0f}ms)", flush=True)
                    await ws.send(json.dumps({"type": "session_active", "session_id": sess.session_id}))
                    # V1.1 §12.2: 唤醒命中 → 灯亮(数字人/信号灯放大), reason=WAKE_DETECTED; 保留 session_active 兼容旧 client
                    await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "WAKE_DETECTED"}))
                    # 唤醒寒暄"我在，有什么可以帮助您"(~300ms, after_state=LISTENING 播完保持会话等命令): 用户反馈唤醒成功
                    # 比"我有什么可以帮助您的"寒暄短, 连读风险用 skip_frames=50+pending_mic 缓解(用户自然在嗯。后说命令)
                    # wake 不寒暄(连读直接回命令, 避免双回复); 只唤醒由 ASR listen 分支寒暄
                    # 60-WAKE-ACK-TIMEOUT: wake 超时兜底寒暄(解"唤醒灯亮但没寒暄")。
                    #   真机铁证: VAD 持续拒端点(VOICE_TOO_WEAK) → ASR 不触发 → listen 分支/ WAKE_FIRST_ACK 失效。
                    #   定时器独立于 ASR: 1.5s 内有命令 → ASR reply/tool 分支取消定时器执行命令;
                    #                     1.5s 内纯唤醒词 → listen 分支播 WAKE_ACK 并取消定时器;
                    #                     1.5s 超时无命令 → 兜底播 WAKE_ACK。
                    if WAKE_ACK_TIMEOUT_V31:
                        _cancel_wake_ack_timeout(sess)  # 清旧轮次残留(重 wake 覆盖)
                        sess.wake_ack_timeout_epoch = sess.session_epoch
                        sess.wake_ack_timeout_task = asyncio.create_task(
                            _wake_ack_timeout_fire(ws, sess, sess.session_epoch))
                    continue

                # tool_result: Android 执行 tool 完回 result → 确认 TTS
                if t == 'tool_result':
                    req_id = obj.get('request_id', '')
                    status = obj.get('status', '')
                    confirm = sess.pending.pop(req_id, None)
                    if confirm:
                        ok_text, fail_text = confirm
                        reply = ok_text if status == 'success' else (fail_text or '执行失败，已通知前台')
                        await synthesize_and_send(ws, reply, sess)
                    continue

                # V1.1 §7: barge_in(client 发, SPEAKING 期间本地 KWS/VAD 检测用户说话) → 停 TTS 下发 + 转 LISTENING
                # 不依赖 server AEC(client 本地判断); sess.barge_stop=True 让 send_pcm_streaming 中断当前 TTS 下发
                if t == 'barge_in':
                    print(f"[barge_in] client 打断 → 停 TTS + 转 LISTENING (帧{sess.frames}, 前态={sess.state})", flush=True)
                    # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: barge_in 新代际
                    _bump_on_barge_in(sess)
                    sess.barge_stop = True  # 中断 TTS 下发(send_pcm_streaming 循环检查)
                    sess.speaking = False
                    _cancel_followup_task(sess)  # 清 GUARD task(若有)
                    # FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727: barge_in 标记当前 tts 终态=interrupted
                    # 防止 client 后续 stopPlayer 触发的 playback_interrupted 或迟到的 complete 再推进 after_state
                    if sess.active_tts_id is not None and not sess.playback_terminal_received:
                        old_tts = sess.active_tts_id
                        sess.playback_terminal_received = True
                        print(f"[PLAYBACK] ACK_INTERRUPTED tts_id={old_tts} session={sess.session_id} "
                              f"event=barge_in reason=barge_in after_state_aborted={sess.active_tts_after_state} "
                              f"mode={PLAYBACK_ACK_MODE}", flush=True)
                    _cancel_playback_ack_timeout(sess)
                    sess.state = 'LISTENING'
                    sess.aec_mic.clear()
                    sess.silence_frames = 0
                    sess.listening_idle_frames = 0
                    await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "BARGE_IN"}))
                    await ws.send(json.dumps({"type": "state_change", "state": "LISTENING"}))  # 兼容旧 client
                    continue

                # ===== FIX-PLAYBACK-COMPLETE-PROTOCOL-20260727 + FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: 播放完成 ACK =====
                if t == 'playback_complete':
                    ack_tts_id = obj.get('tts_id', '')
                    ack_session_epoch = obj.get('session_epoch')
                    ack_utterance_id = obj.get('utterance_id')
                    played_frames = obj.get('played_frames', -1)
                    client_mono_ms = obj.get('client_mono_ms', -1)
                    ack_wait_ms = (time.monotonic() - sess.playback_ack_wait_started) * 1000 \
                        if sess.playback_ack_wait_started else -1
                    # 校验: session+tts_id 匹配 + 未终态 + 等待态
                    if ack_tts_id != sess.active_tts_id:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=tts_id_mismatch(active={sess.active_tts_id}) "
                              f"played_frames={played_frames} client_mono_ms={client_mono_ms} "
                              f"mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: epoch 校验(防新会话收旧 ACK)
                    if ack_session_epoch is not None and ack_session_epoch != sess.session_epoch:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=epoch_mismatch(ack={ack_session_epoch} "
                              f"cur={sess.session_epoch}) played_frames={played_frames} "
                              f"mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    if sess.playback_terminal_received:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=already_terminal(重复 ACK 拒绝) "
                              f"played_frames={played_frames} mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    # 标终态(幂等: 后续重复 ACK 拒绝)
                    sess.playback_terminal_received = True
                    after_state = sess.active_tts_after_state
                    print(f"[PLAYBACK] ACK_COMPLETE tts_id={ack_tts_id} session={sess.session_id} "
                          f"event=ACK_COMPLETE total_bytes={sess.tts_total_bytes} "
                          f"audio_duration_ms={int(sess.tts_audio_duration_ms)} "
                          f"ack_wait_ms={int(ack_wait_ms)} tail_ms={POST_PLAYBACK_TAIL_MS} "
                          f"after_state={after_state} played_frames={played_frames} "
                          f"client_mono_ms={client_mono_ms} mode={PLAYBACK_ACK_MODE}", flush=True)
                    if PLAYBACK_ACK_MODE == 'ack':
                        # ack 模式: 取消超时兜底, 等 tail 后推进 after_state
                        _cancel_playback_ack_timeout(sess)
                        # 先取消旧 GUARD task(ack 模式本不创建, 但防御性)
                        _cancel_followup_task(sess)
                        sess.followup_task = asyncio.create_task(
                            _advance_after_playback(ws, sess, after_state, reason="ACK_COMPLETE"))
                    # shadow/legacy: 仅记录, 状态走旧 GUARD(不动)
                    continue

                if t == 'playback_interrupted':
                    ack_tts_id = obj.get('tts_id', '')
                    ack_session_epoch = obj.get('session_epoch')
                    ack_utterance_id = obj.get('utterance_id')
                    reason = obj.get('reason', 'unknown')
                    played_frames = obj.get('played_frames', -1)
                    client_mono_ms = obj.get('client_mono_ms', -1)
                    ack_wait_ms = (time.monotonic() - sess.playback_ack_wait_started) * 1000 \
                        if sess.playback_ack_wait_started else -1
                    # 校验: tts_id 匹配 + 未终态
                    if ack_tts_id != sess.active_tts_id:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=interrupted_tts_id_mismatch(active={sess.active_tts_id}) "
                              f"interrupted_reason={reason} played_frames={played_frames} "
                              f"mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: epoch 校验
                    if ack_session_epoch is not None and ack_session_epoch != sess.session_epoch:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=interrupted_epoch_mismatch(ack={ack_session_epoch} "
                              f"cur={sess.session_epoch}) interrupted_reason={reason} "
                              f"played_frames={played_frames} mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    if sess.playback_terminal_received:
                        print(f"[PLAYBACK] STALE_ACK tts_id={ack_tts_id} session={sess.session_id} "
                              f"event=STALE_ACK reason=interrupted_already_terminal "
                              f"interrupted_reason={reason} mode={PLAYBACK_ACK_MODE}", flush=True)
                        continue
                    sess.playback_terminal_received = True
                    aborted_after = sess.active_tts_after_state
                    print(f"[PLAYBACK] ACK_INTERRUPTED tts_id={ack_tts_id} session={sess.session_id} "
                          f"event=ACK_INTERRUPTED total_bytes={sess.tts_total_bytes} "
                          f"audio_duration_ms={int(sess.tts_audio_duration_ms)} "
                          f"ack_wait_ms={int(ack_wait_ms)} after_state_aborted={aborted_after} "
                          f"reason={reason} played_frames={played_frames} client_mono_ms={client_mono_ms} "
                          f"mode={PLAYBACK_ACK_MODE}", flush=True)
                    if PLAYBACK_ACK_MODE == 'ack':
                        # interrupted: 取消超时 + 不执行旧 after_state
                        # 注意: 若 reason=barge_in, barge_in 消息通常已先到并已转 LISTENING, 此处仅清理
                        _cancel_playback_ack_timeout(sess)
                        _cancel_followup_task(sess)
                        # 若 barge_in 消息未先到(网络乱序/client 只发 interrupted), 补转 LISTENING
                        if sess.state in ('GUARD', 'SPEAKING'):
                            sess.state = 'LISTENING'
                            sess.aec_mic.clear()
                            sess.silence_frames = 0
                            sess.listening_idle_frames = 0
                            await ws.send(json.dumps({"type": "avatar_state", "state": "ACTIVE", "reason": "PLAYBACK_INTERRUPTED"}))
                            await ws.send(json.dumps({"type": "state_change", "state": "LISTENING"}))
                    # shadow/legacy: 仅记录, 状态走旧 GUARD/barge_in 路径
                    continue

                # play_tts 仅测试用: sess=None 不改会话状态(纯播 TTS, 避免 IDLE 时 SPEAKING→GUARD 无 mic 帧驱动倒计时卡死)
                # [仅容量测试用] play_tts_text: 任意 text 触发完整 TTS 路径(cache 判定 + miss 合成)
                # 不改 synthesize_and_send 主逻辑, 只加 WS 入口供压测客户端调用
                if t == 'play_tts_text':
                    _tts_text = obj.get('text', '') or '你好，我是小智语音助手。'
                    await synthesize_and_send(ws, _tts_text, None)
                    continue
                if t == 'play_tts':
                    await synthesize_and_send(ws, '你好，我是小智语音助手。', None)
    except Exception as e:
        print(f"[err] {e}", flush=True)
    finally:
        # FIX-STALE-ASR-TTS-SESSION-LIVENESS-20260728: WS disconnect 代际切换
        # (防 reconnect 后旧 ASR 迟到结果污染新 session; 旧 task 在 sess GC 后自然消失)
        try:
            _bump_session_epoch(sess, reason="ws_disconnect")
        except Exception:
            pass
        if sess.aec_mic:
            write_wav(OUT_WAV, bytes(sess.aec_mic), 16000, 1)
        print(f"[disconnect] up_frames={sess.frames}", flush=True)


async def main():
    print(f"[server] ws://0.0.0.0:{PORT} (Day4 + 方案A: INT8 + Cache, cache_size={len(TTS_CACHE)})", flush=True)
    # 预加载 persistent ASR(避免首次 ASR 请求 6s 加载模型)
    try:
        print("[startup] 预加载 Qwen3-ASR persistent proc(~6s)...", flush=True)
        await asyncio.to_thread(_get_asr_proc)
        print("[startup] ASR 预加载完成, 首次 ASR 将~1s", flush=True)
        # 51-ASR-P95: ASR preload 成功后启动 warm keepalive daemon(默认 on, flag off 跳过)
        _start_asr_warm_thread()
    except Exception as e:
        print(f"[startup] ASR 预加载失败({e}), 将 lazy 加载", flush=True)
    # 51/63 Phase2: WS server(原) + HTTP server(:8774 候选) 并发
    # 红线: PHASE2_GATEWAY_HTTP=0 时不启 HTTP, 退化为纯 WS(与生产同形态)
    if not PHASE2_GATEWAY_HTTP:
        async with websockets.serve(handler, "0.0.0.0", PORT, max_size=None):
            await asyncio.Future()
        return
    from aiohttp import web as _aio_web
    _http_app = _phase2_build_http_app()
    _http_runner = _aio_web.AppRunner(_http_app)
    await _http_runner.setup()
    _http_site = _aio_web.TCPSite(_http_runner, "0.0.0.0", PHASE2_HTTP_PORT)
    await _http_site.start()
    print(f"[phase2] HTTP 端点已起 :{PHASE2_HTTP_PORT} "
          f"(POST /internal/knowledge/shadow-load|query|activate, GET /healthz)", flush=True)
    async with websockets.serve(handler, "0.0.0.0", PORT, max_size=None):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
