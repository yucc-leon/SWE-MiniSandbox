#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}

EVAL_CONFIG_PATH=${EVAL_CONFIG_PATH:-${CONFIG_PATH:-${ROOT_DIR}/config/sweagent_infer_ascend_officiallike.yaml}}
SCORE_CONFIG_PATH=${SCORE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_score_ascend.yaml}
PREPARE_CONFIG_PATH=${PREPARE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_prepare_ascend.yaml}
MODEL_NAME=${MODEL_NAME:-}
MODEL_PATH=${MODEL_PATH:-}
API_BASE=${API_BASE:-}

INFERENCE_TASK_RUNTIME_ROOT=${INFERENCE_TASK_RUNTIME_ROOT:-}
INFERENCE_TASK_FILE=${INFERENCE_TASK_FILE:-}
PROBE_RUNTIME_ROOT=${PROBE_RUNTIME_ROOT:-}
PROBE_REMOTE_SERVER=${PROBE_REMOTE_SERVER:-1}
PROBE_TIMEOUT=${PROBE_TIMEOUT:-600}
PROBE_POLL_INTERVAL=${PROBE_POLL_INTERVAL:-5}
PROBE_REQUEST_TIMEOUT=${PROBE_REQUEST_TIMEOUT:-10}
PROBE_CHECK_CHAT=${PROBE_CHECK_CHAT:-1}
PROBE_CHAT_MODEL=${PROBE_CHAT_MODEL:-${MODEL_NAME}}

INSTANCE_SLICE=${INSTANCE_SLICE:-:500}
NUM_WORKERS=${NUM_WORKERS:-16}
PREPARE_FIRST=${PREPARE_FIRST:-1}
PREPARE_NUM_WORKERS=${PREPARE_NUM_WORKERS:-${NUM_WORKERS}}
POSTPROCESS_DATASET_SIZE=${POSTPROCESS_DATASET_SIZE:-500}
POSTPROCESS_SCORING=${POSTPROCESS_SCORING:-1}
PIPELINE_SCORING=${PIPELINE_SCORING:-0}
PIPELINE_SCORE_NUM_WORKERS=${PIPELINE_SCORE_NUM_WORKERS:-2}
PIPELINE_SCORE_BATCH_SIZE=${PIPELINE_SCORE_BATCH_SIZE:-4}
PIPELINE_SCORE_MAX_CONCURRENT_SHARDS=${PIPELINE_SCORE_MAX_CONCURRENT_SHARDS:-1}
PIPELINE_SCORE_POLL_SECONDS=${PIPELINE_SCORE_POLL_SECONDS:-60}
PIPELINE_SCORE_STABLE_SECONDS=${PIPELINE_SCORE_STABLE_SECONDS:-5}
WATCHDOG_ENABLE=${WATCHDOG_ENABLE:-1}
WATCHDOG_STALE_SECONDS=${WATCHDOG_STALE_SECONDS:-1800}
WATCHDOG_POLL_SECONDS=${WATCHDOG_POLL_SECONDS:-60}
WATCHDOG_RECOVERY_GRACE_SECONDS=${WATCHDOG_RECOVERY_GRACE_SECONDS:-180}

EVAL_RUNTIME_ROOT=${EVAL_RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-eval-sweagent7b-remote-formal}
SCORE_RUNTIME_ROOT=${SCORE_RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-score-sweagent7b-remote-formal}
PREDICTIONS_PATH=${PREDICTIONS_PATH:-${EVAL_RUNTIME_ROOT}/output/preds.json}
PIPELINE_SCORE_LOG_PATH=${PIPELINE_SCORE_LOG_PATH:-${SCORE_RUNTIME_ROOT}/pipeline_scoring.log}

if [[ ! -f "${EVAL_CONFIG_PATH}" ]]; then
  echo "eval config not found: ${EVAL_CONFIG_PATH}" >&2
  exit 1
fi
if [[ ! -f "${SCORE_CONFIG_PATH}" ]]; then
  echo "score config not found: ${SCORE_CONFIG_PATH}" >&2
  exit 1
fi

mkdir -p "${EVAL_RUNTIME_ROOT}" "${SCORE_RUNTIME_ROOT}"

