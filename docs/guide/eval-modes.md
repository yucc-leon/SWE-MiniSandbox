# Evaluation Modes

本文档把当前仓库里的 evaluation 口径拆成几层，避免把“官方对齐实验”和“工程化增强实验”混在一起解释。

## 1. 先说结论

当前仓库里默认常用的 `officiallike` 配置，不应理解为“已经等同官方评测”。

它更准确的定义是：

- 运行时仍然是 MiniSandbox，而不是官方 Docker harness
- scoring 虽然调用了 `swebench.harness.grading`
- 但 generation 和 scoring 都叠加了本地工程化改动

因此，它适合做工程迭代和模型横向比较，不适合直接拿来回答“和官方 leaderboard 差多少”。

## 2. 推荐的三层口径

### 2.1 MiniSandbox Baseline

这是当前最适合做“分数对齐”实验的口径。

目标是：

- 保留当前必须存在的基础设施差异
  - MiniSandbox
  - `use_chroot: false`
  - OpenAI-compatible 推理服务
  - patch replay scoring
- 但尽量关掉额外的行为改动

建议使用：

- generation: `config/sweagent_infer_ascend_minisandbox_baseline.yaml`
- scoring: `config/sweagent_score_ascend_minisandbox_baseline.yaml`

这套 baseline 目前显式关掉了两类最容易污染对齐实验的偏差：

- generation 不再附加 repo-scan guard，也不再在 prompt 里强行要求“不要扫全仓”
- scoring 关闭本地 `swebench` 豁免项：
  - `instance_to_skip`
  - `pytest-dev/pytest` 的 `minversion` 放宽
  - `instance_map(...)` 的 failure 过滤
  - `pylint-dev/pylint` 的特判裁剪

### 2.2 Engineering Mode

这是当前默认在跑的工程增强口径。

典型入口和配置是：

- `sh/run_sweagent_formal_ascend.sh`
- `config/sweagent_infer_ascend_officiallike.yaml`
- `config/sweagent_score_ascend.yaml`

它的目标不是最小偏差，而是更稳地把全流程跑通。典型增强包括：

- repo-scan guard
- 更强的 prompt 行为约束
- local scoring overrides
- prepare/prewarm
- watchdog / remote inference probe / 运维侧恢复逻辑

这层结果适合：

- 模型间横向比较
- 工程稳定性迭代
- 从 evaluation 过渡到 RL training 的基础设施联调

但不适合直接声称“等同官方分数”。

### 2.3 Strict Official

这一层当前仓库里还没有完整落地。

原因很简单：

- runtime 不是官方 Docker harness
- scoring 还是 patch replay
- 仍然经过本仓库的 orchestration 路径

所以严格意义上的官方对齐，仍然需要单独保留一条更接近官方 harness 的实验链。

## 3. 现在怎么跑 baseline

formal wrapper 现在已经支持把 generation config 和 scoring config 分开指定。

本地推理模式：

```bash
ROOT=$(pwd)
EVAL_CONFIG_PATH="${ROOT}/config/sweagent_infer_ascend_minisandbox_baseline.yaml" \
SCORE_CONFIG_PATH="${ROOT}/config/sweagent_score_ascend_minisandbox_baseline.yaml" \
INSTANCE_SLICE=:30 \
bash "${ROOT}/sh/run_sweagent_formal_ascend.sh"
```

远端推理模式：

```bash
ROOT=$(pwd)
EVAL_CONFIG_PATH="${ROOT}/config/sweagent_infer_ascend_minisandbox_baseline.yaml" \
SCORE_CONFIG_PATH="${ROOT}/config/sweagent_score_ascend_minisandbox_baseline.yaml" \
INFERENCE_TASK_RUNTIME_ROOT=.runtime/remote-infer-sweagent7b \
INSTANCE_SLICE=:30 \
bash "${ROOT}/sh/run_sweagent_formal_remote_infer.sh"
```

## 4. 当前 baseline 仍然有哪些偏差

即便用了 `minisandbox_baseline`，它也不是官方原样。至少还有这些偏差：

- runtime 仍是 MiniSandbox，不是官方 Docker harness
- `use_chroot: false` 会带来路径重写和 shell 行为差异
- generation 仍通过 OpenAI-compatible server，而不是官方闭环 provider 设置
- scoring 仍通过 `empty` agent replay `preds.json`

所以它的正确定位是：

`cleaner Minisandbox baseline for score-alignment experiments`

而不是：

`official score reproduction`
