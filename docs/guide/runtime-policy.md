# Runtime Policy

本文档固定当前项目的运行时边界：

- **evaluation 可以使用 no-chroot 作为受限机器上的替代方案**
- **RL training 必须使用 chroot/mount namespace 或 Docker/container 等价隔离**

这个边界的目标是避免把评测调试方案误用成训练 reward 方案。

## 1. 结论

no-chroot 的定位是 evaluation fallback，而不是 training runtime。

当前 Ascend/远端推理评测链路可以继续使用 MiniSandbox no-chroot：

- 本地 CPU 机器提交 SWE-agent generation
- 远端 OpenAI-compatible vLLM/vLLM-Ascend server 提供模型推理
- scoring 阶段 replay `preds.json`
- SWE-bench harness 给出 official-style resolved

这条链路适合：

- 模型横向对比
- 远端推理服务调试
- scoring pipeline 调试
- 受限机器上验证评测吞吐和 resolved rate

RL training 仍然要求 chroot/mount namespace。原因是训练会反复消费 reward；no-chroot 中任何路径重写、`/tmp`、`/proc`、venv cache 的偶发污染都可能变成系统性梯度噪声。

## 2. Evaluation Fallback: No-Chroot

评测 fallback 使用这些配置和入口：

- generation config: `config/sweagent_infer_ascend_officiallike.yaml`
- scoring config: `config/sweagent_score_ascend.yaml`
- formal local wrapper: `sh/run_sweagent_formal_ascend.sh`
- formal remote wrapper: `sh/run_sweagent_formal_remote_infer.sh`
- pipeline scoring: `PIPELINE_SCORING=1`

这些配置显式使用：

```yaml
instances:
  deployment:
    use_chroot: false
```

当前 no-chroot 已做的稳定性加固：

- 每个 sandbox 启动时设置实例级 `TMPDIR/TMP/TEMP=$root_dir/tmp`
- no-chroot 路径重写覆盖 `/testbed`、tools、`/root`、`/run_tests.sh`、`/res.patch`
- shared venv cache 解压加文件锁
- symlink 替换使用临时 symlink + `os.replace`
- no-chroot teardown 不再 unlink 进程共享的 absolute shared venv symlink

这些修复降低评测噪声，但不等价于 namespace 隔离。

## 3. Training Runtime: Chroot Required

SkyRL/SWE-agent RL training 必须走更强隔离：

- `use_chroot: true`
- 容器/Pod 具备 `CAP_SYS_ADMIN` 或等价权限
- `unshare --mount` 可用
- `chroot` 可用
- 或使用 Docker/container deployment，而不是 MiniSandbox no-chroot

训练入口中应显式传入：

```bash
+generator.sweagent.instances.deployment.use_chroot=true
```

训练前置检查：

```bash
bash sh/require_chroot_training.sh
```

如果这一步失败，不应启动 RL training。可以继续跑 evaluation fallback，但不能把 no-chroot reward 用作 policy update。

## 4. Why This Boundary Exists

评测和训练对环境噪声的容忍度不同。

评测中，一道题偶发因为环境问题计 0，主要影响统计误差。

训练中，同一道题会被多次采样，false negative reward 会被优化器反复放大。典型风险包括：

- 修复正确但路径重写遗漏导致 reward=0
- 多个 rollout 共享宿主 `/tmp` 或硬编码 `/tmp/foo`
- no-chroot 共享宿主 `/proc`
- shared venv/cache 在高并发下被另一个实例改写或删除
- retry 掩盖环境错误，吞吐下降但 reward 噪声仍进入训练

因此当前策略是：

- no-chroot: keep it stable enough for evaluation fallback
- chroot/Docker: use it for training reward

## 5. Operational Checklist

评测 fallback:

1. 确认远端 vLLM server `/v1/models` 和 chat probe 正常。
2. 使用 `sh/run_sweagent_formal_remote_infer.sh` 或 `sh/run_sweagent_formal_ascend.sh`。
3. 对正式分数使用 scoring `results.json`，不要用 generation 本地 reward summary。
4. 监控 `error_instances`、failed shard、empty patch、submitted rate。

Pipeline scoring 吞吐调优：

- `PIPELINE_SCORE_BATCH_SIZE`: 每个 scoring shard 包含多少实例。
- `PIPELINE_SCORE_NUM_WORKERS`: 每个 shard 内的 SWE-agent `run-batch` worker 数。
- `PIPELINE_SCORE_MAX_CONCURRENT_SHARDS`: 同时运行多少个 scoring shard。
- 有效评分并发约等于 `PIPELINE_SCORE_NUM_WORKERS * PIPELINE_SCORE_MAX_CONCURRENT_SHARDS`。

在 96 CPU core 的机器上，可以先试：

```bash
PIPELINE_SCORE_BATCH_SIZE=8
PIPELINE_SCORE_NUM_WORKERS=4
PIPELINE_SCORE_MAX_CONCURRENT_SHARDS=2
```

如果 load、I/O、venv cache 都稳定，再逐步提高到 `8 * 4 = 32` 或类似规模。不要一开始直接拉满，因为 scoring 不是纯 CPU，环境构建和测试 I/O 也会争用。

训练:

1. 先运行 `bash sh/require_chroot_training.sh`。
2. 确认训练 config 或命令行覆盖 `use_chroot=true`。
3. 在小 batch 上做 reward replay canary，确认同 patch 重复评分一致。
4. 只有在 reward 稳定后再扩大 rollout 并发。
