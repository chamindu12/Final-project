import csv
import sys
import importlib.util
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

folder = root / "security-reports/performance"
folder.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
report = folder / f"portal-throughput-{stamp}.csv"

with report.open("w", newline="") as output:
    writer = csv.writer(output)
    writer.writerow([
        "role", "sample", "requested_mib", "http_code",
        "received_bytes", "duration_s", "mib_per_s", "mbit_per_s",
    ])

    for role, mb, default_port in (
        ("staff", 10, "8081"),
        ("guest", 1, "8082"),
    ):
        port = default_port
        browser = auth.Browser(
            f"https://localhost:{port}", context=context
        )
        auth.login(browser, role, values)
        rates = []

        try:
            # One warm-up followed by five measured downloads.
            for index in range(6):
                time.sleep(2)
                start = time.perf_counter()
                status, body, headers = browser.request(
                    f"/speedtest?mb={mb}"
                )
                elapsed = time.perf_counter() - start

                expected = mb * 1024 * 1024
                valid = (
                    status == 200
                    and len(body) == expected
                    and headers.get("Content-Length") == str(expected)
                    and body.count(b"x") == expected
                )

                mib_per_s = len(body) / (1024 * 1024) / elapsed
                mbit_per_s = len(body) * 8 / 1_000_000 / elapsed

                if index > 0:
                    writer.writerow([
                        role, index, mb, status, len(body),
                        f"{elapsed:.6f}",
                        f"{mib_per_s:.3f}",
                        f"{mbit_per_s:.3f}",
                    ])
                    output.flush()

                if not valid:
                    raise RuntimeError(
                        f"{role} download failed validation: "
                        f"HTTP {status}, bytes={len(body)}"
                    )

                if index > 0:
                    rates.append(mbit_per_s)
                    print(
                        f"{role} {index}/5: "
                        f"{elapsed:.3f} s, "
                        f"{mbit_per_s:.3f} Mbit/s"
                    )

            print(
                f"{role} median: "
                f"{statistics.median(rates):.3f} Mbit/s"
            )
        finally:
            original_failure = sys.exc_info()[0] is not None
            try:
                time.sleep(2)
                csrf = auth.form_token(browser, "/")
                time.sleep(2)
                status, _, _ = browser.request(
                    "/logout", {"csrf_token": csrf}
                )
                auth.check(status, 302, f"{role} sign out")

            except Exception as error:
                if not original_failure:
                    raise
                print(f"Cleanup warning: {error}", file=sys.stderr)
print("Evidence:", report)
