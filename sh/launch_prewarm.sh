#!/bin/bash
# Generate per-shard prewarm YAMLs from tp_prewarm_template.yaml and submit.
# usage: launch_prewarm.sh <shard_idx>...   e.g.  launch_prewarm.sh 0   |   launch_prewarm.sh 1 2 3
set -euo pipefail
ROOT=/path/to/SWE-MiniSandbox
cd "$ROOT"
for i in "$@"; do
  out="sh/.gen_prewarm_sh$i.yaml"
  sed "s/__SHARD__/$i/g" sh/tp_prewarm_template.yaml > "$out"
  echo "=== submitting shard $i ($(tr '|' '\n' < vendor/prewarm_shard${i}_filter.txt | grep -c .) instances) ==="
  tp task submit -f "$out" -y 2>&1 | tail -2
done
