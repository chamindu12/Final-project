"""Exercise deployment orchestration without a Docker daemon or live credentials."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def setup_driver(tmp_path, monkeypatch, fail_config=False):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("ui-deploy.sh", "https-compose.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    (scripts / "ui-tests.sh").write_text("#!/bin/sh\nexit 0\n")
    (scripts / "ui-check-config.sh").write_text(
        "#!/bin/sh\nexit " + ("4" if fail_config else "0") + "\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    trace = tmp_path / "calls.txt"
    for name in ("docker", "python3"):
        executable = commands / name
        executable.write_text(f'#!/bin/sh\nprintf \'{name} %s\\n\' "$*" >> "$UI_TRACE"\n')
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("UI_TRACE", str(trace))
    return scripts / "ui-deploy.sh", trace


@pytest.mark.parametrize("mode", ["https", "http"])
def test_deploy_recreates_dashboard_with_the_selected_profile(tmp_path, monkeypatch, mode):
    script, trace = setup_driver(tmp_path, monkeypatch)
    result = subprocess.run(["bash", str(script), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = trace.read_text().splitlines()
    deployment = next(line for line in lines if " up -d " in line)
    assert "--force-recreate" in deployment and "--no-deps" in deployment
    assert "dashboard staff-portal guest-portal nginx-web nginx-proxy" in deployment
    assert ("docker-compose.https.yml" in deployment) is (mode == "https")
    assert any(" build staff-portal" in line for line in lines)
    assert any("ui-smoke-check.py" in line for line in lines)


def test_failed_configuration_does_not_build_or_recreate_services(tmp_path, monkeypatch):
    script, trace = setup_driver(tmp_path, monkeypatch, fail_config=True)
    result = subprocess.run(["bash", str(script), "https"], capture_output=True, text=True)
    assert result.returncode == 4
    assert not trace.exists()


def test_invalid_profile_does_not_run_deployment(tmp_path, monkeypatch):
    script, trace = setup_driver(tmp_path, monkeypatch)
    result = subprocess.run(["bash", str(script), "invalid"], capture_output=True, text=True)
    assert result.returncode == 2
    assert not trace.exists()
