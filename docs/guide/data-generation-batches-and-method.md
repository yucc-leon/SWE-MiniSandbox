# SWE SFT 数据生成:批次谱系与方法论

> ⚠️ **2026-06-29 校正(数字过时)**:本文 §1 的计数(159 chroot SFT、~990 gold→1、faithful ~800+)是 **打分器修复前**的,用了后来发现 vacuous 的打分器 + 弱泄露过滤,**虚高**。当前实测(核实计数):真实可用 SFT = **133**(877 生成→281 真 resolve→133 真干净,~15%);RL 池 = **1864**(gold→1∧buggy→0 双向验证)。注:133 是"修好的打分器 + 能力型泄露检测"下的测量值,随过滤口径会浮动,非绝对常数。**方法论部分(Python-only 过滤、full-deps、repo×family 多样性网格)仍有效**——只是计数当上界看。权威数字见 `swe-data-options-review.md` / `swe-agent-training-handoff.md`。

> 记录我们产出 chroot-validated SWE SFT 数据的**所有批次**(干啥、产出、是否被取代)+ **方法演进**与**关键设计决策**。新会话/接手者先读这份,避免对不上批次。
> 最后更新:2026-06-22

---

## 1. 一句话现状

- **可信 chroot SFT 成品**:`vendor/sft-data/sft_chroot_combined.jsonl` = **159 条**(147 salvaged + 12 早期),清洗后训练集 `sft_swe_clean.parquet` = 140 条。
- **faithful 保真池(2026-06-23 纠正,之前严重低估)**:screening 全部跑完、**建成率 94-96%**,gold→1 实际:py500 **229** + divgrid-a **176** + divgrid-b **232** + screen300 67 + screen600 126 + salvage 164 ≈ **~990 gold→1**(faithful≈gold→1∧buggy→0,~90% → 估 ~800+)。之前报的 "496/早停" 是**我把 monitor 运行中快照当成了最终数**——**没有早停 bug**,screening 健康。buggy 验证当时按快照小 filter 跑、也低估,需按全量 gold→1 重跑 buggy 才得准确 faithful。
- **教训**:task 结束后必须按 exit_statuses/实例目录数核实**最终**值,不要用 monitor 中途快照下结论。
- **训练**:`sh/sft/run_swe_sft_4b.sh` 已就绪(verl FSDP, Qwen3-4B),待 4 张 NPU。
- **GLM 新采样**:尚未跑(被成本 gate);159 全是 salvage 来的。

---

## 2. 批次谱系

| 批次 | 规模 | 目的 | 产出 | 状态 |
|---|---|---|---|---|
| **chroot-diverse-v2** | 80 repos | proxy 修复后首个 chroot 筛选 | ~18 gold→1 (23%) | 缓存已**作废**(旧 detached-HEAD 代码建,分支 ref 修复前) |
| **salvage-pool** | 175 | 把旧 no-chroot GLM patch 在 chroot 下重打分捞回 | 164 gold→1(94%,预筛集)→ 159 faithful → **157 salvaged → 147 SFT** | ✅ 核心产出,0 新 GLM |
| **screen300** | +300 | 首个"扩筛"批(overnight) | 191/300 built, **67 faithful**(46 repos) | 含少量非Python、无 full-deps |
| **screen600** | +600 | "扩筛更多" | 361/600 built(239 failed:**216 非Python(Go)** + 其他), 126 gold→1, **94 faithful** | **被取代**;暴露 3 个问题(见 §3) |
| **fulldeps-test** | 30 | A/B 验证 `SWESMITH_FULL_DEPS=1` | 缺依赖 gold→0 翻转 **8/28 (28%)** | ✅ → full-deps 设默认 |
| **screen-py500** | 500 | 首个 Python-only + full-deps 批 | 早期 gold→1 ~64% | 🔄 在产 |
| **divgrid-A/B** | 1113 | **多样性网格**:每未覆盖 (repo×家族) 格 1 个 | 121 repos × 16 家族均衡 | 🔄 在产(当前正解) |

---

## 3. 方法演进(踩过的坑 → 修正)

