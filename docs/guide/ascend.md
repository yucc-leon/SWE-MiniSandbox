# Ascend Guide

This note tracks the current bring-up plan for running SWE evaluation and RL preparation on an Ascend workstation.

## Current Assumptions

The examples below match the current machine layout:

- Miniforge root: `/sharedata/liyuchen/miniforge3`
- MiniSandbox Python backends: `/sharedata/liyuchen/minisandbox-conda/{3.6,3.7,3.8,3.9,3.10,3.11,3.12}/miniconda3`
- Evaluation env: `swe-sandbox`
- Model serving env: `vllm-ascend-cann8.3-v011-clean`
- Local models: `/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507`, `/sharedata/liyuchen/models/Qwen3-8B`

`npu-smi` is available on this machine, so local NPU-backed serving is possible.

## Evaluation Path

The recommended split is:

1. Run the OpenAI-compatible model server in the Ascend vLLM env.
2. Run `sweagent run-batch` in the MiniSandbox env.

Bootstrap the evaluation env first:

```bash
bash sh/bootstrap_minisandbox_backends.sh
bash sh/bootstrap_swe_sandbox_env.sh
```

Start a local model server:

```bash
bash sh/serve_qwen_ascend.sh
```

Run SWE-bench evaluation after adjusting `DATASET_DIR` if needed:

```bash
DATASET_DIR=/path/to/SWE-bench_Verified/data \
bash sh/run_sweagent_eval_ascend.sh
```

For the current docker-free SWE-bench path, separate generation from scoring:

1. Generate predictions with `sh/run_sweagent_eval_ascend.sh`
2. Rescore the resulting `preds.json` with `sh/run_swebench_scoring_ascend.sh`

This keeps the runtime sandbox/container-free while aligning the final metric to
the SWE-bench `resolved` notion instead of the older local reward summary.

Recommended generation entrypoint for the official-like docker-free path:

```bash
ROOT=$(pwd)
RUNTIME_ROOT="${ROOT}/.runtime/ascend-eval-officiallike" \
CONFIG_PATH="${ROOT}/config/sweagent_infer_ascend_officiallike.yaml" \
API_BASE=http://127.0.0.1:8000/v1 \
AUTO_START_SERVER=0 \
MODEL_NAME=/sharedata/liyuchen/models/sweagent-7b \
MAX_MODEL_LEN=32768 \
NUM_WORKERS=10 \
INSTANCE_SLICE=:500 \
POSTPROCESS_EVAL=0 \
bash sh/run_sweagent_eval_ascend.sh
```

Recommended official-style rescoring entrypoint:

```bash
ROOT=$(pwd)
RUNTIME_ROOT="${ROOT}/.runtime/ascend-score-officiallike" \
PREDICTIONS_PATH="${ROOT}/.runtime/ascend-eval-officiallike/output/preds.json" \
NUM_WORKERS=10 \
INSTANCE_SLICE=:500 \
POSTPROCESS_SCORING=1 \
bash sh/run_swebench_scoring_ascend.sh
```

The rescoring output is written under `RUNTIME_ROOT` and includes:

- `results.json`: aggregate resolved/unresolved summary
- `instance_results.jsonl`: per-instance official-style grading result

Current defaults are the working 8.3 baseline:

- `sh/serve_qwen_ascend.sh` uses `vllm-ascend-cann8.3-v011-clean`, sources Ascend `set_env.sh`, uses `Qwen3-4B-Instruct-2507`, `MAX_MODEL_LEN=65536`, `ENFORCE_EAGER=0`, `--block-size 128`, `--max-num-batched-tokens 4096`, and `--max-num-seqs 128`
- `sh/run_sweagent_eval_ascend.sh` uses `NUM_WORKERS=1` and `INSTANCE_SLICE=:1`
- `sh/run_sweagent_eval_ascend.sh` points `instances.deployment.conda_env` at `/sharedata/liyuchen/minisandbox-conda`
- the evaluation helper exports `PYTHONPATH` for the local workspace and does not reinstall editable packages unless `BOOTSTRAP_EDITABLES=1`
- `sh/run_sweagent_eval_ascend.sh` will auto-start `sh/serve_qwen_ascend.sh` when `agent.model.api_base` is unavailable, then wait for `/v1/models` before entering `run-batch`
- `sh/serve_qwen_ascend.sh` now uses `PREFLIGHT_TIMEOUT=60` because `import vllm` on the 8.3 env takes about 28 seconds on this machine
- on CANN 8.3, `Application startup complete` can appear well before the API is actually ready; on this machine we observed roughly `~97s` between `Application startup complete` and the first successful `GET /v1/models 200` on a known-good `sweagent-7b` multi-server run
- because of that delay, do not diagnose the engine as broken from early `503` results alone; always let the helper wait through the full readiness window unless there is an explicit crash, OOM, or traceback

