# Resume 32B Baseline Fixview 500 Evaluation

This note records the local CPU-side state needed to resume the in-progress
SWE-agent-LM 32B evaluation after a machine reboot. The remote NPU vLLM server
is expected to remain available.

## Run Identity

- Branch/commit at capture: `feat/no-sysadmin`, `2514c0a`
- Captured at: `2026-04-22T10:55:06Z`
- Remote API base: `http://192.168.123.93:8001/v1`
- Served model name: `sweagent-32b`
- Agent model name: `openai/sweagent-32b`
- Model path: `/sharedata/liyuchen/models/sweagent-32b`
- Eval config: `config/sweagent_infer_ascend_minisandbox_baseline.yaml`
- Score config: `config/sweagent_score_ascend_minisandbox_baseline.yaml`
- Instance slice: `:500`
- Generation workers: `16`
- Pipeline score batch size: `8`
- Pipeline score workers: `4`
- Pipeline score max concurrent shards: `2`

Runtime roots:

```bash
export EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851
export SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851
```

Progress at capture:

```text
generation alive: yes
scoring alive: yes
eval pid: 3555067
score pid: 3555052
traj: 340/500
pred files: 331/500
final preds.json: missing
format_errors: 0
json pred: 331
nonempty patches: 308
empty patches: 23
bad json: 0

scored/completed: 307/500
resolved: 97
unresolved: 210
score errors: 5
full-500 resolved_rate: 19.4%
resolved over scored: 31.6%

pipeline shards: 39 completed, 2 running, 0 failed
pipeline scored_ids: 312
pipeline running_ids: 16
```

## If There Is Time Before Shutdown

Prefer a graceful local stop. This should preserve completed instance outputs and
ask the pipeline scorer not to start more shards.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox

export EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851
export SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851

touch "$SCORE_RUNTIME_ROOT/STOP"

PGID=$(ps -o pgid= -p "$(cat "$EVAL_RUNTIME_ROOT/run_batch.pid")" | tr -d ' ')
kill -TERM "-$PGID"

pgrep -af 'ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851|ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851' || true
```

It is also acceptable if the machine is stopped without this. The generation run
is resumable at instance granularity, and scoring shards can be normalized after
reboot.

## Resume After Reboot

1. Enter the repository and update code if needed.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox
git status --short
git pull --ff-only
```

2. Check the remote server. If the IP changed, update `API_BASE` in the resume
command below.

```bash
curl -sS --max-time 10 http://192.168.123.93:8001/v1/models
```

3. Clear a stale `STOP` file and normalize stale scoring shards.

Any shard whose metadata says `running` after a reboot was interrupted. Mark it
as `aborted`; the pipeline scorer will then requeue its instance IDs because
`aborted` is neither `completed`, `running`, nor `failed`.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox

export SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851

rm -f "$SCORE_RUNTIME_ROOT/STOP"

/sharedata/liyuchen/miniforge3/envs/swe-sandbox/bin/python - <<'PY'
import json
import time
from pathlib import Path
from sh.pipeline_scoring_ascend import refresh_state

score_root = Path(".runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851")
for meta in sorted((score_root / "shards").glob("shard-*/metadata.json")):
    data = json.loads(meta.read_text())
    if data.get("status") == "running":
        data["status"] = "aborted"
        data["aborted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta.write_text(json.dumps(data, indent=2) + "\n")

state = refresh_state(score_root)
print("scored", len(state["scored_ids"]))
print("running", len(state["running_ids"]))
print("failed", len(state["failed_ids"]))
PY
```

4. Recover any completed trajectories that were written before their `.pred`
file. This handles the narrow crash window between trajectory save and
prediction save.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox

/sharedata/liyuchen/miniforge3/envs/swe-sandbox/bin/python - <<'PY'
import json
from pathlib import Path

output_root = Path(".runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851/output")
recovered = 0
for traj in sorted(output_root.glob("*/*.traj")):
    pred = traj.with_suffix(".pred")
    if pred.exists():
        continue
    try:
        data = json.loads(traj.read_text())
    except Exception:
        continue
    info = data.get("info") or {}
    exit_status = info.get("exit_status")
    if not exit_status or exit_status == "early_exit":
        continue
    pred_data = {
        "reward": info.get("reward"),
        "test_out": info.get("test_out"),
        "p2p": info.get("p2p"),
        "f2p": info.get("f2p"),
        "model_name_or_path": output_root.name,
        "instance_id": traj.parent.name,
        "model_patch": info.get("submission"),
    }
    pred.write_text(json.dumps(pred_data))
    recovered += 1
    print("recovered", pred)
print("recovered_count", recovered)
PY
```

5. Relaunch the same formal remote run with the same runtime roots.

`run-batch` skips existing complete `.traj` files unless `redo_existing` is set,
so completed generation instances should not be rerun. Incomplete current
workers may be rerun.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox

export EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851
export SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851
export API_BASE=http://192.168.123.93:8001/v1
export MODEL_NAME=openai/sweagent-32b
export MODEL_PATH=/sharedata/liyuchen/models/sweagent-32b
export PROBE_CHAT_MODEL=sweagent-32b
export EVAL_CONFIG_PATH=$(pwd)/config/sweagent_infer_ascend_minisandbox_baseline.yaml
export SCORE_CONFIG_PATH=$(pwd)/config/sweagent_score_ascend_minisandbox_baseline.yaml
export INSTANCE_SLICE=:500
export NUM_WORKERS=16
export PREPARE_FIRST=0
export POSTPROCESS_DATASET_SIZE=500
export PIPELINE_SCORING=1
export PIPELINE_SCORE_BATCH_SIZE=8
export PIPELINE_SCORE_NUM_WORKERS=4
export PIPELINE_SCORE_MAX_CONCURRENT_SHARDS=2
export PIPELINE_SCORE_POLL_SECONDS=30
export PIPELINE_SCORE_STABLE_SECONDS=5

bash sh/run_sweagent_formal_remote_infer.sh
```

## Status Checks

Use this after relaunch to confirm the run is advancing.

```bash
cd /sharedata/liyuchen/workspace/SWE-MiniSandbox

ROOT=.runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851
SROOT=.runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851

pgrep -af 'run_sweagent_formal_remote_infer|sweagent run-batch|pipeline_scoring_ascend|eval_watchdog'
printf 'traj=%s\n' "$(find "$ROOT/output" -name '*.traj' | wc -l)"
printf 'pred=%s\n' "$(find "$ROOT/output" -name '*.pred' | wc -l)"

/sharedata/liyuchen/miniforge3/envs/swe-sandbox/bin/python - <<'PY'
import json
from pathlib import Path
from collections import Counter

score_root = Path(".runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851")
results = score_root / "results.json"
if results.exists():
    d = json.loads(results.read_text())
    print({k: d.get(k) for k in [
        "submitted_instances", "scored_instances", "resolved_instances",
        "empty_patch_instances", "error_instances", "resolved_rate",
    ]})

state_file = score_root / "pipeline_state.json"
if state_file.exists():
    s = json.loads(state_file.read_text())
    print({
        "next_shard_index": s.get("next_shard_index"),
        "shards": dict(Counter(x.get("status") for x in s.get("shards", []))),
        "scored_ids": len(s.get("scored_ids", [])),
        "running_ids": len(s.get("running_ids", [])),
        "failed_ids": len(s.get("failed_ids", [])),
    })
PY
```
