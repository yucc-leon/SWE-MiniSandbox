#!/usr/bin/env bash
# SWE-agent cold-start SFT on Qwen3-4B-Instruct-2507 (Ascend/NPU).
# Cloned from SAT-dci-grep/scripts/run_dci_coldstart_sft_4b.sh (verl FSDP sft_trainer),
# data swapped to our chroot-validated SWE SFT (messages-format parquet).
#
#   Data : vendor/sft-data/sft_swe_clean.parquet  (140 rows: last-turn=assistant, <=28K est tokens)
#   Base : Qwen3-4B-Instruct-2507 (HF)
#   SMOKE=1 (default): 5 steps to validate the loop.  SMOKE=0: 1 epoch.
set -euo pipefail

KT=/path/to/KTAscendRL
SWE=/path/to/SWE-MiniSandbox
VERL_DIR="${VERL_DIR:-$KT/verl}"
CONDA_BASE=/path/to/workspace/miniforge3
export PATH="$CONDA_BASE/envs/verl-rl-train/bin:$CONDA_BASE/bin:$PATH"
export PYTHONPATH="$VERL_DIR:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1

MODEL_PATH="${MODEL_PATH:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}"
TRAIN_PARQUET="${TRAIN_PARQUET:-$SWE/vendor/sft-data/sft_swe_clean.parquet}"
SMOKE="${SMOKE:-1}"
EXP="${EXP:-swe_sft_4b}"
SAVE_DIR="${SAVE_DIR:-$SWE/vendor/sft-ckpt/$EXP}"
NPROC="${NPROC:-4}"
# SWE trajectories run longer than grepseek (median ~13K, up to ~28K after our filter),
# so max_length is larger than dci's 16384, and SP=4 (=NPROC) keeps per-NPU sequence
# memory in check. Drop these if smoke OOMs.
ULYSSES_SP_SIZE="${ULYSSES_SP_SIZE:-4}"
MAX_LENGTH="${MAX_LENGTH:-28672}"
TRAIN_BATCH_SIZE="${TRAIN_BATCH_SIZE:-16}"
LR="${LR:-5e-6}"
TOTAL_EPOCHS="${TOTAL_EPOCHS:-3}"
SAVE_FREQ="${SAVE_FREQ:-50}"
STEPS_ARG=""
[ "$SMOKE" = "1" ] && STEPS_ARG="trainer.total_training_steps=5"

set +u
source /usr/local/Ascend/ascend-toolkit/set_env.sh 2>/dev/null || true
source /usr/local/Ascend/nnal/atb/set_env.sh 2>/dev/null || true
set -u
export PYTORCH_NPU_ALLOC_CONF="${PYTORCH_NPU_ALLOC_CONF:-garbage_collection_threshold:0.8}"
export HYDRA_FULL_ERROR=1 PYTHONUNBUFFERED=1

if [ -n "${WANDB_API_KEY_FILE:-}" ] && [ -r "$WANDB_API_KEY_FILE" ]; then
    export WANDB_API_KEY="$(tr -d '[:space:]' < "$WANDB_API_KEY_FILE")"
fi
LOGGER='[console]'
[ -n "${WANDB_API_KEY:-}" ] && LOGGER='[console,wandb]' && export WANDB_PROJECT=swe_sft

mkdir -p "$SAVE_DIR"
cd "$VERL_DIR"
torchrun --nnodes=1 --nproc_per_node="$NPROC" \
    -m verl.trainer.sft_trainer \
        data.train_files="$TRAIN_PARQUET" \
        data.val_files=null \
        data.messages_key=messages \
        data.train_batch_size="$TRAIN_BATCH_SIZE" \
        data.micro_batch_size_per_gpu=1 \
        data.max_token_len_per_gpu="$MAX_LENGTH" \
        data.use_dynamic_bsz=true \
        data.max_length="$MAX_LENGTH" \
        data.truncation=right \
        data.pad_mode=no_padding \
        data.ignore_input_ids_mismatch=true \
        model.path="$MODEL_PATH" \
        model.use_remove_padding=true \
        model.enable_gradient_checkpointing=true \
        model.enable_activation_offload=true \
        engine.strategy=fsdp \
        engine.dtype=bfloat16 \
        engine.param_offload=true \
        engine.optimizer_offload=true \
        engine.ulysses_sequence_parallel_size="$ULYSSES_SP_SIZE" \
        optim.lr="$LR" \
        optim.weight_decay=0.01 \
        optim.lr_warmup_steps_ratio=0.05 \
        trainer.total_epochs="$TOTAL_EPOCHS" \
        trainer.save_freq="$SAVE_FREQ" \
        trainer.test_freq=-1 \
        trainer.logger="$LOGGER" \
        trainer.experiment_name="$EXP" \
        trainer.default_local_dir="$SAVE_DIR" \
        trainer.nnodes=1 \
        trainer.n_gpus_per_node="$NPROC" \
        trainer.resume_mode=auto \
        trainer.device=npu \
        $STEPS_ARG

# --- auto-convert final FSDP-sharded ckpt -> HF safetensors (runs on THIS NPU node, so
# torch_npu + Ascend driver are present — avoids the gpus:0 'no npu device' merge failures).
# Toggle off with MERGE_HF=0. Skipped for smoke runs.
MERGE_HF="${MERGE_HF:-1}"
if [ "$SMOKE" != "1" ] && [ "$MERGE_HF" = "1" ]; then
    STEP_FILE="$SAVE_DIR/latest_checkpointed_iteration.txt"
    if [ -r "$STEP_FILE" ]; then
        LAST_STEP="$(tr -d '[:space:]' < "$STEP_FILE")"
        CKPT="$SAVE_DIR/global_step_${LAST_STEP}"
        HF_OUT="${HF_OUT:-${SAVE_DIR}_hf}"
        echo "[auto-merge] FSDP -> HF: $CKPT -> $HF_OUT"
        python -m verl.model_merger merge --backend fsdp --local_dir "$CKPT" --target_dir "$HF_OUT" \
            && echo "[auto-merge] DONE -> $HF_OUT" \
            && ls "$HF_OUT"/*.safetensors \
            || echo "[auto-merge] FAILED (train ckpt is intact at $CKPT; merge manually)"
    else
        echo "[auto-merge] no $STEP_FILE — skipping (training may have failed)"
    fi
fi
