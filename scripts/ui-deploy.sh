#!/usr/bin/env bash
# Upgrade a running stack; recreate all services to remove old plaintext bindings.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != https ) ]]; then
  printf 'Usage: bash scripts/ui-deploy.sh [https] (HTTPS only)\n' >&2; exit 2
fi
printf '1/6 Running the full Python regression suite...\n'
bash scripts/ui-tests.sh
printf '2/6 Preparing and validating local TLS...\n'
python3 scripts/tls-setup.py
printf '3/6 Checking HTTPS Compose and Nginx before deployment...\n'
bash scripts/ui-check-config.sh
printf '4/6 Building dashboard and shared portal image...\n'
docker compose build dashboard staff-portal
printf '5/6 Recreating the complete stack, retaining existing named volumes...\n'
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180
printf '6/6 Checking live HTTPS endpoints and authentication...\n'
cert_dir=$(docker compose config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); print(next(v["source"] for v in c["services"]["nginx-proxy"]["volumes"] if v["target"]=="/etc/netlab-tls"))')
python3 scripts/https-check.py --ca-file "$cert_dir/netlab.crt"
python3 scripts/ui-smoke-check.py --ca-file "$cert_dir/netlab.crt"
python3 scripts/auth-check.py --ca-file "$cert_dir/netlab.crt"
printf 'HTTPS deployment checks passed. Run scenario-check.sh and the browser checks next.\n'
