# P0-AUDIO-EMERGENCY-HANDOFF (2026-07-30 20:10)

> 新 session 读取本文件 + 3 个配套(PRODUCTION-TRUTH / CODE-DEVIATION-20 / OPEN-WORK-LEDGER) + 架构md, 直接继续 #12→#15。

## 当前分支 + commit
- 分支: `feature/mt9679-mic-aec-20260730`（注意: 音频恢复是新工作, 可考虑新开分支 `feature/p0-audio-recovery`）
- 最新 commit: `a4eb6e0` §一 架构定级(P0/P1 FAILED + 硬约束 + 标点规则)
- 架构文件首次入 git(v3/3.1/ 之前全 untracked), 已核实无重复(见 ARCHITECTURE-FILE-AUTHORITY-AUDIT.md)

## git 状态
- 已提交: a4eb6e0(架构md), 此前 mic 分支 5 commits(架构章节/报告74/75/AEC勘误/会话报告)
- 未提交: 本交接包(5 docs) + p0欢迎词寒暄长句bug修复.md(用户需求文档) + 其他 v3/3.1 untracked 文件

## 已纠正的两项判断(重要, 勿再犯)
1. **"逗号切7段更差 → 分段不是解药" 是错的**。正解: 失败的是**串行分段实现**(无下段预取/无Client Credit流控/Worker RTF≈1.29)→段越多段间gap越多。按标点Segment仍是 V3.1 硬架构, 必须与**并行预取+终端缓冲+Credit流控**一起实现。
2. **"整条预合成后播" 只能是临时 P0 止损**, 标 `P0_TEMPORARY_NO_DROPOUT_SAFETY_MODE`, Feature Flag 隔离, 仅候选:8774, 不进生产, 明确增加首声延迟。**不替代**最终 Segment 边传边播架构。两层并存: 安全模式止损 + Segment Flow 最终架构。

## 下一 session 第一条命令
```
cd /Users/alamn/agent
cat v3/3.1/P0-AUDIO-EMERGENCY-HANDOFF-20260730-2010.md v3/3.1/P0-AUDIO-PRODUCTION-TRUTH.md v3/3.1/P0-AUDIO-CODE-DEVIATION-20-ITEMS.md
cat v3/3.1/P0-AUDIO-OPEN-WORK-LEDGER.md
# SSH 核生产: ssh root@192.168.3.126 'bash /tmp/state.sh'
# 然后 #12: 候选 server_phase2_candidate.py 实现 P0_SAFE_BUFFER_MODE_V31
```

## 执行顺序(新 session)
#12 P0 止损(候选安全模式) → Codex 审 → 修 → #13 欢迎词早停根因+本地化 → #14 标点Segmenter → #15 预取+Credit流控+一tts_id一AudioTrack → 候选APK → Codex审 → CANDIDATE_READY_FOR_USER_TEST

## 禁止(全程)
- 覆盖正式 APK / 重启改 生产:8765(v3-server) / :8767(worker)
- 宣布 P0/P1 完成 / 丢字解决 / 欢迎词修复完成 / 分段协议完成
- 只生成报告不修代码
- 拿"分段不是解药"当结论

## 最终唯一允许的状态标签
`CANDIDATE_READY_FOR_USER_TEST`
