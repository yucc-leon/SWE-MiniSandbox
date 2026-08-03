# 训练 SWE Agent:教学 + 交接文档

> 用途:这是一份**教学 + 工程交接**文档。在新会话里把它指给助手("读 docs/guide/swe-agent-training-handoff.md,我要继续学/继续做"),即可无缝续上。
> 目标(项目北极星):**让你掌握"训练 SWE agent"的整体方法论与优化思路**,不是刷分。小模型(≤8B)验证有效即可。
> 助手的内部进度笔记在 `~/.claude/.../memory/`(`minisweagent-integration` / `sft-data-and-recipes` / `pipeline-map` / `delivery-setup`)——本文是给**人**看的版本。

---

## 0. 一句话心智模型

> **训练 SWE agent = 教一个模型在"agent 循环"里操作真实代码库;靠模仿更强模型"成功修好 bug 的轨迹"来学;用"补丁是否真的让测试通过"(执行)来度量。**

记住这句话,下面每一步都是它的展开。

---

## 1. 端到端管线(8 个环节 + 每步的"为什么"和优化杠杆)

| # | 环节 | 是什么 | 为什么关键 / 优化杠杆 |
|---|---|---|---|
| 1 | **Agent 脚手架** | 模型↔计算机的接口。mini-swe-agent = 纯 bash + 线性对话;SWE-agent = 结构化工具(str_replace_editor 等)+ function-calling | **数据格式、评测、RL 必须同一套脚手架**,否则训练白费。通用性(mini,任何模型可跑) vs 能力上限(重型,带编辑护栏)的权衡 |
| 2 | **执行环境** | agent 跑命令 + 判 bug 修没修(跑 FAIL_TO_PASS 测试)的地方。Docker(重) vs MiniSandbox(chroot/no-chroot,轻) | 要可复现、隔离、可规模化。**reward = 打补丁后 FAIL_TO_PASS 过没过**。这是整个"度量尺子" |
| 3 | **训/测分离** | 训练用 SWE-Gym/SWE-smith;评测用 held-out SWE-bench Verified | **铁律**:训到测试集 = 数据泄漏,所有分数作废 |
| 4 | **轨迹生成(蒸馏)** | 强 teacher(可用 API 前沿模型)在训练任务上跑 agent 循环,产 (issue→动作→观测→patch) | teacher 越强轨迹越好;温度/采样数/step 限;execution-free(廉价上量) vs execution-backed(真实) |
| 5 | **质量过滤 + 修复** | 只留**真的修好了**(execution-verified resolved)的轨迹;修 submit 格式、去重、长度分桶 | **数据质量是 SFT 上限的命脉**——只模仿成功行为(= rejection sampling) |
| 6 | **SFT** | 小模型在过滤后的轨迹上微调,**掩码正确**(只在模型自己的 assistant 动作算 loss,环境观测掩掉) | 单阶段 vs 两阶段(SWE-HERO:execution-free 大规模 → execution-backed 精修);base 选型、seq-len、LR、packing;swift/MindSpeed(产 mcore,再转 HF) |
| 7 | **评测** | SFT 后模型,**同一脚手架**,held-out Verified,看 resolved 率 | pass@1 / pass@k;retry/chooser;推理时扩展 |
| 8 | **RL(最大涨分杠杆)** | 执行奖励的 agentic RL(DeepSWE 式),越过 SFT 天花板 | 可验证奖励(resolved)、GRPO/PPO、reward sandbox(我们这套 chroot)、verifier |

闭环:**SFT 数据 → 训模型 → serve → gen→score → resolved 对比基线 →(可选)RL**。

---

## 2. 我们这套环境的具体落地(本仓库)

