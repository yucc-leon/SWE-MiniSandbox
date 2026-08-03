#!/usr/bin/env bash
# SWE-smith env-build smoke: build ONE swesmith task env (clone github.com/swesmith/<repo>
# + venv) and run the gold pre_check (swesmith applies the bug patch REVERSE = the fix,
# + test_patch, runs FAIL_TO_PASS -> should resolve). Runs on the LOGIN NODE (has network,
# no chroot needed for cache-building; auto-falls back to use_chroot=False). Confirms the
# swesmith pipeline works on our aarch64 stack AND seeds the gitcache+venv for reuse.
set -uo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
RUN_ENV_BIN="${MINIFORGE_ROOT}/envs/swe-sandbox/bin"
# NOT under .runtime (root-owned, unwritable by `claude` on the login node). Shared NFS path
# so an offline pod (root) can later reuse the gitcache/venv this builds.
BASE=${BASE:-${ROOT_DIR}/vendor/swesmith-cache/smoke}
mkdir -p "${BASE}"

export PATH="${RUN_ENV_BIN}:${PATH}"
export CONDA_PREFIX="${MINIFORGE_ROOT}/envs/swe-sandbox"
export PYTHONPATH="${ROOT_DIR}/SWE-ReX/src:${ROOT_DIR}/SWE-agent:${ROOT_DIR}/sandboxdev:${ROOT_DIR}/SWE-bench:${ROOT_DIR}/R2E-Gym/src${PYTHONPATH:+:${PYTHONPATH}}"
# network needed to clone swesmith repos; the env proxy blocks github -> clear it
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export NO_PROXY=127.0.0.1,localhost
export LITELLM_LOCAL_MODEL_COST_MAP=True

python -m sweagent run-batch \
  --config "${ROOT_DIR}/config/swesmith_infer.yaml" \
  --env_type sandbox \
  --num_workers 1 \
  --output_dir "${BASE}/output" \
  --agent.type empty \
  --agent.pre_check true \
  --instances.type swesmith \
  --instances.path "${ROOT_DIR}/dataset/SWE-smith" \
  --instances.split train \
  --instances.start 0 \
  --instances.end "${INSTANCE_END:-60000}" \
  --instances.filter "${INSTANCE_FILTER:-.*}" \
  --instances.slice ":1" \
  --instances.deployment.data_type swesmith \
  --instances.deployment.use_chroot false \
  --instances.deployment.root_base "${BASE}/sandbox" \
  --instances.deployment.git_base_path "${BASE}/gitcache" \
  --instances.deployment.shared_venv "${BASE}/shared_venv" \
  --instances.deployment.conda_env /path/to/workspace/minisandbox-conda \
  --instances.deployment.wheelhouse "${ROOT_DIR}/vendor/minisandbox-wheelhouse" \
  --instances.deployment.tool_path "${ROOT_DIR}/SWE-agent/tools"
echo "SMOKE_EXIT=$?"
