#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MINIFORGE_ROOT=${MINIFORGE_ROOT:-/sharedata/liyuchen/miniforge3}
ENV_PREFIX=${ENV_PREFIX:-${MINIFORGE_ROOT}/envs/swe-sandbox}
LITELLM_VERSION=${LITELLM_VERSION:-1.82.6}

CONDA_BIN="${MINIFORGE_ROOT}/bin/conda"
ACTIVATE_SCRIPT="${MINIFORGE_ROOT}/bin/activate"
if [[ ! -x "${CONDA_BIN}" ]]; then
  echo "conda binary not found: ${CONDA_BIN}" >&2
  exit 1
fi
if [[ ! -f "${ACTIVATE_SCRIPT}" ]]; then
  echo "activate script not found: ${ACTIVATE_SCRIPT}" >&2
  exit 1
fi

SAFE_RUNTIME_DEPS=(
  "litellm==${LITELLM_VERSION}"
  requests
  rich
  numpy
  pandas
  datasets
  ruamel.yaml
  tenacity
  unidiff
  simple-parsing
  rich-argparse
  flask
  flask-cors
  flask-socketio
  "pydantic>=2"
  pydantic-settings
  python-dotenv
  GitPython
  ghapi
  tabulate
  textual
  fastapi
  uvicorn
  pexpect
  bashlex
  python-multipart
  chardet
  pytest
  beautifulsoup4
  docker
  modal
  pre-commit
)

LOCAL_EDITABLES=(
  "${ROOT_DIR}/SWE-ReX"
  "${ROOT_DIR}/SWE-agent"
  "${ROOT_DIR}/SWE-bench"
  "${ROOT_DIR}/sandboxdev"
  "${ROOT_DIR}/R2E-Gym"
)

"${CONDA_BIN}" create -y -p "${ENV_PREFIX}" python=3.11 pip setuptools wheel
source "${ACTIVATE_SCRIPT}" "${ENV_PREFIX}"

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

python -m pip install --upgrade pip
python -m pip install "${SAFE_RUNTIME_DEPS[@]}"
python -m pip install --no-deps \
  -e "${LOCAL_EDITABLES[0]}" \
  -e "${LOCAL_EDITABLES[1]}" \
  -e "${LOCAL_EDITABLES[2]}" \
  -e "${LOCAL_EDITABLES[3]}" \
  -e "${LOCAL_EDITABLES[4]}"

python - <<'PY'
import litellm
import sweagent
import swebench
import swerex
import swesandbox

print("bootstrap-ok")
print("litellm", getattr(litellm, "__version__", "n/a"))
print("sweagent", getattr(sweagent, "__version__", "n/a"))
print("swebench", getattr(swebench, "__version__", "n/a"))
print("swerex", getattr(swerex, "__version__", "n/a"))
PY
