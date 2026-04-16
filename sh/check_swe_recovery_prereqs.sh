#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_ROOT_BASE=${RUNTIME_ROOT_BASE:-${ROOT_DIR}/.runtime}
PROFILE_PATH=${1:-}

if [[ -n "${PROFILE_PATH}" ]]; then
  if [[ ! -f "${PROFILE_PATH}" ]]; then
    echo "profile not found: ${PROFILE_PATH}" >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  source "${PROFILE_PATH}"
fi

MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
RUN_ENV_NAME=${RUN_ENV_NAME:-swe-sandbox}
VLLM_ENV_NAME=${VLLM_ENV_NAME:-vllm-ascend-cann8.3-v011-clean}
CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-/sharedata/liyuchen/minisandbox-wheelhouse}
DATASET_DIR=${DATASET_DIR:-${ROOT_DIR}/dataset/SWE-bench/SWE-bench_Verified/data}
MODEL_PATH=${MODEL_PATH:-/sharedata/liyuchen/models/Qwen3-4B-Instruct-2507}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-/usr/local/Ascend/nnal/atb/set_env.sh}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}
SHARED_VENV_ROOT=${SHARED_VENV_ROOT:-${RUNTIME_ROOT_BASE}/shared_venv}
GITCACHE_ROOT=${GITCACHE_ROOT:-${RUNTIME_ROOT_BASE}/gitcache}

failures=0
warnings=0

check_path() {
  local label=$1
  local path=$2
  local kind=${3:-any}
  local ok=1
  case "${kind}" in
    dir)
      [[ -d "${path}" ]] || ok=0
      ;;
    file)
      [[ -f "${path}" ]] || ok=0
      ;;
    exe)
      [[ -x "${path}" ]] || ok=0
      ;;
    any)
      [[ -e "${path}" ]] || ok=0
      ;;
    *)
      echo "unknown check kind: ${kind}" >&2
      exit 2
      ;;
  esac
  if [[ "${ok}" == "1" ]]; then
    printf 'OK   %-22s %s\n' "${label}" "${path}"
  else
    printf 'MISS %-22s %s\n' "${label}" "${path}"
    failures=$((failures + 1))
  fi
}

check_cmd() {
  local cmd=$1
  if command -v "${cmd}" >/dev/null 2>&1; then
    printf 'OK   %-22s %s\n' "command:${cmd}" "$(command -v "${cmd}")"
  else
    printf 'MISS %-22s %s\n' "command:${cmd}" "not found"
    failures=$((failures + 1))
  fi
}

warn_path() {
  local label=$1
  local path=$2
  if [[ -e "${path}" ]]; then
    printf 'OK   %-22s %s\n' "${label}" "${path}"
  else
    printf 'WARN %-22s %s\n' "${label}" "${path}"
    warnings=$((warnings + 1))
  fi
}

echo "[Machine Profile]"
printf 'ROOT_DIR=%s\n' "${ROOT_DIR}"
printf 'MINIFORGE_ROOT=%s\n' "${MINIFORGE_ROOT}"
printf 'RUN_ENV_NAME=%s\n' "${RUN_ENV_NAME}"
printf 'VLLM_ENV_NAME=%s\n' "${VLLM_ENV_NAME}"
printf 'CONDA_BACKEND_ROOT=%s\n' "${CONDA_BACKEND_ROOT}"
printf 'WHEELHOUSE_ROOT=%s\n' "${WHEELHOUSE_ROOT}"
printf 'DATASET_DIR=%s\n' "${DATASET_DIR}"
printf 'MODEL_PATH=%s\n' "${MODEL_PATH}"
printf 'SHARED_VENV_ROOT=%s\n' "${SHARED_VENV_ROOT}"
printf 'GITCACHE_ROOT=%s\n' "${GITCACHE_ROOT}"
echo

echo "[Required Commands]"
check_cmd git
check_cmd curl
check_cmd npu-smi
echo

echo "[Required Paths]"
check_path repo_root "${ROOT_DIR}" dir
check_path miniforge "${MINIFORGE_ROOT}" dir
check_path run_env_python "${MINIFORGE_ROOT}/envs/${RUN_ENV_NAME}/bin/python" exe
check_path vllm_env_python "${MINIFORGE_ROOT}/envs/${VLLM_ENV_NAME}/bin/python" exe
check_path conda_backends "${CONDA_BACKEND_ROOT}" dir
check_path wheelhouse "${WHEELHOUSE_ROOT}" dir
check_path dataset "${DATASET_DIR}" dir
check_path model "${MODEL_PATH}" dir
check_path ascend_nnal "${ASCEND_NNAL_ENV}" file
check_path ascend_toolkit "${ASCEND_TOOLKIT_ENV}" file
echo

echo "[Fast Restore Caches]"
warn_path shared_venv_root "${SHARED_VENV_ROOT}"
warn_path gitcache_root "${GITCACHE_ROOT}"
echo

echo "[Notes]"
echo "1. Fast restore assumes the new machine can mount the same cache paths."
echo "2. shared_venv is path-sensitive; moving it to a different root usually forces rebuild."
echo "3. Cold restore is still possible via sh/bootstrap_*.sh plus serving-env rebuild from docs/guide/ascend.md."
echo

if (( failures > 0 )); then
  echo "recovery-prereqs: FAIL (${failures} missing item(s))"
  exit 1
fi

if (( warnings > 0 )); then
  echo "recovery-prereqs: OK with WARNINGS (${warnings})"
  exit 0
fi

echo "recovery-prereqs: OK"
