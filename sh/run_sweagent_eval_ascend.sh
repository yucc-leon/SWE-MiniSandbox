#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-/sharedata/liyuchen/minisandbox-wheelhouse}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
CONFIG_PATH=${CONFIG_PATH:-${ROOT_DIR}/config/sweagent_infer_ascend.yaml}
API_BASE=${API_BASE:-http://127.0.0.1:8001/v1}
MODEL_NAME=${MODEL_NAME:-/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507}
MODEL_PATH=${MODEL_PATH:-}
AGENT_MAX_INPUT_TOKENS=${AGENT_MAX_INPUT_TOKENS:-}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-}
NUM_WORKERS=${NUM_WORKERS:-4}
DATA_PARALLEL_SIZE=${DATA_PARALLEL_SIZE:-1}
DATA_PARALLEL_SIZE_LOCAL=${DATA_PARALLEL_SIZE_LOCAL:-}
API_SERVER_COUNT=${API_SERVER_COUNT:-}
BOOTSTRAP_EDITABLES=${BOOTSTRAP_EDITABLES:-0}
INSTANCE_SLICE=${INSTANCE_SLICE:-:1}
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
RUNTIME_ROOT=${RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-eval}
PREFLIGHT_TIMEOUT=${PREFLIGHT_TIMEOUT:-10}
NO_PROXY_LIST=${NO_PROXY_LIST:-127.0.0.1,localhost,0.0.0.0}
AUTO_START_SERVER=${AUTO_START_SERVER:-1}
SERVER_SCRIPT=${SERVER_SCRIPT:-${ROOT_DIR}/sh/serve_qwen_ascend.sh}
SERVER_START_TIMEOUT=${SERVER_START_TIMEOUT:-420}
SERVER_LOG_PATH=${SERVER_LOG_PATH:-${RUNTIME_ROOT}/vllm-server.log}
SERVER_PID_PATH=${SERVER_PID_PATH:-${RUNTIME_ROOT}/vllm-server.pid}
API_PORT=${API_BASE%/v1}
API_PORT=${API_PORT##*:}
PREPARE_FIRST=${PREPARE_FIRST:-0}
PREPARE_SCRIPT=${PREPARE_SCRIPT:-${ROOT_DIR}/sh/run_sweagent_prepare_ascend.sh}
PREPARE_CONFIG_PATH=${PREPARE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_prepare_ascend.yaml}
PREPARE_NUM_WORKERS=${PREPARE_NUM_WORKERS:-${NUM_WORKERS}}
PREPARE_OUTPUT_DIR=${PREPARE_OUTPUT_DIR:-${RUNTIME_ROOT}/prepare/output}
POSTPROCESS_EVAL=${POSTPROCESS_EVAL:-1}
POSTPROCESS_DATASET_SIZE=${POSTPROCESS_DATASET_SIZE:-}
WATCHDOG_ENABLE=${WATCHDOG_ENABLE:-0}
WATCHDOG_STALE_SECONDS=${WATCHDOG_STALE_SECONDS:-1800}
WATCHDOG_POLL_SECONDS=${WATCHDOG_POLL_SECONDS:-60}
WATCHDOG_RECOVERY_GRACE_SECONDS=${WATCHDOG_RECOVERY_GRACE_SECONDS:-180}

if [[ -z "${MODEL_PATH}" ]]; then
  if [[ "${MODEL_NAME}" == openai//* ]]; then
    MODEL_PATH="/${MODEL_NAME#openai//}"
  elif [[ "${MODEL_NAME}" == /* ]]; then
    MODEL_PATH="${MODEL_NAME}"
  fi
fi

MODEL_BASENAME=$(basename "${MODEL_PATH:-${MODEL_NAME}}")
if [[ -z "${AGENT_MAX_INPUT_TOKENS}" ]]; then
  case "${MODEL_BASENAME}" in
    Qwen3-4B-Instruct-2507|Qwen3-4B-*)
      AGENT_MAX_INPUT_TOKENS=65536
      ;;
    sweagent-7b|SWE-agent-LM-7B*|SWE-agent-LM-32B*|sweagent-32b|sweagent-lm-32b)
      AGENT_MAX_INPUT_TOKENS=32768
      ;;
    *)
      AGENT_MAX_INPUT_TOKENS=16384
      ;;
  esac
fi
if [[ -z "${MAX_MODEL_LEN}" ]]; then
  MAX_MODEL_LEN="${AGENT_MAX_INPUT_TOKENS}"
fi

ACTIVATE_SCRIPT="${MINIFORGE_ROOT}/bin/activate"
RUN_ENV_PREFIX="${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}"
RUN_ENV_BIN="${RUN_ENV_PREFIX}/bin"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

if [[ -z "${DATASET_DIR}" ]]; then
  echo "Set DATASET_DIR to your SWE-bench Verified dataset directory." >&2
  exit 1
fi

mkdir -p \
  "${RUNTIME_ROOT}/output" \
  "${RUNTIME_ROOT}/sandbox" \
  "${RUNTIME_ROOT}/gitcache" \
  "${RUNTIME_ROOT}/shared_venv" \
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
  echo "Checked interpreter from MINIFORGE_ROOT=${MINIFORGE_ROOT} with timeout=${PREFLIGHT_TIMEOUT}s." >&2
  echo "Try verifying ${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin/python manually before running evaluation." >&2
  exit 1
fi

if ! timeout "${PREFLIGHT_TIMEOUT}" python - <<'PY' >/dev/null
import sweagent
import swerex
import swesandbox
print("imports-ok")
PY
then
  echo "Python preflight passed, but SWE evaluation imports failed." >&2
  echo "Expected local sources via PYTHONPATH under ${ROOT_DIR}." >&2
  exit 1
fi

if [[ "${BOOTSTRAP_EDITABLES}" == "1" ]]; then
  python -m pip install -e "${ROOT_DIR}/SWE-ReX"
  python -m pip install -e "${ROOT_DIR}/SWE-agent"
  python -m pip install -e "${ROOT_DIR}/SWE-bench"
  python -m pip install -e "${ROOT_DIR}/R2E-Gym"
  python -m pip install -e "${ROOT_DIR}/sandboxdev"
fi

if [[ "${PREPARE_FIRST}" == "1" ]]; then
  if [[ ! -x "${PREPARE_SCRIPT}" ]]; then
    echo "prepare script not found or not executable: ${PREPARE_SCRIPT}" >&2
    exit 1
  fi
  if [[ ! -f "${PREPARE_CONFIG_PATH}" ]]; then
    echo "prepare config not found: ${PREPARE_CONFIG_PATH}" >&2
    exit 1
  fi
  echo "running prepare-first stage into ${RUNTIME_ROOT}/prepare"
  env \
    PYTHON_BIN="${RUN_ENV_BIN}/python" \
    CONFIG_PATH="${PREPARE_CONFIG_PATH}" \
    DATASET_PATH="${DATASET_DIR}" \
    DATA_TYPE="swebench" \
    DATASET_SPLIT="train" \
    PREP_DIR="${RUNTIME_ROOT}/prepare" \
    PREP_OUTPUT_DIR="${PREPARE_OUTPUT_DIR}" \
    NUM_WORKERS="${PREPARE_NUM_WORKERS}" \
    ROOT_BASE="${RUNTIME_ROOT}/prepare/sandbox" \
    GIT_BASE_PATH="${RUNTIME_ROOT}/gitcache" \
    SHARED_VENV="${RUNTIME_ROOT}/shared_venv" \
    CONDA_ENV="${CONDA_BACKEND_ROOT}" \
    WHEELHOUSE="${WHEELHOUSE_ROOT}" \
    TOOL_PATH="${ROOT_DIR}/SWE-agent/tools" \
    RUN_PREP=1 \
    bash "${PREPARE_SCRIPT}"
fi

wait_for_api() {
  local deadline now
  deadline=$((SECONDS + SERVER_START_TIMEOUT))
  while true; do
    # On the current Ascend 8.3 stack, `Application startup complete` can precede
    # the first successful `/v1/models` by roughly 1-2 minutes. Do not treat early
    # 503s as engine corruption unless the process exits or logs an explicit error.
    if curl -fsS --max-time 5 "${API_BASE}/models" >/dev/null 2>&1; then
      return 0
    fi
    now=${SECONDS}
    if (( now >= deadline )); then
      return 1
    fi
    sleep 2
  done
}

if ! curl -fsS --max-time 5 "${API_BASE}/models" >/dev/null 2>&1; then
  if [[ "${AUTO_START_SERVER}" != "1" ]]; then
    echo "model api is unavailable at ${API_BASE} and AUTO_START_SERVER=0" >&2
    exit 1
  fi
  if [[ ! -x "${SERVER_SCRIPT}" ]]; then
    echo "server script not found or not executable: ${SERVER_SCRIPT}" >&2
    exit 1
  fi
  mkdir -p "$(dirname "${SERVER_LOG_PATH}")"
  if [[ -f "${SERVER_PID_PATH}" ]]; then
    old_pid=$(cat "${SERVER_PID_PATH}" 2>/dev/null || true)
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
      echo "model api is unavailable, but stale server pid is still running: ${old_pid}" >&2
      echo "check ${SERVER_LOG_PATH} or kill pid ${old_pid} manually" >&2
      exit 1
    fi
    rm -f "${SERVER_PID_PATH}"
  fi
  echo "model api unavailable at ${API_BASE}, starting local vLLM server..."
  nohup env \
    MODEL_PATH="${MODEL_PATH}" \
    MAX_MODEL_LEN="${MAX_MODEL_LEN}" \
    PORT="${API_PORT}" \
    bash "${SERVER_SCRIPT}" >"${SERVER_LOG_PATH}" 2>&1 &
  server_pid=$!
  echo "${server_pid}" > "${SERVER_PID_PATH}"
  if ! wait_for_api; then
    echo "local vLLM server failed to become ready within ${SERVER_START_TIMEOUT}s" >&2
    echo "pid: ${server_pid}" >&2
    echo "log: ${SERVER_LOG_PATH}" >&2
    exit 1
  fi
fi

python -m sweagent run-batch \
  --config "${CONFIG_PATH}" \
  --env_type sandbox \
  --num_workers "${NUM_WORKERS}" \
  --output_dir "${RUNTIME_ROOT}/output" \
  --instances.database "${DATASET_DIR}" \
  --instances.slice "${INSTANCE_SLICE}" \
  --instances.deployment.root_base "${RUNTIME_ROOT}/sandbox" \
  --instances.deployment.git_base_path "${RUNTIME_ROOT}/gitcache" \
  --instances.deployment.shared_venv "${RUNTIME_ROOT}/shared_venv" \
  --instances.deployment.tool_path "${ROOT_DIR}/SWE-agent/tools" \
  --instances.deployment.conda_env "${CONDA_BACKEND_ROOT}" \
  --instances.deployment.wheelhouse "${WHEELHOUSE_ROOT}" \
  --agent.model.api_base "${API_BASE}" \
  --agent.model.name "${MODEL_NAME}" \
  --agent.model.max_input_tokens "${AGENT_MAX_INPUT_TOKENS}" \
  "$@" &
run_pid=$!
echo "${run_pid}" > "${RUNTIME_ROOT}/run_batch.pid"

watchdog_pid=""
if [[ "${WATCHDOG_ENABLE}" == "1" ]]; then
  python "${ROOT_DIR}/sh/eval_watchdog.py" \
    --runtime-root "${RUNTIME_ROOT}" \
    --run-pid "${run_pid}" \
    --stale-seconds "${WATCHDOG_STALE_SECONDS}" \
    --poll-seconds "${WATCHDOG_POLL_SECONDS}" \
    --recovery-grace-seconds "${WATCHDOG_RECOVERY_GRACE_SECONDS}" &
  watchdog_pid=$!
  echo "${watchdog_pid}" > "${RUNTIME_ROOT}/watchdog.pid"
fi

wait "${run_pid}"
run_rc=$?

if [[ -n "${watchdog_pid}" ]]; then
  kill -TERM "${watchdog_pid}" 2>/dev/null || true
  wait "${watchdog_pid}" 2>/dev/null || true
fi

if [[ "${POSTPROCESS_EVAL}" == "1" ]]; then
  postprocess_args=(
    "${ROOT_DIR}/sh/postprocess_eval.py"
    --runtime-root "${RUNTIME_ROOT}"
  )
  if [[ -n "${POSTPROCESS_DATASET_SIZE}" ]]; then
    postprocess_args+=(--dataset-size "${POSTPROCESS_DATASET_SIZE}")
  fi
  python "${postprocess_args[@]}"
fi

exit "${run_rc}"
