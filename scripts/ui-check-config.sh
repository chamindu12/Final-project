#!/usr/bin/env bash
# Validate the sole HTTPS configuration before touching running containers.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != https ) ]]; then
  printf 'Usage: bash scripts/ui-check-config.sh [https] (HTTPS only)\n' >&2; exit 2
fi
python3 scripts/tls-setup.py --check
# Read interpolated Compose values without shell-sourcing credentials from .env.
cert_dir=$(docker compose config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); print(next(v["source"] for v in c["services"]["nginx-proxy"]["volumes"] if v["target"]=="/etc/netlab-tls"))')
cert_gid=$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["nginx-proxy"]["group_add"][0])')
docker run --rm --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --group-add "$cert_gid" \
  -v "$cert_dir:/etc/netlab-tls:ro" \
  -v "$PWD/services/reverse-proxy/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$PWD/services/reverse-proxy/error-pages:/etc/nginx/netlab-errors:ro" \
  --entrypoint nginx "${UI_NGINX_IMAGE:-netlab/nginx:1.30.6-secure}" -t
printf 'PASS: HTTPS Compose and Nginx configuration.\n'