- **机器**:本地 = `/path/to/SWE-MiniSandbox`(NFS,改代码处);算力 = `tp` CLI 起的昇腾 NPU pod(跑实验)。两者共享 `/sharedata`。
- **脚手架**:选了 **mini-swe-agent**(submodule,v2.3.0)——通用性最强,2026 主流训练选择。
- **环境**:MiniSandbox no-Docker(chroot 需特权 pod;无特权自动退 no-chroot)。
- **serve**:`sh/serve_qwen_ascend.sh`(vllm-ascend-cann8.5-v017,OpenAI 兼容)。
- **生成 harness**:`sh/run_minisweagent_gen.py`(并行 ThreadPoolExecutor)+ `.sh` 启动器。
- **轨迹→SFT**:`sh/traj_to_sft.py`(对齐 SWE-smith 官方 collect_trajs:resolved-only 过滤 + messages 格式)。
- **打分**:`sh/run_swebench_scoring_ascend.sh`(empty-agent patch replay + 官方 grading)。
- **训练数据**:SWE-smith 59k 原始任务在 `dataset/SWE-smith`;离线 gitcache 复用 `vendor/swesmith-cache/`。
- **训练后端**:swift / `megatron_ascend_sft`(MindSpeed),`config/tune/*` 是 torchtune 模板。
- **RL**:SkyRL(`SkyRL/`)+ 已验证的 chroot reward sandbox。

---

## 3. 当前进度(诚实,更新至 2026-06-28)

**全链路已端到端搭通,RL 闭环在 NPU 上跑起来了** ✅(下面每条都验证过)
- **数据生成**:GLM teacher × mini-swe-agent,container-free venv+chroot,aarch64 跑通。
- **打分管线**:审计 + 修复 6 个 bug(见第 4 节),三态验证(空补丁→0 / gold→1 / buggy→0),负控 gate 固化进 `swesmith_score.sh` 防回归。
- **8B 冷启动 SFT**:verl FSDP 8 卡训成(loss 0.69→0.377),HF 模型已合并(`vendor/sft-ckpt/swe_sft_8b_hf`);训练脚本接了"训完自动转 HF"。
- **RL 池**:复检后 **1864 条可信**(gold→1 ∧ buggy→0 双向,修好的 grader)。
- **RL 接线**:复用 SkyRL `examples/mini_swe_agent` generator,适配 container-free env + 我们的 scorer(`sh/rl/mini_swe_rl/`);tiny GRPO **机械闭环已跑通**(rollout→reward→GRPO→ckpt,task 成功)。⚠️ 但 reward 全 0(4B 在 tiny 实例上解出 0)→ **尚无学习信号**;要真学需 reward 方差(更易实例/更大 n_samples/SFT 冷启动模型)。即"管线通≠RL 有效"。

**数据真相(诚实,关键教训)**:swesmith 自产数据真实可用率 **15%**(877 生成→281 真 resolve→133 真干净),不是早期号称的 430/900。崩塌两段:vacuous 打分虚标(见第 4 节)+ git 历史泄露 ≥47%。详见 [[true-data-funnel-2026-06-26]]、`docs/guide/swe-data-options-review.md`。

**现实分数校准**:8B ≠ 32B(SWE-Hero 32B 才 62%)。7-8B 真实区间 ~15-25%。**我们的故事不是分数,是"端到端打通 + 审计出系统性 reward/数据问题 + 在干净 reward 上跑通闭环"**(面向 Agent/基模算法研究工程师岗,判断力 > 分数)。
**全景框架**见 `docs/guide/swe-agent-landscape-framework.md`(7 维度 + 各工作贡献映射 + 横切张力)。

---

## 4. 血泪坑(面试/答辩讲这些最显功力 —— 现象→根因→修法)

