#!/usr/bin/env bash
# Copy this file to a machine-local profile and adjust paths before sourcing it.

export PROJECT_ROOT=${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
export MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
export RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${PROJECT_ROOT}/.runtime}
export RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
export VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.3-v011-clean}

export CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
export WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-/sharedata/liyuchen/minisandbox-wheelhouse}
export DATASET_DIR=${DATASET_DIR:-${PROJECT_ROOT}/dataset/SWE-bench/SWE-bench_Verified/data}
export MODEL_PATH=${MODEL_PATH:-/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507}

export ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-/usr/local/Ascend/nnal/atb/set_env.sh}
export ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}

export API_BASE=${API_BASE:-http://127.0.0.1:8001/v1}
export MODEL_NAME=${MODEL_NAME:-${MODEL_PATH}}
export SERVE_PORT=${SERVE_PORT:-8001}
export PROXY_PORT=${PROXY_PORT:-8000}

# Keep these on a stable mount point if you want fast restore on another machine.
export SHARED_VENV_ROOT=${SHARED_VENV_ROOT:-${RUNTIME_ROOT_BASE}/shared_venv}
export GITCACHE_ROOT=${GITCACHE_ROOT:-${RUNTIME_ROOT_BASE}/gitcache}
