"""Unit tests for the dashboard API. A fake Docker client is used, so no
Docker engine is needed (this also runs in GitHub Actions)."""
import importlib.util
import json
import os
import re
import sys
import uuid
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest
from docker.errors import NotFound

DASHBOARD_APP = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")
os.environ.setdefault("PORTAL_SECRET_KEY", "test-secret-key-0123456789")
os.environ.setdefault("ADMIN_USERS", "admin:AdminPass1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
import app as dash  # noqa: E402

HEADERS = {"X-Requested-With": "dashboard-ui"}


def token_of(client):
    html = client.get("/login").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def new_client():
    """A test client that is signed in as the admin."""
    c = dash.app.test_client()
    r = c.post("/login", data={"csrf_token": token_of(c), "username": "admin",
                               "password": "AdminPass1"})
    assert r.status_code == 302
    return c


STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 2_000_000},
                  "system_cpu_usage": 1_000_000_000, "online_cpus": 4},
    "precpu_stats": {"cpu_usage": {"total_usage": 1_000_000},
                     "system_cpu_usage": 900_000_000},
    "memory_stats": {"usage": 50 * 1048576, "limit": 128 * 1048576,
                     "stats": {"inactive_file": 10 * 1048576}},
    "networks": {"eth0": {"rx_bytes": 2 * 1048576, "tx_bytes": 1048576}},
}


def fake(name, status="running", project="netlab"):
    c = MagicMock()
    c.name, c.short_id, c.status = name, "abc123", status
    c.labels = {"com.docker.compose.project": project,
                "com.docker.compose.service": name}
    c.attrs = {
        "Config": {"Image": "img:1", "User": "101",
                   "ExposedPorts": {"8080/tcp": {}},
                   "Healthcheck": {"Interval": 10_000_000_000}},
        "HostConfig": {"Memory": 128 * 1048576, "NanoCpus": 500_000_000,
                       "CapDrop": ["ALL"], "ReadonlyRootfs": True,
                       "SecurityOpt": ["no-new-privileges:true"],
                       "RestartPolicy": {"Name": "unless-stopped"},
                       "PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "8080"}]}},
        "NetworkSettings": {"Networks": {"lab_net": {"IPAddress": "172.20.0.10"}},
                            "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"},
                                                   {"HostIp": "::", "HostPort": "8080"}]}},
        "State": {"Health": {"Status": "healthy"}, "StartedAt": "2026-10-10T09:00:00Z"},
        "RestartCount": 1,
    }
    c.logs.return_value = b"2026 line1\n2026 line2\n"
    c.stats.return_value = STATS
    return c


@pytest.fixture()
def client():
    web, dashboard, foreign = fake("nginx-web"), fake("dashboard"), fake("foreign", project="other")
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [web, dashboard]

    def get(name):
        found = {"nginx-web": web, "dashboard": dashboard, "foreign": foreign}
        if name in found:
            return found[name]
        raise NotFound("missing")

    docker_client.containers.get.side_effect = get
    dash._client = docker_client
    yield new_client(), web
    dash._client = None


def test_health_works_without_docker():
    dash._client = None
    assert dash.app.test_client().get("/api/health").json == {"status": "ok"}


def test_ui_page_is_served(client):
    r = client[0].get("/")
    assert r.status_code == 200 and b"NetLab Control" in r.data


def test_list_contains_new_fields(client):
    items = client[0].get("/api/containers").json
    web = next(i for i in items if i["name"] == "nginx-web")
    assert web["networks"] == {"lab_net": "172.20.0.10"}
    assert web["ports"] == [{"label": "8080\u21928080/tcp", "published": True}]
    assert web["mem_limit_mb"] == 128.0 and web["cpu_limit"] == 0.5
    assert web["health_interval_s"] == 10
    assert web["hardening"] == {"user": "101", "non_root": True, "cap_drop": ["ALL"],
                                "read_only": True, "no_new_privileges": True}
    assert next(i for i in items if i["name"] == "dashboard")["protected"] is True


def test_all_stats_in_one_call(client):
    d = client[0].get("/api/stats").json
    assert set(d["stats"]) == {"nginx-web", "dashboard"}
    s = d["stats"]["nginx-web"]
    assert s["running"] and s["cpu_percent"] == 4.0
    assert s["mem_used_mb"] == 40.0 and s["net_rx_bytes"] == 2 * 1048576


def test_stats_failure_is_isolated(client):
    client[1].stats.side_effect = KeyError("boom")
    d = client[0].get("/api/stats").json["stats"]
    assert d["nginx-web"]["running"] is False and d["dashboard"]["running"] is True


def test_logs_and_limits(client):
    r = client[0].get("/api/containers/nginx-web/logs?tail=2")
    assert r.json["lines"] == ["2026 line1", "2026 line2"]
    assert client[0].get("/api/containers/nginx-web/logs?tail=abc").status_code == 400