1. **特权 pod 选错 NPU**:特权容器能看见宿主全部 8 卡,vLLM 默认抓物理 device 0(别人的忙卡)。根因:CANN 不自动读分配信息。修:`export ASCEND_RT_VISIBLE_DEVICES=$ASCEND_VISIBLE_DEVICES`(分配信息在 env 里)。
2. **脚手架↔数据↔评测格式必须一致**:XML function-calling 格式的数据/模型,放进 bash-backticks 评测会崩(Qwen3-4B 在 XML 下 `exit_format` 空 patch;换 bash 反引号就能产真 patch)。
3. **训/测泄漏**:SWE-bench Verified 只能评测,SFT 数据必须来自 train 集(SkyRL preprocess 明确:train=SWE-Gym,eval=Verified)。
4. **离线 pod 取仓库**:pod host 层有直连外网,但 **chroot 里没 /etc/resolv.conf → 无 DNS**;`git fetch` 在 chroot 内失败。修:用 host 层(no-chroot)建缓存,或预缓存 repo/venv 给 chroot 离线复用;`git fetch || true`(commit 本地已有)。代理 `…225:1080` 会 abort git CONNECT,别用。
5. **patch 要从 `git diff` 真实抓**,不信模型自报的 submit 回显(弱模型常把 submit 协议搞错,回显是 `cat: patch.txt: No such file`)。
6. **打分尺子先做正向对照**:用 gold patch 喂打分,必须被判 resolved,尺子才可信(我们这么发现了 chroot 的 wheelhouse 挂载缺失、缓存复用导致 editable 安装跳过等)。
7. **gold preds 的实例顺序**:raw `datasets` 的 `:5` ≠ harness `--instances.slice :5`(前者 astropy,后者 sympy/django)。按 instance_id 构 preds,别靠顺序。
8. **重型仓库 install 超时**:conan 这类 install_env.sh > 700s;smoke 用轻仓库(oauthlib)。
9. **规模诚实**:别在简历/汇报里写没量到的分数。

**—— 2026-06-28 这轮审计新增(最能讲的几个)——**

10. **vacuous reward(打分形同虚设,最重要)**:model-patch 打分路径里,空补丁(没做任何修复)也判 resolved。根因:`model_patch_file` 参数只在 SWEBench 数据源接了、swesmith 数据源**静默丢弃** → 评的还是原始 bug diff,被 `patch --fuzz` 的 auto-reverse 巧合"反成"了 gold 修复 → 永远过。**怎么抓到的**:lockdown 实验出现"100% resolve + 空补丁也 resolved"的反常 → 设计"**空补丁负控**"(faithful 实例空补丁必须判 0)定位。修法:swesmith 数据源接上 `model_patch_file`;并把负控做成打分脚本的自检 gate 防回归。**讲法:reward hacking / eval 完整性,agent RL 最容易翻车的点。**
11. **救火式排查的局限 → 多角度对抗审计**:我一个人线性排查反复漏根因。改用"4 角度并行审 + 每个发现独立 skeptic 证伪"一轮挖出 13 个确认问题。**讲法:静默错(打分/奖励)必须靠可执行验收 gate + 对抗式交叉验证,不能靠肉眼读代码。**
12. **测试名子串假通过**:grader 用 Python 子串匹配,没收集到的测试会"蹭"同名兄弟(`test_exc_info` 蹭 `test_exc_info_renamed`)的通过状态。修:锚定匹配(精确/参数化 `[...]`/类前缀)。
13. **buggy 门槛混淆**:faithfulness 筛查的 buggy→0 只看 `reward==0`,而 0 混了"测试没跑起来(ModuleNotFound)"。导致 ~104 个污染进 RL 池。修:改"≥1 个 F2P 实测 FAILED"。**讲法:reward==0 不等于"bug 还在",要区分"判失败"和"没判成"。**
14. **swesmith 的 git 历史结构性泄露**:bug = 把干净代码改坏(合成 revert),答案同时在 git历史+PyPI+远程 → 满能力 agent(裸 bash)必抄。**讲法:① 泄露是数据集性质不是 bug;② 动作空间=泄露攻击面;③ 治环境不治 agent(选真实-PR 数据 R2E-Gym,而非阉割 agent 或 flatten 历史)。**
15. **过度声称的毛病(自我修正)**:我多次提前宣布"修好了/RL 池安全",被用户和复检推翻(RL 池实有 6.4% 污染)。**教训:只认带验收 gate 的结论,不口头打包票;按全量分母老实算(15% 不是 46%)。**
16. **模型合并要在 NPU 节点**:FSDP 分片转 HF safetensors 用 `verl.model_merger`,分片是 npu 张量 → 必须在带 torch_npu+驱动的 NPU 节点跑(gpus:0 节点会"no npu device"失败)。已写进训练脚本自动转。

---

## 5. 学习路径(让"做过 SWE agent"经得起追问)

