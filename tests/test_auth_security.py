"""resolved container identity and revocable role sessions."""
import importlib.util
import io
import re
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docker.errors import NotFound

ROOT = Path(__file__).resolve().parents[1]
SECRET = "step1-test-secret-0123456789abcdef"
ACCOUNTS = {"admin": ("admin", "AdminPass1"), "staff": ("alice", "StaffPass1"),
            "guest": ("visitor", "GuestPass1")}
HEADERS = {"X-Requested-With": "dashboard-ui"}


def load_app(monkeypatch, root, role, db_path=None):
    folder = "dashboard" if role == "admin" else "portal"
    name, password = ACCOUNTS[role]
    monkeypatch.setenv("PORTAL_SECRET_KEY", SECRET)
    monkeypatch.setenv("PORTAL_ROLE", role if role != "admin" else "guest")
    monkeypatch.setenv("ADMIN_USERS", f"{name}:{password}")
    monkeypatch.setenv("PORTAL_USERS", f"{name}:{password}")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "28800")
    monkeypatch.setenv("DATA_DIR", str(root / "data"))
    monkeypatch.setenv("SESSION_DB_PATH", str(db_path or root / "sessions.sqlite3"))
    monkeypatch.syspath_prepend(str(ROOT / folder))
    mod_name = f"step1_{role}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / folder / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module


