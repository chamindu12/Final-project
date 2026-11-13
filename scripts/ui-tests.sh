#!/usr/bin/env bash
# Run the complete Python regression suite without changing host Python packages.
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/w:ro" -w /w \
  python:3.12-slim-trixie \
  sh -ec 'python -m pip install --quiet --no-cache-dir --target /tmp/test-tools -r dashboard/requirements.txt -r portal/requirements.txt pytest flake8 && export PYTHONPATH=/tmp/test-tools && python -m flake8 dashboard/app.py dashboard/session_store.py portal/app.py portal/session_store.py tests scripts/auth-check.py scripts/ui-smoke-check.py --max-line-length=100 && python -m pytest tests -q -p no:cacheprovider'
