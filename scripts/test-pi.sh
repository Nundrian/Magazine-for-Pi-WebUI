#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p evidence/pi-tests
export PYTHONPATH="$PWD/magazine-pi${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$PWD/evidence/pi-tests"
"${MAGAZINE_TEST_PYTHON:-python3}" -m pytest tests --basetemp="$PWD/evidence/pi-tests/current" --junitxml="$PWD/evidence/pi-final.xml" -q -s
