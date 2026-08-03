#!/usr/bin/env bash
# Launcher for the mini-swe-agent generation harness (Milestone 1/2).
# Activates swe-sandbox, sets PYTHONPATH (incl mini-swe-agent/src), auto-starts the
# 8.5 vLLM server if needed, then runs sh/run_minisweagent_gen.py.
# Run inside a PRIVILEGED tp pod (image 346) for chroot.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}

export EVAL_CONFIG_PATH=${EVAL_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_infer_ascend_officiallike_chroot.yaml}
export DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
export INSTANCE_SLICE=${INSTANCE_SLICE:-:1}
export RUNTIME_ROOT=${RUNTIME_ROOT:-${ROOT_DIR}/.runtime/mswea-gen-qwen4b}
export OUT_DIR=${OUT_DIR:-${RUNTIME_ROOT}/output}
export WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-${ROOT_DIR}/vendor/minisandbox-wheelhouse}
export CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/path/to/workspace/minisandbox-conda}
export STEP_LIMIT=${STEP_LIMIT:-40}

# --- model / serving ---
export MODEL_PATH=${MODEL_PATH:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}
export SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-Qwen3-4B-Instruct-2507}
export MODEL_NAME=${MODEL_NAME:-openai/Qwen3-4B-Instruct-2507}
export API_BASE=${API_BASE:-http://127.0.0.1:8001/v1}
export VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.5-v017}
AUTO_START_SERVER=${AUTO_START_SERVER:-1}
SERVER_START_TIMEOUT=${SERVER_START_TIMEOUT:-600}
API_PORT=${API_BASE%/v1}; API_PORT=${API_PORT##*:}

mkdir -p "${OUT_DIR}"

# --- activate eval env ---
RUN_ENV_BIN="${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin"
export PATH="${RUN_ENV_BIN}:${PATH}"
export CONDA_PREFIX="${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}"
export PYTHONPATH="${ROOT_DIR}/SWE-ReX/src:${ROOT_DIR}/SWE-agent:${ROOT_DIR}/sandboxdev:${ROOT_DIR}/SWE-bench:${ROOT_DIR}/R2E-Gym/src:${ROOT_DIR}/mini-swe-agent/src${PYTHONPATH:+:${PYTHONPATH}}"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export NO_PROXY=127.0.0.1,localhost,0.0.0.0; export no_proxy="${NO_PROXY}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export MSWEA_SILENT_STARTUP=1
# local model isn't in litellm's price map -> don't let cost tracking abort the agent loop
export MSWEA_COST_TRACKING=${MSWEA_COST_TRACKING:-ignore_errors}

# CRITICAL: pin the runtime to THIS pod's ALLOCATED NPUs. The privileged pod (needed
# for chroot) can see ALL host NPUs, and vLLM otherwise defaults to physical device 0
# (another job's NPU). The scheduler exports our allocation in ASCEND_VISIBLE_DEVICES.
export ASCEND_RT_VISIBLE_DEVICES="${ASCEND_RT_VISIBLE_DEVICES:-${ASCEND_VISIBLE_DEVICES:-${NPU_VISIBLE_DEVICES:-}}}"
echo "[mswea-gen] ASCEND_RT_VISIBLE_DEVICES=${ASCEND_RT_VISIBLE_DEVICES} (allocated: ASCEND_VISIBLE_DEVICES=${ASCEND_VISIBLE_DEVICES:-unset})"

# --- auto-start vLLM server ---
# External API teachers (e.g. glm-5.2) require auth on /v1/models; include the bearer for the
# availability probe so a 401 isn't mistaken for "API down".
CURL_AUTH=()
if [[ -n "${MSWEA_API_KEY:-}" && "${MSWEA_API_KEY}" != "local" ]]; then
  CURL_AUTH=(-H "Authorization: Bearer ${MSWEA_API_KEY}")
fi
if ! curl -fsS --max-time 5 "${CURL_AUTH[@]}" "${API_BASE}/models" >/dev/null 2>&1; then
  if [[ "${AUTO_START_SERVER}" == "1" && "${TENSOR_PARALLEL_SIZE:-1}" -gt 1 ]]; then
    # Multi-card path: a big model (e.g. Qwen3-30B-A3B) won't fit on one NPU. Serve ONCE
    # across ALL allocated devices with tensor/data parallelism. ASCEND_RT_VISIBLE_DEVICES
    # is already pinned to this pod's allocation above (critical on privileged/chroot pods).
    echo "[mswea-gen] starting vLLM TP=${TENSOR_PARALLEL_SIZE} DP=${DATA_PARALLEL_SIZE:-1} across devices ${ASCEND_RT_VISIBLE_DEVICES} (${MODEL_PATH})"
    nohup env MODEL_PATH="${MODEL_PATH}" SERVED_MODEL_NAME="${SERVED_MODEL_NAME}" \
      ASCEND_RT_VISIBLE_DEVICES="${ASCEND_RT_VISIBLE_DEVICES}" \
      VLLM_ENV_NAME="${VLLM_ENV_NAME}" PORT="${API_PORT}" \
      TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE}" DATA_PARALLEL_SIZE="${DATA_PARALLEL_SIZE:-1}" \
      MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}" GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.9}" \
      MAX_NUM_SEQS="${MAX_NUM_SEQS:-256}" ENFORCE_EAGER="${ENFORCE_EAGER:-0}" \
      ENABLE_EXPERT_PARALLEL="${ENABLE_EXPERT_PARALLEL:-0}" ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-0}" \
      ASCEND_PERF_TUNING="${ASCEND_PERF_TUNING:-0}" \
      bash "${ROOT_DIR}/sh/serve_qwen_ascend.sh" >"${RUNTIME_ROOT}/vllm-server.log" 2>&1 &
    spid=$!
    deadline=$((SECONDS + SERVER_START_TIMEOUT))
    serve_ready=0
    while true; do
      if curl -fsS --max-time 5 "${API_BASE}/models" >/dev/null 2>&1; then serve_ready=1; break; fi
      if ! kill -0 "${spid}" 2>/dev/null; then echo "[mswea-gen] TP serve exited early; see ${RUNTIME_ROOT}/vllm-server.log" >&2; break; fi
      (( SECONDS >= deadline )) && { echo "[mswea-gen] TP serve timeout" >&2; kill "${spid}" 2>/dev/null; break; }
      sleep 5
    done
    [[ "${serve_ready}" == "1" ]] || { echo "[mswea-gen] TP serve failed; see ${RUNTIME_ROOT}/vllm-server.log" >&2; exit 1; }
    echo "[mswea-gen] vLLM TP server ready"
  elif [[ "${AUTO_START_SERVER}" == "1" ]]; then
    # Single-card path (small models): allocated NPUs can include a flaky chip (privileged
    # pod, physical addressing). Try each allocated device as the sole device; fast-fail.
    IFS=',' read -ra DEVS <<< "${ASCEND_RT_VISIBLE_DEVICES:-0}"
    serve_ready=0
    for dev in "${DEVS[@]}"; do
      echo "[mswea-gen] starting vLLM on NPU device ${dev} (${VLLM_ENV_NAME}, ${MODEL_PATH})"
      nohup env MODEL_PATH="${MODEL_PATH}" SERVED_MODEL_NAME="${SERVED_MODEL_NAME}" \
        ASCEND_RT_VISIBLE_DEVICES="${dev}" \
        VLLM_ENV_NAME="${VLLM_ENV_NAME}" PORT="${API_PORT}" MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}" GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.85}" ENFORCE_EAGER="${ENFORCE_EAGER:-1}" MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}" \
        bash "${ROOT_DIR}/sh/serve_qwen_ascend.sh" >"${RUNTIME_ROOT}/vllm-server.log" 2>&1 &
      spid=$!
      deadline=$((SECONDS + SERVER_START_TIMEOUT))
      while true; do
        if curl -fsS --max-time 5 "${API_BASE}/models" >/dev/null 2>&1; then serve_ready=1; break; fi
        if ! kill -0 "${spid}" 2>/dev/null; then echo "[mswea-gen] serve on device ${dev} exited early; trying next" >&2; break; fi
        (( SECONDS >= deadline )) && { echo "[mswea-gen] timeout on device ${dev}" >&2; kill "${spid}" 2>/dev/null; break; }
        sleep 3
      done
      if [[ "${serve_ready}" == "1" ]]; then echo "[mswea-gen] vLLM ready on device ${dev}"; break; fi
    done
    [[ "${serve_ready}" == "1" ]] || { echo "[mswea-gen] no allocated NPU could serve; see ${RUNTIME_ROOT}/vllm-server.log" >&2; exit 1; }
  else
    echo "[mswea-gen] API ${API_BASE} unavailable and AUTO_START_SERVER=0" >&2; exit 1
  fi
fi

exec python "${ROOT_DIR}/sh/run_minisweagent_gen.py"