## Verified Bring-up

The following path is now verified on this workstation:

- `vllm-ascend-cann8.3-v011-clean` can serve `Qwen3-4B-Instruct-2507` with `DP=1`, `API_SERVER_COUNT=1`, `MAX_MODEL_LEN=65536`, `ENFORCE_EAGER=0`
- `/health` and `/v1/models` return `200`
- `chat/completions` returns valid responses
- `sh/run_sweagent_eval_ascend.sh` can complete a smoke run against that local server, producing `.traj`, `.pred`, `.patch`, and `run_batch_exit_statuses.yaml`
- `sh/run_swebench_scoring_ascend.sh` can replay `preds.json` in the same docker-free sandbox and produce `results.json` / `instance_results.jsonl`

## Framework Invariants

For the docker-free evaluation framework, these properties should hold regardless
of model quality:

- the agent should see logical sandbox paths such as `/testbed`, `/tools`, and `/root`, not host-side `.runtime/.../sandbox/...` paths
- no-chroot observations should not contain large runs of bare carriage returns (`\r`)
- tool bundles should be installed into the sandbox tool root once, without duplicate uploads or `/root/tools` assumptions
- patch extraction should come from the environment abstraction rather than a hard-coded working directory assumption

If one of these invariants regresses, treat it as a framework bug before drawing
any conclusion from resolve rate.

Recommended smoke test:

```bash
MODEL_PATH=/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507 \
PORT=8001 \
API_SERVER_COUNT=1 \
MAX_MODEL_LEN=65536 \
ENFORCE_EAGER=0 \
bash sh/serve_qwen_ascend.sh
```

Then in another shell:

```bash
API_BASE=http://127.0.0.1:8018/v1 \
MODEL_NAME=/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507 \
NUM_WORKERS=1 \
INSTANCE_SLICE=:2 \
AUTO_START_SERVER=0 \
bash sh/run_sweagent_eval_ascend.sh
```

## Working Recipe

If you only want the shortest path that is known to work on this machine, use this section.

### 1. Environments to use

- Evaluation env: `swe-sandbox`
- Serving env: `vllm-ascend-cann8.3-v011-clean`
- MiniSandbox backend interpreters:
  `/sharedata/liyuchen/minisandbox-conda/{3.6,3.7,3.8,3.9,3.10,3.11,3.12}/miniconda3`

### 2. How to build the serving env

The important point is that the final `vllm` and `vllm-ascend` are both installed from source, and `vllm-ascend` must be built for A3.

Create a clean env:

```bash
conda create -y -n vllm-ascend-cann8.3-v011-clean python=3.11 pip
conda activate vllm-ascend-cann8.3-v011-clean
```

Install the 8.3-compatible PyTorch/NPU base:

```bash
python -m pip install \
  attrs 'numpy<2.0.0' decorator sympy cffi pyyaml pathlib2 psutil protobuf scipy \
  requests absl-py wheel typing_extensions ml-dtypes tornado flask hypercorn \
  'setuptools<81' quart pandas pandas-stubs 'setuptools-scm>=8' \
  --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

python -m pip install --no-deps \
  torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 torch-npu==2.7.1 \
  --extra-index-url https://mirrors.huaweicloud.com/ascend/repos/pypi/simple \
  --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
```

Install `vllm` from source:

```bash
git clone --depth 1 --branch v0.11.0 https://github.com/vllm-project/vllm.git
cd vllm
export VLLM_TARGET_DEVICE=empty
python -m pip install --no-build-isolation -e .
```

Clone and build `vllm-ascend` from source for A3:

```bash
git clone --depth 1 --branch v0.11.0 https://github.com/vllm-project/vllm-ascend.git
cd vllm-ascend
git submodule update --init --recursive

export SOC_VERSION=ascend910_9391
python -m pip install --no-build-isolation -e .
```

### 3. How to run the model server

The baseline that is currently verified is:

```bash
MODEL_PATH=/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507 \
PORT=8001 \
VLLM_ENV_NAME=vllm-ascend-cann8.3-v011-clean \
API_SERVER_COUNT=1 \
MAX_MODEL_LEN=65536 \
ENFORCE_EAGER=0 \
bash sh/serve_qwen_ascend.sh
```

