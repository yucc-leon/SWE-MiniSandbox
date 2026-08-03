#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.3-v011-clean}
MODEL_PATH=${MODEL_PATH:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}
SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-${MODEL_NAME:-}}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8001}
TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE=${DATA_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE_LOCAL=${DATA_PARALLEL_SIZE_LOCAL:-}
API_SERVER_COUNT=${API_SERVER_COUNT:-1}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-}
DTYPE=${DTYPE:-bfloat16}
BLOCK_SIZE=${BLOCK_SIZE:-128}
MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-}
MAX_NUM_SEQS=${MAX_NUM_SEQS:-128}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.90}
ENABLE_PREFIX_CACHING=${ENABLE_PREFIX_CACHING:-1}
ENFORCE_EAGER=${ENFORCE_EAGER:-0}
ENABLE_AUTO_TOOL_CHOICE=${ENABLE_AUTO_TOOL_CHOICE:-0}
TOOL_CALL_PARSER=${TOOL_CALL_PARSER:-}
PREFLIGHT_TIMEOUT=${PREFLIGHT_TIMEOUT:-60}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-/usr/local/Ascend/nnal/atb/set_env.sh}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}
LOG_DIR=${LOG_DIR:-${ROOT_DIR}/.runtime/serve-logs}
LOG_MODEL_NAME=$(basename "${MODEL_PATH}")
LOG_TIMESTAMP=${LOG_TIMESTAMP:-$(date +%Y%m%dT%H%M%S)}
SERVE_LOG_PATH=${SERVE_LOG_PATH:-${SERVER_LOG_PATH:-${LOG_DIR}/${LOG_MODEL_NAME}-${PORT}-${LOG_TIMESTAMP}.log}}

if [[ "${SERVE_LOG_PATH}" != "none" && -z "${SWE_SERVE_LOG_ACTIVE:-}" ]]; then
  mkdir -p "$(dirname "${SERVE_LOG_PATH}")"
  export SWE_SERVE_LOG_ACTIVE=1
  exec > >(tee -a "${SERVE_LOG_PATH}") 2>&1
  echo "Logging serve output to ${SERVE_LOG_PATH}"
fi

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
      MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
      ;;
    sweagent-7b|SWE-agent-LM-7B*|SWE-agent-LM-32B*|sweagent-32b|sweagent-lm-32b)
      MAX_MODEL_LEN=32768
      MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-32768}
      ;;
    *)
      MAX_MODEL_LEN=16384
      MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
      ;;
  esac
else
  MAX_NUM_BATCHED_TOKENS=${MAX_NUM_BATCHED_TOKENS:-4096}
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

# Ascend inference throughput tuning (gated; safe, version-agnostic env hints). Enable with
# ASCEND_PERF_TUNING=1 for max hardware utilization (per vllm-ascend MoE serving guide).
if [[ "${ASCEND_PERF_TUNING:-0}" == "1" ]]; then
  export PYTORCH_NPU_ALLOC_CONF="${PYTORCH_NPU_ALLOC_CONF:-expandable_segments:True}"
  export HCCL_OP_EXPANSION_MODE="${HCCL_OP_EXPANSION_MODE:-AIV}"
  export HCCL_BUFFSIZE="${HCCL_BUFFSIZE:-1024}"
  export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
  export TASK_QUEUE_ENABLE="${TASK_QUEUE_ENABLE:-1}"
  echo "[serve] ASCEND_PERF_TUNING on: PYTORCH_NPU_ALLOC_CONF/HCCL/OMP/TASK_QUEUE set"
fi

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
if [[ -n "${SERVED_MODEL_NAME}" ]]; then
  echo "Served model name: ${SERVED_MODEL_NAME}"
fi
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
# MoE throughput knobs (verified present in vllm 0.17.0). Off by default so dense models /
# other runs are unaffected; enable for MoE inference (e.g. Qwen3-30B-A3B).
if [[ "${ENABLE_EXPERT_PARALLEL:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--enable-expert-parallel)
fi
if [[ "${ASYNC_SCHEDULING:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--async-scheduling)
fi
if [[ -n "${COMPILATION_CONFIG:-}" ]]; then
  EXTRA_ARGS+=(--compilation-config "${COMPILATION_CONFIG}")
fi
if [[ "${ENABLE_AUTO_TOOL_CHOICE}" == "1" ]]; then
  EXTRA_ARGS+=(--enable-auto-tool-choice)
fi
if [[ -n "${TOOL_CALL_PARSER}" ]]; then
  EXTRA_ARGS+=(--tool-call-parser "${TOOL_CALL_PARSER}")
fi
if [[ -n "${SERVED_MODEL_NAME}" ]]; then
  EXTRA_ARGS+=(--served-model-name "${SERVED_MODEL_NAME}")
fi

# NPU device pinning — GATED behind SWE_PIN_NPU=1 (default OFF).
# On NON-privileged pods the scheduler already isolates+renumbers the allocated NPUs to
# logical 0..N-1, so vLLM should be left alone (DO NOT pin — pinning to physical ids like
# "14,15,8.." breaks the isolated namespace). Only PRIVILEGED pods (which see ALL host NPUs)
# need pinning to the allocation. So opt-in: privileged launchers set SWE_PIN_NPU=1.
if [[ "${SWE_PIN_NPU:-0}" == "1" && -z "${ASCEND_RT_VISIBLE_DEVICES:-}" && -n "${ASCEND_VISIBLE_DEVICES:-}" ]]; then
  export ASCEND_RT_VISIBLE_DEVICES="${ASCEND_VISIBLE_DEVICES}"
fi
echo "[serve] SWE_PIN_NPU=${SWE_PIN_NPU:-0} ASCEND_RT_VISIBLE_DEVICES=${ASCEND_RT_VISIBLE_DEVICES:-<unset>} (alloc ASCEND_VISIBLE_DEVICES=${ASCEND_VISIBLE_DEVICES:-<unset>})"

exec vllm serve "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --dtype "${DTYPE}" \
  "${EXTRA_ARGS[@]}" \
  "$@"
