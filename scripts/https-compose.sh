#!/usr/bin/env bash
# Explicit opt-in HTTPS deployment; base Compose remains the HTTP lab/CI mode.
set -euo pipefail
cd "$(dirname "$0")/.."
export TLS_CERT_DIR="${TLS_CERT_DIR:-$HOME/NetLab-certs}"
export TLS_CERT_GID="${TLS_CERT_GID:-$(id -g)}"
exec docker compose -f docker-compose.yml -f docker-compose.https.yml "$@"