def test_control_needs_header(client):
    c, web = client
    assert c.post("/api/containers/nginx-web/restart").status_code == 403
    assert not web.restart.called
    assert c.post("/api/containers/nginx-web/restart", headers=HEADERS).status_code == 200
    assert web.restart.called


def test_dashboard_is_protected(client):
    c = client[0]
    assert c.post("/api/containers/dashboard/stop", headers=HEADERS).status_code == 403
    assert c.post("/api/containers/dashboard/restart", headers=HEADERS).status_code == 403


def test_unknown_action_and_foreign_container(client):
    c = client[0]
    assert c.post("/api/containers/nginx-web/delete", headers=HEADERS).status_code == 400
    assert c.get("/api/containers/foreign/stats").status_code == 404
    assert c.get("/api/containers/nope/stats").status_code == 404


def test_no_docker_access_gives_503():
    dash._client = None
    original = dash.docker.from_env

    def broken():
        raise dash.DockerException("no socket")

    dash.docker.from_env = broken
    try:
        assert new_client().get("/api/containers").status_code == 503
    finally:
        dash.docker.from_env = original


# ---------- /api/monitoring (Prometheus status card) ----------
def _reply(payload):
    r = MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = json.dumps(payload).encode()
    return r


def _fake_urlopen(targets=None, grafana=None, error=None):
    def opener(url, timeout=None):
        if error:
            raise error
        if "/api/v1/targets" in url:
            return _reply({"status": "success", "data": {"activeTargets": targets}})
        return _reply(grafana)
    return opener


def _monitoring(opener):
    with patch.object(dash, "urlopen", opener):
        return new_client().get("/api/monitoring").json


def test_monitoring_all_up():
    d = _monitoring(_fake_urlopen([{"health": "up"}, {"health": "up"}], {"database": "ok"}))
    assert d == {"ok": True, "prometheus": {"state": "up", "up": 2, "total": 2},
                 "grafana": {"state": "up"}}


def test_monitoring_degraded():
    d = _monitoring(_fake_urlopen([{"health": "up"}, {"health": "down"}], {"database": "bad"}))
    assert d["ok"] is False
    assert d["prometheus"] == {"state": "degraded", "up": 1, "total": 2}
    assert d["grafana"] == {"state": "degraded"}


def test_monitoring_zero_targets_is_degraded():
    d = _monitoring(_fake_urlopen([], {"database": "ok"}))
    assert d["prometheus"]["state"] == "degraded" and d["ok"] is False


def test_monitoring_unreachable_does_not_break_dashboard():
    d = _monitoring(_fake_urlopen(error=URLError("refused")))
    assert d["prometheus"]["state"] == "down" and d["grafana"]["state"] == "down"
    assert dash.app.test_client().get("/api/health").json == {"status": "ok"}


def test_monitoring_bad_reply_is_down():
    bad = MagicMock()
    bad.__enter__.return_value = bad
    bad.read.return_value = b"<html>not json</html>"
    d = _monitoring(lambda url, timeout=None: bad)
    assert d["prometheus"]["state"] == "down" and d["grafana"]["state"] == "down"


def test_monitoring_rejects_non_http_urls():
    with pytest.raises(ValueError):
        dash._fetch_json("file:///etc/passwd")


# ---------- Prometheus-backed chart history ----------
def test_metrics_history_returns_prometheus_samples(client):
    requests = []

    def opener(url, timeout=None):
        params = parse_qs(urlparse(url).query)
        requests.append((params, timeout))
        query = params["query"][0]
        if "container_cpu_usage_seconds_total" in query:
            value = "5"
        elif "container_memory_working_set_bytes" in query:
            value = "25"
        elif "container_network_receive_bytes_total" in query:
            value = "100"
        else:
            value = "50"
        return _reply({
            "status": "success",
            "data": {"resultType": "matrix", "result": [{
                "values": [[1000, value], [1015, str(float(value) + 1)]],
            }]},
        })

    with patch.object(dash, "urlopen", opener):
        response = client[0].get("/api/metrics/history?name=nginx-web&minutes=5")

    assert response.status_code == 200
    assert response.json["name"] == "nginx-web"
    assert [sample["t"] for sample in response.json["samples"]] == [1000.0, 1015.0]
    assert response.json["samples"][0] == {
        "t": 1000.0, "cpu": 5.0, "mem": 25.0, "rx": 100.0, "tx": 50.0,
    }
    assert len(requests) == 4
    assert all(params["step"] == ["15s"] and timeout == dash.HISTORY_TIMEOUT_S
               for params, timeout in requests)
    assert all('container_label_com_docker_compose_service="nginx-web"' in params["query"][0]
               for params, _ in requests)


