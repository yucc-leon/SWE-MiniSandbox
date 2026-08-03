#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <runtime_dir> <instance_slice> <num_workers> [auto_start_server]" >&2
  exit 2
fi

RUNTIME_DIR="$1"
INSTANCE_SLICE="$2"
NUM_WORKERS="$3"
AUTO_START_SERVER="${4:-1}"

mkdir -p "$RUNTIME_DIR"

LAUNCHER_LOG="$RUNTIME_DIR/launcher.log"
LAUNCHER_PID="$RUNTIME_DIR/launcher.pid"

: >"$LAUNCHER_LOG"

setsid env \
  INSTANCE_SLICE="$INSTANCE_SLICE" \
  NUM_WORKERS="$NUM_WORKERS" \
  AUTO_START_SERVER="$AUTO_START_SERVER" \
  bash sh/run_sweagent_eval_ascend.sh \
  >"$LAUNCHER_LOG" 2>&1 < /dev/null &

echo $! >"$LAUNCHER_PID"
echo "pid=$(cat "$LAUNCHER_PID")"
echo "log=$LAUNCHER_LOG"
