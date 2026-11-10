"""HTTPS deployment cookie policy and narrowly trusted gateway scheme headers."""
import importlib.util
from pathlib import Path
import ssl
from urllib.request import HTTPSHandler

import pytest

from test_auth_security import ACCOUNTS, load_app, token_from

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("secure", ["0", "1"])
def test_cookie_flags_and_authentication_in_each_mode(monkeypatch, tmp_path, role, secure):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", secure)
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    base = "https://localhost" if secure == "1" else "http://localhost"
    protected = "/api/containers" if role == "admin" else "/api/whoami"
    assert client.get(protected, base_url=base).status_code == 401
    token = token_from(client.get("/login", base_url=base))
    username, password = ACCOUNTS[role]
    response = client.post("/login", base_url=base, data={
        "csrf_token": token, "username": username, "password": password,
    })
    assert response.status_code == 302
    cookie = client.get_cookie(f"{role}_session")
    assert cookie.secure is (secure == "1")
    assert cookie.http_only and cookie.same_site == "Lax"
    if role != "admin":
        assert client.get(protected, base_url=base).status_code == 200
    current = token_from(client.get("/", base_url=base))
    assert client.post("/logout", base_url=base, data={
        "csrf_token": current}).status_code == 302
    assert client.get(protected, base_url=base).status_code == 401


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("value", ["true", "invalid", ""])
def test_invalid_secure_cookie_setting_fails_closed(monkeypatch, tmp_path, role, value):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", value)
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE must be 0 or 1"):
        load_app(monkeypatch, tmp_path, role)


@pytest.mark.parametrize("secure,expected_scheme", [("0", "http"), ("1", "https")])
def test_portal_redirect_uses_only_gateway_scheme_header(
        monkeypatch, tmp_path, secure, expected_scheme):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", secure)
    monkeypatch.setenv("STAFF_PORT", "8444")
    mod = load_app(monkeypatch, tmp_path, "admin")
    response = mod.app.test_client().get("/portal/staff", headers={
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "untrusted.example",
        "X-Forwarded-Port": "9999",
    })
    assert response.status_code == 302
    assert response.headers["Location"] == f"{expected_scheme}://localhost:8444/"
    if secure == "1":
        assert mod.app.wsgi_app.x_proto == 1
        assert mod.app.wsgi_app.x_for == 0
        assert mod.app.wsgi_app.x_host == 0
        assert mod.app.wsgi_app.x_port == 0
        assert mod.app.wsgi_app.x_prefix == 0


def test_https_gateway_replaces_client_scheme_and_keeps_zone_rules():
    conf = (ROOT / "services/reverse-proxy/https.conf").read_text()
    for port in (8080, 8081, 8082):
        assert f"listen {port} ssl;" in conf
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in conf
    assert conf.count("proxy_set_header X-Forwarded-Proto $scheme;") == 6
    for zone in ("admin", "staff", "guest"):
        assert f"if (${zone}_ok = 0)" in conf


def auth_script():
    spec = importlib.util.spec_from_file_location(
        "https_auth_check", ROOT / "scripts/auth-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auth_checker_uses_verifying_tls_context():
    mod = auth_script()
    context = ssl.create_default_context()
    browser = mod.Browser("https://localhost:8443", context=context)
    handler = next(h for h in browser.opener.handlers if isinstance(h, HTTPSHandler))
    assert handler._context is context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize("args", [["--https"], ["--ca-file", "cert.pem"]])
def test_auth_checker_rejects_incomplete_https_options(monkeypatch, args):
    mod = auth_script()
    monkeypatch.setattr("sys.argv", ["auth-check.py", *args])
    with pytest.raises(SystemExit) as error:
        mod.main()
    assert error.value.code == 2
