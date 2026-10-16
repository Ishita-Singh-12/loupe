#!/bin/sh
# One-command Open Deep Research launcher for loupe (macOS/Linux).
set -e
cd "$(dirname "$0")/.."
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
$PY -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || {
  echo "Python 3.10+ is required."; exit 1; }
if [ ! -x odr/.venv/bin/python ]; then
  echo "First run: creating virtual environment and installing dependencies..."
  $PY -m venv odr/.venv
  odr/.venv/bin/python -m pip install --quiet --upgrade pip
  odr/.venv/bin/python -m pip install --quiet -e "sdk[langchain]" -r odr/requirements.txt
fi
exec odr/.venv/bin/python odr/launcher.py "$@"
