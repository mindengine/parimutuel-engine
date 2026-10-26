#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
"$PY" -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
[ -f data/engine_v2.db ] || python -m pe.v2 rebuild
pytest -q
echo
echo "Ready. Activate with:  source .venv/bin/activate"
