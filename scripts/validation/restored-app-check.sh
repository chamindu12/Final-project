#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
volume="${1:?Usage: bash scripts/validation/restored-app-check.sh RESTORE_VOLUME}"
[[ "$volume" == netlab_staff_restore_* ]] || {
  echo 'Use an isolated netlab_staff_restore_* volume' >&2; exit 1;
}
docker volume inspect "$volume" >/dev/null
mkdir -p security-reports/performance
stamp=$(date +%Y%m%d-%H%M%S)-$$
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=volume,src=$volume,dst=/data,readonly" \
  --entrypoint python "${NETLAB_PORTAL_IMAGE:-netlab/portal:1.0}" - \
  < scripts/validation/netlab-restored-app-check.py \
  2>&1 | tee "security-reports/performance/restored-app-check-${stamp}.txt"
