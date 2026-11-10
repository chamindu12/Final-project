#!/usr/bin/env python3
"""Check login/logout against a running NetLab stack without printing credentials.

Run: python3 scripts/auth-check.py --rate-limit
Optional short-lifetime test: --expiry-seconds 15 (configure SESSION_TTL_SECONDS=10 first).
"""
import argparse
import copy
import http.cookiejar
import os
import re
import ssl
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPCookieProcessor, HTTPRedirectHandler, Request,
                            HTTPSHandler, build_opener)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


class Browser:
    def __init__(self, base, pause=0, context=None):
        self.base = base.rstrip("/")
        self.pause = pause
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(
            HTTPCookieProcessor(self.jar), NoRedirect(), HTTPSHandler(context=context))

    def request(self, path, data=None, headers=None):
        if self.pause:
            time.sleep(self.pause)
        request = Request(self.base + path,
                          data=urlencode(data).encode() if data is not None else None,
                          headers=headers or {})
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.read(), response.headers


def check(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected}, got {actual}")
    print(f"PASS {label}: {actual}")


def form_token(browser, path="/login"):
    status, body, _ = browser.request(path)
    check(status, 200, "form page available")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', body)
    if not match:
        raise RuntimeError("CSRF form token not found")
    return match.group(1).decode()


def configuration():
    root = Path(__file__).resolve().parents[1]
    values = {}
    for raw in (root / ".env").read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    for key in list(values) + ["PROXY_PORT", "STAFF_PORT", "GUEST_PORT",
                               "HTTPS_ADMIN_PORT", "HTTPS_STAFF_PORT", "HTTPS_GUEST_PORT"]:
        if key in os.environ:
            values[key] = os.environ[key]
    return root, values


def login(browser, role, values):
    username, separator, password = values[f"{role.upper()}_USERS"].split(",")[0].partition(":")
    if not separator or not username or not password:
        raise RuntimeError(f"Set {role.upper()}_USERS in .env")
    csrf = form_token(browser)
    status, _, _ = browser.request("/login", {"username": username, "password": password,
                                              "csrf_token": csrf})
    check(status, 302, f"{role} sign in (429 means wait 60 seconds and retry)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate-limit", action="store_true")
    parser.add_argument("--expiry-seconds", type=int, default=0)
    parser.add_argument("--https", action="store_true", help="use HTTPS gateway ports")
    parser.add_argument("--ca-file", help="trusted lab certificate for TLS verification")
    options = parser.parse_args()
    if options.https and not options.ca_file:
        parser.error("--https requires --ca-file; certificate verification is mandatory")
    if options.ca_file and not options.https:
        parser.error("--ca-file requires --https")
    if not 0 <= options.expiry_seconds <= 30:
        raise RuntimeError("Use an expiry wait between 0 and 30 seconds")
    root, values = configuration()
    context = ssl.create_default_context(cafile=options.ca_file) if options.https else None
    scheme = "https" if options.https else "http"
    admin_port = values.get("HTTPS_ADMIN_PORT", "8443") if options.https else values.get(
        "PROXY_PORT", "8080")
    staff_port = values.get("HTTPS_STAFF_PORT", "8444") if options.https else values.get(
        "STAFF_PORT", "8081")
    guest_port = values.get("HTTPS_GUEST_PORT", "8445") if options.https else values.get(
        "GUEST_PORT", "8082")
    admin_origin = f"{scheme}://localhost:{admin_port}"
    urls = {"admin": admin_origin + "/dashboard",
            "staff": f"{scheme}://localhost:{staff_port}",
            "guest": f"{scheme}://localhost:{guest_port}"}
    for role, base in urls.items():
        browser = Browser(base, pause=0.6 if role == "guest" else 0, context=context)
        protected = "/api/containers" if role == "admin" else "/api/whoami"
        check(browser.request(protected)[0], 401, f"{role} anonymous access denied")
        login(browser, role, values)
        if options.https:
            cookie = next((c for c in browser.jar if c.name == f"{role}_session"), None)
            if cookie is None:
                raise RuntimeError(f"{role} session cookie not found")
            check(cookie.secure, True, f"{role} cookie Secure flag")
            check(cookie.has_nonstandard_attr("HttpOnly"), True, f"{role} cookie HttpOnly")
            check(cookie.get_nonstandard_attr("SameSite"), "Lax", f"{role} cookie SameSite")
        check(browser.request(protected)[0], 200, f"{role} signed-in access")
        if options.expiry_seconds:
            print(f"Waiting {options.expiry_seconds} seconds for {role} expiry")
            time.sleep(options.expiry_seconds)
            check(browser.request(protected)[0], 401, f"{role} session expired")
            continue
        if role == "admin":
            container_id = subprocess.check_output(
                ["docker", "compose", "ps", "-q", "dashboard"], cwd=root, text=True,
            ).strip()
            if not container_id:
                raise RuntimeError("Dashboard container ID not found")
            for reference in ("dashboard", container_id[:12], container_id):
                for action in ("stop", "restart"):
                    status = browser.request(f"/api/containers/{reference}/{action}", {},
                                             {"X-Requested-With": "dashboard-ui"})[0]
                    check(status, 403, f"dashboard {action} denied for name/ID")
        csrf = form_token(browser, "/")
        replay = Browser(base, pause=0.6 if role == "guest" else 0, context=context)
        for cookie in browser.jar:
            replay.jar.set_cookie(copy.copy(cookie))
        check(browser.request("/logout", {})[0], 400, f"{role} CSRF-less logout denied")
        check(browser.request(protected)[0], 200, f"{role} invalid logout preserves session")
        csrf = form_token(browser, "/")
        check(browser.request("/logout", {"csrf_token": csrf})[0], 302, f"{role} sign out")
        check(browser.request(protected)[0], 401, f"{role} access removed")
        check(replay.request(protected)[0], 401, f"{role} copied old cookie rejected")
    if options.rate_limit:
        # The same source bucket must cover both aliases, without limiting GET requests.
        aliases = [Browser(admin_origin, context=context),
                   Browser(admin_origin + "/dashboard", context=context)]
        tokens = [form_token(browser) for browser in aliases]
        codes = []
        for i in range(30):
            index = i % 2
            codes.append(aliases[index].request("/login", {
                "username": "invalid-rate-probe", "password": "wrong",
                "csrf_token": tokens[index],
            })[0])
        if not set(codes).issubset({401, 429}):
            raise RuntimeError(f"Unexpected login statuses: {sorted(set(codes))}")
        check(429 in codes, True, "admin login flood limited across both aliases")
        for browser in aliases:
            check(browser.request("/login")[0], 200, "GET login page remains available")
        print("Wait 60 seconds before another admin login test after this flood check.")
    print("All requested authentication checks passed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, URLError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(f"FAIL {error}") from None
