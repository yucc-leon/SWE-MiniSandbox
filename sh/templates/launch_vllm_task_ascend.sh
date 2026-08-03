#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH=${MODEL_PATH:?Set MODEL_PATH to the model directory inside the task container.}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}
PUBLIC_HOST=${PUBLIC_HOST:-${HOST}}
PUBLIC_PORT=${PUBLIC_PORT:-${PORT}}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}

TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE=${DATA_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE_LOCAL=${DATA_PARALLEL_SIZE_LOCAL:-}
API_SERVER_COUNT=${API_SERVER_COUNT:-1}

MAX_MODEL_LEN=${MAX_MODEL_LEN:-32768}
DTYPE=${DTYPE:-bfloat16}
BLOCK_SIZE=${BLOCK_SIZE:-128}
MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
MAX_NUM_SEQS=${MAX_NUM_SEQS:-128}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.90}
ENABLE_PREFIX_CACHING=${ENABLE_PREFIX_CACHING:-1}
ENFORCE_EAGER=${ENFORCE_EAGER:-0}
ENABLE_AUTO_TOOL_CHOICE=${ENABLE_AUTO_TOOL_CHOICE:-0}
TOOL_CALL_PARSER=${TOOL_CALL_PARSER:-}
SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-}

CONDA_INIT_SCRIPT=${CONDA_INIT_SCRIPT:-}
CONDA_ENV_NAME=${CONDA_ENV_NAME:-}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-}
PYTHON_BIN=${PYTHON_BIN:-python}
VLLM_BIN=${VLLM_BIN:-vllm}

if [[ -n "${CONDA_INIT_SCRIPT}" ]]; then
  # shellcheck source=/dev/null
  if [[ "$(basename "${CONDA_INIT_SCRIPT}")" == "activate" && -n "${CONDA_ENV_NAME}" ]]; then
    source "${CONDA_INIT_SCRIPT}" "${CONDA_ENV_NAME}"
  else
    source "${CONDA_INIT_SCRIPT}"
    if [[ -n "${CONDA_ENV_NAME}" ]]; then
      conda activate "${CONDA_ENV_NAME}"
    fi
  fi
elif [[ -n "${CONDA_ENV_NAME}" ]]; then
  echo "[task] CONDA_ENV_NAME is set but CONDA_INIT_SCRIPT is missing" >&2
  exit 1
fi

if [[ -n "${ASCEND_NNAL_ENV}" && -f "${ASCEND_NNAL_ENV}" ]]; then
  # shellcheck source=/dev/null
  source "${ASCEND_NNAL_ENV}"
fi
if [[ -n "${ASCEND_TOOLKIT_ENV}" && -f "${ASCEND_TOOLKIT_ENV}" ]]; then
  # shellcheck source=/dev/null
  source "${ASCEND_TOOLKIT_ENV}"
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
  echo "[task] model path not found: ${MODEL_PATH}" >&2
  exit 1
fi

if ! command -v "${VLLM_BIN}" >/dev/null 2>&1; then
  echo "[task] vllm binary not found: ${VLLM_BIN}" >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import importlib
import sys

try:
    mod = importlib.import_module("vllm")
except Exception as exc:
    print(f"[task] failed to import vllm: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"[task] vllm_version={getattr(mod, '__version__', 'unknown')}")
PY

extra_args=(
  --block-size "${BLOCK_SIZE}"
  --max-num-seqs "${MAX_NUM_SEQS}"
  --max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
  --data-parallel-size "${DATA_PARALLEL_SIZE}"
)

if [[ "${ENABLE_PREFIX_CACHING}" == "1" ]]; then
  extra_args+=(--enable-prefix-caching)
fi
if [[ -n "${DATA_PARALLEL_SIZE_LOCAL}" ]]; then
  extra_args+=(--data-parallel-size-local "${DATA_PARALLEL_SIZE_LOCAL}")
fi
if [[ -n "${API_SERVER_COUNT}" ]]; then
  extra_args+=(--api-server-count "${API_SERVER_COUNT}")
fi
if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  extra_args+=(--enforce-eager)
fi
if [[ "${ENABLE_AUTO_TOOL_CHOICE}" == "1" ]]; then
  extra_args+=(--enable-auto-tool-choice)
fi
if [[ -n "${TOOL_CALL_PARSER}" ]]; then
  extra_args+=(--tool-call-parser "${TOOL_CALL_PARSER}")
fi
if [[ -n "${SERVED_MODEL_NAME}" ]]; then
  extra_args+=(--served-model-name "${SERVED_MODEL_NAME}")
fi

echo "[task] launch_mode=remote_inference"
echo "[task] model_path=${MODEL_PATH}"
if [[ -n "${SERVED_MODEL_NAME}" ]]; then
  echo "[task] served_model_name=${SERVED_MODEL_NAME}"
fi
if [[ -n "${PUBLIC_BASE_URL}" ]]; then
  echo "[task] openai_api_base=${PUBLIC_BASE_URL}"
else
  echo "[task] openai_api_base=http://${PUBLIC_HOST}:${PUBLIC_PORT}/v1"
fi
echo "[task] tensor_parallel_size=${TENSOR_PARALLEL_SIZE}"
echo "[task] data_parallel_size=${DATA_PARALLEL_SIZE}"
echo "[task] api_server_count=${API_SERVER_COUNT}"

exec "${VLLM_BIN}" serve "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --dtype "${DTYPE}" \
  "${extra_args[@]}" \
  "$@"
