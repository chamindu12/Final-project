# HTTPS-only configuration

The only deployment file is docker-compose.yml. Admin, staff and guest use
https://localhost:8080, :8081 and :8082 respectively. Grafana :3000 and Prometheus
:9090 are also served through TLS gateway listeners. There is no plaintext browser
mode, no duplicate port-variable family and no separate overlay.

Run `python3 scripts/tls-setup.py` before first deployment. It uses
`$HOME/NetLab-certs` by default and persists the absolute directory/group in .env.
It backs up .env privately, removes obsolete port/mode overrides, preserves accounts,
and refuses to overwrite incomplete or invalid existing TLS files. Key mode is 640;
certificate-directory mode is 750. The gateway remains non-root and mounts TLS read-only.

```bash
bash scripts/ui-deploy.sh
bash scripts/scenario-check.sh
```

The current self-signed certificate is valid for the new ports if its identity and
expiry are valid: TLS certificates identify hostnames/IPs, not TCP port numbers.
Browser trust and protocol selection are separate: type the full https:// URL.
Use the Docker host's browser, or SSH/VM/WSL forwarding for a different machine.
To forward a remote Linux host to Windows PowerShell, replace LINUX_IP:

```powershell
ssh -N -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 -L 8082:127.0.0.1:8082 -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 chamindu_linux@LINUX_IP
```

## Browser certificate trust

Verify the public certificate fingerprint on the Linux host with
`openssl x509 -in "$HOME/NetLab-certs/netlab.crt" -noout -fingerprint -sha256`.
For a Windows browser, transfer only netlab.crt from your own host, verify its
fingerprint, and import it into your current user's Trusted Root Certification
Authorities using Windows certificate management (certmgr.msc). Restart the browser.
This trusts that certificate in the chosen account. Remove it when the lab is retired.
For Linux Chrome/Firefox, use the browser's certificate/Authorities manager to import
the public lab certificate for website trust; the exact UI depends on browser/OS.
The private netlab.key must stay on the Docker host. CLI checks explicitly trust
netlab.crt with --ca-file/--cacert rather than disabling verification.

## Checks and cookies

```bash
curl --cacert "$HOME/NetLab-certs/netlab.crt" https://localhost:8080/healthz
python3 scripts/https-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
python3 scripts/auth-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
```

Inspect cookies with DevTools > Application > Storage > Cookies after login.
Check Secure, HttpOnly and SameSite=Lax, then test logout and protected API denial.
HttpOnly blocks page JavaScript access; it does not hide cookies from DevTools.
The browser can inspect decrypted requests/responses; TLS protects traffic in transit.

TLS terminates at the gateway; private Docker HTTP upstreams and scrape/health
endpoints remain. No HTTP web endpoint is published from a backend container.
The gateway's local liveness probe uses --no-check-certificate only inside that
container. External acceptance checks verify certificate identity and trust.

For short session expiry checks, change SESSION_TTL_SECONDS temporarily, recreate
dashboard/staff-portal/guest-portal, run auth-check.py --ca-file ... --expiry-seconds 15,
then restore the normal TTL and recreate those three services. Do not run a flood
rate-limit check immediately before interactive login; allow 60 seconds to recover.
