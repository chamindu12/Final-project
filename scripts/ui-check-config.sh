#!/usr/bin/env bash
# Check Compose and the real Nginx configuration before recreating services.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-https}"
image="${UI_NGINX_IMAGE:-netlab/nginx:1.30.6-secure}"
case "$mode" in
  https)
    cert_dir="${TLS_CERT_DIR:-$HOME/NetLab-certs}"
    test -f "$cert_dir/netlab.crt" && test -f "$cert_dir/netlab.key"
    bash scripts/https-compose.sh config -q
    docker run --rm --read-only --tmpfs /tmp \
      --cap-drop ALL --security-opt no-new-privileges:true \
      --group-add "${TLS_CERT_GID:-$(id -g)}" \
      -v "$cert_dir:/etc/netlab-tls:ro" \
      -v "$PWD/services/reverse-proxy/https.conf:/etc/nginx/conf.d/default.conf:ro" \
      -v "$PWD/services/reverse-proxy/error-pages:/etc/nginx/netlab-errors:ro" \
      --entrypoint nginx "$image" -t
    ;;
  http)
    docker compose config -q
    docker run --rm --read-only --tmpfs /tmp \
      --cap-drop ALL --security-opt no-new-privileges:true \
      -v "$PWD/services/reverse-proxy/default.conf:/etc/nginx/conf.d/default.conf:ro" \
      -v "$PWD/services/reverse-proxy/error-pages:/etc/nginx/netlab-errors:ro" \
      --entrypoint nginx "$image" -t
    ;;
  *) printf 'Usage: bash scripts/ui-check-config.sh [https|http]\n' >&2; exit 2 ;;
esac
printf 'PASS: Compose and Nginx configuration.\n'
