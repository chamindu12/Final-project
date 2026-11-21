#!/usr/bin/env bash
# Run the complete Python regression suite without changing host Python packages.
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v node >/dev/null 2>&1 &&
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)'; then
  node --test tests/test_log_viewer.cjs
else
  echo "Node.js 20+ unavailable: log-viewer JavaScript tests run in GitHub CI."
fi
docker run --rm \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/w:ro" -w /w \
  python:3.12-slim-trixie \
  sh -ec 'if ! command -v openssl >/dev/null; then apt-get update -qq && apt-get install -y -qq --no-install-recommends openssl; fi; python -m pip install --quiet --no-cache-dir --target /tmp/test-tools -r dashboard/requirements.txt -r portal/requirements.txt pytest flake8 PyYAML && export PYTHONPATH=/tmp/test-tools && python -m flake8 dashboard/app.py dashboard/session_store.py dashboard/log_stream.py portal/app.py portal/session_store.py tests scripts/https-check.py scripts/tls-setup.py scripts/auth-check.py scripts/ui-smoke-check.py --max-line-length=100 && python -m pytest tests -q -p no:cacheprovider'
