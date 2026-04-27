#!/usr/bin/env bash
set -euo pipefail

MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
CONDA_BACKEND_ROOT=${CONDA_BACKEND_ROOT:-/sharedata/liyuchen/minisandbox-conda}
WHEELHOUSE_ROOT=${WHEELHOUSE_ROOT:-/sharedata/liyuchen/minisandbox-wheelhouse}
PY_VERSIONS=${PY_VERSIONS:-"3.6 3.7 3.8 3.9 3.10 3.11 3.12"}
PACKAGES=${PACKAGES:-"mpmath==1.3.0 flake8 flake8-comprehensions pip setuptools wheel chardet asgiref sqlparse pytz"}

ACTIVATE_SCRIPT="${MINIFORGE_ROOT}/bin/activate"
if [[ ! -f "${ACTIVATE_SCRIPT}" ]]; then
  echo "activate script not found: ${ACTIVATE_SCRIPT}" >&2
  exit 1
fi

mkdir -p "${WHEELHOUSE_ROOT}"

for py_version in ${PY_VERSIONS}; do
  py_bin="${CONDA_BACKEND_ROOT}/${py_version}/miniconda3/bin/python"
  if [[ ! -x "${py_bin}" ]]; then
    echo "skip python ${py_version}: ${py_bin} not found" >&2
    continue
  fi

  target_dir="${WHEELHOUSE_ROOT}/${py_version}"
  mkdir -p "${target_dir}"

  echo "downloading wheels for python ${py_version} -> ${target_dir}"
  "${py_bin}" -m pip download \
    --disable-pip-version-check \
    --dest "${target_dir}" \
    ${PACKAGES}
done

echo "wheelhouse ready under ${WHEELHOUSE_ROOT}"
