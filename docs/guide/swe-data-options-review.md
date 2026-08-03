# SWE 训练数据方案 Review(数据集选型 + 泄露结构分析)

> 目的:把散落的数据集调研归拢成一份可 review 的选型依据。核心结论先行:**数据集的"泄露性"是结构性的、由造数方式决定——合成-revert(SWE-smith)天生漏题,真实-PR(R2E-Gym/SWE-bench/SWE-Gym)天生不漏。** 训练真实 SWE-agent 能力应走真实-PR。

## 0. 一条主线:泄露是数据集性质,不是 bug

- **SWE-smith 的 bug = "把干净代码改坏"(合成 revert)** → "答案"=原始干净代码,**同时存在于 git 历史(Initial commit)、PyPI(`pip install` 上游)、GitHub 远程**。一个满能力 agent(裸 bash + 网)**必然**能从这三处之一抄到。堵漏需同时断 git历史+网,但这**废掉真实能力**(`git log/blame`、`pip`)。→ 堵漏=损真实性,死循环。
- **真实-PR 的 bug = 新写的修复** → 世上没有第二份答案 → agent 放开用 git+pip,**结构性不漏**。
- 推论(设计原则):**治环境不治 agent**;优先选结构性干净的数据集,而不是给坏数据集打补丁(flatten 历史/断网都是半吊子,且损 realism)。
- 相关洞察:**动作空间 ↔ 泄露攻击面**——裸 bash(mini-swe-agent)暴露 git;受限 ACI(SWE-agent)收窄但属"阉割 agent"的错误修法。详见记忆 [[scaffold-actionspace-leak]]、[[swesmith-githistory-leak]]、[[true-data-funnel-2026-06-26]]。

## 1. 数据集对比

| 数据集 | 类型 | 规模(约) | 泄露风险 | 可执行环境 | 形态 | 在我们 stack 的成本 |
|---|---|---|---|---|---|---|
| **SWE-smith**(现用) | 合成 bug(revert)| ~59k 实例 | 🔴 结构性(历史+PyPI+远程)| **container-free**(我们的 MiniSandbox,venv+conda+chroot)| 实例(需自己生成轨迹)| 已全套接好 |
| **SWE-bench (Verified/Multilingual)** | 真实 PR | Verified 500(**eval 用**)| 🟢 无(fix 在未来提交)| docker(官方)| 实例 | eval 标准;train 接入有成本 |
| **SWE-bench train** | 真实 PR | ~19k | 🟢 无 | docker | 实例 | 接入有成本 |
| **SWE-Gym** | 真实任务,训练向 | ~2.4k(verified env)| 🟢 无 | docker | 实例 | 上游 mini_swe_agent 例子原生支持 |
| **R2E-Gym** | 真实可执行环境 | ~8k+ | 🟢 无 | docker | 实例 | **已在 stack**(PYTHONPATH + reward parser 就用它)→ pivot 成本最低 |
| **SWE-Hero**(nvidia)| **现成轨迹**(蒸馏自 Qwen3-Coder-480B)| 34k 轨迹 / 11.7k issue | 🟢 无(建在 R2E-Gym/SWE-Gym 上)| —(已是轨迹)| **现成 SFT 轨迹** | CC-BY-4.0 可商用;**OpenHands scaffold → 需格式转换** |
| **SWE-smith-trajectories**(官方)| 现成轨迹 | — | 🟡 大概率比我们干净(SWE-agent ACI 非裸 bash,本地 demo 0 命中 git-leak,**未在全量集证实**)| —(轨迹)| 现成轨迹 | scaffold 不匹配我们(记忆已记为不可用)|

## 2. 两类数据集 → 两种用法

- **实例数据集**(SWE-smith / SWE-bench / SWE-Gym / R2E-Gym):提供 repo+bug+测试+环境,**需自己用 teacher 生成轨迹**(我们这套:GLM teacher + mini-swe-agent)。RL 也用它们做可执行奖励池。
- **现成轨迹数据集**(SWE-Hero / SWE-smith-trajectories):**直接是 SFT 数据,绕开生成**(避开我们 877→133 那个崩塌)。代价 = **scaffold 格式转换**(OpenHands/ACI → 我们的 mini-swe-agent 格式),有损耗风险。

## 3. 我们自己数据的真实状况(警示)

