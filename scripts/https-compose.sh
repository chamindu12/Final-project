#!/usr/bin/env bash
# Compatibility command: the base Compose file is now HTTPS-only.
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose "$@"
