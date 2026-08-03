# [草稿·未发布] 走通 SWE-agent 流程(三):harness 层——推理接入与训练

> 系列发心见系列(一)。本篇讲**harness 层**:agent scaffold、推理服务接入、SFT/RL 训练接线,以及一个决定训练成败的隐性 bug(虚假奖励)。
> 状态:骨架;部分环节(RL 全跑)未完成,如实标注。发布前:统一语言、复核数字。

## 0. 问题
把模型接成能"读题→改代码→验证"的闭环,需要 scaffold(动作空间)、推理服务、训练框架三者接线。每一处都有它的坑。

## 1. Scaffold:动作空间 ↔ 泄露的张力
- 选 mini-swe-agent(纯 bash 单工具,适合 vanilla 模型;无专用编辑/搜索工具)。
- 张力:动作空间越大(能 `git log`/`pip download`),越容易触发数据泄露(见系列二)——scaffold 设计与数据构造耦合。

## 2. 推理接入
- vLLM-ascend serving(aarch64);MoE 推理优化;privileged 多卡设备 pinning。
- 已知坑:合并 SFT 模型加载 `Engine core initialization failed`(待查,独立问题)。

## 3. 训练接线
- SFT:verl FSDP 4B recipe,数据为 messages-parquet。
- RL:SkyRL wiring——`SWEMiniSandboxEnv(BaseTextEnv)`,reward fn 走**同一 pre_check 路径**(train/eval 一致),fresh-env 打分(避开 rollout 后 reset bug)。
- 状态:SFT recipe 就绪;RL 接线有 glue gap(fork 里缺 example imports),**全流程 RL 未完整跑通**(如实标注)。

## 4. 核心坑:虚假奖励(vacuous reward)
- 打分路径有 vacuous bug:**空 patch 也被判 resolved**。后果:早期 "resolve" 标签虚高,漏斗坍塌一半来自这。
- 修复 = sanity gate:空 patch 必须 0 分;gold→1 ∧ buggy→0 双向预检(negative control)。修好后可信 RL 池 = 1864。
- **通用教训:任何执行式奖励都必须有 negative-control 闸(已知该失败的输入必须失败),否则 RL 会奖励空动作。** 这是 harness/reward 层最容易被忽略、也最致命的一环。

## 5. train/eval 一致性
- 奖励函数与评测走**同一 pre_check 路径**,避免训练/评测口径漂移。

## 6. 结论(harness 层)
- scaffold、推理、训练三者接线都各有坑;最隐蔽的是奖励可信性——没有 negative control,一切训练信号都可能是幻觉。
- (待补:RL 全流程跑通后的闭环结果。)

## 相关
- `docs/guide/swe-agent-training-handoff.md`、RL 接线 spec、`genpool-scoring-vacuous`
- 系列(一)系统层、(二)数据层
