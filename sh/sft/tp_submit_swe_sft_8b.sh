#!/usr/bin/env bash
# tp submission for SWE-agent cold-start SFT on Qwen3-8B (reuses run_swe_sft_4b.sh, which is
# fully parametrized: just override MODEL_PATH/TRAIN_PARQUET/NPROC/SP/EXP). 8B uses 8 NPUs +
# SP=8 (so the ~28K sequences shard to ~3.5K tokens/NPU) on top of the 4B recipe's full
# param/optimizer/activation offload + gradient checkpointing.
# RUN:  bash sh/sft/tp_submit_swe_sft_8b.sh                    (5-step smoke, 8 NPU)
#       SMOKE=0 RUN_TAG=r1 bash sh/sft/tp_submit_swe_sft_8b.sh (full: 3 epochs)
set -euo pipefail
SWE=/path/to/SWE-MiniSandbox
IMAGE="${IMAGE:-346}"
SMOKE="${SMOKE:-1}"
RUN_TAG="${RUN_TAG:-smoke1}"
NPROC="${NPROC:-8}"
DATA="${DATA:-$SWE/vendor/sft-data/sft_clean.parquet}"
MODEL="${MODEL:-/path/to/workspace/models/Qwen3-8B}"
if [ "$SMOKE" = "1" ]; then MAXRUN=7200; else MAXRUN=43200; fi
JOBNAME="${JOBNAME:-swe-sft-8b-${RUN_TAG}}"
LOG=$SWE/vendor/sft-ckpt/swe_sft_8b_${RUN_TAG}.log
mkdir -p "$SWE/vendor/sft-ckpt"

/usr/local/bin/tp --dc 19 task submit \
  --name "$JOBNAME" --image "$IMAGE" --gpu "$NPROC" --cpu 64 --mem 768 --replicas 1 \
  --max-run-seconds "$MAXRUN" -y \
  --cmd "WANDB_API_KEY_FILE=/path/to/workspace/.wandb_api_key SMOKE=$SMOKE \
         MODEL_PATH=$MODEL TRAIN_PARQUET=$DATA NPROC=$NPROC ULYSSES_SP_SIZE=$NPROC EXP=swe_sft_8b \
         bash $SWE/sh/sft/run_swe_sft_4b.sh 2>&1 | tee $LOG"
