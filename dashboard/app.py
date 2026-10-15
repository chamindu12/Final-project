import os
import time
from concurrent.futures import ThreadPoolExecutor

import docker
from docker.errors import DockerException, NotFound
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

PROJECT = os.environ.get("COMPOSE_PROJECT", "netlab")
PROTECTED = {"dashboard"}  # the dashboard must not stop or restart itself
MAX_LOG_LINES = 500
MB = 1024 * 1024

# Control requests must carry this header. Browsers do not let other
# websites add it to a cross-site request, which blocks CSRF.
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "dashboard-ui"

_client = None


def get_client():
    """Connect lazily so the app still starts (and /api/health works)
    if the Docker socket is unreachable."""
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def project_containers():
    return get_client().containers.list(
        all=True, filters={"label": f"com.docker.compose.project={PROJECT}"}
    )


def get_container(name):
    """Return a container only if it belongs to this compose project."""
    c = get_client().containers.get(name)
    if c.labels.get("com.docker.compose.project") != PROJECT:
        raise NotFound("container is not part of this project")
    return c


def port_list(c):
    cfg = c.attrs.get("Config", {})
    host_cfg = c.attrs.get("HostConfig", {})
    live = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
    configured = host_cfg.get("PortBindings") or {}
    exposed = cfg.get("ExposedPorts") or {}
    result = []
    for port in sorted(set(exposed) | set(live) | set(configured)):
        bindings = live.get(port) or configured.get(port) or []
        seen = set()
        for b in bindings:
            host_port = b.get("HostPort")
            host_ip = b.get("HostIp", "")
            if host_port in seen:
                continue
            seen.add(host_port)
            host = host_port
            if host_ip not in ("", "0.0.0.0", "::"):
                host = f"{host_ip}:{host_port}"
            result.append({"label": f"{host}\u2192{port}", "published": True})
        if not bindings:
            result.append({"label": f"{port} (internal)", "published": False})
    return result


def hardening(c):
    cfg = c.attrs.get("Config", {})
    host_cfg = c.attrs.get("HostConfig", {})
    user = cfg.get("User") or ""
    uid = user.split(":")[0]
    sec_opts = host_cfg.get("SecurityOpt") or []
    return {
        "user": user or "root",
        "non_root": uid not in ("", "0", "root"),
        "cap_drop": host_cfg.get("CapDrop") or [],
        "read_only": bool(host_cfg.get("ReadonlyRootfs")),
        "no_new_privileges": any(
            o.startswith("no-new-privileges") and not o.endswith("false")
            for o in sec_opts
        ),
    }


def summary(c):
    state = c.attrs.get("State", {})
    cfg = c.attrs.get("Config", {})
    host_cfg = c.attrs.get("HostConfig", {})
    nets = c.attrs.get("NetworkSettings", {}).get("Networks") or {}
    mem = host_cfg.get("Memory") or 0
    nano = host_cfg.get("NanoCpus") or 0
    interval = (cfg.get("Healthcheck") or {}).get("Interval") or 0
    return {
        "name": c.name,
        "id": c.short_id,
        "service": c.labels.get("com.docker.compose.service"),
        "image": cfg.get("Image"),
        "status": c.status,
        "health": state.get("Health", {}).get("Status", "none"),
        "restart_count": c.attrs.get("RestartCount", 0),
        "started_at": state.get("StartedAt"),
        "protected": c.name in PROTECTED,
        "networks": {n: v.get("IPAddress", "") for n, v in nets.items()
                     if v.get("IPAddress")},
        "ports": port_list(c),
        "restart_policy": host_cfg.get("RestartPolicy", {}).get("Name") or "no",
        "mem_limit_mb": round(mem / MB, 1) if mem else None,
        "cpu_limit": round(nano / 1e9, 2) if nano else None,
        "health_interval_s": round(interval / 1e9) if interval else None,
        "hardening": hardening(c),
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
        "net_rx_bytes": rx,
        "net_tx_bytes": tx,
    }


def stats_for(c):
    """Stats for one container; never raises, so one failure cannot
    break the whole /api/stats reply."""
    try:
        if c.status != "running":
            return {"running": False}
        return {"running": True, **parse_stats(c.stats(stream=False))}
    except (DockerException, KeyError, TypeError, ValueError):
        return {"running": False, "error": "stats unavailable"}


@app.after_request
def no_store(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(NotFound)
def handle_not_found(e):
    return jsonify({"error": "container not found"}), 404


@app.errorhandler(DockerException)
def handle_docker_error(e):
    return jsonify({"error": "docker engine error", "detail": str(e)}), 503


@app.get("/")
def index():
    return render_template("index.html", project=PROJECT)


@app.get("/api")
def api_index():
    return jsonify({
        "service": "dashboard-api",
        "endpoints": [
            "GET /api/health",
            "GET /api/containers",
            "GET /api/stats",
            "GET /api/containers/<name>/stats",
            "GET /api/containers/<name>/logs?tail=100",
            "POST /api/containers/<name>/start|stop|restart "
            "(needs header X-Requested-With: dashboard-ui)",
        ],
    })


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/containers")
def list_containers():
    items = project_containers()
    return jsonify(sorted((summary(c) for c in items), key=lambda x: x["name"]))


@app.get("/api/stats")
def all_stats():
    """Stats of every running project container, fetched in parallel."""
    items = project_containers()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(stats_for, items))
    return jsonify({
        "ts": time.time(),
        "stats": {c.name: r for c, r in zip(items, results)},
    })


@app.get("/api/containers/<name>/stats")
def container_stats(name):
    c = get_container(name)
    if c.status != "running":
        return jsonify({"name": name, "running": False})
    data = parse_stats(c.stats(stream=False))
    return jsonify({"name": name, "running": True, "ts": time.time(), **data})


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
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return jsonify({"error": f"missing header {CSRF_HEADER}: {CSRF_VALUE}"}), 403
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