1. **网络**:pod "clone roulette" 真因 = 代理认证(`git proxyAuthMethod=basic` + 保留代理 + 清 `GIT_ASKPASS`),不是没 VPN。→ pod 10/10 克隆。详见 memory `proxy-egress-rootcause`。
2. **克隆缓存**:浅取 `checkout FETCH_HEAD` 脱离分支 → reset 失败 → 旧版加 `|| true` 创可贴。**根治**:`checkout -B {base_commit} FETCH_HEAD`(建真分支 ref),删 band-aid。→ **旧缓存全作废,需新代码重建**。memory `clone-branchref-rootfix`。
3. **非 Python 浪费**:数据集 222 repo 里只有 **134 是 Python**(`MAP_REPO_TO_SPECS`),Go 等建不了。→ **所有 filter 限定 Python-only**。
4. **保真率低(env-drift)**:aarch64 重建 venv ≠ x86 canonical 镜像 → 版本/依赖敏感测试失败。gold→0 中 **~79% 是迁移漂移**(68% 缺依赖 + ~10% 版本)。→ `SWESMITH_FULL_DEPS=1` 默认(装 extras + requirements),缺依赖翻转 ~28%,保真 35%→~50-60%。
5. **家族偏斜**:早期 per-repo 轮询取第一个 → combine_file 独大。→ **(repo×家族) 网格**:128 repo × 16 家族 = 1364 格,每格代表一种 bug 类型 × 项目。

---

## 4. 关键设计决策:轨迹采样的多样性

**两个轴别混淆:**
- **多样性/广度** = 1364 个 (repo×家族) 格(超过即"同类型同项目的另一个具体 bug");
- **数据量/深度** = ~12-25k faithful(50k × 保真率,每格采多个)。

**采样设计(为何不是 full grid、也不是海采):** SFT 技能**可分解**(repo 导航 ⊥ bug 类型推理),不需要每个 R×T 组合。最优 = **双边际均衡 + 配对轮转**:每类型出现 K 次(各落不同 repo)、每 repo 出现 M 次(各不同类型),不填满网格、冗余最低、泛化最好。脚本 `sh/balanced_sample_select.py`。

**官方数据为何不直接用:** `SWE-bench/SWE-smith-trajectories`(26k,5k 训了 SWE-agent-LM-32B)是 **SWE-agent 脚手架 + Claude 在 x86** 采的;我们用 **mini-swe-agent**(纯 bash),格式不兼容 → SFT/infer 错位 → **必须自产**。官方只作对标/规模参照。

---

## 5. 流水线(自洽链路)

```
50k Python 实例(128 repo × 16 家族 × 多实例)
  │  divgrid 广覆盖筛选(Python-only + full-deps + gold→1∧buggy→0)
  ▼
广而全的 faithful 池(目标铺满 1364 格)
  │  balanced_sample_select.py(双边际均衡)
  ▼
差异化采样清单  →  GLM(mini-swe-agent clean 协议)现采轨迹  ← 待批预算
  │  traj_to_sft → parquet
  ▼
SFT(verl FSDP, Qwen3-4B, sh/sft/)  →  导出 HF  →  chroot eval
```

---

## 6. 待决策 / 下一步

- **GLM 采样预算**:在 faithful 池上跑 GLM 现采(每实例可多 attempt),花费换轨迹量。规模待定。
- **NPU**:训练/eval 需 4+ 张;个人配额 32,常被其他项目占满。空出即可起训。
- **faithful 池目标规模**:广度封顶 1364 格;量靠深度,按 GLM 预算定。

---
## 纠正 2026-06-23(读 MiniSandbox README/论文 arxiv:2602.11210 后)
**之前"swesmith 镜像/docker≈100% 保真"的归因有误,纠正:**
- MiniSandbox **不用容器镜像**,是 `venv + 多版本 conda backend + 轻量环境预缓存`(README 原文 + 论文摘要 "eliminate the need for bulky container images")。我们方式与它一致。
- "与 Docker 精度相当" = **RL 训练表现**(1600 SWE-Smith 样本 × 200 步,两后端各训一版性能相当),**不是 per-instance 构建/保真率**。所以我们随机实例 ~50% 保真与之不矛盾(它用 curated 1600)。
- gold→0 主因(实测):**pytest/conftest/插件工具链版本不匹配 17/20**,非缺应用依赖,full-deps 修不到(它装 app extras)。**架构无关**。
- 真正的修法方向:① 按 repo 对齐 pytest+插件版本(env.yml 里 pin 的);② 查并复用 MiniSandbox 的 Conda Backend Files(HF lblankl/MiniSandbox)。
- **架构(aarch64)纯贡献很小**;主损失是测试工具链重建不全(方法,x86 同法也会有)。
