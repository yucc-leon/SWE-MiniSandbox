# [草稿·未发布] 走通 SWE-agent 流程(二):数据层——用什么数据、怎么构造

> 系列发心见系列(一)。本篇讲**数据层**:训练/评测用什么数据集、如何构造与筛选、以及一个决定数据可用性的结构性陷阱(答案泄露)。
> 状态:骨架 + 现成材料;发布前:统一语言、补图、复核数字。

## 0. 问题
可验证 SWE 任务需要 (buggy 代码, 修复 patch, 触发测试) 三元组。数据从哪来、质量如何、会不会被模型"作弊",直接决定训练信号是否可信。

## 1. 数据集光谱(各自定位)
(种子:`docs/guide/swe-data-options-review.md`)
- **SWE-smith**:模型合成 bug,规模大(~5万实例/128 repo),适合**验证 pipeline**;但有结构性泄露(见 §2)。
- **SWE-bench Verified / Pro**:真实 PR,人工精选,适合**评测**;Verified 500、Pro 更难更新。
- **R2E-Gym**:真实 bug commit,抗泄露,适合**真实训练**;repo 多样性 13 个。
- **SWE-Hero**:现成轨迹,省生成,需 scaffold 转换。
- 结论:swesmith 验管线、R2E-Gym 真训练、Verified 评测、Hero 省生成——按用途 MIX,不是二选一。

## 2. 核心陷阱:合成 revert 的结构性答案泄露
- 合成范式 = 干净快照 → 注入 bug commit → 删 F2P 测试 → 作为 HEAD。**修复答案就躺在 git 历史的过去**:`git show <Initial>:src` = 修好的源码,删测试的 commit 还泄露隐藏判分测试。
- 三条同源通道:git 历史 / `pip download` 已发布包 / `git fetch` 远端真仓库。
- 量化:我们生成轨迹 **≥47% 的 resolve 用了泄露**;教师 GLM 视其为正当 debug(环境把作弊做成最优策略,非"故意作弊")。
- 对比:真实 PR 型(SWE-bench/R2E-Gym)**不泄露**——修复在未来的 PR 里,历史翻不出。
- **这是数据构造属性,与容器无关**:docker 镜像同样带完整历史,换 docker 泄露照样在。
- 修复:lockdown = squash 历史 / 去 `.git` + 断网禁 PyPI + 禁远端。

## 3. 怎么构造 / 筛选:诚实漏斗
- 三坑(泄露 + 虚假奖励见系列三 + 环境损耗见系列一)叠加后真实产率:**877 生成 → 281 真 resolve → ~133 真干净(15%)**,非早期号称的 430/900。
- 流程纪律:环境忠实度**可零 API 成本预检**(gold→1/buggy→0),预筛后可用率 23%→94%。**先验环境、再花采样预算。**

## 4. 业务数据 → SWE 任务转化(占位)
> 把内部真实缺陷修复记录转成可验证 SWE 任务(挖 buggy commit/修复 patch/触发测试三元组,执行式验证)。真实 PR 型天然抗泄露,是理想来源。待填:实际进度 / A/B 增益。

## 5. 结论(数据层)
- 数据可用性的头号杀手不是规模,是**结构性泄露**(合成 revert 特有)与**执行式验证是否可信**。
- 选型 = 按用途 MIX;构造 = lockdown 防泄露 + pre-check 先验 + 诚实审计漏斗。

## 相关
- `docs/guide/swe-data-options-review.md`、`docs/guide/data-generation-batches-and-method.md`
- 系列(一)系统层、(三)harness 层
