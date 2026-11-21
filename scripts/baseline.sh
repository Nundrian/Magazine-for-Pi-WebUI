#!/bin/sh
set -eu
# Maintainer-only provenance reconstruction, never point at the authoritative original.
: "${MAGAZINE_ISOLATED_REFERENCE:?Set an isolated extracted Beta 5 directory}"
: "${MAGAZINE_BASELINE_VENV:?Set an isolated absolute venv path}"
: "${MAGAZINE_BASELINE_EVIDENCE:?Set an absolute evidence directory}"
cd "$MAGAZINE_ISOLATED_REFERENCE"
mkdir -p "$MAGAZINE_BASELINE_EVIDENCE"
python3 -m venv "$MAGAZINE_BASELINE_VENV"
"$MAGAZINE_BASELINE_VENV/bin/pip" install -q -r requirements-test.txt -e .
"$MAGAZINE_BASELINE_VENV/bin/python" -m pytest tests --junitxml="$MAGAZINE_BASELINE_EVIDENCE/baseline-core.xml" -q
PYTHONPATH=integrations/open_webui/function "$MAGAZINE_BASELINE_VENV/bin/python" -m pytest integrations/open_webui/function/tests --junitxml="$MAGAZINE_BASELINE_EVIDENCE/baseline-function.xml" -q
