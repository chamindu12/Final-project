# NetLab performance and recovery results

Measurements collected on 10–11 November 2026 using the local Docker
deployment. Environment details are retained in
security-reports/performance/environment.txt.

These are baseline observations, not agreed performance acceptance targets.

## HTTPS page response times

Three warm-up requests and 20 measured requests per endpoint.
Requests were sequential, with two-second pauses. All 60 measured
requests returned HTTP 200.
------------------------------------------------------
| Endpoint     | Mean      | P95       | Maximum     |
|--------------|-----------|-----------|-------------|
| Landing page | 19.738 ms | 36.095 ms | 37.185 ms   |
| Staff login  | 20.241 ms | 43.870 ms | 43.950 ms   |
| Guest login  | 29.430 ms | 77.975 ms | 123.569 ms  |
------------------------------------------------------
Timing includes connection establishment, TLS and response transfer.
An earlier rapid-request attempt encountered HTTP 429 and was excluded
from this completed baseline.

## Authenticated dashboard API

The containers API was measured after three warm-ups, using 20 requests.
All measured responses returned HTTP 200 and valid JSON.
-----------------------------------------------------
| Mean       | Median     | P95        | Maximum    |
|------------|------------|------------|------------|
| 168.856 ms | 153.198 ms | 191.854 ms | 596.137 ms |
-----------------------------------------------------
The maximum was retained. Its cause was not established.
Timing includes reading the response body, but excludes browser rendering.

## Dashboard restart control and recovery

Three separate nginx-web restart trials were performed through the
authenticated dashboard API. Container start timestamps changed and
the container was confirmed healthy after each trial.

All elapsed times below begin at restart request initiation.
-------------------------------------------------------------------------------
| Measurement                                     | Median  | Range           |
|-------------------------------------------------|---------|-----------------|
| API response                                    | 1.134 s | 0.695 – 3.760 s |
| First successful page after an observed failure | 2.101 s | 1.607 – 4.738 s |
-------------------------------------------------------------------------------
These are polling observations, not exact outage durations.
Three trials do not establish a reliable P95 or long-term availability.

## Portal download throughput

One warm-up and five measured downloads were performed per role.
All measured downloads returned HTTP 200, the expected byte count,
Content-Length and payload.
----------------------------------------------------------
| Role  | Payload | Median          | Range              |
|-------|---------|-----------------|--------------------|
| Staff | 10 MiB  | 1649.825 Mbit/s | 534.839 – 2132.779 |
| Guest | 1 MiB   | 124.839 Mbit/s  | 69.299 – 184.828   |
----------------------------------------------------------
This measures the local host-to-HTTPS-gateway-to-portal path.
It does not establish physical network or simulated-zone bandwidth.
Different payload sizes prevent direct role-to-role speed comparisons.
The guest restriction is a download-size limit, not a measured bandwidth cap.

## Log visibility

Five unique request markers were found on the first dashboard log API poll.
----------------------------------------------------------
| Observation                   | Median    | Maximum    |
|-------------------------------|-----------|------------|
| From request initiation       | 62.657 ms | 121.371 ms |
| From page response completion | 44.870 ms | 80.131 ms  |
----------------------------------------------------------
These include log API response time, exclude browser rendering, and do
not identify the exact instant the log entry was written.

## Monitoring freshness

Two corrected runs collected 30 observations each. All observations had up=1.
---------------------------------------------------------------------------------------
| Run   | Mean scrape age | Maximum scrape age | Mean source age | Maximum source age |
|-------|-----------------|--------------------|-----------------|--------------------|
| 13:31 | 7.754 s         | 14.162 s           | 7.903 s         | 14.178 s           |
| 13:35 | 7.073 s         | 15.008 s           | 7.820 s         | 15.589 s           |
---------------------------------------------------------------------------------------
Scrape age uses time()-timestamp(up); source age uses
time()-container_last_seen. The configured scrape interval is 15 seconds,
which is not a hard maximum freshness guarantee.

The earlier CSV calculation based on a bare instant-query result timestamp
is excluded as scrape-age evidence.

## Metric calculation verification

For one saved idle nginx-web Docker snapshot, the running image's
parse_stats() output matched independently calculated expected values:

- CPU: 0.00%.
- Cache-adjusted memory: 13.1 MiB of 128.0 MiB, or 10.20%.
- Network RX: 6,124 bytes; TX: 25,144 bytes.

This verifies calculation consistency for that snapshot, not measurement
accuracy under load. Network counters are cumulative bytes.
Fields named "_mb" use MiB (1,048,576 bytes).

## Staff data backup and restore

A consistent backup was restored into a separate volume.
Six regular-file paths and SHA-256 hashes matched.

An isolated application check then verified:

- Anonymous API rejection and CSRF-protected login.
- Correct authenticated staff identity and successful home rendering.
- Loading five restored notes.
- Downloading five restored files with matching content hashes.

The live staff login page remained available after backup.
Sessions, other services, filesystem metadata and symlinks were outside
this validation scope. The isolated application check used Flask's test
client, not a live TLS connection.

## Evidence and limits

Raw CSV, JSON and validation outputs are retained under
security-reports/performance. Reproduction instructions are in VALIDATION.md.

Results describe this local deployment with sequential requests.
Concurrent-user capacity, sustained load, browser display latency and
resource-metric accuracy under load have not been established.
