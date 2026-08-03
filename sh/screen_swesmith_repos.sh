#!/usr/bin/env bash
# Multi-repo swesmith gold-resolve generalization screen.
# For each instance: build env (login node) + run gold pre_check. Isolated BASE per repo.
# Concurrency-capped. Confirms the depth/HEAD^ fixes generalize and surfaces which repos
# need per-repo install completion. Login node only (no NPU).
set -uo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCREEN_DIR="${ROOT_DIR}/vendor/swesmith-cache/screen"
mkdir -p "${SCREEN_DIR}"
CONC="${CONC:-4}"

INSTANCES=(
  "Suor__funcy.207a7810.func_basic__0gq79msc"
  "dbader__schedule.82a43db1.func_basic__1d2lxayf"
  "bottlepy__bottle.a8dfef30.func_basic__0mdlomrj"
  "mahmoud__boltons.3bfcfdd0.func_basic__00rajvaw"
  "keleshev__schema.24a30457.func_basic__0aje89jo"
  "r1chardj0n3s__parse.30da9e4f.func_basic__0i1oecyo"
  "rustedpy__result.0b855e1e.func_basic__08l7xwcj"
  "pyparsing__pyparsing.533adf47.func_basic__0002aaoe"
)

run_one() {
  local iid="$1"
  local tag="${iid%%.*}"            # repo short tag
  local base="${SCREEN_DIR}/${tag}"
  local log="${SCREEN_DIR}/${tag}.log"
  rm -rf "${base}"
  BASE="${base}" INSTANCE_END=59136 INSTANCE_FILTER=".*${iid}" \
    timeout 1800 bash "${ROOT_DIR}/sh/run_swesmith_envbuild_smoke.sh" > "${log}" 2>&1
  echo "FINISHED ${tag} (exit $?)"
}
export -f run_one
export ROOT_DIR SCREEN_DIR

printf "%s\n" "${INSTANCES[@]}" | xargs -P "${CONC}" -I{} bash -c 'run_one "$@"' _ {}
echo "=== ALL BUILDS DONE ==="
