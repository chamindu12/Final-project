# NetLab: Containerized Network Services Using Docker

NetLab combines Nginx web/proxy services, DNS, a Flask administration dashboard,
staff/guest portals, and cAdvisor/Prometheus/Grafana on isolated Docker bridge networks.

## Live container logs

The administrator dashboard follows Docker stdout/stderr with UTC timestamps,
container identity, pause/filter/auto-scroll controls and a bounded recent tail.
Staff/guest portals record structured actions and response status; gateway requests
can be correlated using `X-Request-ID`. DNS queries are also logged. See
[container logging and verification](docs/LOGGING.md) for deployment, retention,
privacy boundaries and the authenticated HTTPS live-log check.

## HTTPS-only operation

All browser-facing services use HTTPS. There is one deployment configuration,
`docker-compose.yml`; ordinary `docker compose up` cannot select a plaintext mode.

| Service | Browser URL |
|---|---|
| Landing | https://localhost:8080/ |
| Administration dashboard | https://localhost:8080/dashboard/ |
| Staff portal | https://localhost:8081/ |
| Guest portal | https://localhost:8082/ |
| Grafana | https://localhost:3000/ |
| Prometheus targets | https://localhost:9090/targets |

Ports are bound to IPv4 loopback. Use the Docker host's browser; a browser on a
separate Windows host/VM needs forwarding. Certificates cover localhost/127.0.0.1.
There are no separate HTTP ports and no HTTP redirect listener. HTTP requests on
the TLS ports are rejected. The former 8443/8444/8445 mappings are removed.

TLS terminates at nginx-proxy. Private backend HTTP, Prometheus scrapes and local
health probes remain inside Docker; application backends and monitoring containers
have no published web ports. DNS uses UDP 5353 and is not an HTTP service.

## First deployment

Requirements: Linux (Ubuntu 22.04+), Docker Engine/Compose v2, Python 3.9+,
OpenSSL and access to the Docker daemon. WSL2 is also suitable for local use.

```bash
cp .env.example .env
# Edit .env: configure unique accounts/passwords, a random PORTAL_SECRET_KEY,
# GRAFANA_ADMIN_PASSWORD and the Docker socket's actual DOCKER_GID.
python3 scripts/tls-setup.py
docker compose build
bash scripts/ui-check-config.sh
docker compose up -d --wait --wait-timeout 180
python3 scripts/https-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
python3 scripts/ui-smoke-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
python3 scripts/auth-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
bash scripts/scenario-check.sh
```

The TLS setup reuses a valid existing certificate/key, validates localhost identity,
refuses a partial/mismatched pair, and never prints credentials. It creates a localhost
self-signed certificate only when both files are absent. The private key stays outside
the repository. It saves a private .env backup before removing obsolete port/mode
settings and writing TLS_CERT_DIR/TLS_CERT_GID. Existing account/secret values remain.
Use TLS_CERT_DIR/TLS_CERT_GID environment overrides when needed. New lab certificates
last 365 days; renew explicitly before expiry. The check refuses a certificate with
less than one hour remaining.

Self-signed HTTPS needs explicit trust. CLI checks use --ca-file / --cacert. Browser
trust requires importing the public certificate into the relevant browser/OS trust
store; do not distribute the private key. See [docs/HTTPS.md](docs/HTTPS.md).

## Update an existing running installation

Apply the supplied guarded package from your current UI branch, then:

```bash
bash scripts/ui-deploy.sh
bash scripts/scenario-check.sh
```

The deploy script runs Python tests/lint, prepares TLS, validates Compose and real
Nginx syntax, builds dashboard/shared portal images, recreates the entire stack to
remove old direct HTTP bindings, and runs verified HTTPS/UI/authentication checks.
It retains named volumes. Do not use `docker compose down -v` on your installation.
Monitoring data, notes, uploaded files and sessions are stored in existing volumes.

`bash scripts/https-compose.sh ...` remains an alias for `docker compose ...` for
older commands. The separate HTTPS overlay and duplicate Nginx config were removed.
`ui-deploy.sh http` is rejected before deployment. The --https checker flag is accepted
for compatibility but HTTPS is always used, and a CA file is mandatory.

## Functional and evaluation checks

```bash
bash scripts/ui-tests.sh
bash scripts/preflight.sh
bash scripts/scenario-check.sh
```

The scenario checker validates certificate trust from host, staff-client and
isolated-test, and checks role/zone policies, DNS, direct-backend isolation,
download limits and rate limits. Only the public certificate is copied into client
tmpfs. Set NETLAB_CA_FILE if the certificate directory is customized.

See [docs/VALIDATION.md](docs/VALIDATION.md) for performance measurements and
[docs/UI_UPGRADE.md](docs/UI_UPGRADE.md) for browser acceptance checks. Historical
security/performance reports record earlier measured configurations; rerun evaluation
after this migration rather than treating those measurements as new evidence.

## Security boundaries

Browser sessions use Secure, HttpOnly and SameSite=Lax cookies, CSRF-protected
forms and server-side revocation. Application startup rejects insecure cookie mode.
Only the gateway may provide the trusted scheme header; backend ports stay private.
HTTPS complements role/zone policy and container hardening; it does not establish
those controls on its own. Monitoring gateway listeners retain the host/lab zone
restriction. Grafana retains its own authentication; Prometheus remains accessible
to the local admin network without a new application login.

The Docker socket is powerful. Keep the dashboard private to the management plane
and retain its self-protection. HTTPS is a local academic setup with a self-signed
certificate, not a claim of production certification or end-to-end backend TLS.
