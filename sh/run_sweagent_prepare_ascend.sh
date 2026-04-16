#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}

MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
PYTHON_BIN=${PYTHON_BIN:-${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin/python}

DATASET_PATH=${DATASET_PATH:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
DATA_TYPE=${DATA_TYPE:-swebench}
DATASET_SPLIT=${DATASET_SPLIT:-train}
DATASET_SUBSET=${DATASET_SUBSET:-}
INSTANCE_SLICE=${INSTANCE_SLICE:-}
LOAD_FROM_DISK=${LOAD_FROM_DISK:-0}

PREP_ROOT=${PREP_ROOT:-${RUNTIME_ROOT_BASE}/ascend-prepare}
PREP_NAME=${PREP_NAME:-$(date -u +%Y%m%dT%H%M%SZ)}
PREP_DIR=${PREP_DIR:-${PREP_ROOT}/${PREP_NAME}}

CONFIG_PATH=${CONFIG_PATH:-${ROOT_DIR}/config/sweagent_prepare_ascend.yaml}
PREP_OUTPUT_DIR=${PREP_OUTPUT_DIR:-${PREP_DIR}/output}
NUM_WORKERS=${NUM_WORKERS:-8}
EVAL_TIMEOUT=${EVAL_TIMEOUT:-300}
RUN_PREP=${RUN_PREP:-1}

ROOT_BASE=${ROOT_BASE:-${PREP_DIR}/sandbox}
GIT_BASE_PATH=${GIT_BASE_PATH:-${PREP_DIR}/gitcache}
SHARED_VENV=${SHARED_VENV:-${PREP_DIR}/shared_venv}
CONDA_ENV=${CONDA_ENV:-/sharedata/liyuchen/minisandbox-conda}
WHEELHOUSE=${WHEELHOUSE:-/sharedata/liyuchen/minisandbox-wheelhouse}
TOOL_PATH=${TOOL_PATH:-${ROOT_DIR}/SWE-agent/tools}

mkdir -p "${PREP_DIR}"

SUMMARY_JSON="${PREP_DIR}/prep-summary.json"
PREWARM_JSONL="${PREP_DIR}/prewarm-dataset.jsonl"
SIMPLE_JSONL="${PREP_DIR}/prewarm-instances.jsonl"
STATUS_YAML="${PREP_OUTPUT_DIR}/run_batch_exit_statuses.yaml"
REPORT_JSON="${PREP_DIR}/prep-failure-report.json"

load_from_disk_flag=()
if [[ "${LOAD_FROM_DISK}" == "1" ]]; then
  load_from_disk_flag+=(--load-from-disk)
fi

subset_flag=()
if [[ -n "${DATASET_SUBSET}" ]]; then
  subset_flag+=(--subset "${DATASET_SUBSET}")
fi

slice_flag=()
if [[ -n "${INSTANCE_SLICE}" ]]; then
  slice_flag+=(--instance-slice "${INSTANCE_SLICE}")
fi

echo "[prepare] building environment bucket summary"
"${PYTHON_BIN}" "${ROOT_DIR}/sandboxdev/swesandbox/prep_plan.py" \
  --path "${DATASET_PATH}" \
  --data-type "${DATA_TYPE}" \
  --split "${DATASET_SPLIT}" \
  "${load_from_disk_flag[@]}" \
  "${subset_flag[@]}" \
  "${slice_flag[@]}" \
  --output-summary-json "${SUMMARY_JSON}" \
  --output-prewarm-jsonl "${PREWARM_JSONL}"

echo "[prepare] generating empty-agent prewarm run"
prep_cmd=(
  "${PYTHON_BIN}" "${ROOT_DIR}/sandboxdev/swesandbox/prep_run.py"
  --path "${DATASET_PATH}"
  --data-type "${DATA_TYPE}"
  --split "${DATASET_SPLIT}"
  "${load_from_disk_flag[@]}"
  "${subset_flag[@]}"
  "${slice_flag[@]}"
  --prep-dir "${PREP_DIR}"
  --config "${CONFIG_PATH}"
  --output-dir "${PREP_OUTPUT_DIR}"
  --num-workers "${NUM_WORKERS}"
  --python-bin "${PYTHON_BIN}"
  --root-base "${ROOT_BASE}"
  --git-base-path "${GIT_BASE_PATH}"
  --shared-venv "${SHARED_VENV}"
  --conda-env "${CONDA_ENV}"
  --wheelhouse "${WHEELHOUSE}"
  --tool-path "${TOOL_PATH}"
  --eval-timeout "${EVAL_TIMEOUT}"
)
if [[ "${RUN_PREP}" == "1" ]]; then
  prep_cmd+=(--run)
fi
"${prep_cmd[@]}"

if [[ "${RUN_PREP}" == "1" && -f "${STATUS_YAML}" ]]; then
  echo "[prepare] summarizing failures by environment bucket"
  "${PYTHON_BIN}" "${ROOT_DIR}/sandboxdev/swesandbox/prep_report.py" \
    --path "${DATASET_PATH}" \
    --data-type "${DATA_TYPE}" \
    --split "${DATASET_SPLIT}" \
    "${load_from_disk_flag[@]}" \
    "${subset_flag[@]}" \
    "${slice_flag[@]}" \
    --status-yaml "${STATUS_YAML}" \
    --output-json "${REPORT_JSON}"
fi

cat <<EOF
[prepare] prep_dir=${PREP_DIR}
[prepare] summary=${SUMMARY_JSON}
[prepare] prewarm_dataset=${PREWARM_JSONL}
[prepare] prewarm_instances=${SIMPLE_JSONL}
[prepare] output_dir=${PREP_OUTPUT_DIR}
[prepare] failure_report=${REPORT_JSON}
EOF
