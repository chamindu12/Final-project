"""UI integration checks for asset policy, feedback and preserved access boundaries."""
import io
from pathlib import Path

import pytest

from test_portal import ZONE, load, login, page_token

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def ui(monkeypatch, tmp_path):
    return load(monkeypatch, "staff", "alice:StaffPass1", tmp_path)


@pytest.mark.parametrize("role", ["staff", "guest"])
def test_public_signin_and_local_assets(monkeypatch, tmp_path, role):
    mod = load(monkeypatch, role, "alice:StaffPass1", tmp_path / role)
    client = mod.app.test_client()
    response = client.get("/login")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'aria-controls="password"' in html
    assert '<style' not in html and '<script>' not in html
    assert 'src="/static/js/portal.js"' in html
    assert 'href="/static/css/netlab.css"' in html
    for path, mime in [("css/netlab.css", "text/css"), ("js/portal.js", "javascript")]:
        asset = client.get("/static/" + path)
        assert asset.status_code == 200
        assert mime in asset.content_type
        assert len(asset.data) > 200
    csp = response.headers["Content-Security-Policy"]
    assert "style-src 'self'" in csp and "script-src 'self'" in csp
    assert "connect-src 'self'" in csp and "'unsafe-inline'" not in csp
    assert client.get("/api/whoami").status_code == 401


@pytest.mark.parametrize("role", ["staff", "guest"])
def test_access_help_does_not_require_or_expose_session(monkeypatch, tmp_path, role):
    mod = load(monkeypatch, role, "alice:StaffPass1", tmp_path / role)
    response = mod.app.test_client().get("/help")
    assert response.status_code == 200
    assert b"No self-service password reset" in response.data
    assert b"StaffPass1" not in response.data
    if role == "staff":
        assert b"Share files without overwriting" in response.data


def test_ajax_upload_has_real_success_and_one_time_feedback(ui):
    client = ui.app.test_client()
    login(client, "alice", "StaffPass1")
    response = client.post("/files", headers={"Accept": "application/json"}, data={
        "csrf_token": page_token(client), "file": (io.BytesIO(b"complete file"), "brief.txt"),
    })
    assert response.status_code == 201
    assert response.json["redirect"] == "/#files"
    assert client.get("/files/brief.txt").data == b"complete file"
    html = client.get("/").get_data(as_text=True)
    assert "Your file was uploaded" in html
    assert html.index("Your file was uploaded") > html.index('id="files"')
    assert html.count("Your file was uploaded") == 1
    assert "Your file was uploaded" not in client.get("/").get_data(as_text=True)
    info = ui.list_files()[0]
    assert info["bytes"] == len(b"complete file")
    assert info["modified"].endswith("+00:00")
    assert "owner" not in info


@pytest.mark.parametrize("accept", ["text/html", "application/json"])
def test_duplicate_feedback_keeps_original_and_status(ui, accept):
    client = ui.app.test_client()
    login(client, "alice", "StaffPass1")
    for content, expected in [(b"original", 302), (b"replacement", 409)]:
        response = client.post("/files", headers={"Accept": accept}, data={
            "csrf_token": page_token(client), "file": (io.BytesIO(content), "same.txt"),
        })
        assert response.status_code == (201 if expected == 302 and "json" in accept else expected)
    assert client.get("/files/same.txt").data == b"original"
    if "json" in accept:
        assert "Rename your file" in response.json["error"]
    else:
        assert b"NetLab" in response.data and b"Your existing file is safe" in response.data


def test_ajax_login_expiry_does_not_save_upload(ui):
    client = ui.app.test_client()
    login(client, "alice", "StaffPass1")
    csrf = page_token(client)
    with client.session_transaction() as session:
        sid = session["sid"]
    ui.auth_sessions.revoke(sid)
    response = client.post("/files", headers={"Accept": "application/json"}, data={
        "csrf_token": csrf, "file": (io.BytesIO(b"not saved"), "expired.txt"),
    })
    assert response.status_code == 401
    assert not (ui.FILES_DIR / "expired.txt").exists()


