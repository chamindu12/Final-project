import csv
import json
import ssl
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

url = os.environ.get("NETLAB_WEB_URL", "https://localhost:8443/web/")
context = ssl.create_default_context(
    cafile=os.environ.get("NETLAB_CA_FILE", str(Path.home() / "NetLab-certs/netlab.crt"))
)
root = Path(__file__).resolve().parents[2] / "security-reports/performance"
root.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
report = root / f"web-recovery-{stamp}.csv"


def page_ok():
    with urlopen(url, context=context, timeout=2) as response:
        body = response.read()
        return (
            response.status == 200
            and b"NetLab Network Services" in body
        )


if not page_ok():
    raise SystemExit("Baseline page check failed; restart cancelled.")

with report.open("w", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(["elapsed_s", "event", "detail"])

    start = time.monotonic()
    process = subprocess.Popen(
        ["docker", "restart", "nginx-web"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    writer.writerow([0, "restart_requested", "nginx-web"])

    command_done = None
    page_ready = None
    first_recovery = None
    healthy = None
    saw_unavailable = False

    while time.monotonic() - start < 90:
        elapsed = time.monotonic() - start

        if command_done is None and process.poll() is not None:
            stdout, stderr = process.communicate()
            command_done = time.monotonic() - start
            writer.writerow([
                command_done, "restart_command_finished",
                f"exit={process.returncode}",
            ])
            if process.returncode != 0:
                raise RuntimeError(stderr or stdout)

        try:
            available = page_ok()
            detail = f"content_ok={available}"
        except Exception as error:
            available = False
            detail = f"{type(error).__name__}: {error}"

        observed = time.monotonic() - start
        writer.writerow([observed, "page_probe", detail])
        if not available:
            saw_unavailable = True
        if available and saw_unavailable and first_recovery is None:
            first_recovery = observed
            writer.writerow([observed, "first_page_after_failure", "200; content verified"])
        if available and command_done is not None and page_ready is None:
            page_ready = observed

        result = subprocess.run(
            ["docker", "inspect", "nginx-web",
             "--format", "{{json .State}}"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        state = json.loads(result.stdout)
        status = state.get("Health", {}).get("Status", "missing")
        if command_done is not None and healthy is None:
            if state.get("Running") and status == "healthy":
                healthy = time.monotonic() - start
                writer.writerow([healthy, "docker_healthy", status])

        output.flush()
        if page_ready is not None and healthy is not None:
            break
        time.sleep(0.5)
    else:
        raise SystemExit(f"Recovery timed out. Evidence: {report}")

print("First successful page after observed failure (s):", first_recovery)
print(f"Restart command completion observed: {command_done:.3f} s")
print(f"First successful page after command: {page_ready:.3f} s")
print(f"Docker healthy observed: {healthy:.3f} s")
print("Unavailable page observed:", saw_unavailable)
print("Evidence:", report)
