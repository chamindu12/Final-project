# Container vulnerability findings

Review date: 11 November 2026.

## Scope and method

Trivy 0.76.2 scanned exported images using the vulnerability scanner,
filtered to HIGH and CRITICAL severities. Eleven deployed services
share eight distinct images.

Counts represent package/binary finding entries per distinct image,
not unique vulnerabilities, affected containers or confirmed exploits.
LOW and MEDIUM findings were outside this scan scope.

## Results

-----------------------------------------------------------------------------
| Image group | Before remediation HIGH | After remediation HIGH | CRITICAL |
|-------------|-------------------------|------------------------|----------|
| Dashboard   | 47                      | 47                     | 0        |
| Portal      | 47                      | 47                     | 0        |
| Nginx       | 2                       | 0                      | 0        |
| cAdvisor    | 12                      | 12                     | 0        |
| Grafana     | 4                       | 4                      | 0        |
| Client      | 16                      | 0                      | 0        |
| DNS         | 16                      | 0                      | 0        |
| Prometheus  | 2                       | 2                      | 0        |
| Total       | 146                     | 112                    | 0        |
-----------------------------------------------------------------------------

The final column combines unchanged-image scans with remediated candidate
scans from the same day. Deployed image identities were checked against
the candidate image map.

## Completed remediation

- Nginx: upgraded libxslt from 1.1.43-r3 to 1.1.43-r4.
- Client and DNS: rebuilt images with krb5-libs 1.22.3-r0,
  replacing 1.22.2-r1.
- Added libxslt to the Nginx Dockerfile upgrade command.
- Candidate scans reported zero HIGH/CRITICAL entries for these images.
- Configuration syntax, HTTPS authentication, staff DNS and six HTTPS
  zone-access checks passed after deployment.
- Previous images were retained under before-security-20261111-161904
  rollback tags.

## Unresolved findings and actions

-------------------------------------------------------------------------------------------------------------------------------------------
| Images             | Entries | Scan evidence                      | Required next action                                                |
|--------------------|---------|------------------------------------|---------------------------------------------------------------------|
| Dashboard / Portal | 94      | Debian packages; no fixed version  | Check distribution advisories,                                      |
|                    |         | listed in these reports            | affected functionality and later package updates                    |
| cAdvisor           | 12      | Embedded Go dependencies/toolchain;| Evaluate an upstream image or source                                |
|                    |         | fixed versions listed              | rebuild containing compatible fixes                                 |
| Grafana            | 4       | Embedded Go dependencies;          | Evaluate a compatible upstream                                      |
|                    |         | fixed versions listed              | release containing fixes                                            |
| Prometheus         | 2       | Same vulnerability reported in     | Evaluate an upstream release or rebuild with the fixed dependency   |
|                    |         | prometheus and promtool            |                                                                     |
-------------------------------------------------------------------------------------------------------------------------------------------

All unresolved entries remain pending applicability review.
A listed dependency fix does not prove that a compatible application
image is available. Operating-system package upgrades do not replace
dependencies compiled into Go binaries.

No finding has been declared a false positive, not affected or accepted
risk without supporting analysis.

## Deployment controls and limitations

Network separation, gateway access restrictions, authentication and
container hardening reduce exposure but do not establish that an
individual vulnerability is unreachable or harmless.

HTTPS terminates at Nginx; application traffic on the backend network
remains HTTP. Grafana and Prometheus retain their separate loopback
bindings.

Zero CRITICAL findings is not a guarantee that an image is secure.
Results depend on image contents, scan coverage and the vulnerability
database available at scan time.

## Evidence

Original running-image reports:
security-reports/current-20261111-154004/

Remediated candidate reports:
security-reports/candidates-20261111-160140/

Each directory includes an image-map.csv.
Retain the raw reports alongside this review. Older scans are historical
evidence and must not replace the current-image results.