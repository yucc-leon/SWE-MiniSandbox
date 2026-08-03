# 夜间推进报告 2026-06-25 → 早间交接

## 一句话头条
排查"lockdown 实验 100% resolve"这个可疑结果时,**发现了打分 harness 的一个真 bug**:`SWE_SANDBOX_SCORE_MODEL_PATCH` 这条 **model-patch 打分路径是 vacuous 的**——一个已验证 faithful 的实例,即便给**空补丁(无任何修复)也会判 resolved**。这影响了所有用这条路径打的 resolve 标签(包括 SFT 的 430"clean"集、以及生成阶段约 96% 的"resolved")。**RL 池不受影响,安全。**

## 怎么发现的(verify-before-assert 起作用)
- lockdown 实验(无网+无 git 历史,50 样本)打分回来 **50/50=100% resolve**,且其中 **13 个空 patch 也判 resolved**。空 patch=没做修复,不可能 resolve → 立刻判定是打分问题,没有上报"记忆底=100%"这个假结论。

## 隔离实验(全部零 GLM 成本,纯打分)
| 实验 | 配置 | 结果 | 含义 |
|---|---|---|---|
| diag-buggy2 | chroot=false, buggy→0 路径, 无 model_patch | 5/5 reward 0 | ✓ 正确(buggy 态 f2p 正确失败) |
| diag-chroot-true-buggy | chroot=true, buggy→0 路径 | 5/5 reward 0 | ✓ 正确 → **chroot 无关** |
| diag-modelpath-cfalse | chroot=false, **model-patch 路径**, 空 patch | 3/4 reward 1 | ✗ vacuous |
| nonet-rescore | chroot=true, model-patch 路径, 13 空 patch | 13/13 reward 1 | ✗ vacuous |
| sft402-rescore | chroot=false, model-patch 路径, **402 真补丁** | 353/358 = **98%** reward 1 | ✗ vacuous(5 个 0 多半是 apply 失败,非真区分) |

**结论:真凶 = model-patch 打分路径本身;不是 chroot,也不是新旧 cache(我先后两个根因猜测都被实验推翻)。** 坏路径在 faithful 集上给 98% resolve = "补丁能 apply 就过",不是"真修复"。生成阶段那 ~96%"resolved"同理。决定性反证 = 空补丁本应判 0、它却判 1。

## 代码层面(已读到的)
- `batch_instances.py:435`:给了 `model_patch_file` 时 `ds['patch']`(=swesmith 的 bug diff)被模型补丁覆盖。
- `swe_sbenv.py:174-229` pre_check:空补丁时跳过应用,应停在 buggy 态。
- `repo.py:50-64` reset = `git reset --hard; git checkout {base_commit}`(bug 分支),**不依赖 ds['patch']** → base 应是 buggy。
- **疑点未锁定**:我读到的两条路径(buggy→0 vs model-patch-空)在代码上**应当等价**,却系统性给出相反 reward。分歧在我没读到的编排/状态层(疑似 `--agent.pre_check` 在 run_batch 里触发了额外的 gold 校验/二次应用,把 repo 留在 fixed 态)。**精确定位需要你对自己 harness 预期流程的了解,或带 instrumentation 重跑一次。**

## 影响盘点
- **安全:RL 池(1620 = 954 faithful_verified + 666 ds_buggy)**。它是用 gold→1(deepscreen)+ buggy→0(ds_buggy 666/688)双向筛的,**这条路径已证明正确**(vacuous 打分器过不了 buggy→0)。RL reward 也走这条路径。
- **受影响:SFT 的 resolve 标签**。430"clean"SFT 的 resolved 是用坏路径打的 → **resolve 未独立验证**。但这 430(及其补丁可回收的 402)仍然是:faithful 实例 + 非空补丁 + 干净轨迹(已过 git-leak 过滤)。GLM 是强 teacher(~90%+ resolve),先验上多数是真修复。**可作 cold-start SFT(教格式/推理),correctness 由 RL 阶段的正确打分兜底。**

## 成本(守住"别花太多 / 每次调用记账")
- 今晚**无新增显著 GLM 开销**:所有诊断/重打分任务都是 empty-agent 打分(不调模型)。
- **没有启动 300-lockdown 扩量**:它的依据(R_B 实验)作废了——实验没"显示值得",反而暴露了打分 bug。符合"若实验显示值得才花"。

## 当前任务状态
- **8B SFT smoke(7575)= 已验真成功**:loss 下降 █▅▃▂▁→0.692,global_step_5 ckpt 完整保存(FSDP SP=8 + 全 offload,8 卡不 OOM),MFU 0.197。**训练路径跑通。**
- **🎉 8B 全量 SFT 训练已完成**(7590 任务成功,SMOKE=0,3 epoch×26=78 步 on 430)。**train/loss 0.69→0.377**(降约一半,真学习),~20s/step,无 OOM。ckpt:`vendor/sft-ckpt/swe_sft_8b/global_step_{5,50,78}`。**全流程打通:自产 SFT 数据 → Ascend 8 卡 verl FSDP SFT → 训好的 8B cold-start 模型。**
  - ⚠️ **下一步(不阻塞)**:`global_step_78/huggingface/` 只有 config+tokenizer,权重是 FSDP 分片(`model_world_size_8_rank_*.pt`)。下游推理/eval 前需跑 verl model-merger 合成 HF safetensors(同你给 4b 做过的)。
- **RL 池扩充中**(7591 ds2-buggy):deepscreen2 的 620 个新 gold→1 正在做 buggy→0 过滤(正确路径,无 GLM),通过者并入 RL 池(当前 1620 → 预计 ~2200)。
- cold-start SFT 数据:430(vendor/sft-data/sft_clean.parquet)。resolve 口径=未独立验证(诚实标注)。
- RL 池:1620 就绪。
- 7582(坏路径定量)最终 354/359 = 98.6% vacuous。

## 需要你决策
1. **打分 harness bug**:要不要修 model-patch 打分路径(你的代码,我可以配合定位)?还是接受 resolve-unverified 的 cold-start SFT 先训起来?
2. 8B 全量训练:NPU 一空就按现有 430 起?还是等打分修好、重筛 resolve 后再起?
