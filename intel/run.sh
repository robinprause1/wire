#!/bin/sh
set -e
cd "$(dirname "$0")/.."
if [ -n "$WIRE_PYTHON" ]; then
  exec "$WIRE_PYTHON" -m intel
fi
if [ -x intel/.venv/bin/python ]; then
  exec intel/.venv/bin/python -m intel
fi
python3 -m venv intel/.venv
intel/.venv/bin/pip install -r intel/requirements.txt
exec intel/.venv/bin/python -m intel