**读论文(按序,各 1–2h)**:
1. SWE-bench(基准、resolved 怎么判)
2. SWE-agent(ACI 脚手架)+ mini-swe-agent(极简脚手架,为何适合训练)
3. SWE-Gym / SWE-smith(训练任务+环境怎么造)
4. SWE-RL / DeepSWE(RL、reward 设计)
5. R2E-Gym、SWE-ZERO→SWE-HERO(更优数据 + 两阶段 SFT)

**必须能脱口而出的概念**:agent loop / ACI;轨迹格式与 loss 掩码;execution-based reward;rejection-sampling SFT(只训 resolved);train/test 卫生;agentic RL(GRPO/PPO + 可验证奖励);蒸馏(strong teacher → small student)。

**最有效的学法 = 亲手重走每一步**:能默画第 1 节的管线图 + 每步杠杆;每个第 4 节的坑能讲清现象→根因→修法;跑通"基线→SFT→分数变化"最小闭环,亲眼看分数动。

---

## 6. 关键文件 / 命令地图

- 生成:`sh/run_minisweagent_gen.{py,sh}`(env: `INSTANCE_SLICE/FILTER`, `NUM_WORKERS`, `GITCACHE_ROOT`, `SHARED_VENV_ROOT`, `MODEL_NAME`, `API_BASE`)
- 打分:`sh/run_swebench_scoring_ascend.sh`(`CONFIG_PATH`=chroot score yaml, `PREDICTIONS_PATH`, `INSTANCE_FILTER`, 缓存 env)
- 轨迹→SFT:`sh/traj_to_sft.py --traj-dir … --out … [--resolved-from results.json]`
- swesmith 环境 smoke:`sh/run_swesmith_envbuild_smoke.sh`(`INSTANCE_FILTER`, `BASE`)
- 配置:`config/sweagent_infer_ascend_officiallike_chroot.yaml`、`config/sweagent_score_ascend_chroot.yaml`、`config/swesmith_infer.yaml`、`config/tune/*`(torchtune SFT 模板)
- tp:`tp task submit -f file.yaml -y`(**特权 + chroot 必须走 YAML 设 `privileged: true`**;`--gpu N` = 2N NPU)
- 数据:`dataset/SWE-smith`(59k 训练任务);`dataset/SWE-bench/SWE-bench_Verified`(评测,500)

---

## 7. 下一步(新会话可直接接)

1. **修 swesmith 打分顺序**:让 `reset_swesmith_tests` 在 `test_patch` **之前**跑(别冲掉规范测试);用轻仓库 gold 对照确认 resolve=1。(`sandbox_deployment.py` 的 `_calculate_reward_swesmith`/`apply_golden`/`setup_env_swesmith` + `reset_swesmith_tests`)
2. **teacher 采样**:强 teacher(API,经 proxy/远端 serve)跑生成 harness 在 SWE-smith train 上,产几百~上千条;打分过滤 resolved。
3. **转 SFT**:`traj_to_sft.py --resolved-from`。
4. **SFT 8B**:swift/MindSpeed(或 torchtune config/tune)→ mcore 转 HF。
5. **评测**:serve SFT 模型 → 生成→打分 → resolved vs 0/10。
6. **(可选)RL**:SkyRL + chroot reward,DeepSWE 式。

---

## 8. 在新会话里可以这样问我(请教清单)

- "把第 1 节的管线逐环节展开讲,每步举一个具体决策和它的 trade-off。"
- "解释 loss 掩码:为什么观测要掩、assistant 才算 loss?给我看转换器怎么做的。"
- "为什么选 mini-swe-agent 不选 SWE-agent?通用性具体强在哪?"
- "execution-based reward 和 SFT 的 rejection sampling 是什么关系?"
- "带我把 swesmith 打分顺序 bug 修了,边修边讲 swesmith 的 reverse-patch 语义。"
- "两阶段 SWE-HERO 为什么有效?execution-free 那步具体怎么产数据?"
- "RL 接到我们这套 chroot reward 上,需要改什么?GRPO 的 reward 怎么设计?"
