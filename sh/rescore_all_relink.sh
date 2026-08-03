#!/bin/bash
# Re-score ALL sampled attempts WITH SWE_RELINK_EDITABLE=1 (fixes editable-install false-negatives).
# Sequential (one at a time) -> no shared-gitcache race. 8 workers.
ROOT=/path/to/SWE-MiniSandbox
source /path/to/workspace/miniforge3/etc/profile.d/conda.sh; conda activate swe-sandbox
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export PYTHONPATH=$ROOT/SWE-ReX/src:$ROOT/SWE-agent:$ROOT/sandboxdev:$ROOT/SWE-bench:$ROOT/R2E-Gym/src
export LITELLM_LOCAL_MODEL_COST_MAP=True SWE_SANDBOX_SCORE_MODEL_PATCH=1 SWE_RELINK_EDITABLE=1
# name|patches_json|cache_pool
JOBS=(
 "gate|$ROOT/vendor/gate_recovered_nonempty.json|broad-pool"
 "r2a1|$ROOT/vendor/sft-data/bulk_r2_a1_nonempty.json|broad-pool-r2"
 "r2a2|$ROOT/vendor/sft-data/bulk_r2_a2_nonempty.json|broad-pool-r2"
 "r2a3|$ROOT/vendor/sft-data/bulk_r2_a3_nonempty.json|broad-pool-r2"
 "r1a2|$ROOT/vendor/sft-data/bulk_r1_a2_nonempty.json|broad-pool"
 "r1a3|$ROOT/vendor/sft-data/bulk_r1_a3_nonempty.json|broad-pool"
 "r1a4|$ROOT/vendor/sft-data/bulk_r1_a4_nonempty.json|broad-pool"
 "deepen|$ROOT/vendor/sft-data/bulk_deepen_nonempty.json|deepen-pool"
)
for job in "${JOBS[@]}"; do
  IFS='|' read -r name patches pool <<< "$job"
  echo "=== RESCORE $name (relink) ==="
  hsh=$(python3 -c "import json;print('|'.join(k.rsplit('__',1)[1] for k in json.load(open('$patches'))))")
  filt=".*__($hsh)"
  n=$(python3 -c "import json;print(len(json.load(open('$patches'))))")
  SC=$ROOT/vendor/swesmith-cache/rescore-$name; rm -rf $SC; mkdir -p $SC/sandbox
  python -m sweagent run-batch \
    --config $ROOT/config/swesmith_infer.yaml --env_type sandbox --num_workers 8 \
    --agent.type empty --agent.pre_check true \
    --instances.type swesmith --instances.path $ROOT/dataset/SWE-smith --instances.split train \
    --instances.start 0 --instances.end 59136 \
    --instances.filter "$filt" --instances.slice ":$n" \
    --instances.model_patch_file "$patches" \
    --instances.deployment.data_type swesmith --instances.deployment.use_chroot false \
    --instances.deployment.root_base $SC/sandbox \
    --instances.deployment.git_base_path $ROOT/vendor/swesmith-cache/$pool/gitcache \
    --instances.deployment.shared_venv $ROOT/vendor/swesmith-cache/$pool/shared_venv \
    --instances.deployment.conda_env /path/to/workspace/minisandbox-conda \
    --instances.deployment.wheelhouse $ROOT/vendor/minisandbox-wheelhouse \
    --instances.deployment.tool_path $ROOT/SWE-agent/tools \
    --output_dir $SC/output || true
done
echo "=== RESCORE ALL DONE ==="