If `Application startup complete` appears but `/health` is still `503`, keep waiting. On this machine the 8.3 stack can take another 1-2 minutes before the API becomes healthy, and we have already seen a clean run where the first `GET /v1/models 200` arrived roughly `97s` after startup-complete.

Do not treat this specific pattern as evidence that the inference engine is damaged:

- `Application startup complete`
- early `/health` or `/v1/models` returns `503`
- no traceback, no OOM, and no process exit

That combination has repeatedly turned out to be a slow-ready state rather than a broken server.

### 4. Quick checks

Check health:

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  curl -sS http://127.0.0.1:8001/health
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  curl -sS http://127.0.0.1:8001/v1/models
```

Note: on this machine, forgetting to clear `ALL_PROXY`/`all_proxy` can produce
misleading localhost `503` responses with `Proxy-Connection: close`. Always use
the `env -u ...` form above when probing local ports.

Check one real completion:

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  curl -sS http://127.0.0.1:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507",
    "messages": [{"role": "user", "content": "reply with ok"}],
    "max_tokens": 8,
    "temperature": 0
  }'
```

Check evaluation can really talk to the server:

```bash
API_BASE=http://127.0.0.1:8001/v1 \
MODEL_NAME=/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507 \
NUM_WORKERS=1 \
INSTANCE_SLICE=:2 \
AUTO_START_SERVER=0 \
bash sh/run_sweagent_eval_ascend.sh
```

Success means:

- `/health` returns `200`
- `/v1/models` returns `200`
- `chat/completions` returns JSON instead of `503`
- evaluation produces `.traj`, `.pred`, `.patch`, and `run_batch_exit_statuses.yaml`

### 5. Multi-server entrypoint

The recommended multi-NPU orchestration entrypoint is:

```bash
bash sh/run_sweagent_eval_ascend_multi.sh
```

It now:

- detects the available NPU device ids from `npu-smi`
- groups them by `NPUS_PER_SERVER`
- computes how many model servers can be launched on the current machine
- either shards the evaluation across those servers or fronts them with the local proxy

The older `sh/run_sweagent_eval_ascend_dual.sh` name is still kept as a compatibility alias, but it is no longer limited to exactly two servers.

## Pitfalls We Actually Hit

These issues were real on this machine and are worth checking first before suspecting the evaluation code.

### 1. The old `v013` env was not clean

The historical `vllm-ascend-cann8.5-v013` env had conflicting package metadata and produced misleading symptoms such as:

- `Application startup complete` followed by `/health` or `/v1/models` returning `503`
- `no-eager` appearing to "half work" and then collapsing under real requests

The root cause was not one flag. The environment itself was inconsistent.

### 2. Official `vllm-ascend` wheels can be the wrong device type

On this A3 workstation, the official `vllm-ascend==0.17.0rc1` wheel from pip installed an A2 build.

The failure looked like:

```text
AssertionError: Current device type: AscendDeviceType.A3 does not match the installed version's device type: AscendDeviceType.A2

### 3. Auto-start can fail for two unrelated reasons

We hit both of these while validating `AUTO_START_SERVER=1`:

- a short preflight timeout will kill the server before startup because `import vllm` in the 8.3 env takes about 28 seconds
- if you run the auto-start smoke inside a restrictive Codex sandbox, `vllm serve` may fail to bind `8001` with `PermissionError: [Errno 1] Operation not permitted`

If the local shell can start the server manually but the auto-start smoke fails, check these two items first before debugging the evaluation code.
```

The fix was to rebuild `vllm-ascend` from source with:

```bash
SOC_VERSION=ascend910_9391
```

Do not assume that "official wheel installed successfully" means it matches the actual Ascend device type.

### 3. `pkg_resources` may be missing even when `setuptools` is installed

With newer `setuptools`, `torch_npu -> torchair` failed during worker startup with:

```text
ModuleNotFoundError: No module named 'pkg_resources'
```

Pinning `setuptools<81` restored `pkg_resources` and removed that startup failure.

### 4. `ALL_PROXY` caused fake localhost failures

Clearing only `HTTP_PROXY` and `HTTPS_PROXY` was not enough. When `ALL_PROXY` was left set, requests to `127.0.0.1` were still routed out through the machine proxy, which produced fake `503` errors from the proxy instead of the local model server.

Always clear:

- `http_proxy`
- `https_proxy`
- `HTTP_PROXY`
- `HTTPS_PROXY`
- `all_proxy`
- `ALL_PROXY`

and keep `NO_PROXY=127.0.0.1,localhost,0.0.0.0`.

If a localhost probe returns headers like these:

```text
HTTP/1.1 503 Service Unavailable
Proxy-Connection: close
Content-Length: 0
```

