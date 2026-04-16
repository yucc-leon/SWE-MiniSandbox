#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.3-v011-clean}
MODEL_PATH=${MODEL_PATH:-/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8001}
TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE=${DATA_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE_LOCAL=${DATA_PARALLEL_SIZE_LOCAL:-}
API_SERVER_COUNT=${API_SERVER_COUNT:-1}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-}
DTYPE=${DTYPE:-bfloat16}
BLOCK_SIZE=${BLOCK_SIZE:-128}
MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
MAX_NUM_SEQS=${MAX_NUM_SEQS:-128}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.90}
ENABLE_PREFIX_CACHING=${ENABLE_PREFIX_CACHING:-1}
ENFORCE_EAGER=${ENFORCE_EAGER:-0}
ENABLE_AUTO_TOOL_CHOICE=${ENABLE_AUTO_TOOL_CHOICE:-0}
TOOL_CALL_PARSER=${TOOL_CALL_PARSER:-}
PREFLIGHT_TIMEOUT=${PREFLIGHT_TIMEOUT:-60}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-/usr/local/Ascend/nnal/atb/set_env.sh}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}

ACTIVATE_SCRIPT="${MINIFORGE_ROOT}/bin/activate"
VLLM_ENV_PREFIX="${MINIFORGE_ROOT}/envs/${VLLM_ENV_NAME}"
VLLM_ENV_BIN="${VLLM_ENV_PREFIX}/bin"

if [[ ! -d "${MODEL_PATH}" ]]; then
  echo "model path not found: ${MODEL_PATH}" >&2
  exit 1
fi

MODEL_BASENAME=$(basename "${MODEL_PATH}")
if [[ -z "${MAX_MODEL_LEN}" ]]; then
  case "${MODEL_BASENAME}" in
    Qwen3-4B-Instruct-2507|Qwen3-4B-*)
      # Match the long-context recommendation for Qwen3-4B on Ascend,
      # while still keeping the rest of our NPU-specific serve settings.
      MAX_MODEL_LEN=65536
      ;;
    sweagent-7b|SWE-agent-LM-7B*)
      MAX_MODEL_LEN=32768
      ;;
    *)
      MAX_MODEL_LEN=16384
      ;;
  esac
fi

if [[ -x "${VLLM_ENV_BIN}/python" ]]; then
  export PATH="${VLLM_ENV_BIN}:${PATH}"
  export CONDA_PREFIX="${VLLM_ENV_PREFIX}"
else
  if [[ ! -f "${ACTIVATE_SCRIPT}" ]]; then
    echo "activate script not found: ${ACTIVATE_SCRIPT}" >&2
    exit 1
  fi
  source "${ACTIVATE_SCRIPT}" "${VLLM_ENV_NAME}"
fi
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,0.0.0.0}"
export no_proxy="${no_proxy:-${NO_PROXY}}"

restore_errexit=0
restore_nounset=0
if [[ $- == *e* ]]; then
  restore_errexit=1
  set +e
fi
if [[ $- == *u* ]]; then
  restore_nounset=1
  set +u
fi
if [[ -f "${ASCEND_NNAL_ENV}" ]]; then
  # Needed on this workstation so torch_npu and ATB shared libs resolve correctly.
  source "${ASCEND_NNAL_ENV}"
fi
if [[ -f "${ASCEND_TOOLKIT_ENV}" ]]; then
  source "${ASCEND_TOOLKIT_ENV}"
fi
if [[ "${restore_errexit}" == "1" ]]; then
  set -e
fi
if [[ "${restore_nounset}" == "1" ]]; then
  set -u
fi

if ! timeout "${PREFLIGHT_TIMEOUT}" python -S -c "print('python-ok')" >/dev/null 2>&1; then
  echo "python in env '${VLLM_ENV_NAME}' is unhealthy or hanging." >&2
  echo "Checked interpreter from MINIFORGE_ROOT=${MINIFORGE_ROOT} with timeout=${PREFLIGHT_TIMEOUT}s." >&2
  echo "Try verifying ${MINIFORGE_ROOT}/envs/${VLLM_ENV_NAME}/bin/python manually before starting vLLM." >&2
  exit 1
fi

if ! timeout "${PREFLIGHT_TIMEOUT}" python - <<'PY' >/dev/null
import vllm
print(vllm.__version__)
PY
then
  echo "Python preflight passed, but importing vllm failed in env '${VLLM_ENV_NAME}'." >&2
  exit 1
fi

echo "Serving model from ${MODEL_PATH}"
echo "Endpoint: http://${HOST}:${PORT}/v1"
echo "Env: ${VLLM_ENV_NAME}"
echo "Ascend toolkit env: ${ASCEND_TOOLKIT_ENV}"
echo "Ascend NNAL env: ${ASCEND_NNAL_ENV}"

EXTRA_ARGS=(
  --block-size "${BLOCK_SIZE}"
  --max-num-seqs "${MAX_NUM_SEQS}"
  --max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
  --data-parallel-size "${DATA_PARALLEL_SIZE}"
)
if [[ "${ENABLE_PREFIX_CACHING}" == "1" ]]; then
  EXTRA_ARGS+=(--enable-prefix-caching)
fi
if [[ -n "${DATA_PARALLEL_SIZE_LOCAL}" ]]; then
  EXTRA_ARGS+=(--data-parallel-size-local "${DATA_PARALLEL_SIZE_LOCAL}")
fi
if [[ -n "${API_SERVER_COUNT}" ]]; then
  EXTRA_ARGS+=(--api-server-count "${API_SERVER_COUNT}")
fi
if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  EXTRA_ARGS+=(--enforce-eager)
fi
if [[ "${ENABLE_AUTO_TOOL_CHOICE}" == "1" ]]; then
  EXTRA_ARGS+=(--enable-auto-tool-choice)
fi
if [[ -n "${TOOL_CALL_PARSER}" ]]; then
  EXTRA_ARGS+=(--tool-call-parser "${TOOL_CALL_PARSER}")
fi

exec vllm serve "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --dtype "${DTYPE}" \
  "${EXTRA_ARGS[@]}" \
  "$@"
