# Current Baseline

本文档记录当前已经跑通、可作为后续模型对齐起点的 SWE-agent LM 7B on Ascend 评测基线。

它不是 strict official reproduction。当前口径更准确地说是：

- MiniSandbox no-chroot runtime
- SWE-agent generation
- OpenAI-compatible remote vLLM server
- patch replay scoring
- SWE-bench harness official-style resolved 判定

如果需要区分不同评测口径，先看 `docs/guide/eval-modes.md`。

## 1. 当前结论

截至 2026-04-20，当前可接受基线是：

- model: `sweagent-7b`
- model path: `/sharedata/liyuchen/models/sweagent-7b`
- inference server: remote vLLM on Ascend Pod
- API base used in this run: `http://192.168.129.148:8001/v1`
- generation config: `config/sweagent_infer_ascend_officiallike.yaml`
- scoring config: `config/sweagent_score_ascend.yaml`
- total evaluated instances: 500
- cumulative resolved: `61/500`
- cumulative resolved rate: `12.2%`

这个数值接近我们希望打平的 x86_64 同模型同设置经验区间 `13-14%`，可以作为后续模型横向比较的第一版工程基线。

同时保留了一次更早的本地 full validation：

- runtime: `.runtime/ascend-score-sweagent7b-real-officiallike-500-4way-prewarm`
- resolved: `64/500`
- resolved rate: `12.8%`

这两次结果都说明当前 Ascend + MiniSandbox 链路大体可用，但还不能声称完全复现官方 leaderboard 分数。

## 2. 运行链路

远端 server 侧使用：

```bash
export MODEL_PATH=/sharedata/liyuchen/models/sweagent-7b
export MODEL_NAME=sweagent-7b
export MAX_MODEL_LEN=32768
export PORT=8001
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox
bash sh/serve_qwen_ascend.sh
```

如果需要让 `/v1/models` 暴露短模型名，必须使用已经包含 `--served-model-name` 支持的新版本脚本，并重启 server。旧进程不会自动继承这个修复。

本地 evaluation + scoring 侧使用：

```bash
API_BASE=http://192.168.129.148:8001/v1 \
MODEL_NAME=/sharedata/liyuchen/models/sweagent-7b \
MODEL_PATH=/sharedata/liyuchen/models/sweagent-7b \
INSTANCE_SLICE=200:500 \
NUM_WORKERS=16 \
PREPARE_NUM_WORKERS=16 \
POSTPROCESS_DATASET_SIZE=300 \
PREPARE_FIRST=0 \
PROBE_TIMEOUT=60 \
PROBE_REQUEST_TIMEOUT=20 \
EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent7b-pod-200-500w16 \
SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent7b-pod-200-500w16 \
bash sh/run_sweagent_formal_remote_infer.sh
```

其他分片只需要替换 `INSTANCE_SLICE`、`POSTPROCESS_DATASET_SIZE`、`EVAL_RUNTIME_ROOT` 和 `SCORE_RUNTIME_ROOT`。

## 3. 分片结果

本轮 500 题由多个分片组成：

| Slice | Workers | Score root | Resolved | Rate | Notes |
| --- | ---: | --- | ---: | ---: | --- |
| `0:50` | 8 | `.runtime/ascend-score-sweagent7b-pod-50w8` | `5/50` | `10.0%` | no scoring error |
| `50:100` | 8 | `.runtime/ascend-score-sweagent7b-pod-50-100w8` | `9/50` | `18.0%` | one scored-without-submission |
| `100:200` | 8 | `.runtime/ascend-score-sweagent7b-pod-100-200w8` | `16/100` | `16.0%` | one scoring error |
| `200:500` | 16 | `.runtime/ascend-score-sweagent7b-pod-200-500w16` | `31/300` | `10.33%` | higher workers, more missing/error cases |

Cumulative:

```text
resolved = 5 + 9 + 16 + 31 = 61
total = 500
resolved_rate = 12.2%
```

The `200:500` shard had more instability:

- submitted instances: `267/300`
- scored instances: `288/300`
- completed instances: `257/300`
- empty patch instances: `77`
- error instances: `12`
- scored without submission instances: `31`

