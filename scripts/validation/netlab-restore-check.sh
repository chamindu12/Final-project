#!/usr/bin/env bash
# Consistent staff data backup; restore only into a fresh, separate volume.
set -euo pipefail
cd "$(dirname "$0")/../.."
stamp=$(date +%Y%m%d-%H%M%S)-$$
backup="${NETLAB_BACKUP_ROOT:-$HOME/NetLab-backups}/staff-restore-${stamp}"
volume="${1:-netlab_staff_restore_${stamp}}"
image="${NETLAB_PORTAL_IMAGE:-netlab/portal:1.0}"

source_volume=$(docker inspect staff-portal --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}{{end}}')
[[ -n "$source_volume" ]] || { echo 'No staff data volume found' >&2; exit 1; }
[[ "$volume" == netlab_staff_restore_* && "$volume" != "$source_volume" ]] || {
  echo 'Destination must be a new netlab_staff_restore_* volume' >&2; exit 1;
}
if docker volume inspect "$volume" >/dev/null 2>&1; then
  echo 'Destination volume already exists; refusing to overwrite it' >&2; exit 1
fi
docker volume inspect "$source_volume" >/dev/null
docker image inspect "$image" >/dev/null
[[ $(docker inspect staff-portal --format '{{.State.Running}}') == true ]] || {
  echo 'Staff portal must initially be running' >&2; exit 1;
}
mkdir -p "$backup"
# Install recovery trap before stopping; preserve the original failure status.
resume() {
  original=$?
  trap - EXIT
  if ! docker compose start staff-portal; then
    echo 'Staff portal restart failed; start it manually' >&2
    exit 1
  fi
  exit "$original"
}
trap resume EXIT
docker compose stop staff-portal

docker run --rm --network none --user 0 \
  --mount "type=volume,src=$source_volume,dst=/data,readonly" \
  --entrypoint tar "$image" -C /data -cpf - . > "$backup/staff-data.tar"

docker run --rm --network none --user 0 \
  --mount "type=volume,src=$source_volume,dst=/data,readonly" \
  --entrypoint sh "$image" \
  -c 'cd /data && find . -type f -exec sha256sum {} \; | LC_ALL=C sort' \
  > "$backup/source-sha256.txt"

docker compose start staff-portal
trap - EXIT
# The destination is deliberately retained for application-level validation.
docker volume create "$volume" > /dev/null
docker run --rm -i --network none --user 0 \
  --mount "type=volume,src=$volume,dst=/restore" \
  --entrypoint tar "$image" -C /restore -xpf - < "$backup/staff-data.tar"
docker run --rm --network none --user 0 \
  --mount "type=volume,src=$volume,dst=/data,readonly" \
  --entrypoint sh "$image" \
  -c 'cd /data && find . -type f -exec sha256sum {} \; | LC_ALL=C sort' \
  > "$backup/restored-sha256.txt"
test -s "$backup/source-sha256.txt"
diff -u "$backup/source-sha256.txt" "$backup/restored-sha256.txt"
printf 'PASS: restored regular-file paths and SHA-256 hashes match.\n'
printf 'Backup: %s\nRestore volume: %s\n' "$backup" "$volume"
printf 'Files checked: '
wc -l < "$backup/source-sha256.txt"
