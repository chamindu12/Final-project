# Local validation and measurement

Run from the repository root. These scripts require a running NetLab stack and
Docker access on the measurement host. They are manual measurements, not load
capacity certification. Close dashboard tabs and avoid simultaneous tests.

HTTPS scripts verify the certificate. Default: ~/NetLab-certs/netlab.crt.
Set NETLAB_CA_FILE for a different path. Authenticated scripts reuse
scripts/auth-check.py and its .env configuration without printing credentials.
The browser entry points use fixed HTTPS ports 8080/8081/8082. For the CLI
recovery script set NETLAB_WEB_URL only if a forwarding origin is required.

## Checks

- netlab-response-times.sh: three warm-ups and twenty timed requests per page.
  Sequential fresh curl processes; TLS setup included. Two-second pauses.
  time_appconnect is cumulative from request start, not TLS-only duration.
- dashboard-api-times.py: three warm-ups plus twenty authenticated containers API
  requests; body read included, browser rendering excluded. JSON syntax checked.
- portal-throughput.py: one warm-up plus five transfers per role; verifies status,
  Content-Length, received bytes and expected payload. MiB=2^20 bytes;
  Mbit/s=10^6 bits/s. Host loopback/TLS/application measurement, not physical or
  simulated zone bandwidth. Different payload sizes are not directly comparable.
- log-visibility.py: five unique requests; first observed access-log visibility via
  dashboard API. Includes request and log API latency, excludes browser display.
- netlab-freshness.py: source age uses time()-container_last_seen; scrape age uses
  time()-timestamp(up). All queries use one evaluation time per row. Requires one
  matching source and target series, healthy up=1, finite nonnegative values.
  The old CSV column derived from a bare instant-vector result timestamp is not
  valid scrape-age evidence. Preserve old evidence; do not overwrite it.
- netlab-recovery.py: one CLI restart of nginx-web; records first successful page
  after an observed failure, command completion observation, and Docker health.
- dashboard-control-time.py: one dashboard API restart of nginx-web; records API
  response and first successful page after an observed failure separately.
  Event rows can be written out of time order: sort elapsed_s for analysis.
  If no failure is observed, first recovery is None, not zero downtime.
  Probe/inspect overhead means polling is not exactly every 0.5 seconds.
  Timeout/failure does not prove restart did not occur. Inspect before retrying.

Recovery scripts cause a brief web interruption. A single recovery observation
is not a reliability distribution or exact outage duration. Use three separate
trials with the service healthy between runs; do not compute a credible P95
from three trials.

## Backup and isolated restore

    bash scripts/validation/netlab-restore-check.sh

Stops staff-portal briefly for a consistent archive and source hash manifest;
starts it again even on a backup failure. Discovers its /data named volume.
Refuses existing destination volumes. An optional first argument names a new
netlab_staff_restore_* destination. NETLAB_BACKUP_ROOT and NETLAB_PORTAL_IMAGE
are optional overrides. Destination volume and backup are retained.
Only regular file paths/content are compared, not a complete filesystem metadata
or symlink audit. Session data and other services are outside this scope.
Keep archives private; they may contain user notes/uploads. Do not commit them.

Use the actual destination printed by the backup step, or the prior restored
volume, for the application check:

    bash scripts/validation/restored-app-check.sh netlab_staff_restore_20261111-010740

Uses a separate network-disabled, read-only container, temporary credentials and
sessions; checks anonymous API rejection, CSRF login, role, home rendering, notes
loading and downloaded hashes. It requires at least one restored file. This is
Flask test-client validation, not a live Nginx/TLS test. Do not run Python with -O.

## Verification before commit

    python3 -m compileall -q scripts/validation
    python3 -m unittest discover -s tests -p test_validation_freshness.py
    python3 -m flake8 scripts/validation tests/test_validation_freshness.py --max-line-length=100

Also run bash -n on each shell script. The isolated freshness tests use mock
Prometheus responses; they do not measure a live stack.

Outputs go to security-reports/performance. Keep failed/partial runs distinct
from successful complete runs. Keep raw evidence, sampling counts, methods,
host/software details and limitations with summaries. No performance acceptance
threshold has been agreed. Do not label a latency value a performance PASS.
