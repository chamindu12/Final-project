"""Tests for the staff/guest portal (no Docker needed)."""
import importlib.util
import io
import os
import re
import sys
import uuid

import pytest

PORTAL_APP = os.path.join(os.path.dirname(__file__), "..", "portal", "app.py")
SECRET = "test-secret-key-0123456789"
ZONE = {"X-Real-IP": "172.22.0.10", "X-Zone": "staff"}


def load(monkeypatch, role, users, data_dir, **extra):
    """Import portal/app.py as its own module, configured for one role."""
    monkeypatch.setenv("PORTAL_ROLE", role)
    monkeypatch.setenv("PORTAL_SECRET_KEY", SECRET)
    monkeypatch.setenv("PORTAL_USERS", users)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    name = f"portal_{role}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, PORTAL_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # Flask finds the templates folder through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def staff(monkeypatch, tmp_path):
    return load(monkeypatch, "staff", "alice:StaffPass1,bob:StaffPass2", tmp_path).app.test_client()


@pytest.fixture()
def guest(monkeypatch, tmp_path):
    return load(monkeypatch, "guest", "visitor:GuestPass1", tmp_path / "g").app.test_client()


def token(client):
    html = client.get("/login", headers=ZONE).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, user, password, headers=ZONE):
    return client.post("/login", headers=headers, data={
        "csrf_token": token(client), "username": user, "password": password})


def page_token(client):
    return re.search(r'name="csrf_token" value="([^"]+)"',
                     client.get("/", headers=ZONE).get_data(as_text=True)).group(1)


def test_health_needs_no_login(staff, guest):
    assert staff.get("/api/health").json == {"status": "ok", "role": "staff"}
    assert guest.get("/api/health").json == {"status": "ok", "role": "guest"}


def test_login_page_shows_zone(staff):
    html = staff.get("/login", headers=ZONE).get_data(as_text=True)
    assert "172.22.0.10" in html and "zone staff" in html


def test_anonymous_is_sent_to_login(staff):
    r = staff.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    assert staff.get("/api/whoami").status_code == 401


def test_login_success_and_whoami(staff):
    assert login(staff, "alice", "StaffPass1").status_code == 302
    d = staff.get("/api/whoami", headers=ZONE).json
    assert d == {"user": "alice", "role": "staff", "client_ip": "172.22.0.10", "zone": "staff"}


def test_wrong_password_and_unknown_user(staff):
    assert login(staff, "alice", "nope").status_code == 401
    assert login(staff, "mallory", "StaffPass1").status_code == 401
    assert staff.get("/api/whoami").status_code == 401


def test_post_without_csrf_is_rejected(staff):
    r = staff.post("/login", data={"username": "alice", "password": "StaffPass1"})
    assert r.status_code == 400


def test_cookie_names_are_per_role(staff, guest):
    login(staff, "alice", "StaffPass1")
    login(guest, "visitor", "GuestPass1")
    assert staff.get_cookie("staff_session") is not None
    assert guest.get_cookie("guest_session") is not None


def test_accounts_are_separate_per_portal(staff, guest):
    assert login(guest, "alice", "StaffPass1").status_code == 401   # staff account on guest portal
    assert login(staff, "visitor", "GuestPass1").status_code == 401  # guest account on staff portal


def test_staff_session_is_not_valid_on_guest_portal(staff, guest):
    login(staff, "alice", "StaffPass1")
    stolen = staff.get_cookie("staff_session").value
    guest.set_cookie("guest_session", stolen)
    assert guest.get("/api/whoami").status_code == 401


def test_logout_clears_session(staff):
    login(staff, "alice", "StaffPass1")
    r = staff.post("/logout", data={"csrf_token": page_token(staff)})
    assert r.status_code == 302
    assert staff.get("/api/whoami").status_code == 401


def test_staff_notes_are_escaped(staff):
    login(staff, "alice", "StaffPass1")
    note = "<script>alert(1)</script>"
    staff.post("/notes", data={"csrf_token": page_token(staff), "text": note})
    html = staff.get("/", headers=ZONE).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_staff_upload_list_download(staff):
    login(staff, "alice", "StaffPass1")
    r = staff.post("/files", data={"csrf_token": page_token(staff),
                                   "file": (io.BytesIO(b"hello staff"), "report one.txt")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    assert "report_one.txt" in staff.get("/", headers=ZONE).get_data(as_text=True)
    d = staff.get("/files/report_one.txt")
    assert d.status_code == 200 and d.data == b"hello staff"
    assert "attachment" in d.headers["Content-Disposition"]


def test_download_cannot_escape_the_files_folder(staff):
    login(staff, "alice", "StaffPass1")
    assert staff.get("/files/..%2f..%2fnotes.json").status_code == 404
    assert staff.get("/files/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code == 404


def test_upload_needs_login(staff):
    r = staff.post("/files", data={"csrf_token": "x", "file": (io.BytesIO(b"x"), "a.txt")},
                   content_type="multipart/form-data")
    assert r.status_code == 400  # rejected by CSRF before anything is saved


def test_guest_cannot_write(guest):
    login(guest, "visitor", "GuestPass1")
    t = page_token(guest)
    assert guest.post("/notes", data={"csrf_token": t, "text": "hi"}).status_code == 404
    assert guest.post("/files", data={"csrf_token": t}).status_code == 404
    html = guest.get("/", headers=ZONE).get_data(as_text=True)
    assert "Announcements" in html and "Shared files" not in html


def test_guest_guide_download(staff, guest):
    assert guest.get("/guide.txt").status_code == 401
    login(guest, "visitor", "GuestPass1")
    assert b"visitor guide" in guest.get("/guide.txt").data
    login(staff, "alice", "StaffPass1")
    assert staff.get("/guide.txt").status_code == 404


def test_speedtest_limits_depend_on_role(staff, guest):
    assert staff.get("/speedtest?mb=1").status_code == 401
    login(staff, "alice", "StaffPass1")
    login(guest, "visitor", "GuestPass1")
    r = staff.get("/speedtest?mb=5")
    assert r.status_code == 200 and len(r.data) == 5 * 1024 * 1024
    assert staff.get("/speedtest?mb=51").status_code == 403
    assert guest.get("/speedtest?mb=1").status_code == 200
    assert guest.get("/speedtest?mb=2").status_code == 403
    assert guest.get("/speedtest?mb=abc").status_code == 400


def test_security_headers(staff):
    r = staff.get("/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_upload_size_limit(monkeypatch, tmp_path):
    portal = load(monkeypatch, "staff", "alice:StaffPass1", tmp_path, MAX_UPLOAD_MB="1")
    client = portal.app.test_client()
    login(client, "alice", "StaffPass1")
    big = io.BytesIO(b"x" * (2 * 1024 * 1024))
    r = client.post("/files", data={"csrf_token": page_token(client), "file": (big, "big.bin")},
                    content_type="multipart/form-data")
    assert r.status_code == 413


def test_refuses_to_start_without_secret_or_users(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        load(monkeypatch, "staff", "a:b", tmp_path, PORTAL_SECRET_KEY="short")
    with pytest.raises(RuntimeError):
        load(monkeypatch, "staff", "", tmp_path)
    with pytest.raises(RuntimeError):
        load(monkeypatch, "admin", "a:b", tmp_path)
