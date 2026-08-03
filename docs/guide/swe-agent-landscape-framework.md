# SWE-Agent 训练全景框架(任务解剖 + 维度拆解 + 贡献映射)

> 目的:对 SWE 类任务做一次整体拆解,把主流工作的核心贡献分到不同维度上,形成一组完整视野。
> 用法:这是"面"(landscape),数据集选型 [[swe-data-options-review]] 是其中一个"点"。

---

## Part 1. 可验证 SWE 任务的解剖

一条可训练/可评测的 SWE 任务实例 =
```
repo + base_commit + 问题描述(issue) + gold patch + 测试(fail-before / pass-after) + 可执行环境
```
- **核心是"测试"**:它把"做得对不对"从人工主观变成**机器可自动判定**(改前 fail、改后 pass)。这是数据能用于训练/RL 奖励的前提。
- agent 的任务:读问题 + 探索 repo → 产出一个 patch → 被测试判定。

## Part 2. 构建 SWE-agent 的 7 个 pipeline 维度

| 维度 | 问题 | 关键权衡 |
|---|---|---|
| **A. 实例来源/构造** | 任务从哪来 | 真实-PR 挖掘 vs 合成 bug 注入;规模 vs 真实性 vs 泄露 |
| **B. 执行环境** | 任务怎么跑 | docker 镜像 vs 容器无关(venv+chroot);复现性 vs 成本/并发规模 |
| **C. 验证/奖励** | 怎么判一个 patch | 测试 oracle(F2P/P2P);RL 奖励设计 + reward hacking + train/eval 一致 |
| **D. Agent scaffold/动作空间** | agent 怎么交互 | 裸 bash vs ACI vs 富工具;能力上限 ↔ 泄露攻击面;**训/评同框架→过拟合,多框架轨迹→鲁棒**(见张力7) |
| **E. 轨迹/数据生成** | 训练数据怎么造 | teacher 蒸馏;execution-free vs execution-based;覆盖 vs grounding |
| **F. 训练算法** | 怎么学 | SFT 冷启动 → RL(GRPO/PPO,outcome reward);数据规模律 |
| **G. 评测/benchmark** | 怎么量 | SWE-bench(Verified/Multilingual/Pro)等标准 |

## Part 3. 贡献映射矩阵(工作 × 维度)

| 工作 | 主要贡献维度 | 一句话贡献 |
|---|---|---|
| **SWE-bench** (2023) | **G** + A + C | 把真实 GitHub issue+PR 变成**可执行测试判定的基准**,定义了这个 task |
| SWE-bench Verified/Multilingual/Pro | **G** | eval 精化:人工校验 500 / 多语言 / 更难 |
| **SWE-Gym** | **A + B** | 真实任务 + **可执行训练环境**(训练向,非纯 eval) |
| **R2E-Gym** | **A + B**(规模) | 规模化的真实可执行环境(~8k+),训练/RL 用 |
| **SWE-smith** | **A + E** | **合成 bug 注入** → 实例规模拉到 ~50k+ + 配套造数工具 |
| **SWE-MiniSandbox**(我们这套) | **B + C** | **容器无关执行**(venv+conda+chroot)+ 容器无关 RL 奖励;docker-vs-无容器 RL 对等 |
| **mini-swe-agent** | **D** | 极简 scaffold(每步一条裸 bash) |
| **SWE-agent** | **D** | **ACI**(Agent-Computer Interface):为 LLM 设计的受限工具面 |
| **OpenHands** | **D** | 富工具 agent 平台(bash+python+浏览器+编辑,事件流) |
| **SWE-Hero** (2026) | **E + F** | **双层数据**:300k execution-free + 13k execution-based,强 teacher(Qwen3-Coder-480B)蒸馏 |
| **SWE-RL** (Meta) | **F** | 在规则化奖励上对 SWE 做 RL |
| **Skywork-SWE** | **F** | SWE 的**数据规模律**(data scaling laws) |

→ 读法:**大多数工作只在 1-2 个维度上做主要贡献。** A/B/E 是"数据与环境"侧(供给),D 是"交互"侧,C/F 是"学习"侧,G 是"度量"侧。

## Part 4. 横切的张力与洞察(这套框架的"灵魂")

1. **真实性 vs 泄露**(A 维核心):合成-revert(SWE-smith)的答案在 git历史+PyPI+远程 → 结构性漏题;真实-PR(fix 是新改动)无第二份答案 → 结构性干净。
2. **动作空间 ↔ 能力上限 ↔ 泄露攻击面**(D×A):裸 bash 暴露 git(能漏),ACI 收窄(但"阉割 agent"是错误修法)。**正解:治环境(选干净数据)不治 agent。**
3. **容器 vs 容器无关**(B):docker 复现性强但重;容器无关(MiniSandbox)轻、利于大规模并发 RL rollout——RL 的 CPU 环境侧才是瓶颈。
4. **train/eval reward 一致性**(C):RL 奖励、eval、数据过滤必须走**同一个 scorer**,否则 train/eval mismatch。reward hacking / vacuous 打分要靠**负控 gate**(空补丁必须判 0)防住。
5. **execution-free vs execution-based 数据**(E):前者覆盖广、便宜(不跑环境),后者有物理 grounding——SWE-Hero 的双层正是这个权衡。
6. **数据规模 vs 质量**(A×E):规模律(Skywork)说越多越好,但我们实测 swesmith 自产数据真实可用率仅 15%(泄露+vacuous)——**规模的前提是干净**。
7. **框架一致性陷阱 vs 跨框架鲁棒性**(D×G):若训练与评测用**同一个 scaffold**(如都 mini-swe-agent),分数会**过拟合到框架**——测的是"模型+这个框架的配合",换框架部署就崩。Kimi 团队的做法:**训练混入多框架轨迹**(mini-swe-agent / SWE-agent-ACI / OpenHands),让模型学"解决问题的能力"而非"框架格式习惯",接入任何框架都稳。⇒ 推论:① 评测应尽量用**中立/标准框架**(社区事实标准 SWE-agent ACI 或 OpenHands),避免"自己框架自己测";② **框架多样性是"多样性"原则的子维度**——补它的方式正是混入异构数据(R2E-Gym=ACI、SWE-Hero=OpenHands),所以接异构数据不只是补数据量,更是补**框架鲁棒性**。
   *(注:Kimi 那条来自口头交流 + 符合"避免过拟合"一般原理,非公开实证;引用时标注。)*

## Part 5. 我们的工作落在哪 + 学到了什么

- **落点**:B(容器无关执行)+ C(容器无关 RL 奖励 + 修复打分管线)+ 全栈打通(A→G 端到端在 Ascend 上)。
- **方法论资产**(批判性发现,正是研究工程师价值):
  - 抓出 reward 的 vacuous bug + 设计负控 gate(C 维)
  - 揭示 swesmith 的结构性泄露 + 动作空间↔泄露因果(A×D 维)
  - 量化真实可用率 15%(A×E 维,不信漂亮数字)
- **改进方向**(基于这张图):数据侧 A 从合成→真实-PR(R2E-Gym);E 借鉴 execution-free 双层;C 保持负控 gate;F 走 SFT 冷启动→GRPO。

---
> 一句话:**SWE-agent = 「可执行测试判定的 task(C/G)」× 「实例供给(A)+ 环境(B)」× 「scaffold 动作空间(D)」× 「数据生成(E)→ 训练(F)」。** 各家工作各占一两个维度;真正的工程判断在横切张力(泄露/realism/一致性/规模-质量)里。
