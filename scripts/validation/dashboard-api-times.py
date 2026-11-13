import csv
import sys
import importlib.util
import json
import math
import ssl
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

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
port = "8080"
browser = auth.Browser(
    f"https://localhost:{port}/dashboard",
    context=context,
)
auth.login(browser, "admin", values)

output_dir = root / "security-reports/performance"
output_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
report = output_dir / f"dashboard-api-times-{stamp}.csv"
times = []

try:
    with report.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow([
            "sample", "http_code", "duration_ms", "valid_json"
        ])

        for index in range(23):
            time.sleep(2)
            start = time.perf_counter()
            status, body, _ = browser.request("/api/containers")
            elapsed = (time.perf_counter() - start) * 1000

            try:
                json.loads(body)
                valid_json = True
            except (ValueError, UnicodeDecodeError):
                valid_json = False

            if index >= 3:
                sample = index - 2
                writer.writerow([
                    sample, status, f"{elapsed:.3f}", valid_json
                ])
                output.flush()

            if status != 200 or not valid_json:
                raise RuntimeError(
                    f"API check failed: HTTP {status}, "
                    f"valid_json={valid_json}"
                )

            if index >= 3:
                times.append(elapsed)
                print(f"Sample {sample}/20: {elapsed:.3f} ms")
finally:
    original_failure = sys.exc_info()[0] is not None
    try:
        csrf = auth.form_token(browser, "/")
        status, _, _ = browser.request(
            "/logout", {"csrf_token": csrf}
        )
        if status != 302:
            raise RuntimeError(f"Logout failed: HTTP {status}")

    except Exception as error:
        if not original_failure:
            raise
        print(f"Cleanup warning: {error}", file=sys.stderr)
ordered = sorted(times)
p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
print(f"\nSamples: {len(times)}")
print(f"Mean: {statistics.mean(times):.3f} ms")
print(f"P95: {p95:.3f} ms")
print(f"Max: {max(times):.3f} ms")
print("Evidence:", report)
