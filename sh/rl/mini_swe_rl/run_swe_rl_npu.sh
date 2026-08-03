#!/bin/bash
# SWE-MiniSandbox RL (GRPO) on Ascend NPU — TINY pipeline-validation run.
# Fuses: codescout's verified NPU env vars + SkyRL mini_swe_agent GRPO params + OUR entry/data/config.
# Goal = CLOSE THE LOOP (rollout -> container-free env -> fixed scorer reward -> GRPO update), not perf.
set -euo pipefail
ROOT=/path/to/SWE-MiniSandbox
SKYRL=/path/to/SkyRL
RLDIR=$ROOT/sh/rl/mini_swe_rl

# ---- Ascend NPU env (from codescout/scripts/run_async_training_npu.sh, verified) ----
source /usr/local/Ascend/ascend-toolkit/latest/bin/setenv.bash 2>/dev/null || true
export ASCEND_RT_VISIBLE_DEVICES=${ASCEND_RT_VISIBLE_DEVICES:-0,1,2,3}
export ASCEND_GLOBAL_LOG_LEVEL=3 ASCEND_SLOG_PRINT_TO_STDOUT=0 ASCEND_LAUNCH_BLOCKING=0
# NOTE: do NOT set expandable_segments:True — vllm-ascend's CaMemAllocator (sleep-mode memory pool,
# needed for colocate_all) asserts against it. (codescout's script used it but ran non-colocated.)
unset PYTORCH_NPU_ALLOC_CONF 2>/dev/null || true
export HCCL_CONNECT_TIMEOUT=360 HCCL_EXEC_TIMEOUT=360 HCCL_IF_BASE_PORT=64033 HCCL_OP_EXPANSION_MODE=AIV
export VLLM_ASCEND_ENABLE_NZ=0
export HYDRA_FULL_ERROR=1 RAY_DEDUP_LOGS=0 RAY_worker_register_timeout_seconds=600
# Our SWEsbEnv venv-tar extraction uses Ray tasks with a custom resource "tar_io" (sandboxdev/
# swesandbox/utils.py) to throttle concurrent I/O. SkyRL's Ray head doesn't advertise it → extract
# tasks never schedule → rollout HANGS (autoscaler "No available node types ... {'tar_io':1.0}").
# Advertise a large tar_io budget on the head so those tasks schedule.
export RAY_OVERRIDE_RESOURCES='{"tar_io": 100000}'
export CUDA_DEVICE_MAX_CONNECTIONS=1 PYTHONUNBUFFERED=1 WANDB_INIT_TIMEOUT=300
export TORCHINDUCTOR_CACHE_DIR=/tmp/cache/torchinductor TRITON_CACHE_DIR=/tmp/cache/triton
export RAY_TMPDIR=/tmp/ray TMPDIR=/tmp
mkdir -p /tmp/ray /tmp/cache logs

# ---- proxy: rollout deploy/clone needs it; vLLM localhost must bypass ----
PURL=$(cat $ROOT/vendor/.proxy_url 2>/dev/null || true)
if [ -n "$PURL" ]; then
  export http_proxy=$PURL https_proxy=$PURL HTTP_PROXY=$PURL HTTPS_PROXY=$PURL
  no_proxy="localhost,127.0.0.1,0.0.0.0,$(hostname -i 2>/dev/null||echo)"; export no_proxy NO_PROXY="$no_proxy"
  git config --global http.proxy "$PURL"; git config --global https.proxy "$PURL"; git config --global http.proxyAuthMethod basic
fi

# ---- PYTHONPATH: NPU patch + SkyRL + our generator dir + the deployment stack (env-step runs in-proc) ----
export PYTHONPATH=$SKYRL/npu_support:$SKYRL/skyrl-train:$SKYRL/skyrl-gym:$RLDIR:\
$ROOT/SWE-ReX/src:$ROOT/SWE-agent:$ROOT/sandboxdev:$ROOT/SWE-bench:$ROOT/R2E-Gym/src:$ROOT/mini-swe-agent/src:${PYTHONPATH:-}
export SWE_SANDBOX_SCORE_MODEL_PATCH=1 SWE_RELINK_EDITABLE=1 SWESMITH_FULL_DEPS=1 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONNOUSERSITE=1
# Point the rollout agent's litellm at SkyRL's local vLLM HTTP endpoint (enabled below on port 8080).
# Without these the agent's `openai/<model>` call hits real OpenAI -> AuthenticationError.
export OPENAI_API_KEY=dummy OPENAI_BASE_URL=http://127.0.0.1:8080/v1
# mini-swe-agent cost-tracking can't map the local model name (openai//sharedata/...) -> ignore it
export MSWEA_COST_TRACKING=ignore_errors

