# 自主数据采集运行状态 — 2026-06-19

> 你不在的这一天我自主跑的记录。范围:**数据收集**(不开 SFT 训练,按你定的)。
> 核心原则:**正式批量前排净所有隐患**(你强调的)。

## ✅ 交付物

### 1. SFT 数据集(干净协议,resolved-only)
- 文件:`vendor/sft-data/sft_combined.jsonl`
- **224 样本 / 162 唯一实例 / 24 仓库 / 全 4 bug 家族**
  - combine_file 76, func_pm 72, func_basic 58, combine_module 18(~3.5M tokens)
- 平均 33 个 assistant 轮次;mini-swe-agent 干净协议(直接改工作树,无 patch.txt)
- 已做 loss 掩码兼容(ms-swift/torchtune messages 格式,环境观测自动掩掉)
- 来源:gate(16)+ r2-bulk 3尝试(63)+ r1-bulk 3尝试(25)+ **深挖 1尝试(120)**;temp=1.0 多解增强
- **深挖策略验证有效**:已验证仓库 gold-pass 56% vs 随机筛 16%(3.5×),唯一实例 42→162

### 2. validated 实例池(双向验证)
- `vendor/broad_all_validated_ids.json`:**52 实例 / 33 仓库**,全部 **gold→1 ∧ buggy→0**(无假阳干扰项)
- + 深挖 **130 validated**(26 仓库)→ 总 validated 池 **182 实例**

### 3. 排净的管线 + 6 类隐患记录(全在 memory)
这一天最大价值不是数据量,是**把半吊子迁移的隐患全挖出来修掉**:
| # | 隐患 | 修复 |
|---|---|---|
| 1 | 补丁捕获只用 `git diff HEAD` | 三级兜底 worktree→submission→messages |
| 2 | 提交协议残留 SWE-agent 的 patch.txt | 建干净 mini-swe-agent 配置 |
| 3 | swesmith model-patch 打分方向反了(reverse) | `SWE_SANDBOX_SCORE_MODEL_PATCH=1` forward |
| 4 | pod 建的缓存 root 属主,登录节点消费权限失败 | 消费端也上 pod |
| 5 | 并发 pod 抢共享 gitcache → git race | 同缓存同时只一个 pod / 串行 |
| 6 | YAML `>-` 折叠下 for 循环套复杂 run-batch 失败 | 用脚本文件或单 pod |

**关键认知**:harness 的 `git diff HEAD` 会系统性漏抓 agent 已落地的编辑(机制未定但可靠绕过)。**指标是"打分后 resolve 率"不是"非空率"**——打分自动滤掉 apply 失败的垃圾。

## 🔧 运行纪律
- GLM 并发=4 + `num_retries=6` 退避自限流;**429 全程 0**(你不在,我没冒险裸冲)
- 同时只跑一个 GLM 采样 pod(避免触限流)
- 注意到账号有你其他实验(flo-*)在跑,克制不挤占

## 🔄 仍在跑
- **深挖 Stage A(task 6986,CPU)**:33 个已验证仓库各取更多实例(313 探针)→ 预计 yield 远高于随机筛(known-good 仓库)→ 显著扩唯一实例池。完后 Stage B + 采样会继续累积 SFT。

## ⏭️ 你回来后的下一步
1. 看 `sft_combined.jsonl` 是否够大(你提过 swesmith 官方 5k+;本轮受 GLM 2-4 并发限速,~104,要更多需提并发/多跑几天——可让运维放开并发)
2. 定 SFT:基座 **Qwen3-8B**(你已定),mcore(MindSpeed/swift)通路;掩码 assistant-only
3. SFT 后同脚手架在 Verified :100 切片 re-eval 对比基线(8B≈7%)

详细技术细节见 memory:`autonomous-run-2026-06-18.md` + `sft-data-and-recipes.md`。
