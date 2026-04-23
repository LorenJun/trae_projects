#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "[openclaw] project root: ${ROOT_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[openclaw] python3 not found"
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "[openclaw] creating virtualenv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "[openclaw] upgrading pip"
python3 -m pip install --upgrade pip

echo "[openclaw] installing python dependencies"
python3 -m pip install -r "${ROOT_DIR}/requirements-openclaw.txt"

echo "[openclaw] installing playwright chromium"
python3 -m playwright install chromium

if [ -f "${ROOT_DIR}/package.json" ] && command -v npm >/dev/null 2>&1; then
  echo "[openclaw] installing npm dependencies"
  (cd "${ROOT_DIR}" && npm install)
else
  echo "[openclaw] skip npm install (package.json or npm missing)"
fi

echo "[openclaw] environment setup complete"
echo "[openclaw] next step:"
echo "  cd ${ROOT_DIR}/europe_leagues && python3 prediction_system.py health-check --json"