MODEL="${MODEL:-/path/to/SWE-MiniSandbox/vendor/sft-ckpt/swe_sft_8b_hf}"   # ① learning run: SFT cold-start 8B (not base 4B)
DATA=$ROOT/vendor/rl-data
CKPT="${CKPT:-/tmp/swe_rl_ckpt}"
TRAJ="${TRAJ:-/tmp/swe_rl_trajs}"
# NROLL=8: GRPO needs intra-group variance (n=1 → advantage≡0 → no gradient). venv-extract is
# file-lock-protected (_ensure_extracted_venv_cache), robustness guard skips any race-failed rollout.
NUM_TRAIN=${NUM_TRAIN:-2} NUM_INFER=${NUM_INFER:-2} NROLL=${NROLL:-8} BSZ=${BSZ:-4}
mkdir -p "$CKPT" "$TRAJ"

LOG=logs/$(date +%m%d_%H%M)_swe_rl_npu.log
echo "== SWE-RL NPU tiny run | model=$MODEL train=$NUM_TRAIN infer=$NUM_INFER nroll=$NROLL =="
set -x
python -m main_swe_rl_npu \
  data.train_data="['$DATA/train.parquet']" \
  data.val_data="['$DATA/validation.parquet']" \
  trainer.algorithm.advantage_estimator=grpo \
  trainer.policy.model.path="$MODEL" \
  trainer.strategy=fsdp2 \
  trainer.placement.colocate_all=false \
  trainer.placement.colocate_policy_ref=true \
  trainer.placement.policy_num_gpus_per_node=$NUM_TRAIN \
  trainer.placement.ref_num_gpus_per_node=$NUM_TRAIN \
  trainer.policy.fsdp_config.cpu_offload=true \
  trainer.epochs=1 \
  trainer.train_batch_size=$BSZ \
  trainer.policy_mini_batch_size=$BSZ \
  trainer.micro_train_batch_size_per_gpu=1 \
  trainer.micro_forward_batch_size_per_gpu=1 \
  trainer.eval_before_train=false \
  trainer.eval_interval=-1 \
  trainer.ckpt_interval=999 \
  trainer.hf_save_interval=999 \
  trainer.flash_attn=false \
  trainer.use_sample_packing=false \
  generator.backend=vllm \
  generator.num_inference_engines=$NUM_INFER \
  generator.inference_engine_tensor_parallel_size=1 \
  generator.run_engines_locally=true \
  generator.async_engine=true \
  generator.batched=false \
  generator.n_samples_per_prompt=$NROLL \
  generator.gpu_memory_utilization=0.8 \
  generator.enforce_eager=true \
  generator.weight_sync_backend=hccl \
  generator.enable_http_endpoint=True \
  generator.http_endpoint_host='127.0.0.1' \
  generator.http_endpoint_port=8080 \
  +generator.engine_init_kwargs.enable_auto_tool_choice=true \
  +generator.engine_init_kwargs.tool_call_parser="hermes" \
  generator.max_turns=10 \
  generator.max_input_length=16384 \
  generator.sampling_params.max_generate_length=2048 \
  generator.sampling_params.temperature=1.0 \
  +generator.max_env_workers=4 \
  +generator.miniswe_config_path="$RLDIR/minisandbox_swesmith.yaml" \
  +generator.miniswe_traj_dir="$TRAJ" \
  trainer.algorithm.use_kl_loss=false \
  trainer.logger=console \
  trainer.project_name=swe_rl_npu \
  trainer.run_name=swe-rl-tiny \
  trainer.ckpt_path="$CKPT" \
  2>&1 | tee "$LOG"
