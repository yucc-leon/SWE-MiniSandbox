# 归档文档（已被取代，仅作历史/溯源）

这些文档是**特定时间点的状态快照或已被推翻的阶段性结论**，2026-06-29 文档重建时归档。**不要当作当前真相**——多数包含 fix 之前的乐观数字（用了后来发现 vacuous 的打分器）。

| 文档 | 为何归档 |
|---|---|
| overnight-status-2026-06-17.md | 过夜状态快照;称 swesmith 打分"彻底收口/无假阳性"——被 06-25 vacuous-scorer 发现推翻 |
| autonomous-run-status-2026-06-19.md | 一日自动run 日志;"182 validated/打分已修"等 resolve 标签后证 vacuous |
| overnight-report-2026-06-25.md | vacuous-bug 发现报告(有溯源价值),但结论"RL 池 1620 安全/430 clean"已被 1864/133 取代 |
| 周会汇报-详细版-archive.md | 详细版周报草稿;自身已标注"2014 clean pool"为过度表述 |
| handoff-32b-baseline-fixview-500.md | 32B 基线复现 runbook(已完结run);自标"历史 resume context" |
| project-summary-swe-minisandbox.md | pivot 前的项目总结(框定为"复现上游 32B eval ~40%");无 SFT/RL/数据漏斗,已被 handoff 取代 |
| env-completeness-experiment.md | Tier-1/2 实验日志;结论已并入 faithfulness-loss-analysis;含过时 ~35-50% 快照 |
| ideaTALK.md | architecture.md 的变体旧副本 + junk 命名;不在导航 |

## 当前权威文档（新人按序读）
1. `../guide/swe-agent-training-handoff.md` — 主交接（全流程 + 诚实现状）
2. `../guide/swe-agent-landscape-framework.md` — 概念全景（7 维度 + 张力）
3. `../guide/swe-data-options-review.md` — 数据策略/选型
4. `../guide/rl-loop-status-2026-06-29.md` — 最新 RL 状态
5. `../周会汇报.md` — 当前周报

> 关键校正口径(均经核实):真实可用 SFT = **133**(非 430/900);RL 池 = **1864**(非 1620/2014);打分器 6 bug 已修;RL 闭环**机械上**已通(task 成功),但 reward 全 0、**尚无学习信号**(非"RL 有效")。