这部分下降可能来自 slice 难度，也可能来自 `NUM_WORKERS=16` 下远端服务和 sandbox 调度的长尾不稳定。当前不能只凭这一片判断模型能力下降。

## 4. 当前配置口径

当前默认评测入口是：

- `sh/run_sweagent_formal_remote_infer.sh`

它做的是：

1. probe remote OpenAI-compatible server
2. run generation with `sh/run_sweagent_eval_ascend.sh`
3. require `preds.json`
4. run scoring with `sh/run_swebench_scoring_ascend.sh`

当前 generation config 是 `config/sweagent_infer_ascend_officiallike.yaml`：

- prompt 尽量保持 SWE-agent 风格
- tool call parser 是 `xml_function_calling`
- vLLM 不需要启用 `TOOL_CALL_PARSER`
- temperature 是 `0.0`
- max input tokens 是 `32768`
- step limit 是 `400`
- total time limit 是 `1200`

当前 scoring config 是 `config/sweagent_score_ascend.yaml`：

- agent type 是 `empty`
- 读取 generation 阶段产出的 patch
- 在 sandbox 内 replay patch
- 用 SWE-bench harness 判定 resolved

## 5. 已知偏差和风险

当前基线适合做模型横向比较和工程链路联调，但有以下偏差：

- runtime 是 MiniSandbox no-chroot，不是官方 Docker harness。
- scoring 是 patch replay，不是官方原始一体化运行路径。
- generation 通过远端 OpenAI-compatible vLLM，不是官方 provider 设置。
- `officiallike` 配置包含工程增强，不等同 strict official。
- 高 worker 数下可能提高吞吐，但也可能放大长尾、空 patch、server timeout 或 sandbox 调度问题。

因此，对外表述建议使用：

```text
Ascend MiniSandbox official-style evaluation baseline: 61/500 = 12.2%.
```

不建议表述为：

```text
Official SWE-bench Verified score: 12.2%.
```

## 6. 下一步建议

短期建议保持 prompt 不动，先用同一条链路测试更多模型：

- 只替换 `API_BASE`、`MODEL_NAME`、`MODEL_PATH`
- 固定 `EVAL_CONFIG_PATH` 和 `SCORE_CONFIG_PATH`
- 优先记录 `resolved_rate`、`empty_patch_instances`、`error_instances`、`scored_without_submission_instances`
- 对比模型时尽量保持相同 slice、workers、server 并发和 max context

如果要继续向 RL training 过渡，建议先把 evaluation 稳定性指标纳入实验表。resolved rate 只能说明最终结果，empty/error/no-submission 更能定位训练数据和 rollout 基础设施的问题。

## 7. 下一候选模型：SWE-agent-LM-32B

下一轮精度对齐优先下载并测试同家族模型：

- Hugging Face repo: `SWE-bench/SWE-agent-LM-32B`
- local path: `/sharedata/liyuchen/models/sweagent-32b`
- served model name: `sweagent-32b`

下载完成后，远端 server 侧建议启动为：

```bash
export MODEL_PATH=/sharedata/liyuchen/models/sweagent-32b
export MODEL_NAME=sweagent-32b
export SERVED_MODEL_NAME=sweagent-32b
export MAX_MODEL_LEN=32768
export PORT=8001

cd /sharedata/liyuchen/workspace/SWE-MiniSandbox
bash sh/serve_qwen_ascend.sh
```

本地先跑 smoke：

```bash
API_BASE=http://192.168.129.148:8001/v1 \
MODEL_NAME=sweagent-32b \
MODEL_PATH=/sharedata/liyuchen/models/sweagent-32b \
INSTANCE_SLICE=:5 \
NUM_WORKERS=4 \
PREPARE_FIRST=0 \
POSTPROCESS_DATASET_SIZE=5 \
EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent-32b-smoke-5 \
SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent-32b-smoke-5 \
bash sh/run_sweagent_formal_remote_infer.sh
```

如果 `:5` 没有协议、格式或 scoring 问题，再跑 `:50`。不要直接用 500 题判断新模型，因为 server 兼容性、batch token 设置和长尾稳定性都需要先确认。
