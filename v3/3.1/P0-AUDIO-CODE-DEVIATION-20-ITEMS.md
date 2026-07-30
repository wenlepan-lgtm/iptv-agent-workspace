# P0-AUDIO-CODE-DEVIATION-20-ITEMS (Codex 2026-07-30 独立核实)

> 20 处代码偏离架构。每项: 架构要求 / 代码位置 / 实际行为 / 严重度 / 修复 / 验收。
> 代码: .126 `/root/v3-gateway/interaction_core/server.py` + `/root/39-comboAB-patch-8767.py`。

| # | 架构要求 | 代码位置 | 实际行为 | 严重 | 修复 | 验收 |
|---|---|---|---|---|---|---|
| 1 | §20.7.2 标点(含逗号)切, 10-30字/段 | server.py split_sentences:905-908 | 多句号时 early-return `if len(parts)>1`→逗号切永不执行→只按句号切(3段) | 高 | 按所有有效标点切(排除小数/URL/缩写/日期/IP), 每段≤30字 | 1000例 join==原文, 无字数硬切 |
| 2 | §33 默认25字, >30二次切 | punctuation_segment.py:27-31 | 最大40字→本例仍33/36字长段 | 中 | 改默认25, >30二次切 | 段长分布≤30 |
| 3 | §20.7.7 段N播放时段N+1预合成(流水线) | server.py:2657-2666,2729-2743 (PIPELINE_PARALLEL_V31=0) | **串行**: 段N播完才合成段N+1 | 高(P0丢字) | 实现真流水线: 段N开始播→异步起段N+1合成; 需Worker RTF<0.9或安全模式 | segment_gap P50<200ms |
| 4 | 段间无gap, 记录 actual_gap_ms | 无 | 未验证下段首PCM在上一段播完前到达, 无gap指标 | 高 | 记录段间gap, 段N+1首PCM须在段N缓冲耗尽前到 | gap P95<400ms |
| 5 | MIN_SEG 任一段<阈值不切 | server.py SENTENCE_SPLIT_MIN_SEG_SEC | 只查第一段, 与"任一段"注释不符 | 中 | 改为查每段 | 短段不切一致 |
| 6 | §20.6 Audio Segment Protocol V2 (tts_segment_start/end 状态机) | client hello client_segment_protocol_v2=false | 走 V1, 无段级状态机 | 高 | 实现 V2 段协议 | 段连续/不乱序 |
| 7 | SHORT/NORMAL/SAFE 自适应启播 + watermark + underrun升档 | V1路径无 | 未执行启播档位/水位/underrun后升档 | 高(P0丢字) | 实现自适应缓冲(START 800ms/LOW 400/TARGET 1200/HIGH 2500/MAX 5000) | underrun=0 |
| 8 | RTF准入: RTF≥1回退整包预合成 | 无 | 无RTF准入, RTF>1仍过早启播→underrun | 高(P0) | 统计 effective_rtf_p95, ≥1或供给缺口>缓冲→完整预合成后播(#12安全模式) | RTF≥1时不丢字 |
| 9 | §20.7.7 同tts_id共享模型实例/推理上下文 | 39-comboAB每段重建WS+inference_sft, model.py:180-186每次新UUID/HiFT/Flow cache | 每段重新初始化(非native continuous), 加~0.97s首块延迟 | 高 | Worker持久会话/段级多路复用, 复用上下文 | 段间无重复init |
| 10 | 段级seed一致(音色连续) | Worker请求无segment_index/seed | 无跨段seed策略 | 中 | 传segment_index+固定seed(同tts_id) | 音色段间一致 |
| 11 | chunk_index按Segment校验 | chunk_seq_state全局递增 | 跨段全局递增, 非按段 | 中 | chunk_index按Segment重置校验 | 段内chunk连续 |
| 12 | 最后chunk标记 is_last_chunk=true | 单段bypass永远 is_last_chunk=False | 无真正最后chunk标记 | 中 | 最后PCM chunk标is_last_chunk=true | 末chunk正确 |
| 13 | 仅最终PCM标is_last | 最后段每个worker chunk标is_last_segment=True | 误标: 每chunk末20ms都标last | 中 | 只最终PCM标last | 无误标 |
| 14 | playback_complete 核对 received/written/underrun/seq_gap (§L2491,2572,3875) | server.py:4070-4105 | 不核对, 14次underrun仍接受正常complete | 高(完整性) | ACK前核对四点字节+underrun, 异常→tts_error非complete | 异常不发complete |
| 15 | 完整记录客户端ACK字段 | server.py:3945 只记前200字符 | underrun等尾部字段被截断, 无法复盘 | 高(可观测) | 记录完整ACK JSON | 日志可复盘underrun |
| 16 | 四点字节核对ACK时闭环 | 只发送时记Worker/Gateway两点 | ACK时不核 client received/written | 高 | ACK闭环四点(received==written==worker) | 四点一致 |
| 17 | 任一段失败→tts_error | server.py:2796-2798 段超时只日志继续 | 最终仍 send_complete=True | 高 | 段失败→tts_error, 不发complete | 失败正确报错 |
| 18 | client_first_pkt_ms 单一时钟 | server.py:1281,2649 混 time.time/time.monotonic | 产出无效指标(1784252001075) | 中 | 统一 monotonic | 指标有效 |
| 19 | reply_text_sha256 追踪 | 无 | 未发/未验, 无法整条一致性追踪 | 中 | 发+验 reply_text_sha256 | 文本/PCM可追踪 |
| 20 | join契约统一(§20.7.5严格相等 vs §33 normalize) | 当前走normalize | 架构自身契约冲突 | 中 | 架构统一为"normalize(join)==normalize(reply)" | 契约一致 |

## 根因总结(Codex)
客户端在**供给曲线落后于播放曲线**时过早启播: :8767合成有效RTF≈1.29(>1) + 串行分段重复init(~0.97s首块) + Gateway固定SEND_DELAY_MS节流 → 有限预缓冲耗尽 → AudioTrack underrun → 丢字。**非分段粒度问题**(实测逗号切7段更差, 因串行+无预取+无流控)。RTF数据来源: 外滩交互 tts_99634966_0006 send_elapsed=17984ms vs audio_dur=13908ms(样本1, 需更多样本确认P95)。

## P0止损顺序
#8(RTF≥1回退整包) → #3/#4(真流水线+预取, 需Worker RTF<0.9) → #14/#16(完整性闭环) → #6/#7(V2协议+自适应缓冲) → 其余。
