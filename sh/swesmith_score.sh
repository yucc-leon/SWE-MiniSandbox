#!/bin/bash
# CANONICAL swesmith model-patch scorer — single source of truth for the CORRECT flag set.
# Built because the same class of bug kept recurring: a mode-specific flag (reverse direction,
# SWE_RELINK_EDITABLE, ...) silently omitted in hand-assembled YAMLs -> plausible-but-wrong
# reward 0 (false negatives). Encode the invariant so it can't be forgotten + self-check the
# output so a silent misconfig becomes a LOUD error.
#
# Usage: swesmith_score.sh <patches.json> <cache_pool_name> <out_dir> [num_workers]
#   patches.json : {instance_id: {model_patch: "diff ..."}}  (model's FORWARD fix)
#   cache_pool   : name under vendor/swesmith-cache/ holding gitcache/ + shared_venv/
#   out_dir      : where to write output/preds.json
set -uo pipefail
ROOT=/path/to/SWE-MiniSandbox
PATCHES="$1"; POOL="$2"; OUT="$3"; NW="${4:-8}"   # default 8 workers (>8 races on shared gitcache)
CACHE="$ROOT/vendor/swesmith-cache/$POOL"

source /path/to/workspace/miniforge3/etc/profile.d/conda.sh; conda activate swe-sandbox
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export PYTHONPATH=$ROOT/SWE-ReX/src:$ROOT/SWE-agent:$ROOT/sandboxdev:$ROOT/SWE-bench:$ROOT/R2E-Gym/src
export LITELLM_LOCAL_MODEL_COST_MAP=True
# --- THE INVARIANT (the two flags that MUST go together for swesmith model-patch scoring) ---
export SWE_SANDBOX_SCORE_MODEL_PATCH=1   # model patch is a FORWARD fix (not gold's reverse)
export SWE_RELINK_EDITABLE=1             # re-link `pip install -e .` to THIS testbed (else import fails -> false-neg)

# precondition checks
[ -f "$PATCHES" ] || { echo "FATAL: patches not found: $PATCHES"; exit 1; }
[ -d "$CACHE/gitcache" ] || { echo "FATAL: cache gitcache missing: $CACHE/gitcache"; exit 1; }
HSH=$(python3 -c "import json;print('|'.join(k.rsplit('__',1)[1] for k in json.load(open('$PATCHES'))))")
N=$(python3 -c "import json;print(len(json.load(open('$PATCHES'))))")
[ "$N" -gt 0 ] || { echo "FATAL: 0 patches in $PATCHES"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT/sandbox"

echo "[swesmith_score] scoring $N patches | cache=$POOL | workers=$NW | RELINK+MODEL_PATCH=on"
python -m sweagent run-batch \
  --config $ROOT/config/swesmith_infer.yaml --env_type sandbox --num_workers "$NW" \
  --agent.type empty --agent.pre_check true \
  --instances.type swesmith --instances.path $ROOT/dataset/SWE-smith --instances.split train \
  --instances.start 0 --instances.end 59136 \
  --instances.filter ".*__($HSH)" --instances.slice ":$N" \
  --instances.model_patch_file "$PATCHES" \
  --instances.deployment.data_type swesmith --instances.deployment.use_chroot false \
  --instances.deployment.root_base "$OUT/sandbox" \
  --instances.deployment.git_base_path "$CACHE/gitcache" \
  --instances.deployment.shared_venv "$CACHE/shared_venv" \
  --instances.deployment.conda_env /path/to/workspace/minisandbox-conda \
  --instances.deployment.wheelhouse $ROOT/vendor/minisandbox-wheelhouse \
  --instances.deployment.tool_path $ROOT/SWE-agent/tools \
  --output_dir "$OUT" || true

# --- POSTCONDITION SELF-CHECK: turn silent false-negatives into a LOUD warning ---
python3 - "$OUT/preds.json" "$N" "$PATCHES" <<'PY'
import json, sys, re
p, n = sys.argv[1], int(sys.argv[2])
try: d = json.load(open(p))
except Exception: print(f"[swesmith_score] WARN: no preds at {p}"); sys.exit(0)
# CANARY GATE: any EMPTY-model_patch entry in the input is a negative control that MUST score 0
# (an empty patch = no fix => a faithful instance must FAIL its F2P). If an empty patch scores 1,
# the scorer is vacuous/regressed -> ABORT LOUDLY. Callers should include 1-3 known-faithful
# instances with model_patch="" as canaries. (Guards the model_patch_file-ignored class of bug.)
try:
    patches = json.load(open(sys.argv[3]))
    canary = [k for k,v in patches.items() if not str(v.get('model_patch','')).strip()]
    bad = [k for k in canary if d.get(k,{}).get('reward')==1]
    if canary:
        print(f"[swesmith_score] CANARY: {len(canary)} empty-patch control(s); passed-as-resolved={len(bad)}")
    if bad:
        print(f"[swesmith_score] ❌❌ CANARY FAILED: empty patch scored reward=1 (VACUOUS scorer!) -> {bad[:3]} "
              f"— DO NOT trust these scores; scoring path is broken again.")
        sys.exit(3)
except (IndexError, FileNotFoundError):
    pass
res = sum(1 for x in d.values() if x.get('reward')==1)
# detect the editable/import false-negative signature among reward-0
susp = [k for k,x in d.items() if x.get('reward')==0 and re.search(
        r'No module named|collected 0 items|ModuleNotFoundError|ImportError', x.get('test_out','') or '')]
miss = n - len(d)
print(f"[swesmith_score] RESULT resolve={res}/{len(d)} (scored {len(d)}/{n}; {miss} not deployed)")
if susp:
    print(f"[swesmith_score] ⚠️ {len(susp)} reward-0 look like IMPORT/COLLECTION errors (editable/dep false-negative?) "
          f"e.g. {susp[:3]} — investigate, do NOT treat as genuine non-fix")
if miss > n*0.15:
    print(f"[swesmith_score] ⚠️ {miss} patches not deployed (>15%) — likely gitcache RACE; re-run at <=8 workers")
PY
