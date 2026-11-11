import csv
import sys
import importlib.util
import json
import secrets
import ssl
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
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

folder = root / "security-reports/performance"
folder.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
report = folder / f"log-visibility-{stamp}.csv"
delays = []

auth.login(browser, "admin", values)

try:
    with report.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow([
            "trial", "marker", "request_duration_s",
            "visibility_from_start_s",
            "visibility_after_response_s", "polls",
        ])

        for trial in range(1, 6):
            time.sleep(2)
            marker = "netlab-probe-" + secrets.token_hex(12)
            start = time.monotonic()

            with urlopen(
                origin + "/web/?probe=" + marker,
                context=context,
                timeout=15,
            ) as response:
                body = response.read()
                if (
                    response.status != 200
                    or b"NetLab Network Services" not in body
                ):
                    raise RuntimeError("Probe page validation failed")

            response_done = time.monotonic()
            deadline = response_done + 30
            polls = 0

            while time.monotonic() < deadline:
                status, body, _ = browser.request(
                    "/api/containers/nginx-proxy/logs?tail=200"
                )
                polls += 1
                if status != 200:
                    raise RuntimeError(
                        f"Log API returned HTTP {status}"
                    )

                data = json.loads(body)
                lines = data.get("lines")
                if (
                    data.get("name") != "nginx-proxy"
                    or not isinstance(lines, list)
                    or not all(isinstance(line, str) for line in lines)
                ):
                    raise RuntimeError("Invalid log API response")

                if any(
                    f"probe={marker} " in line
                    and '"GET /web/?probe=' in line
                    for line in lines
                ):
                    observed = time.monotonic()
                    delay = observed - start
                    writer.writerow([
                        trial, marker,
                        f"{response_done - start:.6f}",
                        f"{delay:.6f}",
                        f"{observed - response_done:.6f}",
                        polls,
                    ])
                    output.flush()
                    delays.append(delay)
                    print(
                        f"Trial {trial}/5: "
                        f"{delay:.3f} s from request start; "
                        f"{polls} poll(s)"
                    )
                    break

                time.sleep(1)
            else:
                raise RuntimeError(
                    f"Marker not found within observation window: {marker}"
                )

    print(f"Median visibility: {statistics.median(delays):.3f} s")
    print(f"Maximum visibility: {max(delays):.3f} s")
    print("Evidence:", report)
finally:
    original_failure = sys.exc_info()[0] is not None
    try:
        time.sleep(2)
        csrf = auth.form_token(browser, "/")
        status, _, _ = browser.request(
            "/logout", {"csrf_token": csrf}
        )
        auth.check(status, 302, "admin sign out")
    except Exception as error:
        if not original_failure:
            raise
        print(f"Cleanup warning: {error}", file=sys.stderr)
