#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Portfolio Ledger - one-shot local bootstrap.
#
# Creates ./venv (if missing), installs every dependency from
# requirements.txt and runs the test suite to verify the install.
#
# Usage:
#     ./setup.sh                # bootstrap from scratch
#     source venv/bin/activate  # then activate the venv in your shell
# ---------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="venv"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Error: ${PYTHON_BIN} not found on PATH." >&2
    echo "Install Python 3.12+ or set PYTHON_BIN to a different interpreter." >&2
    exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Creating virtual environment in ./${VENV_DIR}/ ..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

echo "Installing dependencies into ./${VENV_DIR}/ ..."
"./${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
"./${VENV_DIR}/bin/pip" install -r requirements.txt
"./${VENV_DIR}/bin/pip" install -e .

echo
echo "Running test suite ..."
"./${VENV_DIR}/bin/python" -m pytest tests/ -q

cat <<'EOF'

Setup complete. Activate the venv with:

    source venv/bin/activate

Then run (after: source venv/bin/activate):

    pl process
    pl generate-reports

Same commands work as: python -m app.main …
From the repo root you can also use: make process  or  make reports
(uses ./venv/bin/python; no need to activate the venv for make).

EOF
