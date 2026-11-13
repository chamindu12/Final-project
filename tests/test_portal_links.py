"""Public landing links use configured host ports without requiring admin login."""
import importlib.util
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def load_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRET_KEY", "portal-links-test-secret-0123456789")
    monkeypatch.setenv("ADMIN_USERS", "admin:TestPassword")
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.delenv("STAFF_PORT", raising=False)
    monkeypatch.delenv("GUEST_PORT", raising=False)
    store_spec = importlib.util.spec_from_file_location(
        "portal_links_store", ROOT / "dashboard/session_store.py")
    store = importlib.util.module_from_spec(store_spec)
    store_spec.loader.exec_module(store)
    monkeypatch.setitem(sys.modules, "session_store", store)

    def load(**ports):
        for key, value in ports.items():
            monkeypatch.setenv(key, str(value))
        name = "portal_links_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, ROOT / "dashboard/app.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.mark.parametrize("ports", [{}, {"STAFF_PORT": "9081", "GUEST_PORT": "9082"}])
@pytest.mark.parametrize("role", ["staff", "guest"])
@pytest.mark.parametrize("base,host,scheme", [
    ("http://localhost:9080", "localhost", "https"),
    ("http://127.0.0.1:9080", "127.0.0.1", "https"),
    ("https://[::1]:9443", "[::1]", "https"),
])
def test_public_link_preserves_host_and_uses_configured_port(
        load_dashboard, ports, role, base, host, scheme):
    mod = load_dashboard(**ports)
    client = mod.app.test_client()
    response = client.get(f"/portal/{role}", base_url=base)
    port = ports.get(role.upper() + "_PORT", "8081" if role == "staff" else "8082")
    assert response.status_code == 302
    assert response.headers["Location"] == f"{scheme}://{host}:{port}/"
    assert "Set-Cookie" not in response.headers
    assert client.get("/api/containers").status_code == 401


@pytest.mark.parametrize("value", ["0", "-1", "65536", "invalid"])
def test_invalid_port_configuration_is_rejected(load_dashboard, value):
    with pytest.raises(RuntimeError, match="STAFF_PORT and GUEST_PORT"):
        load_dashboard(STAFF_PORT=value)


def test_landing_page_uses_public_redirect_links():
    html = (ROOT / "services/nginx-web/html/index.html").read_text()
    assert 'href="/portal/staff"' in html
    assert 'href="/portal/guest"' in html
    assert 'href="/dashboard/"' in html
    assert "data-port" not in html
    assert "localhost:8081" not in html and "localhost:8082" not in html
