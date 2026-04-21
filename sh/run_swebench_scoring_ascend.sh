#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-/sharedata/liyuchen/minisandbox-wheelhouse}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
CONFIG_PATH=${CONFIG_PATH:-${ROOT_DIR}/config/sweagent_score_ascend.yaml}
NUM_WORKERS=${NUM_WORKERS:-4}
INSTANCE_SLICE=${INSTANCE_SLICE-:1}
INSTANCE_FILTER=${INSTANCE_FILTER:-.*}
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
DATASET_SPLIT=${DATASET_SPLIT:-test}
RUNTIME_ROOT=${RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-score}
SANDBOX_ROOT=${SANDBOX_ROOT:-${RUNTIME_ROOT}/sandbox}
GITCACHE_ROOT=${GITCACHE_ROOT:-${RUNTIME_ROOT}/gitcache}
SHARED_VENV_ROOT=${SHARED_VENV_ROOT:-${RUNTIME_ROOT}/shared_venv}
PREDICTIONS_PATH=${PREDICTIONS_PATH:-}
PREFLIGHT_TIMEOUT=${PREFLIGHT_TIMEOUT:-10}
NO_PROXY_LIST=${NO_PROXY_LIST:-127.0.0.1,localhost,0.0.0.0}
POSTPROCESS_SCORING=${POSTPROCESS_SCORING:-1}

ACTIVATE_SCRIPT="${MINIFORGE_ROOT}/bin/activate"
RUN_ENV_PREFIX="${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}"
RUN_ENV_BIN="${RUN_ENV_PREFIX}/bin"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

if [[ -z "${PREDICTIONS_PATH}" ]]; then
  echo "Set PREDICTIONS_PATH to the preds.json you want to rescore." >&2
  exit 1
fi

if [[ ! -f "${PREDICTIONS_PATH}" ]]; then
  echo "predictions file not found: ${PREDICTIONS_PATH}" >&2
  exit 1
fi

mkdir -p \
  "${RUNTIME_ROOT}/output" \
  "${SANDBOX_ROOT}" \
  "${GITCACHE_ROOT}" \
  "${SHARED_VENV_ROOT}" \
  "${WHEELHOUSE_ROOT}"

if [[ -x "${RUN_ENV_BIN}/python" ]]; then
  export PATH="${RUN_ENV_BIN}:${PATH}"
  export CONDA_PREFIX="${RUN_ENV_PREFIX}"
else
  if [[ ! -f "${ACTIVATE_SCRIPT}" ]]; then
    echo "activate script not found: ${ACTIVATE_SCRIPT}" >&2
    exit 1
  fi
  source "${ACTIVATE_SCRIPT}" "${RUN_ENV_NAME}"
fi

export PYTHONPATH="${ROOT_DIR}/SWE-ReX/src:${ROOT_DIR}/SWE-agent:${ROOT_DIR}/sandboxdev:${ROOT_DIR}/SWE-bench:${ROOT_DIR}/R2E-Gym/src${PYTHONPATH:+:${PYTHONPATH}}"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export NO_PROXY="${NO_PROXY_LIST}${NO_PROXY:+,${NO_PROXY}}"
export no_proxy="${NO_PROXY}"
export LITELLM_LOCAL_MODEL_COST_MAP=True

if ! timeout "${PREFLIGHT_TIMEOUT}" python -S -c "print('python-ok')" >/dev/null 2>&1; then
  echo "python in env '${RUN_ENV_NAME}' is unhealthy or hanging." >&2
  exit 1
fi

if ! timeout "${PREFLIGHT_TIMEOUT}" python - <<'PY' >/dev/null
import sweagent
import swerex
import swesandbox
from datasets import load_dataset
print("imports-ok")
PY
then
  echo "Python preflight passed, but scoring imports failed." >&2
  exit 1
fi

set +e
python -m sweagent run-batch \
  --config "${CONFIG_PATH}" \
  --env_type sandbox \
  --num_workers "${NUM_WORKERS}" \
  --output_dir "${RUNTIME_ROOT}/output" \
  --instances.database "${DATASET_DIR}" \
  --instances.split "${DATASET_SPLIT}" \
  --instances.filter "${INSTANCE_FILTER}" \
  --instances.slice "${INSTANCE_SLICE}" \
  --instances.model_patch_file "${PREDICTIONS_PATH}" \
  --instances.deployment.root_base "${SANDBOX_ROOT}" \
  --instances.deployment.git_base_path "${GITCACHE_ROOT}" \
  --instances.deployment.shared_venv "${SHARED_VENV_ROOT}" \
  --instances.deployment.tool_path "${ROOT_DIR}/SWE-agent/tools" \
  --instances.deployment.conda_env "${CONDA_BACKEND_ROOT}" \
  --instances.deployment.wheelhouse "${WHEELHOUSE_ROOT}" \
  "$@"
run_rc=$?
set -e

if [[ "${POSTPROCESS_SCORING}" == "1" ]]; then
  python "${ROOT_DIR}/sh/postprocess_official_scoring.py" \
    --runtime-root "${RUNTIME_ROOT}" \
    --predictions-path "${PREDICTIONS_PATH}" \
    --dataset-path "${DATASET_DIR}" \
    --dataset-split "${DATASET_SPLIT}" \
    --instances-filter "${INSTANCE_FILTER}" \
    --instances-slice "${INSTANCE_SLICE}"
fi

exit "${run_rc}"
