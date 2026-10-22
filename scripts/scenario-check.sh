#!/usr/bin/env bash
# NetLab scenario check: proves the admin / staff / guest policy from each position.
#   Admin  = your machine (browser or curl on localhost)
#   Staff  = the staff-client container (staff_net)
#   Guest  = the isolated-test container (isolated_net)
# Usage (from the project root, stack running):  bash scripts/scenario-check.sh
# Optional: SKIP_ICMP=1 (no ping checks)  SKIP_DNS=1 (no dig checks)
set -u
cd "$(dirname "$0")/.." || exit 1

DOCKER=${DOCKER:-docker}
ENV_FILE=${ENV_FILE:-.env}
P_ADMIN=${P_ADMIN:-8080}; P_STAFF=${P_STAFF:-8081}; P_GUEST=${P_GUEST:-8082}   # ports inside the network
env_val() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-; }
HP_ADMIN=$(env_val PROXY_PORT); HP_STAFF=$(env_val STAFF_PORT); HP_GUEST=$(env_val GUEST_PORT)
ADMIN_URL=${ADMIN_URL:-http://localhost:${HP_ADMIN:-8080}}                      # ports on your machine
STAFF_HOST_URL=${STAFF_HOST_URL:-http://localhost:${HP_STAFF:-8081}}
GUEST_HOST_URL=${GUEST_HOST_URL:-http://localhost:${HP_GUEST:-8082}}
PROXY_STAFF=${PROXY_STAFF:-172.22.0.11}   # gateway address inside staff_net
PROXY_GUEST=${PROXY_GUEST:-172.21.0.11}   # gateway address inside isolated_net
STAFF_IP=${STAFF_IP:-172.22.0.10}
GUEST_IP=${GUEST_IP:-172.21.0.10}
BACKEND=${BACKEND:-172.20.0.10}           # nginx-web, lab_net only
DASHBOARD_IP=${DASHBOARD_IP:-172.20.0.20}
DNS_STAFF=${DNS_STAFF:-172.22.0.53}
DNS_LAB=${DNS_LAB:-172.20.0.53}

first_pair() { echo "$1" | cut -d, -f1; }
SPAIR=$(first_pair "$(env_val STAFF_USERS)"); STAFF_USER=${SPAIR%%:*}; STAFF_PASS=${SPAIR#*:}
GPAIR=$(first_pair "$(env_val GUEST_USERS)"); GUEST_USER=${GPAIR%%:*}; GUEST_PASS=${GPAIR#*:}
APAIR=$(first_pair "$(env_val ADMIN_USERS)"); ADMIN_USER=${APAIR%%:*}; ADMIN_PASS=${APAIR#*:}
if [ -z "$STAFF_USER" ] || [ -z "$GUEST_USER" ] || [ -z "$ADMIN_USER" ]; then
  echo "Set ADMIN_USERS, STAFF_USERS and GUEST_USERS (name:password) in $ENV_FILE first."; exit 2
fi

pass=0; fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then pass=$((pass+1)); printf "PASS  %-66s %s\n" "$1" "$3"
  else fail=$((fail+1)); printf "FAIL  %-66s expected=%s got=%s\n" "$1" "$2" "$3"; fi
}
yesno() { [ "$1" -ne 0 ] && echo yes || echo no; }   # exit status -> "failed?" yes/no

# ---- helpers that run INSIDE a client container -----------------------------
inc() { local c=$1; shift; "$DOCKER" exec "$c" "$@"; }
code_in() { inc "$1" curl -s -o /dev/null -m 5 -w '%{http_code}' "${@:2}"; }
login_in() { # container base-url user password -> HTTP status of the login POST
  "$DOCKER" exec -i -e BASE="$2" -e U="$3" -e P="$4" "$1" sh -s <<'EOS'
rm -f /tmp/jar
tok=$(curl -s -m 5 -c /tmp/jar "$BASE/login" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p')
curl -s -o /dev/null -m 5 -w '%{http_code}' -b /tmp/jar -c /tmp/jar \
  --data-urlencode "csrf_token=$tok" --data-urlencode "username=$U" \
  --data-urlencode "password=$P" "$BASE/login"
EOS
}
whoami_in() { inc "$1" curl -s -m 5 -b /tmp/jar "$2/api/whoami" | tr -d ' \n'; }
json_field() { echo "$1" | grep -o "\"$2\":\"[^\"]*\"" | cut -d'"' -f4; }
unreachable_in() { inc "$1" curl -s -o /dev/null -m 3 "$2" >/dev/null 2>&1; yesno $?; }

# ---- helper that runs on the host (the admin's machine) ---------------------
login_host() { # base-url user password jar
  local tok
  rm -f "$4"
  tok=$(curl -s -m 5 -c "$4" "$1/login" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p')
  curl -s -o /dev/null -m 5 -w '%{http_code}' -b "$4" -c "$4" --data-urlencode "csrf_token=$tok" \
    --data-urlencode "username=$2" --data-urlencode "password=$3" "$1/login"
}

JAR=$(mktemp); trap 'rm -f "$JAR" "$JAR.2" "$JAR.3"' EXIT

echo "== ADMIN (host machine, management plane)"
check "landing page is the front door"              200 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$ADMIN_URL/")"
check "landing page offers the three sign-ins"      3 "$(curl -s -m 5 "$ADMIN_URL/" | grep -c 'sign in</a>')"
check "dashboard data is closed without a login"    401 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$ADMIN_URL/dashboard/api/containers")"
check "dashboard page sends visitors to its login"  302 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$ADMIN_URL/dashboard/")"
check "admin account works on the dashboard"        302 "$(login_host "$ADMIN_URL/dashboard" "$ADMIN_USER" "$ADMIN_PASS" "$JAR.3")"
check "dashboard opens after the admin login"       200 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' -b "$JAR.3" "$ADMIN_URL/dashboard/")"
check "staff account is rejected by the dashboard"  401 "$(login_host "$ADMIN_URL/dashboard" "$STAFF_USER" "$STAFF_PASS" "$JAR.2")"
check "admin account is rejected by the staff portal" 401 "$(login_host "$STAFF_HOST_URL" "$ADMIN_USER" "$ADMIN_PASS" "$JAR.2")"
check "staff portal login page reachable"           200 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$STAFF_HOST_URL/login")"
check "guest portal login page reachable"           200 "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$GUEST_HOST_URL/login")"
check "staff account works on the staff portal"     302 "$(login_host "$STAFF_HOST_URL" "$STAFF_USER" "$STAFF_PASS" "$JAR")"
check "staff account is rejected by the guest portal" 401 "$(login_host "$GUEST_HOST_URL" "$STAFF_USER" "$STAFF_PASS" "$JAR.2")"
check "guest account is rejected by the staff portal" 401 "$(login_host "$STAFF_HOST_URL" "$GUEST_USER" "$GUEST_PASS" "$JAR.2")"
sv=$(grep staff_session "$JAR" | awk '{print $7}')
check "staff session cookie is useless on the guest portal" 401 \
  "$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H "Cookie: guest_session=$sv" "$GUEST_HOST_URL/api/whoami")"

echo "== STAFF PC (staff-client on staff_net)"
SB="http://$PROXY_STAFF:$P_STAFF"
if [ -z "${SKIP_DNS:-}" ]; then
  check "internal DNS answers for staff (portal.lab.local)" "$PROXY_STAFF" \
    "$(inc staff-client dig +short +time=2 +tries=1 @"$DNS_STAFF" -p 5353 portal.lab.local)"
fi
check "staff portal login page via the gateway"     200 "$(code_in staff-client "$SB/login")"
check "staff login with a staff account"            302 "$(login_in staff-client "$SB" "$STAFF_USER" "$STAFF_PASS")"
W=$(whoami_in staff-client "$SB")
check "gateway reports role=staff"                  staff "$(json_field "$W" role)"
check "gateway reports zone=staff"                  staff "$(json_field "$W" zone)"
check "gateway sees the real client address"        "$STAFF_IP" "$(json_field "$W" client_ip)"
check "staff wrong password is refused"             401 "$(login_in staff-client "$SB" "$STAFF_USER" "not-the-password")"
login_in staff-client "$SB" "$STAFF_USER" "$STAFF_PASS" >/dev/null
check "staff 10 MB speed test"                      10485760 "$(inc staff-client sh -c "curl -s -m 30 -b /tmp/jar '$SB/speedtest?mb=10' | wc -c")"
check "staff blocked from the admin plane (:$P_ADMIN)" 403 "$(code_in staff-client "http://$PROXY_STAFF:$P_ADMIN/")"
check "staff blocked from the guest portal (:$P_GUEST)" 403 "$(code_in staff-client "http://$PROXY_STAFF:$P_GUEST/login")"
check "staff cannot reach the web server directly"  yes "$(unreachable_in staff-client "http://$BACKEND:8080/")"
check "staff cannot reach the dashboard container"  yes "$(unreachable_in staff-client "http://$DASHBOARD_IP:5000/")"
check "staff cannot reach the guest network gateway" yes "$(unreachable_in staff-client "http://$PROXY_GUEST:$P_GUEST/login")"
if [ -z "${SKIP_ICMP:-}" ]; then
  if inc staff-client ping -c 1 -W 2 "$PROXY_STAFF" >/dev/null 2>&1; then
    inc staff-client ping -c 1 -W 2 "$BACKEND" >/dev/null 2>&1
    check "staff ping to the back end fails (no route)" yes "$(yesno $?)"
  else
    echo "SKIP  ping is not permitted in staff-client (set SKIP_ICMP=1 to hide this)"
  fi
fi

echo "== GUEST PC (isolated-test on isolated_net)"
GB="http://$PROXY_GUEST:$P_GUEST"
gpause() { sleep 0.6; }   # stay under the guest rate limit (2 requests per second)
gpause; check "guest portal login page via the gateway" 200 "$(code_in isolated-test "$GB/login")"
gpause; check "guest login with a guest account"     302 "$(login_in isolated-test "$GB" "$GUEST_USER" "$GUEST_PASS")"
gpause; W=$(whoami_in isolated-test "$GB")
check "gateway reports role=guest"                  guest "$(json_field "$W" role)"
check "gateway reports zone=guest"                  guest "$(json_field "$W" zone)"
check "gateway sees the real client address"        "$GUEST_IP" "$(json_field "$W" client_ip)"
gpause; check "staff account is refused on the guest portal" 401 "$(login_in isolated-test "$GB" "$STAFF_USER" "$STAFF_PASS")"
gpause; login_in isolated-test "$GB" "$GUEST_USER" "$GUEST_PASS" >/dev/null
gpause; check "guest speed test above 1 MB is refused" 403 "$(code_in isolated-test -b /tmp/jar "$GB/speedtest?mb=5")"
gpause; check "guest 1 MB speed test"               1048576 "$(inc isolated-test sh -c "curl -s -m 30 -b /tmp/jar '$GB/speedtest?mb=1' | wc -c")"
gpause; check "guest blocked from the staff portal (:$P_STAFF)" 403 "$(code_in isolated-test "http://$PROXY_GUEST:$P_STAFF/login")"
gpause; check "guest blocked from the admin plane (:$P_ADMIN)"  403 "$(code_in isolated-test "http://$PROXY_GUEST:$P_ADMIN/")"
check "guest cannot reach the web server directly"  yes "$(unreachable_in isolated-test "http://$BACKEND:8080/")"
check "guest cannot reach the staff network gateway" yes "$(unreachable_in isolated-test "http://$PROXY_STAFF:$P_STAFF/login")"
if [ -z "${SKIP_DNS:-}" ]; then
  inc isolated-test dig +short +time=2 +tries=1 @"$DNS_LAB" -p 5353 web.lab.local >/dev/null 2>&1
  check "guest gets no answer from the internal DNS (timeout)" yes "$(yesno $?)"
fi
if [ -z "${SKIP_ICMP:-}" ]; then
  if inc isolated-test ping -c 1 -W 2 "$PROXY_GUEST" >/dev/null 2>&1; then
    inc isolated-test ping -c 1 -W 2 "$BACKEND" >/dev/null 2>&1
    check "guest ping to the back end fails (no route)" yes "$(yesno $?)"
  else
    echo "SKIP  ping is not permitted in isolated-test (set SKIP_ICMP=1 to hide this)"
  fi
fi
sleep 1
flood=$("$DOCKER" exec -i -e URL="$GB/login" isolated-test sh -s <<'EOS'
for i in $(seq 1 30); do curl -s -o /dev/null -m 5 -w '%{http_code}\n' "$URL"; done | grep -c 429
EOS
)
check "guest request flood is rate limited (HTTP 429)" yes "$([ "${flood:-0}" -gt 5 ] && echo yes || echo no)"

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]