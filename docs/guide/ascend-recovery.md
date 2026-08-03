# Ascend Recovery Guide

This note turns the current bring-up state into a recovery checklist for another machine.

## Bottom Line

The current SWE setup is recoverable on another machine, but recovery speed depends on whether you can reuse the same path layout and prebuilt assets.

- Fast restore:
  - the new machine can mount the same paths for Miniforge, model weights, dataset, wheelhouse, `shared_venv`, and `gitcache`
  - you mostly need to relaunch serving and rerun generation or scoring
- Cold restore:
  - the new machine only has the repository
  - you must rebuild the Python envs, backend interpreters, wheelhouse, and the Ascend vLLM serving env

## What Must Exist

These are the current machine-level assumptions used by the Ascend path:

- Miniforge root: `/path/to/workspace/miniforge3`
- evaluation env: `swe-sandbox`
- serving env: `vllm-ascend-cann8.3-v011-clean`
- MiniSandbox backends: `/path/to/workspace/minisandbox-conda/{3.6,3.7,3.8,3.9,3.10,3.11,3.12}/miniconda3`
- wheelhouse root: `/path/to/workspace/minisandbox-wheelhouse`
- model root: `/path/to/workspace/models/Qwen3-4B-Instruct-2507`
- dataset root: `dataset/SWE-bench/SWE-bench_Verified/data`

The simplest way to codify these values for another machine is to copy:

- `sh/ascend_recovery_profile.example.sh`

into a machine-local profile and source it before serving or evaluation.

## Check Before Switching Machines

Run:

```bash
bash sh/check_swe_recovery_prereqs.sh
```

Or with a machine-local profile:

```bash
bash sh/check_swe_recovery_prereqs.sh /path/to/ascend_recovery_profile.sh
```

This verifies:

- required commands such as `git`, `curl`, `npu-smi`
- the evaluation env and serving env interpreters
- model and dataset paths
- Ascend toolkit environment scripts
- cache roots that matter for fast restore

## Fast Restore

Use this path when the new machine can mount the same cache and asset roots.

### Required reusable assets

- Miniforge envs under `/path/to/workspace/miniforge3/envs`
- backend interpreters under `/path/to/workspace/minisandbox-conda`
- wheelhouse under `/path/to/workspace/minisandbox-wheelhouse`
- model directory under `/path/to/workspace/models`
- dataset directory
- cached repo tarballs under `gitcache`
- cached virtualenv tarballs under `shared_venv`

### Important limitation

`shared_venv` is path-sensitive. The repo docs already note that changing the `shared_venv` root can invalidate cached venvs because the path is hard-coded inside the environment.

That means:

- same mount path: fast restore is realistic
- different mount path: expect venv cache rebuild

## Cold Restore

Use this path when the new machine only has the repository.

### 1. Rebuild the evaluation env

```bash
bash sh/bootstrap_minisandbox_backends.sh
bash sh/bootstrap_swe_sandbox_env.sh
bash sh/bootstrap_minisandbox_wheelhouse.sh
```

### 2. Rebuild the serving env

Follow the serving-env instructions in:

- `docs/guide/ascend.md`

The heavy part is the Ascend vLLM env, because it depends on a source build of:

- `vllm`
- `vllm-ascend`
- matching Ascend toolkit and NNAL installs

### 3. Smoke-test serving

```bash
MODEL_PATH=/path/to/workspace/models/Qwen3-4B-Instruct-2507 \
PORT=8001 \
bash sh/serve_qwen_ascend.sh
```

Then probe locally without environment proxies:

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  curl --noproxy '*' -sS http://127.0.0.1:8001/v1/models
```

### 4. Resume evaluation or scoring

Generation:

```bash
API_BASE=http://127.0.0.1:8001/v1 \
MODEL_NAME=/path/to/workspace/models/Qwen3-4B-Instruct-2507 \
bash sh/run_sweagent_eval_ascend.sh
```

Scoring:

```bash
PREDICTIONS_PATH=/path/to/preds.json \
bash sh/run_swebench_scoring_ascend.sh
```

## What Is Easy To Move

- repository code
- evaluation/scoring scripts
- generation outputs such as `preds.json`
- official-style scoring outputs such as `results.json`

## What Is Expensive To Recreate

- the Ascend vLLM serving env
- `shared_venv` cache if the mount point changes
- repo cache under `gitcache` if it is not preserved

## Recommended Disaster-Recovery Practice

For fast machine failover, keep these roots on stable shared storage when possible:

- Miniforge env roots
- `minisandbox-conda`
- `minisandbox-wheelhouse`
- model weights
- dataset
- `shared_venv`
- `gitcache`

If that is not possible, treat recovery as a cold rebuild and budget time mainly for:

- serving-env rebuild
- first-time environment installation and cache repopulation
