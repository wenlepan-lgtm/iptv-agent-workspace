# P0-AUDIO-PRODUCTION-TRUTH (2026-07-30)

> 生产/候选真实状态快照。新 session 第一步先核对此文件是否仍准。

## 生产（红线，勿碰）
| 项 | 值 |
|---|---|
| :8765 server.py | PID **2046242**, `v3-server.service` active |
| 代码 md5 | `9b838a0b9c2e3ec2eefebaf69233ad77`（server.py, **split_sentences 原版 early-return 已回滚**，grep "if len(parts) > 1" = 1）|
| flags | SEND_DELAY_MS=10, FACILITY_ATTRIBUTE_V31=1, SENTENCE_SPLIT_V31=1, PUNCTUATION_SEGMENT_V31=1, SENTENCE_SPLIT_MIN_SEG_SEC=1.5, TTS_BACKEND_BYPASS_8767=1, PIPELINE_PARALLEL_V31(未在env见,默认0=串行) |
| :8767 TTS worker | PID **3028389**（comboAB GPU FP32, 39-comboAB-patch-8767.py, RTF≈1.19-1.6）|

## 候选（可改）
| 项 | 值 |
|---|---|
| :8774+:8775 gateway候选 | PID **1568328**, `joctv-v31-gateway-phase2-candidate.service` active |
| 候选代码 | `/root/v3-gateway/interaction_core/server_phase2_candidate.py`, md5 `a1813f0035987d6f181733f1c20c873a`（含 P0-1/1c/1d facility 修复）|
| :8090 admin候选 | PID **1561058**, `joctv-v31-admin-candidate` active, drop-in `P4_GATEWAY_BASE_URL=http://127.0.0.1:8774` |

## 禁止
- 不覆盖正式会议屏 APK（com.joctv.mictest）。
- 不重启/改 生产 :8765（v3-server）、:8767（worker）。
- 音频修复只动候选 :8774 + 候选 APK。

## 用户真机验收失败（保持 P0/P1 FAILED）
1. 启动欢迎词播放中途停止（静音误判已排除，真因待查 §二）。
2. 长句（外滩）仍丢字断续（14 underrun, RTF>1）。
3. 本地 Wake Prompt 未验收（当前走 server TTS, 非本地）。
4. Segment 边传边播未完成（实测串行, 无预取）。
5. Client Flow Control 未完成（固定 SEND_DELAY_MS=10 sleep, 非 Credit 水位）。

## 已执行失败实验 + 回滚证据
- 实验: split_sentences 改逗号切(7段) → 丢字更严重(从第3段开始完全听不清)。
- 回滚: `cp server.py.bak-split-20260730 server.py` + `systemctl restart v3-server` → md5 回到 `9b838a0b...`(split_early_return=1)。
- 教训(已纠正): 失败的是**串行分段实现**(无预取/无流控/RTF>1), **不是标点Segment架构**。架构要求是整体(分段+并行预取+Credit流控), 不能只做切分。

## 设备
- .113 (MT9679 会议屏) adb `192.168.3.113:5555`, mictest 源码 `v3/mictest/`, APK com.joctv.mictest + com.aecprobe + com.test.aec(测试APK) 在设备。
