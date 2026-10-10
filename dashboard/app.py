import os

import docker
from docker.errors import DockerException, NotFound
from flask import Flask, jsonify, request

app = Flask(__name__)

PROJECT = os.environ.get("COMPOSE_PROJECT", "netlab")
PROTECTED = {"dashboard"}  # the dashboard must not stop or restart itself
MAX_LOG_LINES = 500
MB = 1024 * 1024

_client = None


def get_client():
    """Connect lazily so the app still starts (and /api/health works) if the socket is unreachable."""
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get_container(name):
    """Return a container only if it belongs to this compose project."""
    c = get_client().containers.get(name)
    if c.labels.get("com.docker.compose.project") != PROJECT:
        raise NotFound("container is not part of this project")
    return c


def summary(c):
    state = c.attrs.get("State", {})
    return {
        "name": c.name,
        "id": c.short_id,
        "service": c.labels.get("com.docker.compose.service"),
        "image": c.attrs["Config"]["Image"],
        "status": c.status,
        "health": state.get("Health", {}).get("Status", "none"),
        "restart_count": c.attrs.get("RestartCount", 0),
        "started_at": state.get("StartedAt"),
    }


def parse_stats(s):
    cpu = s["cpu_stats"]
    pre = s["precpu_stats"]
    cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
    cpu_pct = 0.0
    if sys_delta > 0 and cpu_delta >= 0:
        cpu_pct = cpu_delta / sys_delta * ncpu * 100.0

    mem = s.get("memory_stats", {})
    mstats = mem.get("stats", {})
    cache = mstats.get("inactive_file", mstats.get("total_inactive_file", 0))
    used = max(mem.get("usage", 0) - cache, 0)
    limit = mem.get("limit", 0)
    mem_pct = used / limit * 100.0 if limit else 0.0

    nets = s.get("networks", {}) or {}
    rx = sum(n.get("rx_bytes", 0) for n in nets.values())
    tx = sum(n.get("tx_bytes", 0) for n in nets.values())

    return {
        "cpu_percent": round(cpu_pct, 2),
        "mem_used_mb": round(used / MB, 1),
        "mem_limit_mb": round(limit / MB, 1),
        "mem_percent": round(mem_pct, 2),
        "net_rx_mb": round(rx / MB, 3),
        "net_tx_mb": round(tx / MB, 3),
    }


@app.errorhandler(NotFound)
def handle_not_found(e):
    return jsonify({"error": "container not found"}), 404


@app.errorhandler(DockerException)
def handle_docker_error(e):
    return jsonify({"error": "docker engine error", "detail": str(e)}), 503


@app.get("/")
def index():
    return jsonify({
        "service": "dashboard-api",
        "endpoints": [
            "GET /api/health",
            "GET /api/containers",
            "GET /api/containers/<name>/stats",
            "GET /api/containers/<name>/logs?tail=100",
            "POST /api/containers/<name>/start|stop|restart",
        ],
    })


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/containers")
def list_containers():
    items = get_client().containers.list(
        all=True, filters={"label": f"com.docker.compose.project={PROJECT}"}
    )
    return jsonify(sorted((summary(c) for c in items), key=lambda x: x["name"]))


@app.get("/api/containers/<name>/stats")
def container_stats(name):
    c = get_container(name)
    if c.status != "running":
        return jsonify({"name": name, "running": False})
    data = parse_stats(c.stats(stream=False))
    return jsonify({"name": name, "running": True, **data})


@app.get("/api/containers/<name>/logs")
def container_logs(name):
    c = get_container(name)
    try:
        tail = int(request.args.get("tail", 100))
    except ValueError:
        return jsonify({"error": "tail must be a number"}), 400
    tail = max(1, min(tail, MAX_LOG_LINES))
    raw = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
    return jsonify({"name": name, "lines": raw.splitlines()})


@app.post("/api/containers/<name>/<action>")
def container_action(name, action):
    actions = {
        "start": lambda c: c.start(),
        "stop": lambda c: c.stop(timeout=10),
        "restart": lambda c: c.restart(timeout=10),
    }
    if action not in actions:
        return jsonify({"error": "action must be start, stop or restart"}), 400
    if name in PROTECTED and action != "start":
        return jsonify({"error": f"{name} is protected; {action} is not allowed"}), 403
    c = get_container(name)
    actions[action](c)
    c.reload()
    return jsonify(summary(c))