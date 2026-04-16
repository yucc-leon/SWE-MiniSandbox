#!/usr/bin/env bash
set -euo pipefail

MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
BACKEND_ROOT=${BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
PYTHON_VERSIONS=${PYTHON_VERSIONS:-"3.6 3.7 3.8 3.9 3.10 3.11 3.12"}

CONDA_BIN="${MINIFORGE_ROOT}/bin/conda"
if [[ ! -x "${CONDA_BIN}" ]]; then
  echo "conda binary not found: ${CONDA_BIN}" >&2
  exit 1
fi

for version in ${PYTHON_VERSIONS}; do
  prefix="${BACKEND_ROOT}/${version}/miniconda3"
  echo "[MiniSandbox] ensuring backend ${prefix}"
  "${CONDA_BIN}" create -y -p "${prefix}" "python=${version}" pip setuptools wheel
done

echo
echo "Backend interpreters:"
for version in ${PYTHON_VERSIONS}; do
  "${BACKEND_ROOT}/${version}/miniconda3/bin/python" -V
done