resolve_remote_config() {
  local task_file="${INFERENCE_TASK_FILE}"
  if [[ -z "${task_file}" && -n "${INFERENCE_TASK_RUNTIME_ROOT}" ]]; then
    if [[ -f "${INFERENCE_TASK_RUNTIME_ROOT%/}/inference_task.json" ]]; then
      task_file="${INFERENCE_TASK_RUNTIME_ROOT%/}/inference_task.json"
    elif [[ -f "${INFERENCE_TASK_RUNTIME_ROOT%/}/inference_task.request.json" ]]; then
      task_file="${INFERENCE_TASK_RUNTIME_ROOT%/}/inference_task.request.json"
    else
      task_file="${INFERENCE_TASK_RUNTIME_ROOT%/}/inference_task.json"
    fi
  fi
  if [[ -z "${PROBE_RUNTIME_ROOT}" ]]; then
    if [[ -n "${INFERENCE_TASK_RUNTIME_ROOT}" ]]; then
      PROBE_RUNTIME_ROOT="${INFERENCE_TASK_RUNTIME_ROOT}"
    elif [[ -n "${task_file}" ]]; then
      PROBE_RUNTIME_ROOT="$(cd "$(dirname "${task_file}")" && pwd)"
    else
      PROBE_RUNTIME_ROOT="${EVAL_RUNTIME_ROOT}"
    fi
  fi

  if [[ -n "${API_BASE}" && -n "${MODEL_NAME}" ]]; then
    return 0
  fi
  if [[ -z "${task_file}" ]]; then
    return 0
  fi
  if [[ ! -f "${task_file}" ]]; then
    echo "inference task file not found: ${task_file}" >&2
    exit 1
  fi

  readarray -t resolved < <(
    python - "${task_file}" "${API_BASE}" "${MODEL_NAME}" <<'PY'
import json
import sys
from pathlib import Path

task_file = Path(sys.argv[1])
api_base = sys.argv[2]
model_name = sys.argv[3]
payload = json.loads(task_file.read_text(encoding="utf-8"))
print(api_base or payload.get("api_base") or payload.get("expected_api_base") or "")
print(model_name or payload.get("model_name") or "")
PY
  )
  API_BASE=${resolved[0]:-}
  MODEL_NAME=${resolved[1]:-}
}

resolve_remote_config

if [[ -z "${API_BASE}" ]]; then
  echo "Set API_BASE directly or provide INFERENCE_TASK_RUNTIME_ROOT/INFERENCE_TASK_FILE." >&2
  exit 1
fi
if [[ -z "${MODEL_NAME}" ]]; then
  echo "Set MODEL_NAME directly or record it in inference_task.json." >&2
  exit 1
fi
if [[ -z "${MODEL_PATH}" ]]; then
  MODEL_PATH="${MODEL_NAME}"
fi

if [[ "${PROBE_REMOTE_SERVER}" == "1" ]]; then
  probe_args=(
    probe
    --runtime-root "${PROBE_RUNTIME_ROOT}"
    --api-base "${API_BASE}"
    --timeout "${PROBE_TIMEOUT}"
    --poll-interval "${PROBE_POLL_INTERVAL}"
    --request-timeout "${PROBE_REQUEST_TIMEOUT}"
  )
  if [[ "${PROBE_CHECK_CHAT}" == "1" ]]; then
    probe_args+=(--check-chat --chat-model "${PROBE_CHAT_MODEL}")
  fi
  python "${ROOT_DIR}/sh/run_remote_inference_task.py" "${probe_args[@]}"
fi

echo "[formal-remote] eval_root=${EVAL_RUNTIME_ROOT}"
echo "[formal-remote] score_root=${SCORE_RUNTIME_ROOT}"
echo "[formal-remote] eval_config=${EVAL_CONFIG_PATH}"
echo "[formal-remote] score_config=${SCORE_CONFIG_PATH}"
echo "[formal-remote] model=${MODEL_NAME}"
echo "[formal-remote] api_base=${API_BASE}"
echo "[formal-remote] instance_slice=${INSTANCE_SLICE}"
echo "[formal-remote] num_workers=${NUM_WORKERS}"
echo "[formal-remote] prepare_first=${PREPARE_FIRST}"
echo "[formal-remote] pipeline_scoring=${PIPELINE_SCORING}"

pipeline_score_pid=""
stop_pipeline_scoring() {
  if [[ -n "${pipeline_score_pid}" ]] && kill -0 "${pipeline_score_pid}" 2>/dev/null; then
    touch "${SCORE_RUNTIME_ROOT}/STOP"
    kill -TERM "${pipeline_score_pid}" 2>/dev/null || true
    wait "${pipeline_score_pid}" 2>/dev/null || true
  fi
}
trap stop_pipeline_scoring EXIT