@pytest.mark.parametrize("status,path", [(400, "/login"), (404, "/missing-page")])
def test_html_errors_and_json_errors_keep_status(ui, status, path):
    client = ui.app.test_client()
    for accept in ["text/html", "application/json"]:
        response = (client.post(path, data={}, headers={"Accept": accept}) if status == 400
                    else client.get(path, headers={"Accept": accept}))
        assert response.status_code == status
        assert response.headers["Cache-Control"] == "no-store"
        if accept == "application/json":
            assert "error" in response.json
        else:
            assert b"Return to portal" in response.data and b"NetLab" in response.data


def test_large_ajax_upload_has_json_413(monkeypatch, tmp_path):
    mod = load(monkeypatch, "staff", "alice:StaffPass1", tmp_path, MAX_UPLOAD_MB="1")
    client = mod.app.test_client()
    login(client, "alice", "StaffPass1")
    response = client.post("/files", headers={"Accept": "application/json"}, data={
        "csrf_token": page_token(client),
        "file": (io.BytesIO(b"x" * 2 * 1024 * 1024), "large.bin"),
    })
    assert response.status_code == 413 and "error" in response.json
    assert mod.list_files() == []


def test_notes_and_empty_notes_have_feedback(ui):
    client = ui.app.test_client()
    login(client, "alice", "StaffPass1")
    for text, message in [("An update", "Your note was posted"),
                          ("   ", "Write a note before posting")]:
        response = client.post("/notes", data={"csrf_token": page_token(client), "text": text})
        assert response.status_code == 302
        assert response.headers["Location"] == "/#notices"
        assert message in client.get("/").get_data(as_text=True)
    assert len(ui.load_notes()) == 1


@pytest.mark.parametrize("secure,scheme,label", [("0", "http", "HTTP connection"),
                                                 ("1", "https", "HTTPS connection")])
def test_connection_label_does_not_invent_tls(monkeypatch, tmp_path, secure, scheme, label):
    mod = load(monkeypatch, "staff", "alice:StaffPass1", tmp_path, SESSION_COOKIE_SECURE=secure)
    client = mod.app.test_client()
    base = scheme + "://localhost"
    response = client.get("/login", base_url=base)
    import re
    csrf = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
    client.post("/login", base_url=base, data={
        "csrf_token": csrf, "username": "alice", "password": "StaffPass1"})
    html = client.get("/", base_url=base).get_data(as_text=True)
    assert label in html
    assert ("HTTPS connection" in html) is (secure == "1")


def test_guest_cannot_gain_staff_tools_with_ui_parameters(monkeypatch, tmp_path):
    mod = load(monkeypatch, "guest", "alice:StaffPass1", tmp_path)
    client = mod.app.test_client()
    login(client, "alice", "StaffPass1")
    html = client.get("/?role=staff&can_write=1", headers=ZONE).get_data(as_text=True)
    assert "Shared files" not in html and 'action="/files"' not in html
    assert "Download 1 MiB" in html and "50 MiB</a>" not in html
    assert client.post("/files", data={"csrf_token": page_token(client)}).status_code == 404


def test_assets_are_packaged_and_shared_styles_match():
    css = (ROOT / "portal/static/css/netlab.css").read_bytes()
    assert css == (ROOT / "services/nginx-web/html/assets/netlab.css").read_bytes()
    assert "COPY static static" in (ROOT / "portal/Dockerfile").read_text()
    landing = (ROOT / "services/nginx-web/html/index.html").read_text()
    assert '<style' not in landing
    assert landing.count("sign in</a>") == 3  # Existing scenario-check contract.
    assert 'href="/web/assets/netlab.css"' in landing


def test_gateway_error_assets_exist_in_http_and_https_modes():
    assert "./services/reverse-proxy/error-pages:/etc/nginx/netlab-errors:ro" in (
        ROOT / "docker-compose.yml").read_text()
    for config in ["default.conf", "https.conf"]:
        text = (ROOT / "services/reverse-proxy" / config).read_text()
        assert text.count("alias /etc/nginx/netlab-errors/;") == 3
        assert text.count("        internal;") == 3
        for code in [413, 429, 502, 503, 504]:
            assert text.count(f"error_page {code} /__netlab_errors/{code}.html;") == 3
            html = (ROOT / f"services/reverse-proxy/error-pages/{code}.html").read_text()
            assert f"{code}" in html and "NetLab" in html
            assert "<script" not in html
