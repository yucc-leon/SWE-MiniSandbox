#!/bin/bash
# Sequentially score r1 bulk attempts 2,3,4 (one at a time -> no shared-gitcache race).
set -e
ROOT=/path/to/SWE-MiniSandbox
source /path/to/workspace/miniforge3/etc/profile.d/conda.sh
conda activate swe-sandbox
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export PYTHONPATH=$ROOT/SWE-ReX/src:$ROOT/SWE-agent:$ROOT/sandboxdev:$ROOT/SWE-bench:$ROOT/R2E-Gym/src
export LITELLM_LOCAL_MODEL_COST_MAP=True SWE_SANDBOX_SCORE_MODEL_PATCH=1
BROAD=$ROOT/vendor/swesmith-cache/broad-pool
for i in 2 3 4; do
  echo "=== SCORE r1 attempt_$i ==="
  SC=$ROOT/vendor/swesmith-cache/bulk-r1-a${i}-score
  rm -rf $SC; mkdir -p $SC/sandbox
  python -m sweagent run-batch \
    --config $ROOT/config/swesmith_infer.yaml --env_type sandbox --num_workers 8 \
    --agent.type empty --agent.pre_check true \
    --instances.type swesmith --instances.path $ROOT/dataset/SWE-smith --instances.split train \
    --instances.start 0 --instances.end 59136 \
    --instances.filter "$(cat $ROOT/vendor/bulk_r1_a${i}_filter.txt)" --instances.slice ':18' \
    --instances.model_patch_file $ROOT/vendor/sft-data/bulk_r1_a${i}_nonempty.json \
    --instances.deployment.data_type swesmith --instances.deployment.use_chroot false \
    --instances.deployment.root_base $SC/sandbox \
    --instances.deployment.git_base_path $BROAD/gitcache \
    --instances.deployment.shared_venv $BROAD/shared_venv \
    --instances.deployment.conda_env /path/to/workspace/minisandbox-conda \
    --instances.deployment.wheelhouse $ROOT/vendor/minisandbox-wheelhouse \
    --instances.deployment.tool_path $ROOT/SWE-agent/tools \
    --output_dir $SC/output || true
done
echo "=== ALL R1 SCORING DONE ==="
