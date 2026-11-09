"""Policy probes must distinguish network failures from broken test execution."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scenario-check.sh"


@pytest.fixture()
def probe_env(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("ADMIN_USERS=a:b\nSTAFF_USERS=c:d\nGUEST_USERS=e:f\n")
    docker = tmp_path / "fake-docker"
    docker.write_text('''#!/bin/sh
case "$1" in
  inspect)
    if [ "${INSPECT_STATUS:-0}" != 0 ]; then exit "$INSPECT_STATUS"; fi
    echo "${RUNNING:-true}"
    ;;
  exec) exit "${EXEC_STATUS:-0}" ;;
  *) exit 125 ;;
esac
''')
    docker.chmod(0o755)
    return dict(os.environ, ENV_FILE=str(env_file), DOCKER=str(docker),
                INSPECT_STATUS="0", RUNNING="true", EXEC_STATUS="0",
                SKIP_DNS="", SKIP_ICMP="")


def run_helper(command, env):
    # Load the actual helper definitions, without executing the live scenarios.
    prefix = SCRIPT.read_text().split(
        "# ---- helper that runs on the host", 1)[0]
    return subprocess.run(["bash", "-c", prefix + "\n" + command],
                          env=env, text=True, capture_output=True)


@pytest.mark.parametrize("status,expected", [
    (0, "no"), (7, "yes"), (28, "yes"),
    (1, "ERROR(exit=1)"), (6, "ERROR(exit=6)"),
    (22, "ERROR(exit=22)"), (125, "ERROR(exit=125)"),
    (126, "ERROR(exit=126)"), (127, "ERROR(exit=127)"),
])
def test_http_probe_exit_classification(probe_env, status, expected):
    probe_env["EXEC_STATUS"] = str(status)
    result = run_helper('unreachable_in staff-client http://172.20.0.10:8080/', probe_env)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("expected_code", [1, 9])
@pytest.mark.parametrize("status", [0, 1, 2, 9, 125, 127])
def test_dns_and_ping_accept_only_expected_failure(probe_env, expected_code, status):
    result = run_helper(f"probe_result {status} {expected_code}", probe_env)
    expected = "yes" if status == expected_code else (
        "no" if status == 0 else f"ERROR(exit={status})")
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("settings", [
    {"INSPECT_STATUS": "1"},       # Missing container or unavailable Docker.
    {"RUNNING": "false"},          # Stopped client.
    {"EXEC_STATUS": "127"},        # Missing command.
    {"EXEC_STATUS": "126"},        # Command cannot execute.
])
def test_broken_clients_abort_before_any_pass(probe_env, settings):
    probe_env.update(settings)
    result = subprocess.run(["bash", str(SCRIPT)], env=probe_env,
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "ERROR:" in result.stderr
    assert "PASS " not in result.stdout


def test_client_preflight_success(probe_env):
    result = run_helper("require_clients", probe_env)
    assert result.returncode == 0
