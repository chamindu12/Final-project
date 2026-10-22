#!/usr/bin/env bash
# Pre-flight check. Run from the project root BEFORE "docker compose up":
#   bash scripts/preflight.sh
cd "$(dirname "$0")/.." || exit 1
ok=0; bad=0; warn=0
pass() { ok=$((ok+1)); }
fail() { bad=$((bad+1)); printf 'FAIL  %s\n' "$1"; }
note() { warn=$((warn+1)); printf 'WARN  %s\n' "$1"; }
val() { grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2-; }

echo "== files"
missing=""
for f in docker-compose.yml .env .env.example .gitignore \
  portal/app.py portal/Dockerfile portal/requirements.txt \
  portal/templates/base.html portal/templates/login.html portal/templates/home.html \
  dashboard/app.py dashboard/Dockerfile dashboard/requirements.txt \
  dashboard/templates/index.html dashboard/templates/login.html \
  dashboard/static/js/app.js dashboard/static/css/app.css \
  dashboard/static/vendor/chart.umd.js dashboard/static/vendor/bootstrap.min.css \
  services/client/Dockerfile services/dns/Dockerfile services/dns/dnsmasq.conf \
  services/reverse-proxy/default.conf services/nginx-web/html/index.html \
  monitoring/prometheus/prometheus.yml \
  monitoring/grafana/provisioning/datasources/prometheus.yml \
  monitoring/grafana/provisioning/dashboards/dashboards.yml \
  monitoring/grafana/dashboards/container-network-services.json \
  scripts/scenario-check.sh tests/test_portal.py; do
  [ -f "$f" ] && pass || missing="$missing $f"
done
[ -z "$missing" ] && echo "OK    all expected files are present" || fail "missing:$missing (names are case-sensitive on Linux)"

echo "== line endings (Windows CRLF breaks scripts and configs)"
crlf=$(grep -lU $'\r' docker-compose.yml .env .env.example scripts/*.sh services/*/Dockerfile \
  portal/Dockerfile dashboard/Dockerfile services/reverse-proxy/default.conf services/dns/dnsmasq.conf 2>/dev/null)
[ -z "$crlf" ] && echo "OK    no CRLF line endings" || fail "CRLF line endings in: $crlf  (fix: sed -i 's/\\r\$//' <file>)"

echo "== settings"
for file in .env .env.example; do
  for k in GRAFANA_ADMIN_PASSWORD ADMIN_USERS STAFF_USERS GUEST_USERS PORTAL_SECRET_KEY; do
    [ -n "$(val "$file" "$k")" ] && pass || fail "$k is missing or empty in $file"
  done
done
users_re='^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+:[A-Za-z0-9_-]+)*$'
for file in .env .env.example; do
  for k in ADMIN_USERS STAFF_USERS GUEST_USERS; do
    v=$(val "$file" "$k")
    [ -z "$v" ] || echo "$v" | grep -Eq "$users_re" || fail "$k in $file must look like name:password,name:password (letters, digits, - and _ only)"
  done
done
for file in .env .env.example; do
  [ "$(val "$file" PORTAL_SECRET_KEY | wc -c)" -gt 16 ] || fail "PORTAL_SECRET_KEY in $file needs at least 16 characters"
done
val .env ADMIN_USERS | grep -q ChangeMe && note "ADMIN_USERS in .env still uses the placeholder password"
val .env STAFF_USERS | grep -q ChangeMe && note "STAFF_USERS in .env still uses the placeholder passwords"
val .env GUEST_USERS | grep -q ChangeMe && note "GUEST_USERS in .env still uses the placeholder password"
val .env PORTAL_SECRET_KEY | grep -q '^change_me' && note "PORTAL_SECRET_KEY in .env is still the placeholder (use: openssl rand -hex 32)"
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  git check-ignore -q .env && echo "OK    .env is ignored by git" || fail ".env is NOT ignored by git (add it to .gitignore)"
  git ls-files --error-unmatch .env >/dev/null 2>&1 && fail ".env is already tracked by git (git rm --cached .env)"
fi
grep -q 'portal.lab.local' services/dns/dnsmasq.conf 2>/dev/null || fail "services/dns/dnsmasq.conf has no portal.lab.local record (see dnsmasq.addition.txt)"

echo "== docker"
if ! command -v docker >/dev/null 2>&1; then
  note "docker not found here, so compose and port checks are skipped"
else
  docker info >/dev/null 2>&1 && pass || fail "cannot talk to the Docker daemon (is it running? are you in the docker group?)"
  if docker compose config -q >/dev/null 2>/tmp/preflight.err; then echo "OK    docker compose config is valid"
  else fail "docker compose config: $(head -c 300 /tmp/preflight.err)"; fi
  gid=$(getent group docker | cut -d: -f3); envgid=$(val .env DOCKER_GID)
  [ -n "$gid" ] && [ "$gid" != "$envgid" ] && note "DOCKER_GID in .env is '$envgid' but the docker group id is '$gid'"
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nginx-proxy; then
    echo "OK    stack already running, so port checks are skipped"
  else
    for p in "$(val .env PROXY_PORT)" "$(val .env STAFF_PORT)" "$(val .env GUEST_PORT)" 3000 9090 5353; do
      [ -z "$p" ] && continue
      (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && note "port $p is already in use on this machine (change it in .env or stop the other program)"
    done
    for p in 8080 8081 8082; do   # defaults, when .env does not override them
      case "$p" in 8080) k=PROXY_PORT;; 8081) k=STAFF_PORT;; *) k=GUEST_PORT;; esac
      [ -z "$(val .env $k)" ] && (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && note "port $p is already in use on this machine (set $k in .env)"
    done
  fi
fi

echo
echo "ok=$ok warnings=$warn failed=$bad"
[ "$bad" -eq 0 ]