def test_metrics_history_validates_parameters_and_requires_login():
    anonymous = dash.app.test_client()
    assert anonymous.get("/api/metrics/history?name=nginx-web").status_code == 401
    client = new_client()
    assert client.get("/api/metrics/history").status_code == 400
    assert client.get("/api/metrics/history?name=nginx-web&minutes=abc").status_code == 400
    assert client.get("/api/metrics/history?name=nginx-web&minutes=16").status_code == 400


def test_metrics_history_reports_prometheus_errors(client):
    def opener(url, timeout=None):
        return _reply({"status": "error", "error": "query failed"})

    with patch.object(dash, "urlopen", opener):
        response = client[0].get("/api/metrics/history?name=nginx-web")

    assert response.status_code == 503
    assert response.json["error"] == "historical metrics unavailable"


# ---------- admin sign-in ----------
def test_api_needs_an_admin_session():
    anonymous = dash.app.test_client()
    assert anonymous.get("/api/containers").status_code == 401
    assert anonymous.get("/api/stats").status_code == 401
    assert anonymous.get("/api/metrics/history?name=nginx-web").status_code == 401
    assert anonymous.get("/api/monitoring").status_code == 401
    assert anonymous.post("/api/containers/nginx-web/restart", headers=HEADERS).status_code == 401


def test_health_and_static_files_stay_open():
    anonymous = dash.app.test_client()
    assert anonymous.get("/api/health").json == {"status": "ok"}
    assert anonymous.get("/static/js/app.js").status_code == 200


def test_page_sends_anonymous_users_to_the_login_page():
    r = dash.app.test_client().get("/")
    assert r.status_code == 302 and r.headers["Location"] == "login"


def test_login_page_is_served():
    response = dash.app.test_client().get("/login")
    html = response.get_data(as_text=True)
    assert "Admin sign in" in html and 'name="csrf_token"' in html
    assert response.headers["Cache-Control"] == "no-store"


def test_login_rejects_bad_credentials_and_missing_csrf():
    c = dash.app.test_client()
    t = token_of(c)
    bad = c.post("/login", data={"csrf_token": t, "username": "admin", "password": "nope"})
    assert bad.status_code == 401
    unknown = c.post("/login", data={"csrf_token": t, "username": "mallory",
                                     "password": "AdminPass1"})
    assert unknown.status_code == 401
    assert c.post("/login", data={"username": "admin", "password": "AdminPass1"}).status_code == 400
    assert c.get("/api/containers").status_code == 401


def test_stale_login_form_gets_a_fresh_csrf_token():
    c = dash.app.test_client()
    response = c.post("/login", data={"csrf_token": "stale", "username": "admin",
                                      "password": "AdminPass1"})
    assert response.status_code == 400
    assert b"Please submit this refreshed form." in response.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
    assert c.post("/login", data={"csrf_token": token, "username": "admin",
                                  "password": "AdminPass1"}).status_code == 302


def test_login_gives_access_and_logout_removes_it(client):
    c, _ = client
    assert c.get_cookie("admin_session") is not None
    page = c.get("/")
    assert page.status_code == 200 and b"Signed in as" in page.data
    assert c.get("/api/containers").status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    stale = c.post("/logout", data={})
    assert stale.status_code == 400  # needs the form token
    assert b"please try again" in stale.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', stale.data).group(1).decode()
    assert c.post("/logout", data={"csrf_token": token}).status_code == 302
    assert c.get("/api/containers").status_code == 401


def test_stale_logout_form_is_not_accepted_and_returns_a_fresh_form(client):
    c, _ = client
    stale = c.post("/logout", data={"csrf_token": "stale"})
    assert stale.status_code == 400
    assert b"please try again" in stale.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', stale.data).group(1).decode()
    assert c.post("/logout", data={"csrf_token": token}).status_code == 302


def test_other_role_sessions_are_not_accepted(monkeypatch):
    """A cookie signed for another role (different key) must not open the dashboard."""
    from flask.sessions import SecureCookieSessionInterface
    import hashlib
    other = dash.Flask("other")
    other.config["SECRET_KEY"] = hashlib.sha256(b"test-secret-key-0123456789:staff").hexdigest()
    serializer = SecureCookieSessionInterface().get_signing_serializer(other)
    forged = serializer.dumps({"user": "admin", "role": "admin"})
    c = dash.app.test_client()
    c.set_cookie("admin_session", forged)
    assert c.get("/api/containers").status_code == 401


def load_dashboard(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    name = f"dashboard_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, DASHBOARD_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_dashboard_refuses_to_start_without_secret_or_admin(monkeypatch):
    with pytest.raises(RuntimeError):
        load_dashboard(monkeypatch, PORTAL_SECRET_KEY="short")
    with pytest.raises(RuntimeError):
        load_dashboard(monkeypatch, PORTAL_SECRET_KEY="test-secret-key-0123456789", ADMIN_USERS="")
