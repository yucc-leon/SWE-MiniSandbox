#!/usr/bin/env bash
# 5-instance chroot smoke: serve Qwen3-4B-Instruct-2507 on the 8.5 NPU stack,
# generate patches with the official-like agent, then chroot-replay-score them.
# Validates serve -> generation -> chroot scoring end-to-end before any SFT run.
# Run inside a PRIVILEGED tp pod (image 346) so unshare --mount / chroot works.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# --- serving env: 8.5 vllm-ascend confirmed by probe 6652 ---
export VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.5-v017}

# --- model: small/fast, smoke only (proven naming: openai/<id> + served id) ---
export MODEL_PATH=${MODEL_PATH:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}
export SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-Qwen3-4B-Instruct-2507}
export MODEL_NAME=${MODEL_NAME:-openai/Qwen3-4B-Instruct-2507}
export API_BASE=${API_BASE:-http://127.0.0.1:8001/v1}
export AUTO_START_SERVER=${AUTO_START_SERVER:-1}
export SERVER_START_TIMEOUT=${SERVER_START_TIMEOUT:-600}

# --- chroot configs (use_chroot: true) ---
export EVAL_CONFIG_PATH=${EVAL_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_infer_ascend_officiallike_chroot.yaml}
export SCORE_CONFIG_PATH=${SCORE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_score_ascend_chroot.yaml}

# --- smoke scope: 5 instances, fresh runtime roots ---
export INSTANCE_SLICE=${INSTANCE_SLICE:-:5}
export NUM_WORKERS=${NUM_WORKERS:-4}
export PREPARE_FIRST=${PREPARE_FIRST:-0}
export POSTPROCESS_DATASET_SIZE=${POSTPROCESS_DATASET_SIZE:-5}
export POSTPROCESS_SCORING=${POSTPROCESS_SCORING:-1}
export WATCHDOG_ENABLE=${WATCHDOG_ENABLE:-1}
export WATCHDOG_STALE_SECONDS=${WATCHDOG_STALE_SECONDS:-1200}
export EVAL_RUNTIME_ROOT=${EVAL_RUNTIME_ROOT:-${ROOT_DIR}/.runtime/smoke-chroot-qwen4b-eval}
export SCORE_RUNTIME_ROOT=${SCORE_RUNTIME_ROOT:-${ROOT_DIR}/.runtime/smoke-chroot-qwen4b-score}

echo "[smoke] vllm_env=${VLLM_ENV_NAME} model=${MODEL_NAME} slice=${INSTANCE_SLICE} chroot=true"
exec bash "${ROOT_DIR}/sh/run_sweagent_formal_ascend.sh"
