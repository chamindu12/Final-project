import csv
import sys
import importlib.util
import json
import ssl
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "netlab_auth_check", root / "scripts/auth-check.py"
)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

_, values = auth.configuration()
context = ssl.create_default_context(
    cafile=os.environ.get("NETLAB_CA_FILE", str(Path.home() / "NetLab-certs/netlab.crt"))
)
port = values.get("HTTPS_ADMIN_PORT", "8443")
origin = f"https://localhost:{port}"
browser = auth.Browser(origin + "/dashboard", context=context)


def started_at():
    return subprocess.check_output(
        ["docker", "inspect", "nginx-web",
         "--format", "{{.State.StartedAt}}"],
        text=True, timeout=5,
    ).strip()


def page_probe():
    try:
        with urlopen(
            origin + "/web/", context=context, timeout=2
        ) as response:
            body = response.read()
            valid = (
                response.status == 200
                and b"NetLab Network Services" in body
            )
            return valid, f"HTTP {response.status}; content_ok={valid}"
    except (HTTPError, URLError, TimeoutError) as error:
        return False, str(error)


if not page_probe()[0]:
    raise SystemExit("Baseline page check failed; restart cancelled.")

before = started_at()
auth.login(browser, "admin", values)

folder = root / "security-reports/performance"
folder.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
report = folder / f"dashboard-control-{stamp}.csv"


def restart():
    status, body, _ = browser.request(
        "/api/containers/nginx-web/restart",
        {},
        {"X-Requested-With": "dashboard-ui"},
    )
    finished = time.monotonic()
    if status != 200:
        raise RuntimeError(f"Restart API returned HTTP {status}")
    data = json.loads(body)
    if not isinstance(data, dict) or "error" in data:
        raise RuntimeError("Restart API returned an invalid result")
    return finished


try:
    with report.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["elapsed_s", "event", "detail"])

        with ThreadPoolExecutor(max_workers=1) as executor:
            start = time.monotonic()
            writer.writerow([0, "restart_requested", "nginx-web"])
            future = executor.submit(restart)
            deadline = start + 90
            api_time = None
            page_time = None
            unavailable = False
            first_recovery = None

            while time.monotonic() < deadline:
                if future.done() and api_time is None:
                    api_time = future.result() - start
                    writer.writerow([
                        api_time, "api_response", "HTTP 200; valid JSON"
                    ])
                    if started_at() == before:
                        raise RuntimeError("Container start time unchanged")

                available, detail = page_probe()
                elapsed = time.monotonic() - start
                writer.writerow([elapsed, "page_probe", detail])
                output.flush()

                if not available:
                    unavailable = True
                if available and unavailable and first_recovery is None:
                    first_recovery = elapsed
                    writer.writerow([elapsed, "first_page_after_failure", "200; content verified"])
                if available and api_time is not None:
                    page_time = elapsed
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Page recovery observation timed out")

    print("First successful page after observed failure (s):", first_recovery)
    print(f"Restart API response: {api_time:.3f} s")
    print(f"Successful page after API response: {page_time:.3f} s")
    print("Unavailable page observed:", unavailable)
    print("PASS container start timestamp changed")
    print("Evidence:", report)
finally:
    original_failure = sys.exc_info()[0] is not None
    try:
        csrf = auth.form_token(browser, "/")
        status, _, _ = browser.request(
            "/logout", {"csrf_token": csrf}
        )
        auth.check(status, 302, "admin sign out")
    except Exception as error:
        if not original_failure:
            raise
        print(f"Cleanup warning: {error}", file=sys.stderr)
