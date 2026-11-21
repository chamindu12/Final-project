# Container logs

Open `https://localhost:8080/dashboard/`, sign in as an administrator and use the
Logs container selector. Staff and guest accounts cannot read the admin log API.

## What is recorded

| Container | Output |
|---|---|
| staff-portal / guest-portal | Structured request and action records: login success/failure, authentication/CSRF rejection, logout, staff notice creation, upload success/conflict, file/guide/test-download requests and response status. |
| nginx-proxy | Gateway requests, zone-policy rejections, rate-limit responses and upstream status/timing. Requests rejected here never reach the portal application. |
| dnsmasq | DNS queries/replies with query identifiers (`log-queries=extra`). Health probes can also create DNS traffic. |
| dashboard | Gunicorn access and application/error output. |
| nginx-web | Nginx access/error output; health probes may appear as regular requests. |
| Prometheus / Grafana / cAdvisor | The service's own operational output; not a synthetic audit record for every metric scrape. |
| staff-client / isolated-test | These normally run `sleep infinity` and can legitimately have no output. |

Portal JSON records include an application UTC timestamp, service/role, event,
HTTP method, route template, actual status, response preparation time, validated
authenticated username when available, gateway-reported client/zone and request ID.
Passwords, form bodies, cookies, session identifiers, query strings, notice contents
and uploaded filenames are omitted from portal audit records. Unmatched application
paths are recorded as `<unmatched>` rather than copying arbitrary URL contents.

The gateway overwrites `X-Request-ID` and returns that ID in the response. Its JSON
access record and the portal record share the same ID. Gateway logs include the
URI path, but omit query strings and request bodies. Failed logins do not record
the submitted username. These are operational logs and still include client IPs
and authenticated usernames; retain access to the administrator role.

`response_ms` ends when Flask prepares the response headers. A download is logged
as **requested**, not as proof that the browser received every byte. Gateway
`request_s` measures the gateway's request lifetime and includes different work.

## Live viewing

- **Live** uses an authenticated Server-Sent Events connection to Docker's native
  `logs(stream=True, follow=True)` output. Nginx buffering is disabled for dashboard
  proxy routes; no WebSocket server or additional exposed port is required.
- The viewer retains the selected last 50/100/200/500 physical lines. Identical
  messages are preserved as separate records, not deduplicated by text.
- Each line retains Docker's original UTC timestamp, including subsecond digits.
  The application JSON timestamp is an additional event timestamp. The browser
  connection indicator is not a replacement for the event timestamp.
- Auto-scroll can be disabled while Live stays enabled. The text filter changes
  only the view. Log text is displayed as text, never interpreted as HTML.
- Switching container/tail cancels the previous subscription. Late events and old
  snapshot responses cannot overwrite the current selection.
- An idle connection sends a heartbeat every two seconds. Authentication is checked
  on that cadence even while the container is silent. Expired/revoked sessions end
  the subscription and return the viewer to sign-in.
- Connections rotate after 25 seconds, or reconnect after Docker closes a source.
  Each connection reloads a fresh bounded tail and replaces the previous view. It
  does not append replayed history. A container recreation is identified by its new
  container ID; old-container output is not a durable cross-recreation archive.
- There are two live subscriptions per Gunicorn worker, leaving threads for other
  APIs. Backpressure uses a bounded 256-record queue. Individual very long lines
  are limited to 16 KiB and visibly labelled as truncated.
- If EventSource is unavailable or repeated connections fail, the viewer uses
  serial one-second snapshot polling and labels this fallback. Slow snapshot
  requests never overlap. Refresh reloads the selected tail; turning Live off
  leaves a paused snapshot. Hidden tabs disconnect and reload when shown again.

Docker retains stdout/stderr with the `json-file` driver, `max-size=10m` and
`max-file=3` for each service. Older records rotate; container removal/recreation
also removes that container's local history. Export important evidence separately.
The snapshot API remains compatible at `GET /api/containers/<name>/logs?tail=100`;
the live endpoint is `GET /api/containers/<name>/logs/stream?tail=100`.

## Deploy and check

After updating the source, run the existing complete deployment workflow so the
portal/dashboard images, DNS configuration and container log driver are recreated:

```bash
cd ~/Final-project
bash scripts/ui-deploy.sh
python3 scripts/logs-check.py --ca-file "$HOME/NetLab-certs/netlab.crt"
```

Keep the dashboard on **staff-portal** or **guest-portal** while using that portal
in a second browser tab. Try valid/invalid login, logout, a staff note, an upload,
a duplicate upload (409), a download and an oversized guest test download (403).
Choose **nginx-proxy** for gateway denials and **dnsmasq** for DNS activity.

`logs-check.py` verifies anonymous denial, staff/guest request IDs in live output,
gateway records, a unique real DNS query and revocation of an already-open stream.
It verifies the lab certificate and prints measured request-to-SSE times. Its
default five-second observation budget is an integration check, not proof of the
proposal's two-second browser-rendering target. Use a timestamped browser capture
to evaluate that end-to-end target. The older snapshot timing script now correlates
the gateway response request ID rather than relying on logged query strings.

Python regressions run with `bash scripts/ui-tests.sh`. JavaScript race/retention
tests run with `node --test tests/test_log_viewer.cjs` (Node.js 20 or newer); CI runs
them separately if Node.js 20+ is unavailable on the development host.

Implementation references: [Docker SDK logs](https://docker-py.readthedocs.io/en/stable/containers.html),
[Flask streaming](https://flask.palletsprojects.com/en/stable/patterns/streaming/),
and [Nginx proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).
