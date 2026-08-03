#!/bin/bash
# Sync only the reusable tar artifacts (venv.tar / testbed.tar) from a LOCAL build
# dir to an NFS pool, and prune finished sandbox scratch to bound local disk.
# Rationale: env build = thousands of small-file writes -> do on LOCAL overlay disk
# (fast, not NFS-billed); only the finished single-file tarballs touch NFS.
# usage: prewarm_sync.sh <LOCAL> <NFSPOOL> <SHARD> [loop|once]
set -uo pipefail
LOCAL="$1"; NFSPOOL="$2"; SHARD="$3"; MODE="${4:-loop}"

do_sync() {
  for sub in shared_venv gitcache; do
    [ -d "$LOCAL/$sub" ] || continue
    ( cd "$LOCAL/$sub" && find . -name '*.tar' -print0 | while IFS= read -r -d '' f; do
        d="$NFSPOOL/$sub/$(dirname "$f")"; mkdir -p "$d"; cp -u "$f" "$NFSPOOL/$sub/$f"
      done )
  done
}

if [ "$MODE" = once ]; then do_sync; exit 0; fi

while true; do
  do_sync
  # prune finished per-traj sandbox scratch (idle >5min) so local overlay won't fill
  find "$LOCAL/sandbox" -maxdepth 1 -mindepth 1 -type d -mmin +5 -exec rm -rf {} + 2>/dev/null
  echo "[sync $SHARD] $(date +%H:%M:%S) df_avail=$(df -h "$LOCAL" | tail -1 | awk '{print $4}') venvtar=$(find "$NFSPOOL/shared_venv" -name '*.tar' 2>/dev/null | wc -l) testbedtar=$(find "$NFSPOOL/gitcache" -name '*.tar' 2>/dev/null | wc -l)" >> "$NFSPOOL/sync-$SHARD.log"
  sleep 120
done
