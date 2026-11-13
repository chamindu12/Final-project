#!/usr/bin/env python3
"""Read-only UI smoke checks against the live gateway. No credentials are printed."""
import argparse
import importlib.util
import ssl
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--ca-file")
    options = parser.parse_args()
    if options.https != bool(options.ca_file):
        parser.error("Use --https together with --ca-file; TLS verification is required")
    spec = importlib.util.spec_from_file_location("netlab_ui_auth", ROOT / "scripts/auth-check.py")
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    _, values = auth.configuration()
    context = ssl.create_default_context(cafile=options.ca_file) if options.https else None
    scheme = "https" if options.https else "http"
    ports = {"admin": values.get("HTTPS_ADMIN_PORT", "8443") if options.https else values.get(
        "PROXY_PORT", "8080"), "staff": values.get(
        "HTTPS_STAFF_PORT", "8444") if options.https else values.get("STAFF_PORT", "8081"),
        "guest": values.get("HTTPS_GUEST_PORT", "8445") if options.https else values.get(
            "GUEST_PORT", "8082")}
    passed = 0

    def check(condition, label):
        nonlocal passed
        if not condition:
            raise RuntimeError("FAIL: " + label)
        passed += 1
        print("PASS " + label)

    admin = auth.Browser(f"{scheme}://localhost:{ports['admin']}", pause=0.65, context=context)
    status, body, _ = admin.request("/")
    check(status == 200 and b"Choose your workspace" in body, "landing page")
    check(body.count(b"sign in</a>") == 3, "three role sign-ins")
    status, body, headers = admin.request("/web/assets/netlab.css")
    check(status == 200 and "text/css" in headers.get("Content-Type", "") and len(body) > 1000,
          "landing stylesheet through gateway")
    for role in ("staff", "guest"):
        status, _, headers = admin.request("/portal/" + role)
        location = headers.get("Location", "")
        target = urlsplit(location)
        expected = f"{scheme}://localhost:{ports[role]}/"
        check(status == 302 and target.scheme == scheme
              and target.hostname == "localhost"
              and target.port == int(ports[role]),
              f"{role} redirect retains configured port/scheme "
              f"(status={status}; expected={expected}; received={location!r})")
        browser = auth.Browser(f"{scheme}://localhost:{ports[role]}", pause=0.65, context=context)
        status, body, headers = browser.request("/login")
        check(status == 200 and b'autocomplete="current-password"' in body, role + " login page")
        policy = headers.get("Content-Security-Policy", "")
        check("script-src 'self'" in policy and "style-src 'self'" in policy
              and "'unsafe-inline'" not in policy, role + " asset security policy")
        for asset, mime in (("css/netlab.css", "text/css"), ("js/portal.js", "javascript")):
            status, body, headers = browser.request("/static/" + asset)
            check(status == 200 and mime in headers.get("Content-Type", "") and len(body) > 500,
                  role + " " + asset)
        check(browser.request("/help")[0] == 200, role + " access help")
        check(browser.request("/api/whoami")[0] == 401, role + " anonymous API still denied")
        check(browser.request("/__netlab_errors/429.html")[0] == 404,
              role + " gateway error pages are internal-only")
    print(f"All {passed} live UI checks passed. These do not replace authenticated or zone checks.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, URLError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
