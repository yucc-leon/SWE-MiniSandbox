#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}

EVAL_RUNTIME_ROOT=${EVAL_RUNTIME_ROOT:-}
SCORE_RUNTIME_ROOT=${SCORE_RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-score-pipeline}
FINAL_PREDICTIONS_PATH=${FINAL_PREDICTIONS_PATH:-${EVAL_RUNTIME_ROOT%/}/output/preds.json}
CONFIG_PATH=${CONFIG_PATH:-${ROOT_DIR}/config/sweagent_score_ascend.yaml}
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
DATASET_SPLIT=${DATASET_SPLIT:-test}
INSTANCE_FILTER=${INSTANCE_FILTER:-.*}
INSTANCE_SLICE=${INSTANCE_SLICE:-:500}
NUM_WORKERS=${NUM_WORKERS:-2}
BATCH_SIZE=${BATCH_SIZE:-4}
MAX_CONCURRENT_SHARDS=${MAX_CONCURRENT_SHARDS:-1}
POLL_SECONDS=${POLL_SECONDS:-60}
STABLE_SECONDS=${STABLE_SECONDS:-5}
PYTHON_BIN=${PYTHON_BIN:-${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin/python}
ONCE=${ONCE:-0}
MERGE_ONLY=${MERGE_ONLY:-0}
MAX_SHARDS=${MAX_SHARDS:-}

if [[ -z "${EVAL_RUNTIME_ROOT}" ]]; then
  echo "Set EVAL_RUNTIME_ROOT to the generation runtime root." >&2
  exit 1
fi

args=(
  --eval-runtime-root "${EVAL_RUNTIME_ROOT}"
  --score-runtime-root "${SCORE_RUNTIME_ROOT}"
  --final-predictions-path "${FINAL_PREDICTIONS_PATH}"
  --config-path "${CONFIG_PATH}"
  --dataset-dir "${DATASET_DIR}"
  --dataset-split "${DATASET_SPLIT}"
  --instance-filter "${INSTANCE_FILTER}"
  --instance-slice "${INSTANCE_SLICE}"
  --num-workers "${NUM_WORKERS}"
  --batch-size "${BATCH_SIZE}"
  --max-concurrent-shards "${MAX_CONCURRENT_SHARDS}"
  --poll-seconds "${POLL_SECONDS}"
  --stable-seconds "${STABLE_SECONDS}"
  --python-bin "${PYTHON_BIN}"
)

if [[ "${ONCE}" == "1" ]]; then
  args+=(--once)
fi
if [[ "${MERGE_ONLY}" == "1" ]]; then
  args+=(--merge-only)
fi
if [[ -n "${MAX_SHARDS}" ]]; then
  args+=(--max-shards "${MAX_SHARDS}")
fi

exec python "${ROOT_DIR}/sh/pipeline_scoring_ascend.py" "${args[@]}"