if [[ "${PIPELINE_SCORING}" == "1" ]]; then
  mkdir -p "${SCORE_RUNTIME_ROOT}"
  rm -f "${SCORE_RUNTIME_ROOT}/STOP"
  echo "[formal-remote] starting pipeline scorer into ${SCORE_RUNTIME_ROOT}"
  env \
    EVAL_RUNTIME_ROOT="${EVAL_RUNTIME_ROOT}" \
    SCORE_RUNTIME_ROOT="${SCORE_RUNTIME_ROOT}" \
    FINAL_PREDICTIONS_PATH="${PREDICTIONS_PATH}" \
    CONFIG_PATH="${SCORE_CONFIG_PATH}" \
    DATASET_DIR="${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data" \
    DATASET_SPLIT="test" \
    INSTANCE_SLICE="${INSTANCE_SLICE}" \
    NUM_WORKERS="${PIPELINE_SCORE_NUM_WORKERS}" \
    BATCH_SIZE="${PIPELINE_SCORE_BATCH_SIZE}" \
    MAX_CONCURRENT_SHARDS="${PIPELINE_SCORE_MAX_CONCURRENT_SHARDS}" \
    POLL_SECONDS="${PIPELINE_SCORE_POLL_SECONDS}" \
    STABLE_SECONDS="${PIPELINE_SCORE_STABLE_SECONDS}" \
    bash "${ROOT_DIR}/sh/run_pipeline_scoring_ascend.sh" >"${PIPELINE_SCORE_LOG_PATH}" 2>&1 &
  pipeline_score_pid=$!
  echo "${pipeline_score_pid}" > "${SCORE_RUNTIME_ROOT}/pipeline_scoring.pid"
fi

env \
  CONFIG_PATH="${EVAL_CONFIG_PATH}" \
  PREPARE_CONFIG_PATH="${PREPARE_CONFIG_PATH}" \
  RUNTIME_ROOT="${EVAL_RUNTIME_ROOT}" \
  API_BASE="${API_BASE}" \
  MODEL_NAME="${MODEL_NAME}" \
  MODEL_PATH="${MODEL_PATH}" \
  INSTANCE_SLICE="${INSTANCE_SLICE}" \
  NUM_WORKERS="${NUM_WORKERS}" \
  AUTO_START_SERVER=0 \
  PREPARE_FIRST="${PREPARE_FIRST}" \
  PREPARE_NUM_WORKERS="${PREPARE_NUM_WORKERS}" \
  POSTPROCESS_EVAL=1 \
  POSTPROCESS_DATASET_SIZE="${POSTPROCESS_DATASET_SIZE}" \
  WATCHDOG_ENABLE="${WATCHDOG_ENABLE}" \
  WATCHDOG_STALE_SECONDS="${WATCHDOG_STALE_SECONDS}" \
  WATCHDOG_POLL_SECONDS="${WATCHDOG_POLL_SECONDS}" \
  WATCHDOG_RECOVERY_GRACE_SECONDS="${WATCHDOG_RECOVERY_GRACE_SECONDS}" \
  bash "${ROOT_DIR}/sh/run_sweagent_eval_ascend.sh"

if [[ ! -f "${PREDICTIONS_PATH}" ]]; then
  echo "predictions file not found after generation: ${PREDICTIONS_PATH}" >&2
  exit 1
fi

if [[ "${PIPELINE_SCORING}" == "1" ]]; then
  echo "[formal-remote] waiting for pipeline scorer to drain final predictions"
  wait "${pipeline_score_pid}"
  pipeline_rc=$?
  pipeline_score_pid=""
  trap - EXIT
  if [[ "${pipeline_rc}" != "0" ]]; then
    echo "pipeline scorer failed with rc=${pipeline_rc}; see ${PIPELINE_SCORE_LOG_PATH}" >&2
    exit "${pipeline_rc}"
  fi
  exit 0
fi

env \
  CONFIG_PATH="${SCORE_CONFIG_PATH}" \
  RUNTIME_ROOT="${SCORE_RUNTIME_ROOT}" \
  PREDICTIONS_PATH="${PREDICTIONS_PATH}" \
  INSTANCE_SLICE="${INSTANCE_SLICE}" \
  NUM_WORKERS="${NUM_WORKERS}" \
  POSTPROCESS_SCORING="${POSTPROCESS_SCORING}" \
  bash "${ROOT_DIR}/sh/run_swebench_scoring_ascend.sh"
