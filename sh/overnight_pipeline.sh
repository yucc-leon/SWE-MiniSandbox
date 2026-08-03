#!/bin/bash
# Overnight auto-relay (NO GLM, cheap, idempotent). Detached via setsid.
# 1) wait until SFT-gen (glm-904a2/b2) + RL gold-screen (rl-deepscreen) pred-counts are STABLE
# 2) submit: score-a2, score-b2 (reuse their build-at-path pools), buggy-pass deepscreen gold->1
# 3) wait those stable
# 4) consolidate: SFT clean set (traj_to_sft on resolved) + RL faithful (gold1 & buggy0 merged) + venv routing map
ROOT=/path/to/SWE-MiniSandbox
LOG=$ROOT/vendor/overnight_pipeline.log
TP=$(command -v tp || echo /usr/local/bin/tp)
PY="source /path/to/workspace/miniforge3/etc/profile.d/conda.sh && conda activate swe-sandbox && python3"
cd $ROOT
exec >> "$LOG" 2>&1
echo "==================== overnight start $(date) ===================="

cnt(){ source /path/to/workspace/miniforge3/etc/profile.d/conda.sh; conda activate swe-sandbox 2>/dev/null; python3 -c "import json,sys
try: print(len(json.load(open(sys.argv[1]))))
except: print(-1)" "$1" 2>/dev/null; }

wait_stable(){  # $1=preds.json path  $2=label ; stable = unchanged across 2x300s and >0
  local f="$1" lbl="$2" prev=-1 cur
  while true; do
    cur=$(cnt "$f")
    echo "[wait] $lbl preds=$cur (prev=$prev) $(date +%H:%M)"
    if [ "$cur" -gt 0 ] && [ "$cur" = "$prev" ]; then echo "[wait] $lbl STABLE at $cur"; return 0; fi
    prev=$cur; sleep 300
  done
}

# ---- 1. wait for the three producers ----
wait_stable "$ROOT/vendor/swesmith-cache/glm-904a2/out/preds.json" "gen-a2"
wait_stable "$ROOT/vendor/swesmith-cache/glm-904b2/out/preds.json" "gen-b2"
wait_stable "$ROOT/vendor/swesmith-cache/rl-deepscreen/gold-output/preds.json" "deepscreen-gold"

echo "==================== producers done, submitting downstream $(date) ===================="

# ---- 2a. score a2 + b2 (reuse their build-at-path pools) ----
for s in a2 b2; do
  P=$ROOT/vendor/swesmith-cache/glm-904$s
  sed -e "s#glm-904/out/preds.json#glm-904$s/out/preds.json#" \
      -e "s#rl-warm-pool#glm-904$s#" -e "s#name: glm50-score#name: ov-score-$s#" \
      -e "s#glm-904/score92#glm-904$s/score#" \
      $ROOT/sh/tp_glm50_score.yaml > $ROOT/sh/.ov_score_$s.yaml 2>/dev/null || true
done
# tp_glm50_score.yaml may differ; build score yamls explicitly instead (robust):
for s in a2 b2; do
cat > $ROOT/sh/.ov_score_$s.yaml <<YAML
name: ov-score-$s
type: common
image: "346"
dc: "19"
cpus: 32
ram: 256
gpus: 0
replicas: 1
privileged: true
maxrunseconds: 14400
cmd: >-
  cd $ROOT && ROOT=$ROOT &&
  bash sh/swesmith_score.sh $ROOT/vendor/swesmith-cache/glm-904$s/out/preds.json glm-904$s $ROOT/vendor/swesmith-cache/glm-904$s/score 8
  2>&1 | tee $ROOT/vendor/swesmith-cache/glm-904$s/score.log
YAML
  echo "[submit] ov-score-$s"; "$TP" task submit -f $ROOT/sh/.ov_score_$s.yaml -y
done

# ---- 2b. buggy-pass deepscreen gold->1 (reuse rl-deepscreen pool) ----
source /path/to/workspace/miniforge3/etc/profile.d/conda.sh && conda activate swe-sandbox
python3 -c "import json,re
d=json.load(open('$ROOT/vendor/swesmith-cache/rl-deepscreen/gold-output/preds.json'))
g=[k for k,v in d.items() if v.get('reward')==1]
open('$ROOT/vendor/rl_deepscreen_gold1.txt','w').write('.*__('+'|'.join(sorted(k.rsplit('__',1)[-1] for k in g))+')')
print('[deepscreen] gold1=',len(g))"
cat > $ROOT/sh/.ov_buggy.yaml <<YAML
name: ov-buggy-ds
type: common
image: "346"
dc: "19"
cpus: 64
ram: 512
gpus: 0
replicas: 1
privileged: true
maxrunseconds: 14400
cmd: >-
  cd $ROOT && ROOT=$ROOT &&
  source /path/to/workspace/miniforge3/etc/profile.d/conda.sh && conda activate swe-sandbox &&
  PURL=\$(cat \$ROOT/vendor/.proxy_url) && export http_proxy=\$PURL https_proxy=\$PURL HTTP_PROXY=\$PURL HTTPS_PROXY=\$PURL &&
  git config --global http.proxy "\$PURL" && git config --global https.proxy "\$PURL" && git config --global http.proxyAuthMethod basic &&
  export PYTHONPATH=\$ROOT/SWE-ReX/src:\$ROOT/SWE-agent:\$ROOT/sandboxdev:\$ROOT/SWE-bench:\$ROOT/R2E-Gym/src
  LITELLM_LOCAL_MODEL_COST_MAP=True SWESMITH_FULL_DEPS=1 SWE_SANDBOX_PRECHECK_NO_GOLD=1 &&
  P=\$ROOT/vendor/swesmith-cache/rl-deepscreen &&
  python -m sweagent run-batch --config \$ROOT/config/swesmith_infer.yaml --env_type sandbox --num_workers 16
  --agent.type empty --agent.pre_check true
  --instances.type swesmith --instances.path \$ROOT/dataset/SWE-smith --instances.split train --instances.start 0 --instances.end 59136
  --instances.filter "\$(cat \$ROOT/vendor/rl_deepscreen_gold1.txt)" --instances.slice ':2100'
  --instances.deployment.data_type swesmith --instances.deployment.use_chroot true
  --instances.deployment.root_base \$P/sandbox-buggy --instances.deployment.git_base_path \$P/gitcache --instances.deployment.shared_venv \$P/shared_venv
  --instances.deployment.conda_env /path/to/workspace/minisandbox-conda --instances.deployment.wheelhouse \$ROOT/vendor/minisandbox-wheelhouse --instances.deployment.tool_path \$ROOT/SWE-agent/tools
  --output_dir \$P/buggy-output 2>&1 | tee \$P/buggy.log
YAML
echo "[submit] ov-buggy-ds"; "$TP" task submit -f $ROOT/sh/.ov_buggy.yaml -y

# ---- 3. wait downstream ----
wait_stable "$ROOT/vendor/swesmith-cache/glm-904a2/score/preds.json" "score-a2"
wait_stable "$ROOT/vendor/swesmith-cache/glm-904b2/score/preds.json" "score-b2"
wait_stable "$ROOT/vendor/swesmith-cache/rl-deepscreen/buggy-output/preds.json" "buggy-ds"

echo "==================== consolidating $(date) ===================="
# ---- 4. consolidate ----
# 4a. SFT clean set: traj_to_sft on resolved (a2,b2) + existing batches
for s in a2 b2; do
  python3 sh/traj_to_sft.py --traj-dir vendor/swesmith-cache/glm-904$s/out \
     --resolved-from vendor/swesmith-cache/glm-904$s/score/preds.json \
     --out vendor/sft-data/sft_glm904$s.jsonl || true
done
python3 sh/traj_to_sft.py --traj-dir vendor/swesmith-cache/glm-904/out \
   --resolved-from vendor/swesmith-cache/glm-904/score92/preds.json \
   --out vendor/sft-data/sft_glm904_92.jsonl || true
python3 sh/traj_to_sft.py --traj-dir vendor/swesmith-cache/glm-sample-50/glm50 \
   --resolved-from vendor/swesmith-cache/glm50-rescore/score/preds.json \
   --out vendor/sft-data/sft_glm50.jsonl || true
# 4b. RL faithful merge: existing 954 + deepscreen (gold1 & buggy0)
python3 -c "
import json,glob
faith=set(l.strip() for l in open('vendor/faithful_verified.txt'))
g=set(k for k,v in json.load(open('vendor/swesmith-cache/rl-deepscreen/gold-output/preds.json')).items() if v.get('reward')==1)
try: bd=json.load(open('vendor/swesmith-cache/rl-deepscreen/buggy-output/preds.json'))
except: bd={}
b0=set(k for k,v in bd.items() if v.get('reward')==0)
newf=g & b0
allf=faith | newf
open('vendor/faithful_rl_pool.txt','w').write('\n'.join(sorted(allf))+'\n')
print(f'[consolidate] base faithful={len(faith)} deepscreen new faithful={len(newf)} -> RL pool={len(allf)}')
sft=0
for f in glob.glob('vendor/sft-data/sft_glm*.jsonl'):
    sft+=sum(1 for _ in open(f))
print(f'[consolidate] SFT clean trajectories total={sft}')
"
echo "==================== overnight DONE $(date) ===================="
