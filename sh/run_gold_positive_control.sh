#!/usr/bin/env bash
# Positive control for the chroot scoring ruler: feed the dataset GOLD patches as
# predictions and score them under chroot. If gold -> resolved, scoring correctly
# grades a real fix (the smoke only proved 0-error scoring of EMPTY patches).
# Run in a PRIVILEGED tp pod (image 346). Reuses the smoke's warmed caches.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
PY="${MINIFORGE_ROOT}/envs/swe-sandbox/bin/python"
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
INSTANCE_SLICE=${INSTANCE_SLICE:-:5}
SCORE_RUNTIME_ROOT=${SCORE_RUNTIME_ROOT:-${ROOT_DIR}/.runtime/goldpc2-chroot-score}
PREDS=${PREDS:-${SCORE_RUNTIME_ROOT}/gold_preds.json}
# offline pip find-links root (repo-local, mounted into chroot); bootstrapped with chardet + aux deps
export WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-${ROOT_DIR}/vendor/minisandbox-wheelhouse}
mkdir -p "${SCORE_RUNTIME_ROOT}"

echo "[goldpc] building gold preds (model_patch = dataset gold patch) for ALL instances"
# Build preds for EVERY instance keyed by instance_id, so the harness's own
# --instances.slice selection always finds a matching model_patch regardless of
# dataset load ordering (raw datasets order != harness order).
"${PY}" - "$DATASET_DIR" "$PREDS" <<'PY'
import sys, json
from datasets import load_dataset
ddir, out = sys.argv[1], sys.argv[2]
ds = load_dataset(ddir, split="test")
preds = {}
for r in ds:
    preds[r["instance_id"]] = {
        "instance_id": r["instance_id"],
        "model_name_or_path": "gold",
        "model_patch": r["patch"],
    }
json.dump(preds, open(out, "w"))
print("wrote", len(preds), "gold preds ->", out)
PY

export CONFIG_PATH=${SCORE_CONFIG_PATH:-${ROOT_DIR}/config/sweagent_score_ascend_chroot.yaml}
export RUNTIME_ROOT="${SCORE_RUNTIME_ROOT}"
export PREDICTIONS_PATH="${PREDS}"
export INSTANCE_SLICE="${INSTANCE_SLICE}"
export NUM_WORKERS=${NUM_WORKERS:-4}
export POSTPROCESS_SCORING=1
# fresh caches under this runtime root (no cross-run reuse)

echo "[goldpc] scoring gold patches under chroot (config=${CONFIG_PATH}, wheelhouse=${WHEELHOUSE_ROOT})"
exec bash "${ROOT_DIR}/sh/run_swebench_scoring_ascend.sh"
