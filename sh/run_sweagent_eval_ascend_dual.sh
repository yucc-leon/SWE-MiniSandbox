#!/usr/bin/env bash
set -euo pipefail

# Compatibility wrapper name kept for older commands.
# This script now behaves as a general multi-server Ascend evaluation orchestrator,
# not a fixed dual-server launcher.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}
LAUNCH_SCRIPT=${LAUNCH_SCRIPT:-${ROOT_DIR}/sh/run_sweagent_eval_ascend.sh}
RUNTIME_ROOT=${RUNTIME_ROOT:-${RUNTIME_ROOT_BASE}/ascend-eval-dual}
INSTANCE_SLICE=${INSTANCE_SLICE:-:10}
WORKERS_PER_SERVER=${WORKERS_PER_SERVER:-${WORKERS_PER_SHARD:-8}}
TOTAL_WORKERS=${TOTAL_WORKERS:-}
LOAD_BALANCE_MODE=${LOAD_BALANCE_MODE:-proxy}
PORT_BASE=${PORT_BASE:-8001}
PROXY_PORT=${PROXY_PORT:-8000}
PROXY_LOG=${PROXY_LOG:-${RUNTIME_ROOT}/proxy.log}
PROXY_PID=${PROXY_PID:-${RUNTIME_ROOT}/proxy.pid}
MODEL_NAME=${MODEL_NAME:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}
MODEL_PATH=${MODEL_PATH:-}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-}
NPU_DEVICE_IDS=${NPU_DEVICE_IDS:-}
NPUS_PER_SERVER=${NPUS_PER_SERVER:-}
TENSOR_PARALLEL_SIZE_PER_SERVER=${TENSOR_PARALLEL_SIZE_PER_SERVER:-}
DATA_PARALLEL_SIZE_PER_SERVER=${DATA_PARALLEL_SIZE_PER_SERVER:-}
DATA_PARALLEL_SIZE_LOCAL_PER_SERVER=${DATA_PARALLEL_SIZE_LOCAL_PER_SERVER:-}
API_SERVER_COUNT_PER_SERVER=${API_SERVER_COUNT_PER_SERVER:-1}
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
PYTHON_BIN=${PYTHON_BIN:-${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin/python}
PIPELINE_WAIT=${PIPELINE_WAIT:-0}
POSTPROCESS_EVAL=${POSTPROCESS_EVAL:-1}
POSTPROCESS_DATASET_SIZE=${POSTPROCESS_DATASET_SIZE:-}
PREPARE_FIRST=${PREPARE_FIRST:-0}
PREPARE_SCRIPT=${PREPARE_SCRIPT:-${ROOT_DIR}/sh/run_sweagent_prepare_ascend.sh}
PREPARE_CONFIG_PATH=${PREPARE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_prepare_ascend.yaml}
PREPARE_NUM_WORKERS=${PREPARE_NUM_WORKERS:-${WORKERS_PER_SERVER}}
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
NO_PROXY_LIST=${NO_PROXY_LIST:-127.0.0.1,localhost,0.0.0.0}

mkdir -p "${RUNTIME_ROOT}"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export NO_PROXY="${NO_PROXY_LIST}"
export no_proxy="${NO_PROXY}"

if [[ -z "${MODEL_PATH}" ]]; then
  if [[ "${MODEL_NAME}" == openai//* ]]; then
    MODEL_PATH="/${MODEL_NAME#openai//}"
  elif [[ "${MODEL_NAME}" == /* ]]; then
    MODEL_PATH="${MODEL_NAME}"
  fi
fi

MODEL_BASENAME=$(basename "${MODEL_PATH:-${MODEL_NAME}}")
if [[ -z "${MAX_MODEL_LEN}" ]]; then
  case "${MODEL_BASENAME}" in
    Qwen3-4B-Instruct-2507|Qwen3-4B-*)
      MAX_MODEL_LEN=65536
      ;;
    sweagent-7b|SWE-agent-LM-7B*|SWE-agent-LM-32B*|sweagent-32b|sweagent-lm-32b)
      MAX_MODEL_LEN=32768
      ;;
    *)
      MAX_MODEL_LEN=16384
      ;;
  esac
fi

if [[ -z "${NPUS_PER_SERVER}" ]]; then
  case "${MODEL_BASENAME}" in
    Qwen3-4B-Instruct-2507|Qwen3-4B-*|sweagent-7b|SWE-agent-LM-7B*|SWE-agent-LM-32B*|sweagent-32b|sweagent-lm-32b)
      NPUS_PER_SERVER=1
      ;;
    *)
      NPUS_PER_SERVER=1
      ;;
  esac
fi

if [[ -z "${TENSOR_PARALLEL_SIZE_PER_SERVER}" ]]; then
  TENSOR_PARALLEL_SIZE_PER_SERVER="${NPUS_PER_SERVER}"
fi
if [[ -z "${DATA_PARALLEL_SIZE_PER_SERVER}" ]]; then
  DATA_PARALLEL_SIZE_PER_SERVER=1
fi
if [[ -z "${DATA_PARALLEL_SIZE_LOCAL_PER_SERVER}" && "${DATA_PARALLEL_SIZE_PER_SERVER}" != "1" ]]; then
  DATA_PARALLEL_SIZE_LOCAL_PER_SERVER="${DATA_PARALLEL_SIZE_PER_SERVER}"
fi

detect_npu_device_ids() {
  if [[ -n "${NPU_DEVICE_IDS}" ]]; then
    printf '%s\n' "${NPU_DEVICE_IDS}"
    return 0
  fi
  "${PYTHON_BIN}" - <<'PY'
import re
import subprocess
import sys

try:
    out = subprocess.check_output(["npu-smi", "info", "-m"], text=True, stderr=subprocess.STDOUT)
except Exception:
    out = None

ids = []
if out:
    # Prefer Chip Logic IDs on dual-chip Ascend boards, e.g. 0,1,2,3.
    pat = re.compile(
        r"^\s*(\d+)\s+(\d+)\s+([0-9-]+)\s+([0-9-]+)\s+(\S+)\s*$"
    )
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        logic_id = m.group(3)
        chip_name = m.group(5)
        if logic_id == "-" or not chip_name.startswith("Ascend"):
            continue
        ids.append(int(logic_id))

ids = sorted(set(ids))
if ids:
    print(",".join(str(i) for i in ids))
    raise SystemExit(0)

try:
    out = subprocess.check_output(["npu-smi", "info"], text=True, stderr=subprocess.STDOUT)
except Exception:
    sys.exit(1)

ids = []
pat = re.compile(r"^\|\s*(\d+)\s+\d+\s+\|\s*([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])\s+\|")
for line in out.splitlines():
    m = pat.match(line)
    if m:
        ids.append(int(m.group(1)))
ids = sorted(set(ids))
if not ids:
    sys.exit(2)
print(",".join(str(i) for i in ids))
PY
}

DEVICE_IDS_CSV=$(detect_npu_device_ids)
IFS=',' read -r -a DEVICE_IDS <<<"${DEVICE_IDS_CSV}"
AVAILABLE_NPUS=${#DEVICE_IDS[@]}
if (( AVAILABLE_NPUS == 0 )); then
  echo "failed to detect available NPUs; set NPU_DEVICE_IDS explicitly" >&2
  exit 1
fi
if (( NPUS_PER_SERVER <= 0 )); then
  echo "NPUS_PER_SERVER must be > 0, got ${NPUS_PER_SERVER}" >&2
  exit 1
fi
SERVER_COUNT=$(( AVAILABLE_NPUS / NPUS_PER_SERVER ))
if (( SERVER_COUNT <= 0 )); then
  echo "available NPUs (${AVAILABLE_NPUS}) are insufficient for NPUS_PER_SERVER=${NPUS_PER_SERVER}" >&2
  exit 1
fi
if [[ -z "${TOTAL_WORKERS}" ]]; then
  TOTAL_WORKERS=$(( SERVER_COUNT * WORKERS_PER_SERVER ))
fi

declare -a DEVICE_GROUPS=()
declare -a SERVER_PORTS=()
for ((server_idx=0; server_idx<SERVER_COUNT; server_idx++)); do
  start=$((server_idx * NPUS_PER_SERVER))
  group=()
  for ((offset=0; offset<NPUS_PER_SERVER; offset++)); do
    group+=("${DEVICE_IDS[$((start + offset))]}")
  done
  DEVICE_GROUPS+=("$(IFS=,; echo "${group[*]}")")
  SERVER_PORTS+=("$((PORT_BASE + server_idx))")
done

slice_for_shard() {
  local shard=$1
  local total_shards=$2
  local spec=$3
  local start stop step
  IFS=':' read -r start stop step <<<"${spec}"
  if [[ -z "${step:-}" ]]; then
    step=1
  fi
  local shard_step=$((step * total_shards))
  local shard_start
  if [[ -z "${start:-}" ]]; then
    shard_start=$((shard * step))
  else
    shard_start=$((start + shard * step))
  fi
  if [[ -z "${stop:-}" ]]; then
    printf '%s::%s' "${shard_start}" "${shard_step}"
  else
    printf '%s:%s:%s' "${shard_start}" "${stop}" "${shard_step}"
  fi
}

launch_shard() {
  local shard=$1
  local devices=$2
  local port=$3
  local slice=$4
  local server_log=$5
  local server_pid=$6
  local runtime_dir="${RUNTIME_ROOT}/shard${shard}"
  mkdir -p "${runtime_dir}"

  python3 - <<PY
import os, subprocess
root = ${ROOT_DIR@Q}
runtime_dir = ${runtime_dir@Q}
env = os.environ.copy()
env.update({
    "ASCEND_RT_VISIBLE_DEVICES": ${devices@Q},
    "NUM_WORKERS": ${WORKERS_PER_SERVER@Q},
    "INSTANCE_SLICE": ${slice@Q},
    "AUTO_START_SERVER": "1",
    "API_BASE": f"http://127.0.0.1:${port}/v1",
    "PORT": ${port@Q},
    "SERVER_LOG_PATH": ${server_log@Q},
    "SERVER_PID_PATH": ${server_pid@Q},
    "RUNTIME_ROOT": runtime_dir,
    "MODEL_NAME": ${MODEL_NAME@Q},
    "POSTPROCESS_EVAL": "0",
    "TENSOR_PARALLEL_SIZE": ${TENSOR_PARALLEL_SIZE_PER_SERVER@Q},
    "DATA_PARALLEL_SIZE": ${DATA_PARALLEL_SIZE_PER_SERVER@Q},
    "DATA_PARALLEL_SIZE_LOCAL": ${DATA_PARALLEL_SIZE_LOCAL_PER_SERVER@Q},
    "API_SERVER_COUNT": ${API_SERVER_COUNT_PER_SERVER@Q},
})
cmd = ["bash", ${LAUNCH_SCRIPT@Q}]
with open(os.path.join(runtime_dir, "launcher.log"), "ab", buffering=0) as log:
    p = subprocess.Popen(cmd, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(p.pid)
PY
}

wait_for_server_ready() {
  local port=$1
  local deadline=$((SECONDS + 420))
  while (( SECONDS < deadline )); do
    # Same rule as the single-server path: on this machine `startup complete`
    # does not imply immediate API readiness. Give `/v1/models` the full wait
    # budget before deciding the backend is unhealthy.
    if curl -fsS --max-time 5 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

launch_backend_server() {
  local shard=$1
  local devices=$2
  local port=$3
  local server_log=$4
  local server_pid=$5
  local runtime_dir="${RUNTIME_ROOT}/server${shard}"
  mkdir -p "${runtime_dir}"
  python3 - <<PY
import os, subprocess
root = ${ROOT_DIR@Q}
runtime_dir = ${runtime_dir@Q}
server_log = ${server_log@Q}
server_pid = ${server_pid@Q}
env = os.environ.copy()
env.update({
    "ASCEND_RT_VISIBLE_DEVICES": ${devices@Q},
    "PORT": ${port@Q},
    "MODEL_NAME": ${MODEL_NAME@Q},
    "MODEL_PATH": ${MODEL_PATH@Q},
    "MAX_MODEL_LEN": ${MAX_MODEL_LEN@Q},
    "TENSOR_PARALLEL_SIZE": ${TENSOR_PARALLEL_SIZE_PER_SERVER@Q},
    "DATA_PARALLEL_SIZE": ${DATA_PARALLEL_SIZE_PER_SERVER@Q},
    "DATA_PARALLEL_SIZE_LOCAL": ${DATA_PARALLEL_SIZE_LOCAL_PER_SERVER@Q},
    "API_SERVER_COUNT": ${API_SERVER_COUNT_PER_SERVER@Q},
    "RUNTIME_ROOT": runtime_dir,
})
cmd = ["bash", os.path.join(root, "sh/serve_qwen_ascend.sh")]
with open(server_log, "ab", buffering=0) as log, open(os.devnull, "rb") as devnull:
    p = subprocess.Popen(
        cmd,
        cwd=root,
        env=env,
        stdin=devnull,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
with open(server_pid, "w") as f:
    f.write(str(p.pid))
print(p.pid)
PY
}

launch_proxy() {
  local runtime_dir="${RUNTIME_ROOT}/proxy"
  mkdir -p "${runtime_dir}"
  local -a args=(
    --host 127.0.0.1
    --port "${PROXY_PORT}"
    --model-name "${MODEL_NAME}"
  )
  local port
  for port in "${SERVER_PORTS[@]}"; do
    args+=(--backend "http://127.0.0.1:${port}")
  done
  python3 - "${PYTHON_BIN}" "${ROOT_DIR}/sh/openai_lb_proxy.py" "${PROXY_LOG}" "${PROXY_PID}" "${args[@]}" <<'PY'
import os, subprocess, sys
python_bin = sys.argv[1]
proxy_script = sys.argv[2]
proxy_log = sys.argv[3]
proxy_pid = sys.argv[4]
proxy_args = sys.argv[5:]
cmd = [python_bin, proxy_script, *proxy_args]
with open(proxy_log, "ab", buffering=0) as log, open(os.devnull, "rb") as devnull:
    p = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.dirname(proxy_script)),
        stdin=devnull,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
with open(proxy_pid, "w") as f:
    f.write(str(p.pid))
print(p.pid)
PY
}

launch_proxy_run() {
  local runtime_dir="${RUNTIME_ROOT}/run"
  mkdir -p "${runtime_dir}"
  python3 - <<PY
import os, subprocess
root = ${ROOT_DIR@Q}
runtime_dir = ${runtime_dir@Q}
env = os.environ.copy()
env.update({
    "NUM_WORKERS": ${TOTAL_WORKERS@Q},
    "INSTANCE_SLICE": ${INSTANCE_SLICE@Q},
    "AUTO_START_SERVER": "0",
    "API_BASE": f"http://127.0.0.1:${PROXY_PORT}/v1",
    "RUNTIME_ROOT": runtime_dir,
    "MODEL_NAME": ${MODEL_NAME@Q},
    "POSTPROCESS_EVAL": "0",
})
log_path = os.path.join(runtime_dir, "launcher.log")
cmd = ["bash", ${LAUNCH_SCRIPT@Q}]
with open(log_path, "ab", buffering=0) as log, open(os.devnull, "rb") as devnull:
    p = subprocess.Popen(
        cmd,
        cwd=root,
        env=env,
        stdin=devnull,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
print(p.pid)
PY
}

wait_for_pid_exit() {
  local pid=$1
  while kill -0 "${pid}" 2>/dev/null; do
    sleep 5
  done
}

run_postprocess() {
  local args=(
    "${ROOT_DIR}/sh/postprocess_eval.py"
    --runtime-root "${RUNTIME_ROOT}"
  )
  if [[ -n "${POSTPROCESS_DATASET_SIZE}" ]]; then
    args+=(--dataset-size "${POSTPROCESS_DATASET_SIZE}")
  fi
  "${PYTHON_BIN}" "${args[@]}"
}

run_prepare_stage() {
  local prep_dir=$1
  local prep_output_dir=$2
  local root_base=$3
  local git_base_path=$4
  local shared_venv=$5
  local instance_slice=$6

  if [[ ! -x "${PREPARE_SCRIPT}" ]]; then
    echo "prepare script not found or not executable: ${PREPARE_SCRIPT}" >&2
    exit 1
  fi
  if [[ ! -f "${PREPARE_CONFIG_PATH}" ]]; then
    echo "prepare config not found: ${PREPARE_CONFIG_PATH}" >&2
    exit 1
  fi
  env \
    PYTHON_BIN="${PYTHON_BIN}" \
    CONFIG_PATH="${PREPARE_CONFIG_PATH}" \
    DATASET_PATH="${DATASET_DIR}" \
    DATA_TYPE="swebench" \
    DATASET_SPLIT="train" \
    INSTANCE_SLICE="${instance_slice}" \
    PREP_DIR="${prep_dir}" \
    PREP_OUTPUT_DIR="${prep_output_dir}" \
    NUM_WORKERS="${PREPARE_NUM_WORKERS}" \
    ROOT_BASE="${root_base}" \
    GIT_BASE_PATH="${git_base_path}" \
    SHARED_VENV="${shared_venv}" \
    CONDA_ENV="/path/to/workspace/minisandbox-conda" \
    WHEELHOUSE="/path/to/workspace/minisandbox-wheelhouse" \
    TOOL_PATH="${ROOT_DIR}/SWE-agent/tools" \
    EVAL_TIMEOUT="300" \
    RUN_PREP=1 \
    bash "${PREPARE_SCRIPT}"
}

if [[ "${LOAD_BALANCE_MODE}" == "proxy" ]]; then
  echo "Launching ${SERVER_COUNT} independent servers with least-inflight proxy"
  echo "  devices=${DEVICE_IDS_CSV}"
  echo "  npus_per_server=${NPUS_PER_SERVER} total_workers=${TOTAL_WORKERS} proxy_port=${PROXY_PORT}"
  if [[ "${PREPARE_FIRST}" == "1" ]]; then
    echo "running prepare-first stage for proxy mode into ${RUNTIME_ROOT}/run/prepare"
    run_prepare_stage \
      "${RUNTIME_ROOT}/run/prepare" \
      "${RUNTIME_ROOT}/run/prepare/output" \
      "${RUNTIME_ROOT}/run/prepare/sandbox" \
      "${RUNTIME_ROOT}/run/gitcache" \
      "${RUNTIME_ROOT}/run/shared_venv" \
      "${INSTANCE_SLICE}"
  fi
  declare -a SERVER_PIDS=()
  for ((server_idx=0; server_idx<SERVER_COUNT; server_idx++)); do
    devices="${DEVICE_GROUPS[$server_idx]}"
    port="${SERVER_PORTS[$server_idx]}"
    server_log="${RUNTIME_ROOT}/server-${server_idx}.log"
    server_pid_path="${RUNTIME_ROOT}/server-${server_idx}.pid"
    echo "  backend${server_idx} devices=${devices} port=${port}"
    SERVER_PIDS+=("$(launch_backend_server "${server_idx}" "${devices}" "${port}" "${server_log}" "${server_pid_path}")")
  done
  for port in "${SERVER_PORTS[@]}"; do
    wait_for_server_ready "${port}"
  done
  PROXY_PID_VALUE=$(launch_proxy)
  sleep 2
  RUN_PID=$(launch_proxy_run)
  for ((server_idx=0; server_idx<SERVER_COUNT; server_idx++)); do
    echo "server${server_idx} pid=${SERVER_PIDS[$server_idx]}"
  done
  echo "proxy pid=${PROXY_PID_VALUE}"
  echo "run pid=${RUN_PID}"
  echo "runtime_root=${RUNTIME_ROOT}"
  if [[ "${PIPELINE_WAIT}" == "1" ]]; then
    wait_for_pid_exit "${RUN_PID}"
    if [[ "${POSTPROCESS_EVAL}" == "1" ]]; then
      run_postprocess
    fi
  fi
else
  echo "Launching ${SERVER_COUNT} independent servers and sharded evaluations"
  echo "  devices=${DEVICE_IDS_CSV}"
  echo "  npus_per_server=${NPUS_PER_SERVER} workers_per_server=${WORKERS_PER_SERVER}"
  declare -a SHARD_PIDS=()
  for ((server_idx=0; server_idx<SERVER_COUNT; server_idx++)); do
    slice=$(slice_for_shard "${server_idx}" "${SERVER_COUNT}" "${INSTANCE_SLICE}")
    devices="${DEVICE_GROUPS[$server_idx]}"
    port="${SERVER_PORTS[$server_idx]}"
    server_log="${RUNTIME_ROOT}/server-${server_idx}.log"
    server_pid_path="${RUNTIME_ROOT}/server-${server_idx}.pid"
    echo "  shard${server_idx} devices=${devices} slice=${slice} port=${port}"
    if [[ "${PREPARE_FIRST}" == "1" ]]; then
      echo "  shard${server_idx} prepare-first"
      run_prepare_stage \
        "${RUNTIME_ROOT}/shard${server_idx}/prepare" \
        "${RUNTIME_ROOT}/shard${server_idx}/prepare/output" \
        "${RUNTIME_ROOT}/shard${server_idx}/prepare/sandbox" \
        "${RUNTIME_ROOT}/shard${server_idx}/gitcache" \
        "${RUNTIME_ROOT}/shard${server_idx}/shared_venv" \
        "${slice}"
    fi
    SHARD_PIDS+=("$(launch_shard "${server_idx}" "${devices}" "${port}" "${slice}" "${server_log}" "${server_pid_path}")")
  done
  for ((server_idx=0; server_idx<SERVER_COUNT; server_idx++)); do
    echo "shard${server_idx} pid=${SHARD_PIDS[$server_idx]}"
  done
  echo "runtime_root=${RUNTIME_ROOT}"
  if [[ "${PIPELINE_WAIT}" == "1" ]]; then
    for pid in "${SHARD_PIDS[@]}"; do
      wait_for_pid_exit "${pid}"
    done
    if [[ "${POSTPROCESS_EVAL}" == "1" ]]; then
      run_postprocess
    fi
  fi
fi
