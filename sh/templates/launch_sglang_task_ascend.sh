#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH=${MODEL_PATH:?Set MODEL_PATH to the model directory inside the task container.}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}
PUBLIC_HOST=${PUBLIC_HOST:-${HOST}}
PUBLIC_PORT=${PUBLIC_PORT:-${PORT}}

TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE=${DATA_PARALLEL_SIZE:-1}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-32768}
MEM_FRACTION_STATIC=${MEM_FRACTION_STATIC:-0.90}
SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-}
TRUST_REMOTE_CODE=${TRUST_REMOTE_CODE:-1}

CONDA_INIT_SCRIPT=${CONDA_INIT_SCRIPT:-}
CONDA_ENV_NAME=${CONDA_ENV_NAME:-}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-}
PYTHON_BIN=${PYTHON_BIN:-python}
SGLANG_MODULE=${SGLANG_MODULE:-sglang.launch_server}
SGLANG_EXTRA_ARGS=${SGLANG_EXTRA_ARGS:-}

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

"${PYTHON_BIN}" - <<'PY'
import importlib
import sys

try:
    mod = importlib.import_module("sglang")
except Exception as exc:
    print(f"[task] failed to import sglang: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"[task] sglang_version={getattr(mod, '__version__', 'unknown')}")
PY

launch_cmd=("${PYTHON_BIN}" -m "${SGLANG_MODULE}")
extra_args=()

# Keep this template conservative: pass the flags that are stable across most
# sglang versions, and leave version-specific tuning in SGLANG_EXTRA_ARGS.
extra_args+=(
  --model-path "${MODEL_PATH}"
  --host "${HOST}"
  --port "${PORT}"
  --tp-size "${TENSOR_PARALLEL_SIZE}"
)

if [[ "${DATA_PARALLEL_SIZE}" != "1" ]]; then
  extra_args+=(--dp-size "${DATA_PARALLEL_SIZE}")
fi
if [[ -n "${MAX_MODEL_LEN}" ]]; then
  extra_args+=(--context-length "${MAX_MODEL_LEN}")
fi
if [[ -n "${MEM_FRACTION_STATIC}" ]]; then
  extra_args+=(--mem-fraction-static "${MEM_FRACTION_STATIC}")
fi
if [[ -n "${SERVED_MODEL_NAME}" ]]; then
  extra_args+=(--served-model-name "${SERVED_MODEL_NAME}")
fi
if [[ "${TRUST_REMOTE_CODE}" == "1" ]]; then
  extra_args+=(--trust-remote-code)
fi
if [[ -n "${SGLANG_EXTRA_ARGS}" ]]; then
  # shellcheck disable=SC2206
  more_args=(${SGLANG_EXTRA_ARGS})
  extra_args+=("${more_args[@]}")
fi

echo "[task] launch_mode=remote_inference"
echo "[task] backend=sglang"
echo "[task] model_path=${MODEL_PATH}"
echo "[task] openai_api_base=http://${PUBLIC_HOST}:${PUBLIC_PORT}/v1"
echo "[task] tensor_parallel_size=${TENSOR_PARALLEL_SIZE}"
echo "[task] data_parallel_size=${DATA_PARALLEL_SIZE}"

exec "${launch_cmd[@]}" "${extra_args[@]}" "$@"
