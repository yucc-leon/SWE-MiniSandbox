#!/usr/bin/env bash
set -euo pipefail

if ! command -v unshare >/dev/null 2>&1; then
  echo "ERROR: RL training requires chroot/mount namespace isolation, but 'unshare' is not installed." >&2
  echo "No-chroot MiniSandbox is supported only as an evaluation fallback." >&2
  exit 1
fi

if ! unshare --mount true >/dev/null 2>&1; then
  echo "ERROR: RL training requires 'unshare --mount' permission, usually CAP_SYS_ADMIN in the container/pod." >&2
  echo "No-chroot MiniSandbox is supported only as an evaluation fallback; do not use it for reward training." >&2
  exit 1
fi

if ! command -v chroot >/dev/null 2>&1; then
  echo "ERROR: RL training requires 'chroot', but it is not installed." >&2
  exit 1
fi

echo "chroot/mount namespace preflight passed for RL training."
