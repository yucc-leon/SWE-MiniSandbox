#!/bin/bash
# Detached scheduler: wait until TARGET then submit A (throughput re-measure) + B (GLM-50 guarded).
# Quota-free at fire time (pure tp submit, no Claude). Started via setsid so it survives session end.
ROOT=/path/to/SWE-MiniSandbox
LOG=$ROOT/vendor/scheduled_AB.log
TP="/usr/local/bin/tp"
TARGET=1782248190
cd $ROOT
{
  echo "[sched] started $(date), will fire at $(date -d @$TARGET)"
  while [ "$(date +%s)" -lt "$TARGET" ]; do sleep 300; done
  echo "[sched] === FIRING $(date) ==="
  echo "[sched] submit B (GLM-50 guarded):"
  "$TP" task submit -f $ROOT/sh/tp_glm_sample_50.yaml -y 2>&1
  echo "[sched] submit A (throughput re-measure):"
  "$TP" task submit -f $ROOT/sh/tp_rl_throughput.yaml -y 2>&1
  echo "[sched] === SUBMITTED, done $(date) ==="
} >> $LOG 2>&1
