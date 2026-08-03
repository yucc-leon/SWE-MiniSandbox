# Docker-free 路线收尾复盘(2026-07-02)

> 本文是 aarch64 + docker-free(MiniSandbox venv/conda + chroot)这条路线的**收尾总结**:做了什么、发现了什么、为什么停、切到 x86+docker 后带走什么。
> 细节证据见各专题文档,本文只做汇总,数字均标注来源。

## TL;DR

在 aarch64 NPU 集群上基于 MiniSandbox 无容器方案适配了完整的 SWE-agent 数据管线(环境重建→采样→打分→SFT/RL)。**结论:无容器路线在 swesmith 全实例域上环境忠实率仅 ~35–50%,数据真实可用率 15%,且相对 docker 没有成本/效率优势**——损耗根源是上游(SWE-smith 与 MiniSandbox)均未发布可复原的完整依赖规格,完整环境只以 x86 docker 镜像形式存在。x86+docker 机器到位后切换官方镜像路线,本路线终止。**但本路线期间的核心发现(git 历史泄露、vacuous 打分、预筛纪律)与基础设施无关,全部随迁。**

## 1. 背景

- 约束:可用算力为 aarch64 NPU 集群,无 docker 使用条件 → 采用 MiniSandbox(arxiv:2602.11210)的无容器 venv+conda 方案,叠加 chroot 隔离。
- 注意:MiniSandbox 论文的 "docker≈精度" 指其**精选 1600 样本上的 RL 训练效果**,不是 per-instance 环境忠实率;它本身也是 venv+conda 而非镜像。我们把它用到 swesmith 全实例域,超出了它验证过的范围。

## 2. 做了哪些尝试

**适配工程**(让管线在 aarch64 + 无 docker 下能跑):
- chroot / no-chroot 双交付模式;venv 共享缓存 + wheelhouse + tar 分发;系统包命令剥离(no-chroot);
- clone 稳定性根修(proxy basic-auth + `checkout -B {base_commit} FETCH_HEAD`,6/6 gold 验证);
- 预筛池建设(gold→1 ∧ buggy→0 pre-check gate)+ prewarm(后证冗余,弃)。

**忠实率修复尝试**(均有实测,详见 `faithfulness-loss-analysis.md`):
| 尝试 | 结果 |
|---|---|
| full-deps(常见 extras + test/dev requirements) | 翻转 28%(偏样本),主体未动 |
| swesmith 官方 per-repo install_cmds(正确 extras) | 翻转仅 8% → **证伪"缺 extras"假设** |
| Tier-2 LM 驱动 env 构建(try_install_py) | 受阻:自身依赖 docker,需独立工程 |
| 找上游精确 env dump(HF `lblankl/MiniSandbox`) | 只有基础 python 脚本,**无 per-repo env dump** |

## 3. 核心发现(三层漏斗)

1. **环境忠实率 ~35%(bare)/ ~50%(full-deps)**,vs 官方镜像 ~100%。根因:swesmith 完整测试环境在镜像 build 时由未发布的 `sweenv_<repo>.yml` 装好,运行时规格只有增量 `pip install -e .`;docker-free 重建丢掉那层**弥散的**完整依赖(每个测试文件各缺各的,pytest 采集 error 为主)。**架构(aarch64)纯贡献小,x86 上无镜像重建同样会损**。无单一银弹。
2. **采样产率**:877 条生成 → 281 真 resolve。注意此数≠GLM 能力(极简 scaffold + 环境降级 + 合成任务口径混杂),干净基线未测。
3. **数据干净率:真实可用仅 ~15%(133/877)**。两大杀手与容器无关:**git 历史结构性泄露**(合成 revert 范式必然把答案留在历史里,≥47% resolve 开卷)+ **vacuous 打分 bug**(空 patch 判 resolved)。
4. **流程教训**:忠实度可零 API 成本预检——预筛后可用率 23%→94%(salvage 157/167)。**先验环境、再花采样预算**。

## 4. 成本/效率结论

无容器路线**没有换来成本或效率优势**:
- ~50% 实例直接不可用 → 同等有效样本需近 2 倍构建与筛选;
- venv/git 缓存存储压力大(以致需要 delete_after_create)、wheelhouse/tar 双轨维护、aarch64 wheel 缺口需源码编译;
- 适配与排障(clone、proxy、chroot、打分恢复)占用了大量工程时间,而这些在镜像路线下不存在。

## 5. 决策与迁移

**决策:x86 + docker 机器到位,切换 swesmith 官方镜像路线;本路线代码封存于 `feat/chroot-delivery` 分支,不再投入。**

**随迁(与基础设施无关,是本阶段真正的资产):**
- git 历史泄露的认知与 **lockdown 方案(squash/去 .git + 断网/禁 PyPI)——docker 镜像同样带完整历史,泄露照样存在,lockdown 必须随迁**;
- 打分 sanity gate:空 patch 必须 0 分;gold→1 ∧ buggy→0 双向预检(可信 RL 池 1864 由此而来);
- "先预筛后采样"的预算纪律;真实漏斗审计方法(资产是方法,非那批数据);
- 数据集选型结论(swesmith 验管线 / R2E-Gym 真训练 / SWE-bench Verified 评测)。

**弃置:** chroot/no-chroot 交付、wheelhouse、venv tar 缓存、系统包剥离、full-deps 开关、prewarm——官方镜像下均无对象。

**新机器首批动作建议:**
1. 官方镜像 + mini-swe-agent + 同批实例跑 GLM **干净基线**(校准"环境降级到底压了多少分",也补上第 3.2 节缺的对照);
2. 镜像内实施 lockdown 后再开采样;
3. 空 patch→0 的 sanity gate 先于一切打分跑通。

## 相关文档
- `faithfulness-loss-analysis.md` — 忠实率损失全部证据与尝试记录
- `swe-data-options-review.md` — 泄露结构分析与数据集选型
- `data-generation-batches-and-method.md` — 各数据批次与方法演进
- `swe-agent-training-handoff.md` — 全管线交接(含 1864 可信池、15% 漏斗口径)