SWE-smith + 我们 net-on 生成的真实漏斗(修好 scorer + 泄露检测后):**877 生成 → 281 真 resolve → 仅 ~133 真干净(15% 真实可用,泄露 ≥47%)**。之前号称的 430/900 是 vacuous 打分 + 弱泄露检测虚标的。详见 [[true-data-funnel-2026-06-26]]。→ **印证:在 swesmith 上"自产数据"性价比低且天然脏。**

## 4. 实测代价(2026-06-29 两个 spike 后,修正早期乐观估计)

> 早期以为"R2E 集成成本最低、只换 data_source"——**实测推翻**。真正贵的是**"为任意真实仓库的任意历史 commit 重建可执行环境"**,这是整个 SWE 数据领域公认最贵的活(R2E 自己一个研究项目也只做了 13 repo)。**没有免费午餐:省了建环境,就得吃转换/架构成本。**

| 路线 | 省掉了 | 真实代价 | 证据 |
|---|---|---|---|
| **R2E 自建环境 + 我们 teacher 生成**(数据原生我们格式) | 格式转换 | **建环境 ~2 周**(13 repo;numpy/orange3 aarch64 编译是主风险);easy 档 4-5 repo ~3-4 天 | aiohttp PoC 端到端通(gold=1/buggy=0,空补丁→0,.git 剥离验证);成本分档实测 |
| **SWE-Hero 转成我们(bash)格式** | 建环境 + 生成 | **40% 动作有损转换**(`str_replace_editor` 结构化编辑→bash);中等工程 + 信号失真 | SWE-Hero 轨迹实测:execute_bash 54%(易转)/ str_replace_editor 40%(难)/ think+finish 6% |
| **SWE-Hero 直接用 OpenHands 格式** | 建环境+生成+转换 | **改造训练/评测吃 OpenHands**(架构变动);换来框架多样性 bonus | 同上;且 SWE-Hero 建在 R2E-Gym 实例上(dataset 字段=R2E-Gym-Subset) |

**为什么 swesmith 当初快、R2E 慢(关键差异=环境复用度)**:swesmith 每 repo **1 个环境**(所有 bug 共用一个 commit),且 swesmith **预打包好了**镜像/venv——我们当时只是"把现成环境搭进 chroot"(配置层,所以记忆里"主要在修部署问题")。R2E 每 bug 在**不同历史 commit、依赖各异**,环境**得自己从零造**(依赖层)。**最贵的"造环境"那层,swesmith 帮你省了,R2E 要自己干。**

**SWE-Hero 的一个实用旁路**:它直接给 `model_patch`(最终 diff),不碰轨迹转换也能用作 ① eval gold 参照 ② rejection-sampling 种子 ③ 非-agentic SFT(problem→patch)。

## 5. 选型建议(修正版)

1. **跑通验证**:**SWE-smith 原样**(已接好,container-free)——验机制,接受 reward 虚高。**[已完成:SFT→RL→eval 全跑通]**
2. **"干净数据更好吗"对照实验**:R2E **只做 easy 档 4-5 repo**(~3-4 天,aiohttp PoC 已 day-0),拿一小批真干净数据验证命题——不必全 13 repo 啃 numpy 编译。
3. **不立刻铺开重的**:全 13 repo(~2 周)和 SWE-Hero 全量转换都偏重,**目标(掌握 pipeline)已由 swesmith 全流程达成**,不必在 R2E 上再走一遍贵的环境构建。
4. **SWE-Hero 标记为"框架多样性"储备**:若走多框架鲁棒性(见 landscape 框架张力 #7),直接用其 OpenHands 格式,而非有损转 bash。
5. **eval 标准**:**SWE-bench Verified**(真实对标);短期同框架 mini-swe-agent 评测分数标注"偏乐观"。

## 6. 一句话
> 数据方案的核心权衡 = **多样性 / 质量 / 扩展成本** 三者不可兼得(见 landscape 框架)。swesmith=规模+多样但脏(漏题);R2E=干净但窄(13 repo)且**自建环境 ~2 周**;SWE-Hero=现成干净但 OpenHands 格式需有损转换或架构改造。**没有又快又干净又原生我们格式的路。** 务实路径:swesmith 已跑通管线 → R2E easy 档小批量验证"干净更好" → 视结论再决定是否重投。
