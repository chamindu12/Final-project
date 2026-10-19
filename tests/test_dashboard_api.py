"""Unit tests for the dashboard API. A fake Docker client is used, so no
Docker engine is needed (this also runs in GitHub Actions)."""
import json
import os
import sys
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest
from docker.errors import NotFound

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
import app as dash  # noqa: E402

HEADERS = {"X-Requested-With": "dashboard-ui"}

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
    yield dash.app.test_client(), web
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
        assert dash.app.test_client().get("/api/containers").status_code == 503
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
        return dash.app.test_client().get("/api/monitoring").json


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
        