#!/usr/bin/env bash
# Test, validate, rebuild and recreate the existing UI services in one explicit mode.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-https}"
case "$mode" in
  https) compose=(bash scripts/https-compose.sh) ;;
  http) compose=(docker compose) ;;
  *) printf 'Usage: bash scripts/ui-deploy.sh [https|http]\n' >&2; exit 2 ;;
esac
printf '1/5 Running the full Python regression suite...\n'
bash scripts/ui-tests.sh
printf '2/5 Checking Compose and Nginx before deployment...\n'
bash scripts/ui-check-config.sh "$mode"
printf '3/5 Building the shared portal image...\n'
"${compose[@]}" build staff-portal
printf '4/5 Recreating dashboard and UI services with existing named volumes...\n'
# The dashboard owns portal redirects; reapply the selected profile's scheme/ports.
"${compose[@]}" up -d --no-deps --no-build --pull never \
  --force-recreate --wait --wait-timeout 180 \
  dashboard staff-portal guest-portal nginx-web nginx-proxy
printf '5/5 Checking the live UI endpoints...\n'
if [[ "$mode" == https ]]; then
  python3 scripts/ui-smoke-check.py --https \
    --ca-file "${TLS_CERT_DIR:-$HOME/NetLab-certs}/netlab.crt"
else
  python3 scripts/ui-smoke-check.py
fi
printf 'UI deployment checks passed. Now run auth-check.py and the documented browser checks.\n'