def token_from(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, response.data[:200]
    return match.group(1).decode()


def sign_in(client, role):
    token = token_from(client.get("/login"))
    user, password = ACCOUNTS[role]
    response = client.post("/login", data={"username": user, "password": password,
                                           "csrf_token": token})
    assert response.status_code == 302
    return token


def protected_path(role):
    return "/api/containers" if role == "admin" else "/api/whoami"


def deny_request(client, role):
    assert client.get(protected_path(role)).status_code == 401


@pytest.mark.parametrize("role", ACCOUNTS)
def test_login_rotates_token_and_logout_revokes_copied_cookie(monkeypatch, tmp_path, role):
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    pre_login_token = sign_in(client, role)
    page = client.get("/")
    assert page.status_code == 200
    current_token = token_from(page)
    assert current_token != pre_login_token
    cookie = client.get_cookie(f"{role}_session").value
    with client.session_transaction() as session:
        assert session["user"] == ACCOUNTS[role][0]
        assert session["role"] == role and session["sid"]
    stale = client.post("/logout", data={"csrf_token": pre_login_token})
    assert stale.status_code == 400
    # Rejecting an old form must not silently log the user out.
    assert client.get("/").status_code == 200
    current_token = token_from(client.get("/"))
    assert client.post("/logout", data={"csrf_token": current_token}).status_code == 302
    deny_request(client, role)
    replay = mod.app.test_client()
    replay.set_cookie(f"{role}_session", cookie)
    deny_request(replay, role)
    # Replay cannot mutate data even if the old CSRF token was also copied.
    if role == "staff":
        response = replay.post("/notes", data={"csrf_token": current_token, "text": "bad"})
        assert response.status_code in (400, 302, 401)
        assert mod.load_notes() == []


@pytest.mark.parametrize("role", ACCOUNTS)
def test_absolute_expiry_rejects_session_even_after_recent_activity(monkeypatch, tmp_path, role):
    mod = load_app(monkeypatch, tmp_path, role)
    now = mod.auth_sessions.clock()
    monkeypatch.setattr(mod.auth_sessions, "clock", lambda: now)
    client = mod.app.test_client()
    sign_in(client, role)
    monkeypatch.setattr(mod.auth_sessions, "clock", lambda: now + 28799)
    assert client.get("/").status_code == 200
    monkeypatch.setattr(mod.auth_sessions, "clock", lambda: now + 28800)
    deny_request(client, role)
    assert mod.app.config["SESSION_REFRESH_EACH_REQUEST"] is False


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("change", ["removed", "password_changed"])
def test_removed_or_changed_credentials_revoke_old_session(monkeypatch, tmp_path, role, change):
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    sign_in(client, role)
    users = mod.ADMIN_USERS if role == "admin" else mod.USERS
    user = ACCOUNTS[role][0]
    if change == "removed":
        users.pop(user)
    else:
        users[user] = "NewPassword"
    deny_request(client, role)


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("cookie_value", ["broken", "tampered", "signed_without_sid"])
def test_bad_cookie_is_denied(monkeypatch, tmp_path, role, cookie_value):
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    if cookie_value == "tampered":
        sign_in(client, role)
        cookie_value = client.get_cookie(f"{role}_session").value + "x"
    elif cookie_value == "signed_without_sid":
        serializer = mod.app.session_interface.get_signing_serializer(mod.app)
        cookie_value = serializer.dumps({"user": ACCOUNTS[role][0], "role": role})
    client.set_cookie(f"{role}_session", cookie_value)
    deny_request(client, role)


@pytest.mark.parametrize("source,target", [
    (a, b) for a in ACCOUNTS for b in ACCOUNTS if a != b
])
def test_full_role_cookie_matrix(monkeypatch, tmp_path, source, target):
    source_mod = load_app(monkeypatch, tmp_path / source, source)
    src = source_mod.app.test_client()
    sign_in(src, source)
    copied = src.get_cookie(f"{source}_session").value
    target_mod = load_app(monkeypatch, tmp_path / target, target)
    dst = target_mod.app.test_client()
    dst.set_cookie(f"{target}_session", copied)
    deny_request(dst, target)


@pytest.mark.parametrize("role", ACCOUNTS)
def test_logout_revocation_shared_between_workers_and_restarts(monkeypatch, tmp_path, role):
    db = tmp_path / "shared.sqlite3"
    first = load_app(monkeypatch, tmp_path / "first", role, db)
    second = load_app(monkeypatch, tmp_path / "second", role, db)
    a = first.app.test_client()
    sign_in(a, role)
    cookie = a.get_cookie(f"{role}_session").value
    b = second.app.test_client()
    b.set_cookie(f"{role}_session", cookie)
    assert b.get("/").status_code == 200
    assert b.post("/logout", data={"csrf_token": token_from(b.get("/"))}).status_code == 302
    deny_request(a, role)
    # A restarted process must not resurrect a revoked session.
    third = load_app(monkeypatch, tmp_path / "third", role, db)
    c = third.app.test_client()
    c.set_cookie(f"{role}_session", cookie)
    deny_request(c, role)


@pytest.mark.parametrize("role", ACCOUNTS)
def test_missing_wrong_and_unicode_csrf_preserve_login(monkeypatch, tmp_path, role):
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    sign_in(client, role)
    for sent in (None, "wrong", "සිංහල"):
        data = {} if sent is None else {"csrf_token": sent}
        assert client.post("/logout", data=data).status_code == 400
        assert client.get("/").status_code == 200


@pytest.mark.parametrize("path", ["/notes", "/files"])
def test_anonymous_with_valid_csrf_cannot_write(monkeypatch, tmp_path, path):
    mod = load_app(monkeypatch, tmp_path, "staff")
    client = mod.app.test_client()
    token = token_from(client.get("/login"))
    data = {"csrf_token": token, "text": "must not be saved"}
    if path == "/files":
        data["file"] = (io.BytesIO(b"no access"), "anonymous.txt")
    response = client.post(path, data=data)
    assert response.status_code == (401 if path == "/files" else 302)
    if path == "/notes":
        assert response.headers["Location"].endswith("/login")
    assert mod.load_notes() == []
    assert not (mod.FILES_DIR / "anonymous.txt").exists()


def test_anonymous_cannot_download_existing_staff_file(monkeypatch, tmp_path):
    mod = load_app(monkeypatch, tmp_path, "staff")
    (mod.FILES_DIR / "private.txt").write_text("private")
    assert mod.app.test_client().get("/files/private.txt").status_code == 401


@pytest.mark.parametrize("action", ["stop", "restart"])
@pytest.mark.parametrize("reference", ["dashboard", "a1b2c3", "a1b2c3" * 10 + "abcd"])
def test_dashboard_protected_for_name_and_ids(monkeypatch, tmp_path, action, reference):
    mod = load_app(monkeypatch, tmp_path, "admin")
    container = MagicMock()
    container.name = "dashboard"
    container.labels = {"com.docker.compose.project": "netlab",
                        "com.docker.compose.service": "dashboard"}
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    mod._client = docker_client
    client = mod.app.test_client()
    sign_in(client, "admin")
    response = client.post(f"/api/containers/{reference}/{action}", headers=HEADERS)
    assert response.status_code == 403
    container.stop.assert_not_called()
    container.restart.assert_not_called()


def test_renamed_dashboard_service_is_protected(monkeypatch, tmp_path):
    mod = load_app(monkeypatch, tmp_path, "admin")
    container = MagicMock()
    container.name = "renamed-admin"
    container.labels = {"com.docker.compose.project": "netlab",
                        "com.docker.compose.service": "dashboard"}
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    mod._client = docker_client
    client = mod.app.test_client()
    sign_in(client, "admin")
    assert client.post("/api/containers/renamed-admin/stop", headers=HEADERS).status_code == 403
    container.stop.assert_not_called()


def test_foreign_container_cannot_be_controlled_by_id(monkeypatch, tmp_path):
    mod = load_app(monkeypatch, tmp_path, "admin")
    container = MagicMock()
    container.name = "foreign"
    container.labels = {"com.docker.compose.project": "another-project"}
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    mod._client = docker_client
    client = mod.app.test_client()
    sign_in(client, "admin")
    assert client.post("/api/containers/foreign-id/stop", headers=HEADERS).status_code == 404
    container.stop.assert_not_called()


@pytest.mark.parametrize("role", ACCOUNTS)
def test_cookie_flags_and_session_store_failure(monkeypatch, tmp_path, role):
    mod = load_app(monkeypatch, tmp_path, role)
    client = mod.app.test_client()
    sign_in(client, role)
    cookie = client.get_cookie(f"{role}_session")
    assert cookie.http_only and cookie.same_site == "Lax"

    def unavailable(*args):
        raise sqlite3.OperationalError("test database unavailable")

    monkeypatch.setattr(mod.auth_sessions, "valid", unavailable)
    assert client.get(protected_path(role)).status_code == 503
    assert client.get("/api/health").status_code == 200


def test_concurrent_sessions_and_database_contains_no_raw_secrets(monkeypatch, tmp_path):
    mod = load_app(monkeypatch, tmp_path, "admin")
    store = mod.auth_sessions
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: store.create("admin", "AdminPass1", 28800), range(12)))
    assert len(set(tokens)) == 12
    for token in tokens:
        assert store.valid(token, "admin", "AdminPass1")
    with store.connection() as db:
        rows = repr(db.execute("SELECT * FROM login_sessions").fetchall())
    assert "AdminPass1" not in rows
    assert all(token not in rows for token in tokens)


def test_unknown_container_cannot_trigger_control(monkeypatch, tmp_path):
    mod = load_app(monkeypatch, tmp_path, "admin")
    mod._client = MagicMock()
    mod._client.containers.get.side_effect = NotFound("missing")
    client = mod.app.test_client()
    sign_in(client, "admin")
    assert client.post("/api/containers/unknown/stop", headers=HEADERS).status_code == 404
