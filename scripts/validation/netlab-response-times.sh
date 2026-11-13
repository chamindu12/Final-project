#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

mkdir -p security-reports/performance
stamp=$(date +%Y%m%d-%H%M%S)
report="security-reports/performance/https-response-times-${stamp}.csv"
cert="${NETLAB_CA_FILE:-$HOME/NetLab-certs/netlab.crt}"

printf 'endpoint,http_code,connect_s,tls_s,ttfb_s,total_s\n' > "$report"

for port in 8080 8081 8082; do
  endpoint="https://localhost:${port}/login"
  if [ "$port" = 8080 ]; then
    endpoint="https://localhost:${port}/"
  fi

  echo "Measuring: $endpoint"

  for warmup in 1 2 3; do
    curl --cacert "$cert" --fail --silent --show-error \
      --connect-timeout 5 --max-time 15 \
      -o /dev/null "$endpoint"
    sleep 2
  done

  for sample in {1..20}; do
    curl --cacert "$cert" --fail --silent --show-error \
      --connect-timeout 5 --max-time 15 \
      -o /dev/null \
      -w "$endpoint,%{http_code},%{time_connect},%{time_appconnect},%{time_starttransfer},%{time_total}\n" \
      "$endpoint" >> "$report"
    sleep 2
  done
done

echo "Completed: $report"
