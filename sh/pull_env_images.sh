#!/usr/bin/env bash
# One-shot: pull all Docker images for SWE-agent eval + env-factory.
# RUN ON THE x86 MACHINE THAT HAS DOCKER.
#
#   bash sh/pull_env_images.sh --dry-run          # 1) resolve -> env_images_manifest.txt (for whitelist/mirror)
#   bash sh/pull_env_images.sh                     # 2) pull everything (Verified + Pro + swesmith)
#   SOURCES=verified bash sh/pull_env_images.sh    # or a subset
#   WORKERS=12 bash sh/pull_env_images.sh          # tune parallelism
#
# PREREQ (ops, critical in restricted egress): configure the DOCKER DAEMON proxy first —
# shell http_proxy does NOT affect `docker pull`. See header of pull_env_images.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# HF/git/pip proxy (does NOT cover docker pull — that's the daemon's job, see above)
if [ -f "$ROOT/vendor/.proxy_url" ]; then
  PURL="$(cat "$ROOT/vendor/.proxy_url")"
  export http_proxy="$PURL" https_proxy="$PURL" HTTP_PROXY="$PURL" HTTPS_PROXY="$PURL"
fi
# reuse local swesmith parquet if present (skip re-download)
[ -d "$ROOT/dataset/SWE-smith/data" ] && export SWESMITH_LOCAL_PARQUET="$ROOT/dataset/SWE-smith/data"

PY="${PY:-python}"
exec "$PY" "$ROOT/sh/pull_env_images.py" \
  --sources "${SOURCES:-verified,pro,swesmith}" \
  --workers "${WORKERS:-8}" "$@"
