#!/usr/bin/env bash
# tp submission for SWE-agent cold-start SFT (Qwen3-4B-2507, our chroot-validated data).
# RUN:  bash sh/sft/tp_submit_swe_sft_4b.sh                       (5-step smoke, 4 NPU)
#       SMOKE=0 RUN_TAG=r1 bash sh/sft/tp_submit_swe_sft_4b.sh    (full: 3 epochs)
# NOTE: needs 4 free NPUs (current personal quota 32, must not be full).
set -euo pipefail
SWE=/path/to/SWE-MiniSandbox
IMAGE="${IMAGE:-346}"
SMOKE="${SMOKE:-1}"
RUN_TAG="${RUN_TAG:-smoke1}"
if [ "$SMOKE" = "1" ]; then MAXRUN=7200; else MAXRUN=43200; fi
JOBNAME="${JOBNAME:-swe-sft-4b-${RUN_TAG}}"
LOG=$SWE/vendor/sft-ckpt/swe_sft_4b_${RUN_TAG}.log
mkdir -p "$SWE/vendor/sft-ckpt"

/usr/local/bin/tp --dc 19 task submit \
  --name "$JOBNAME" --image "$IMAGE" --gpu 4 --cpu 32 --mem 512 --replicas 1 \
  --max-run-seconds "$MAXRUN" -y \
  --cmd "WANDB_API_KEY_FILE=/path/to/workspace/.wandb_api_key SMOKE=$SMOKE bash $SWE/sh/sft/run_swe_sft_4b.sh 2>&1 | tee $LOG"
