# NetLab landing and portal UI refresh

Based on GitHub main `41c76f09d268597e90393763e44a14b31ef98d7b` (11 November 2026).
This is a UI and feedback upgrade, not a claim of enterprise security certification.

## What changed

- A branded landing page with three role cards and useful access help. The existing
  `/dashboard/`, `/portal/staff` and `/portal/guest` links remain.
- Shared visual tokens, local CSS, local SVG icons, responsive layouts, visible focus
  styling and reduced-motion support. No CDN, remote font or framework is required.
- A staff workspace with navigation, real file/notice counts, a notice character counter,
  filename search, sorting, explicit downloads and stored-file modification times.
- A simpler guest page with announcements, visitor guide and connection test downloads.
- Staff/guest sign-in with password visibility, password-manager autocomplete, generic
  credential errors and submit feedback. There is no invented password-reset workflow.
- Success messages, branded HTTP errors and real XHR upload transfer progress. Reaching
  100% means bytes were sent; the UI waits for the server before reporting success.
- JavaScript-free forms and downloads still work. Duplicate uploads still return 409
  and never replace the existing file. Expired upload sessions still return 401.
- Connection diagnostics are collapsed, rather than dominating each page. HTTPS labels
  come from the request scheme; HTTP mode is never labelled HTTPS.
- Gateway-generated 413, 429 and 502/503/504 responses have local error pages in both
  HTTP and HTTPS modes. Internal error assets cannot be requested directly. Gateway
  zone-denial rules and rate limits remain unchanged; upstream errors are not intercepted.

The "connection test download" sends fixed-size data; it is not an internet speed meter.
Sizes use MiB/KiB, matching the implementation. File size limits include multipart form
contents, so a file very close to the request limit can be rejected.

## Redirect deployment correction (v2)

The deploy command now also recreates the dashboard with the selected HTTP/HTTPS
profile. The dashboard owns public portal redirects and must receive the matching
`SESSION_COOKIE_SECURE`, `STAFF_PORT` and `GUEST_PORT` environment. An older container
can otherwise keep the wrong scheme/ports. Smoke-check failures now include the
HTTP status, expected redirect and received Location header.

## Asset and security integration

`portal/Dockerfile` now includes `COPY static static`. Rebuild **staff-portal once** to
produce `netlab/portal:1.0`; both portal services use this image. Recreate both services.
The portal CSP permits only same-origin scripts/styles/connects and contains no inline
script/style exception. Gateway error pages are standalone static HTML and permit only
inline CSS, with scripts disabled. They contain no user-generated values.

The landing stylesheet is served at `/web/assets/netlab.css` through the existing admin
web route. Landing-page styling is intended to be used through that gateway. The portal
stylesheet and landing stylesheet must stay identical; a regression test verifies this.
When editing the design system, update `portal/static/css/netlab.css` and copy it to
`services/nginx-web/html/assets/netlab.css`.

No session schema, credentials, secrets, certificate paths or application data volumes
are changed. New file metadata comes from `stat()`, not assumed upload ownership/dates.
The existing authentication, CSRF, role and atomic file-publication checks are retained.

## Install and validate (HTTPS only)

Use the guarded HTTPS package on the existing UI branch. Then run:

```bash
bash scripts/ui-deploy.sh
bash scripts/scenario-check.sh
```

The base Compose configuration is HTTPS-only on 8080/8081/8082. Monitoring ports
3000/9090 also require HTTPS. See docs/HTTPS.md for trust/forwarding instructions.
There is no HTTP deployment alternative or separate HTTPS overlay. Existing
authentication, data and named volumes are retained.

## Manual browser acceptance

Use hard refresh after deployment. Check each item at desktop and narrow widths:

1. Landing CSS loads, access help opens, and all three sign-in links use configured ports.
2. Staff and guest sign-ins work; wrong credentials remain a generic error.
3. Password show/hide works with keyboard activation. Password managers are supported.
4. Staff notes save and render escaped text. Empty notes receive useful feedback.
5. A unique upload succeeds; progress/confirmation are accurate. Download bytes match.
6. A duplicate filename shows a useful 409 error and preserves the original bytes.
7. Search, empty search, sorting, long filenames and sign-out remain usable on mobile.
8. Guest has no file-sharing/upload controls. Guide and 1 MiB downloads work.
9. Session expiry removes API access. An expired upload offers sign-in again.
10. Repeat a small upload with JavaScript disabled; normal form submission still works.
11. Keyboard focus, skip link, text zoom, error announcements and reduced motion work.
12. Browser console has no missing assets, JavaScript exceptions or CSP violations.

Do not claim WCAG conformance from DOM checks alone; real keyboard, visual contrast,
zoom/reflow and assistive-technology checks are also required. No high-load or general
production-readiness claim follows from this UI change.

## Rollback

The installer prints its backup path and stores it in the unpacked package's
`last-backup.txt`. Run that package's `rollback.py` from the project root. It refuses
rollback if any upgraded file has since been changed, so later work is not overwritten.
Rebuild the portal image and recreate the same five services using the configuration restored by that package. Source rollback does not restore or delete application data volumes.

## Evaluation evidence

This release was checked with the original regression suite and additional UI tests.
The delivery README records the exact completed checks and their limits. Re-run the
live gateway/authentication checks on the deployment host. A new portal image should
receive a fresh vulnerability scan before updating scan claims in the final report.