do not blame vLLM yet. That response came from the machine proxy path, not from the local uvicorn server.

### 5. `ENFORCE_EAGER=0` is still not the baseline here

Even after cleaning the environment, `no-eager` was not the stable baseline on this workstation:

- large context settings led to NPU OOM
- shorter settings could still enter unhealthy states during serve startup

For now, the reliable baseline remains:

- `ENFORCE_EAGER=1`
- `MAX_MODEL_LEN=16384`

Long-context tuning can be revisited after evaluation throughput is stable.

### 6. Do not confuse LiteLLM provider names with vLLM model ids

For local OpenAI-compatible vLLM serving, there are two different identifiers:

- provider-style names used by LiteLLM, e.g. `openai//sharedata/...`
- the actual model id exposed by local vLLM, e.g. `/sharedata/...`

The local server accepts the second form, not the first one.

We confirmed on this machine:

- `model=/sharedata/...` -> accepted by vLLM
- `model=openai//sharedata/...` -> rejected by vLLM

There is a second trap here:

- if the client sends `openai//...` straight through, vLLM rejects it
- if the config is switched to pure `/sharedata/...` but the request path still goes through raw LiteLLM provider inference, the client can fail earlier with:

```text
LLM Provider NOT provided
```

The working rule is:

- use the real local model path in evaluation configs and shell scripts
- when talking to a local OpenAI-compatible vLLM server, send requests with the OpenAI client using that real local model path

Do not reopen server debugging until these two checks pass:

1. localhost probes are run with all proxy variables cleared
2. the request payload uses the real served model id, not `openai//...`

## Dependency Safety

This repo currently depends on `litellm` through `SWE-agent` and `R2E-Gym`.
Because the `1.82.7` and `1.82.8` releases were reported compromised on March 25, 2026,
the bootstrap script pins `litellm==1.82.6` instead of leaving it unbounded.

## Config Notes

`config/sweagent_infer_ascend.yaml` is the Ascend-oriented baseline config. It uses:

- MiniSandbox deployment instead of Docker
- local OpenAI-style model serving at `http://127.0.0.1:8001/v1`
- the real served vLLM model ID as `agent.model.name`
  (for local models this is the absolute model path, not `openai//...`)
- `use_chroot: false` to avoid depending on privileged namespace setup

This no-chroot path is an evaluation fallback only. RL training should still use
chroot/mount namespace or Docker/container isolation. See `docs/guide/runtime-policy.md`.

`sh/run_sweagent_eval_ascend.sh` also exports `NO_PROXY=127.0.0.1,localhost,0.0.0.0`
and clears `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` (upper and lower case)
so LiteLLM/OpenAI clients do not accidentally send localhost traffic through the machine proxy.

You can override paths at runtime from `sh/run_sweagent_eval_ascend.sh`.

For larger formal runs, prefer a prewarm-first flow so repository state and shared
virtualenvs are prepared locally before agent generation starts. The repo now ships
`sh/run_sweagent_formal_ascend.sh`, which defaults to:

- `PREPARE_FIRST=1`
- the real served 7B model id `/sharedata/liyuchen/models/sweagent-7b`
- the `officiallike` config
- automatic scoring after generation finishes

This is more stable than letting many concurrent instances do sandbox-internal
remote `git fetch` operations during generation.

## RL Status

RL training is not ready for full Ascend validation yet. The current blockers are structural, not just path-related:

- `SkyRL/skyrl-train/pyproject.toml` pins CUDA-specific `torch`, `flash-attn`, and `vllm` extras.
- `SkyRL/skyrl-train/examples/swe_agent/run_swe_3B_sandbox.sh` hardcodes `generator.backend=vllm` and `generator.weight_sync_backend=nccl`.
- Existing training examples assume NVIDIA-style GPU placement and memory flags.
- no-chroot is intentionally scoped to evaluation fallback; training reward should run with chroot/mount namespace or Docker/container isolation.

## RL Preparation Checklist

Before attempting real Ascend RL training, finish these steps:

1. Keep evaluation working end to end with the local model server.
2. Decide whether RL inference will use `vllm-ascend`, another Ascend backend, or a remote OpenAI-compatible server.
3. Replace CUDA-only package pins and `nccl` assumptions in the SkyRL training stack.
4. Validate distributed communication and placement on Ascend before touching large-scale rollout counts.
5. Run `bash sh/require_chroot_training.sh` on the training node/pod and keep `use_chroot=true` for sandbox training.

Until those items are verified on hardware, treat the RL path as “preparation complete, runtime backend pending”.
