# Local HTTPS mode

The base Compose file is the HTTP lab/CI mode. Opt into HTTPS with:

```bash
bash scripts/https-compose.sh config -q
bash scripts/https-compose.sh build dashboard staff-portal
bash scripts/https-compose.sh up -d --no-deps --wait --wait-timeout 180 dashboard staff-portal guest-portal nginx-proxy
```

Requires Docker Compose 2.24.4 or newer (`!override` replaces HTTP port bindings).
The full stack must already be running before using `--no-deps`.

Provide `netlab.crt` and `netlab.key` in `$HOME/NetLab-certs` or set `TLS_CERT_DIR`.
The certificate SAN must include localhost and 127.0.0.1. For this lab, use the
self-signed certificate explicitly as the trust anchor with `--cacert` / `--ca-file`.
Self-signed trust is not public CA validation and does not automatically make
Windows/Chrome trust the certificate. Never commit the key or copy it into an image.

The proxy remains non-root. Give its supplemental group read access to the key:

```bash
chgrp "$(id -g)" "$HOME/NetLab-certs" "$HOME/NetLab-certs/netlab.key"
chmod 750 "$HOME/NetLab-certs"
chmod 640 "$HOME/NetLab-certs/netlab.key"
```

The wrapper defaults `TLS_CERT_GID` to the host primary group ID. Use a private
host group when other users share this machine. The certificate mount is read-only.

Default entry points:

- https://localhost:8443/ (admin and landing page)
- https://localhost:8444/ (staff)
- https://localhost:8445/ (guest)

Set HTTPS_ADMIN_PORT / HTTPS_STAFF_PORT / HTTPS_GUEST_PORT to change these host
ports. The dashboard uses the same HTTPS portal port values for landing redirects.
The original host HTTP mappings 8080/8081/8082 are removed in this mode.
HTTP requests sent to a TLS listener are rejected; there is no HTTP redirect listener.

Validate identity and auth with:

```bash
curl --cacert "$HOME/NetLab-certs/netlab.crt" https://localhost:8443/healthz
python3 scripts/auth-check.py --https --ca-file "$HOME/NetLab-certs/netlab.crt" --rate-limit
```

The auth checker validates the certificate and hostname. It verifies Secure,
HttpOnly and SameSite=Lax flags, login/logout, cookie replay, self-protection,
and optional login throttling. It has no insecure certificate-bypass option.
Wait 60 seconds after its flood test before another admin login attempt.

Secure cookie mode trusts only one gateway's X-Forwarded-Proto header; the TLS
Nginx configuration overwrites that header. Forwarded host, port, prefix and client
IP middleware overrides are disabled. Application containers must remain private
behind the gateway. Flask health checks use internal HTTP and require no session.
The Nginx health probe skips certificate trust only for its local liveness check;
external validation must use the trusted certificate.

## Scope and limitations

TLS protects client-to-gateway traffic. Gateway-to-Flask traffic is still HTTP
inside the backend network. Grafana/Prometheus loopback endpoints are not converted
to HTTPS by this overlay. No HSTS policy or host trust-store installation is applied.
The HTTP scenario checker is for the base mode, not this TLS listener setup.
Unit tests retain coverage for HTTP behavior and the HTTPS cookie configuration;
run live zone checks separately for HTTPS before claiming full TLS policy validation.

## Return to HTTP lab mode

```bash
docker compose up -d --no-deps --wait --wait-timeout 180 dashboard staff-portal guest-portal nginx-proxy
```

Do not use `down -v`; existing notes, files, metrics and session volumes are preserved.
A Secure cookie from HTTPS is not sent on HTTP, so sign in again after switching mode.
Use the HTTPS wrapper consistently while the TLS deployment is active.
