(() => {
  "use strict";

  // Relative URLs so the page also works under the proxy path /dashboard/
  const API = "api";
  const CSRF = { "X-Requested-With": "dashboard-ui" };
  const LIST_MS = 3000;
  const STATS_GAP_MS = 1000;
  const LOG_MS = 2000;
  const MONITORING_MS = 10000;
  const HISTORY_REFRESH_MS = 15000;
  const MAX_CONTIGUOUS_GAP_MS = 10000;
  const HISTORY_GAP_MS = 30000;
  const DEFAULT_HISTORY_MINUTES = 5;
  const PAST = { start: "started", stop: "stopped", restart: "restarted" };

  const state = {
    containers: [],
    stats: {},
    rates: {},
    lastNet: {},
    restartMeta: {},
    history: {},
    monitoring: null,
    selected: null,
    pending: new Set(),
    lastOk: 0,
    engineOk: null,
    logLines: [],
  };

  const $ = (id) => document.getElementById(id);
  const enc = encodeURIComponent;
  const esc = (v) =>
    String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));

  async function getJSON(url, opts) {
    const res = await fetch(url, Object.assign({ cache: "no-store" }, opts || {}));
    let body = null;
    try { body = await res.json(); } catch (_) { /* reply was not JSON */ }
    if (res.status === 401) {
      window.location.href = "login"; // session ended: back to the admin sign-in page
      throw new Error("login required");
    }
    if (!res.ok) {
      throw new Error((body && (body.error || body.detail)) || "HTTP " + res.status);
    }
    return body;
  }

  /* ---------- formatting ---------- */
  function parseTime(iso) {
    if (!iso || iso.indexOf("0001") === 0) return null;
    const t = Date.parse(iso.replace(/(\.\d{3})\d+/, "$1"));
    return Number.isNaN(t) ? null : t;
  }

  function fmtUptime(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return d + "d " + h + "h";
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + (s % 60) + "s";
    return s + "s";
  }

  function statusBadge(c) {
    const map = {
      running: ["ok", "Running"], exited: ["off", "Exited"], created: ["off", "Created"],
      restarting: ["warn", "Restarting"], paused: ["warn", "Paused"], dead: ["bad", "Dead"],
    };
    const m = map[c.status] || ["off", c.status];
    return '<span class="badge-s ' + m[0] + '">' + esc(m[1]) + "</span>";
  }

  function healthBadge(c) {
    const map = {
      healthy: ["ok", "healthy"], unhealthy: ["bad", "unhealthy"],
      starting: ["warn", "starting"], none: ["off", "no check"],
    };
    const m = map[c.health] || ["off", c.health];
    return '<span class="badge-s ' + m[0] + '">' + esc(m[1]) + "</span>";
  }

  /* ---------- table ---------- */
  function renderTable() {
    const tbody = $("tbody");
    if (!state.containers.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="muted p-4">No containers found for this project.</td></tr>';
      return;
    }
    tbody.innerHTML = state.containers.map((c) => {
      const s = state.stats[c.name];
      const live = s && s.running;
      const run = c.status === "running";
      const busy = state.pending.has(c.name);
      const started = parseTime(c.started_at);
      const uptime = run && started ? "up " + fmtUptime(Date.now() - started) : "";

      let cpuCell = '<span class="muted">-</span>';
      let memCell = cpuCell;
      if (live) {
        const cpuBar = Math.min(100, (s.cpu_percent / ((c.cpu_limit || 1) * 100)) * 100);
        cpuCell = '<div class="metric"><strong>' + s.cpu_percent.toFixed(1) + '%</strong>' +
          '<div class="bar"><i style="width:' + cpuBar.toFixed(0) + '%"></i></div></div>';
        const memCls = s.mem_percent > 90 ? "bad" : s.mem_percent > 75 ? "warn" : "";
        memCell = '<div class="metric"><strong>' + s.mem_used_mb.toFixed(0) + "</strong> / " +
          s.mem_limit_mb.toFixed(0) + ' MB<div class="bar"><i class="mem ' + memCls +
          '" style="width:' + Math.min(100, s.mem_percent).toFixed(0) + '%"></i></div></div>';
      }

      const r = state.rates[c.name];
      const net = run && r
        ? "&darr; " + formatRate(r.rxBps) + "<br>&uarr; " + formatRate(r.txBps) : "-";
      const ip = Object.keys(c.networks).map((n) => c.networks[n]).join(", ") || "-";
      const ports = c.ports.map((p) =>
        '<span class="port ' + (p.published ? "pub" : "int") + '">' + esc(p.label) + "</span>"
      ).join("") || "-";

      const btn = (action, label, cls, disabled, title) =>
        '<button class="btn btn-sm ' + cls + '" data-action="' + action + '"' +
        (disabled ? " disabled" : "") + (title ? ' title="' + esc(title) + '"' : "") +
        ">" + label + "</button>";
      const protTitle = c.protected ? "The dashboard cannot stop or restart itself" : "";
      const buttons =
        btn("start", "Start", "btn-outline-success", busy || run, "") +
        btn("stop", "Stop", "btn-outline-danger", busy || !run || c.protected, protTitle) +
        btn("restart", "Restart", "btn-outline-primary", busy || !run || c.protected, protTitle);

      return '<tr data-name="' + esc(c.name) + '"' + (c.name === state.selected ? ' class="selected"' : "") + ">" +
        '<td><div class="cname">' + esc(c.name) + '</div><div class="cimg">' + esc(c.image) + "</div></td>" +
        "<td>" + statusBadge(c) + '<div class="sub">' + esc(uptime) + "</div></td>" +
        "<td>" + healthBadge(c) + "</td>" +
        '<td class="mono">' + esc(ip) + "</td>" +
        "<td>" + ports + "</td><td>" + cpuCell + "</td><td>" + memCell + "</td>" +
        '<td class="mono net">' + net + "</td>" +
        '<td class="actions">' + buttons + "</td></tr>";
    }).join("");
    $("countBadge").textContent = state.containers.length + " total";
  }

  /* ---------- KPIs and alert ---------- */
  function renderKpis() {
    const list = state.containers;
    const running = list.filter((c) => c.status === "running");
    const unhealthy = list.filter((c) => c.health === "unhealthy");
    const down = list.filter((c) => c.status !== "running");

    $("kpiRunning").innerHTML = running.length + " <small>/ " + list.length + "</small>";
    const ok = !unhealthy.length && !down.length;
    $("kpiRunningSub").innerHTML = ok
      ? '<span class="badge-s ok">All healthy</span>'
      : '<span class="badge-s warn">' + (unhealthy.length + down.length) + " need attention</span>";

    $("kpiUnhealthy").textContent = unhealthy.length;
    $("kpiUnhealthy").className = "kpi-v" + (unhealthy.length ? " bad" : "");

    let cpu = 0;
    let mem = 0;
    let got = false;
    Object.keys(state.stats).forEach((n) => {
      const s = state.stats[n];
      if (s && s.running) { cpu += s.cpu_percent; mem += s.mem_used_mb; got = true; }
    });
    const limit = list.reduce((a, c) => a + (c.mem_limit_mb || 0), 0);
    $("kpiUsage").innerHTML = got
      ? cpu.toFixed(1) + " <small>%</small> &middot; " + mem.toFixed(0) + " <small>MB</small>" : "-";
    $("kpiUsageSub").textContent = limit
      ? "of " + limit.toFixed(0) + " MB allocated by limits · 100% CPU = one core"
      : "100% CPU = one core";

    $("kpiRestarts").textContent = list.reduce((a, c) => a + (c.restart_count || 0), 0);

    const alerts = [];
    if (unhealthy.length) {
      alerts.push({
        severity: "bad",
        title: "Unhealthy:",
        message: unhealthy.map((c) => esc(c.name)).join(", ") +
          ". Docker’s health check is failing. Check the logs below.",
      });
    }
    if (down.length) {
      alerts.push({
        severity: "warn",
        title: "Not running:",
        message: down.map((c) => esc(c.name)).join(", ") + ".",
      });
    }

    if (state.monitoring) {
      const p = state.monitoring.prometheus || {};
      const g = state.monitoring.grafana || {};
      if (p.state === "down") {
        alerts.push({
          severity: "bad", title: "Prometheus unreachable:",
          message: "The Prometheus targets API could not be reached.",
        });
      } else if (p.state === "degraded") {
        alerts.push({
          severity: "warn", title: "Prometheus targets:",
          message: (Number(p.up) || 0) + " of " + (Number(p.total) || 0) + " targets are up.",
        });
      }
      if (g.state === "down") {
        alerts.push({
          severity: "bad", title: "Grafana unreachable:",
          message: "The Grafana health endpoint could not be reached.",
        });
      } else if (g.state === "degraded") {
        alerts.push({ severity: "warn", title: "Grafana degraded:", message: "Grafana did not report a healthy database." });
      }
    }

    $("alertBox").innerHTML = alerts.map((a) =>
      '<div class="banner ' + a.severity + '"><strong>' + a.title + "</strong> " + a.message + "</div>"
    ).join("");
  }

  function renderMonitoring() {
    if (!state.monitoring) return;
    const p = state.monitoring.prometheus || { state: "down", up: 0, total: 0 };
    const g = state.monitoring.grafana || { state: "down" };
    const presentation = (status) => {
      if (status === "up") return { cls: "ok", text: "Healthy" };
      if (status === "degraded") return { cls: "warn", text: "Degraded" };
      return { cls: "bad", text: "Unreachable" };
    };
    const ps = presentation(p.state);
    const gs = presentation(g.state);
    $("prometheusText").textContent = "Prometheus: " + (p.up || 0) + " of " + (p.total || 0) + " targets up";
    $("prometheusBadge").className = "badge-s " + ps.cls;
    $("prometheusBadge").textContent = ps.text;
    $("prometheusService").className = "monitoring-service " + ps.cls;
    $("grafanaText").textContent = g.state === "up" ? "Grafana is healthy" :
      (g.state === "degraded" ? "Grafana database is not healthy" : "Grafana health check failed");
    $("grafanaBadge").className = "badge-s " + gs.cls;
    $("grafanaBadge").textContent = gs.text;
    $("grafanaService").className = "monitoring-service " + gs.cls;
  }

  /* ---------- details and hardening ---------- */
  function renderDetails() {
    const c = state.containers.find((x) => x.name === state.selected);
    const det = $("details");
    const hard = $("hardening");
    if (!c) {
      det.innerHTML = '<span class="muted">Select a container.</span>';
      hard.innerHTML = "";
      return;
    }
    const nets = Object.keys(c.networks).map((n) => n + " \u00b7 " + c.networks[n]).join(", ") || "-";
    const ports = c.ports.map((p) => p.label).join(", ") || "-";
    const limits = (c.cpu_limit ? c.cpu_limit.toFixed(2) + " CPU" : "no CPU limit") + " \u00b7 " +
      (c.mem_limit_mb ? c.mem_limit_mb.toFixed(0) + " MB" : "no memory limit");
    const rows = [
      ["Image", c.image], ["Network", nets], ["Ports", ports],
      ["Restart policy", c.restart_policy],
      ["Health check", c.health_interval_s ? "every " + c.health_interval_s + " s" : "none"],
      ["Restarts", c.restart_count], ["Limits", limits],
    ];
    det.innerHTML = '<dl class="kv">' +
      rows.map((r) => "<dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd>").join("") + "</dl>";

    const h = c.hardening;
    const items = [
      [h.non_root, "Runs as non-root (user " + h.user + ")"],
      [h.cap_drop.indexOf("ALL") !== -1,
        h.cap_drop.length ? "Capabilities dropped (" + h.cap_drop.join(", ") + ")" : "Capabilities not dropped"],
      [h.read_only, "Read-only root filesystem"],
      [h.no_new_privileges, "No new privileges"],
      [!!(c.mem_limit_mb && c.cpu_limit), "Resource limits set"],
    ];
    hard.innerHTML = items.map((i) =>
      '<li><span class="chk ' + (i[0] ? "ok" : "no") + '">' + (i[0] ? "\u2713" : "!") +
      "</span><span>" + esc(i[1]) + "</span></li>").join("");
  }

  /* ---------- charts: timestamp-based history ---------- */
  let chart = null;
  let networkChart = null;
  let historyMinutes = DEFAULT_HISTORY_MINUTES;
  let historyRequestId = 0;
  let rateUnit = { label: "B/s", divisor: 1 };

  function fmtChartTime(value) {
    const date = new Date(Number(value));
    return date.toLocaleTimeString([], {
      hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }

  function formatRate(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    const magnitude = Math.abs(value);
    if (magnitude >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(2) + " MB/s";
    if (magnitude >= 1024) return (value / 1024).toFixed(2) + " KB/s";
    return (value < 10 ? value.toFixed(1) : value.toFixed(0)) + " B/s";
  }

  function buildSeries(items, key, isNetwork) {
    const result = [];
    items.forEach((item) => {
      if (item.gapBefore || (isNetwork && item.netBreakBefore)) {
        result.push({ x: item.breakAt || item.t - 1, y: null });
      }
      result.push({ x: item.t, y: item[key] });
    });
    return result;
  }

  if (window.Chart) {
    chart = new window.Chart($("chartCanvas"), {
      type: "line",
      data: {
        datasets: [
          { label: "CPU %", data: [], borderColor: "#2563eb", backgroundColor: "rgba(37,99,235,.10)",
            fill: true, tension: 0, pointRadius: 0, borderWidth: 2, spanGaps: false, parsing: false },
          { label: "Memory %", data: [], borderColor: "#059669", backgroundColor: "rgba(5,150,105,.08)",
            fill: true, tension: 0, pointRadius: 0, borderWidth: 2, spanGaps: false, parsing: false },
        ],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, suggestedMax: 10, ticks: { callback: (v) => v + "%" } },
          x: { type: "linear", ticks: { maxTicksLimit: 6, callback: (v) => fmtChartTime(v) } },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: {
            title: (items) => items.length ? fmtChartTime(items[0].parsed.x) : "",
            label: (item) => item.dataset.label + ": " +
              (item.parsed.y == null ? "no data" : item.parsed.y.toFixed(2) + "%"),
          } },
        },
      },
    });
    networkChart = new window.Chart($("networkChartCanvas"), {
      type: "line",
      data: {
        datasets: [
          { label: "Inbound", data: [], borderColor: "#2563eb", backgroundColor: "rgba(37,99,235,.10)",
            fill: false, tension: 0, pointRadius: 0, borderWidth: 2, spanGaps: false, parsing: false },
          { label: "Outbound", data: [], borderColor: "#d97706", backgroundColor: "rgba(217,119,6,.08)",
            fill: false, tension: 0, pointRadius: 0, borderWidth: 2, spanGaps: false, parsing: false },
        ],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, min: 0, suggestedMax: 1024,
            ticks: { callback: (v) => (v / rateUnit.divisor).toFixed(rateUnit.divisor === 1 ? 0 : 2) + " " + rateUnit.label } },
          x: { type: "linear", ticks: { maxTicksLimit: 6, callback: (v) => fmtChartTime(v) } },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: {
            title: (items) => items.length ? fmtChartTime(items[0].parsed.x) : "",
            label: (item) => item.dataset.label + ": " + formatRate(item.parsed.y),
          } },
        },
      },
    });
  }

  function renderNetworkSummary(items) {
    const valuesRx = items.map((item) => item.rx).filter((v) => Number.isFinite(v));
    const valuesTx = items.map((item) => item.tx).filter((v) => Number.isFinite(v));
    const latest = items.length ? items[items.length - 1] : null;
    const allRates = valuesRx.concat(valuesTx);
    const highest = allRates.length ? Math.max.apply(null, allRates) : 0;
    if (highest >= 1024 * 1024) rateUnit = { label: "MB/s", divisor: 1024 * 1024 };
    else if (highest >= 1024) rateUnit = { label: "KB/s", divisor: 1024 };
    else rateUnit = { label: "B/s", divisor: 1 };

    const average = (values) => values.length
      ? values.reduce((total, value) => total + value, 0) / values.length : null;
    const peak = (values) => values.length ? Math.max.apply(null, values) : null;
    $("netCurrentRx").textContent = latest ? formatRate(latest.rx) : "—";
    $("netCurrentTx").textContent = latest ? formatRate(latest.tx) : "—";
    $("netPeakRx").textContent = formatRate(peak(valuesRx));
    $("netPeakTx").textContent = formatRate(peak(valuesTx));
    $("netAvgRx").textContent = formatRate(average(valuesRx));
    $("netAvgTx").textContent = formatRate(average(valuesTx));
    $("networkUnitText").textContent = "Inbound / outbound · auto-scaled " + rateUnit.label;
  }

  function refreshChart() {
    $("chartTitle").textContent = state.selected || "-";
    const history = state.history[state.selected] || [];
    const now = Date.now();
    const start = now - historyMinutes * 60 * 1000;
    const visible = history.filter((item) => item.t >= start && item.t <= now);
    if (chart) {
      chart.data.datasets[0].data = buildSeries(history, "cpu", false);
      chart.data.datasets[1].data = buildSeries(history, "mem", false);
      chart.options.scales.x.min = start;
      chart.options.scales.x.max = now;
      chart.update("none");
    }
    renderNetworkSummary(visible);
    if (networkChart) {
      networkChart.data.datasets[0].data = buildSeries(history, "rx", true);
      networkChart.data.datasets[1].data = buildSeries(history, "tx", true);
      networkChart.options.scales.x.min = start;
      networkChart.options.scales.x.max = now;
      networkChart.options.scales.y.ticks.callback = (v) =>
        (v / rateUnit.divisor).toFixed(rateUnit.divisor === 1 ? 0 : 2) + " " + rateUnit.label;
      networkChart.update("none");
    }
  }

  async function loadChartHistory() {
    const name = state.selected;
    if (!name) return;
    const requestId = ++historyRequestId;
    $("chartStatus").textContent = "Loading saved metrics...";
    try {
      const data = await getJSON(API + "/metrics/history?name=" + enc(name) +
        "&minutes=" + historyMinutes);
      if (requestId !== historyRequestId || name !== state.selected) return;
      const offset = Date.now() - data.end * 1000;
      let previousTime = null;
      state.history[name] = data.samples.map((sample) => {
        const t = Math.round(sample.t * 1000 + offset);
        const gapBefore = previousTime !== null && (sample.t * 1000 - previousTime) > HISTORY_GAP_MS;
        const breakAt = gapBefore ? (previousTime + sample.t * 1000) / 2 + offset : t - 1;
        previousTime = sample.t * 1000;
        return {
          t: t,
          cpu: sample.cpu,
          mem: sample.mem,
          rx: sample.rx,
          tx: sample.tx,
          gapBefore: gapBefore,
          netBreakBefore: false,
          breakAt: breakAt,
        };
      });
      $("chartStatus").textContent = data.samples.length
        ? "Historical metrics from Prometheus · refreshed " +
          new Date().toLocaleTimeString([], { hour12: false })
        : "No historical metrics are available for this container and time range.";
      refreshChart();
    } catch (e) {
      if (requestId !== historyRequestId || name !== state.selected) return;
      $("chartStatus").textContent = "Could not load historical metrics: " + e.message;
    }
  }

  function scheduleChartHistory() {
    setTimeout(() => {
      if (!document.hidden) loadChartHistory();
      scheduleChartHistory();
    }, HISTORY_REFRESH_MS);
  }

  /* ---------- polling: containers ---------- */
  function renderAll() {
    renderTable();
    renderKpis();
    renderDetails();
    syncLogSelect();
  }

  function setEngine(ok, msg) {
    state.engineOk = ok;
    $("engineDot").className = "dot " + (ok ? "ok" : "bad");
    $("engineText").textContent = ok ? "connected \u00b7 refresh 3 s" : (msg || "unreachable");
  }

  async function loadContainers() {
    try {
      state.containers = await getJSON(API + "/containers");
      state.lastOk = Date.now();
      setEngine(true);
      if (!state.selected || !state.containers.some((c) => c.name === state.selected)) {
        const def = state.containers.find((c) => c.name === "nginx-web") ||
          state.containers.find((c) => c.status === "running") || state.containers[0];
        state.selected = def ? def.name : null;
        renderAll();
        refreshChart();
        loadChartHistory();
        loadLogs();
        return;
      }
      renderAll();
    } catch (e) {
      setEngine(false, e.message);
    }
  }

  (function listLoop() {
    const next = () => setTimeout(listLoop, LIST_MS);
    if (document.hidden) { next(); return; }
    loadContainers().then(next, next);
  })();

  /* ---------- polling: stats ---------- */
  function ingestStats(d) {
    state.stats = d.stats || {};
    const containersByName = new Map(state.containers.map((container) => [container.name, container]));
    Object.keys(state.stats).forEach((name) => {
      const sample = state.stats[name];
      if (!sample.running) {
        delete state.lastNet[name];
        delete state.rates[name];
        return;
      }

      const ts = Date.now();
      const prev = state.lastNet[name];
      const elapsedMs = prev ? (d.ts - prev.ts) * 1000 : null;
      const gap = Boolean(prev && ts - prev.at > MAX_CONTIGUOUS_GAP_MS);
      const container = containersByName.get(name);
      const meta = container ? {
        restartCount: container.restart_count,
        startedAt: container.started_at,
      } : null;
      const oldMeta = state.restartMeta[name];
      const restartObserved = Boolean(meta && oldMeta && (
        meta.startedAt !== oldMeta.startedAt || meta.restartCount !== oldMeta.restartCount
      ));
      if (meta) state.restartMeta[name] = meta;
      const countersReset = Boolean(restartObserved || (prev &&
        (sample.net_rx_bytes < prev.rx || sample.net_tx_bytes < prev.tx)));

      let rate = null;
      if (prev && !gap && !countersReset && elapsedMs > 200) {
        const seconds = elapsedMs / 1000;
        const rxBps = (sample.net_rx_bytes - prev.rx) / seconds;
        const txBps = (sample.net_tx_bytes - prev.tx) / seconds;
        rate = { rx: rxBps / 1024, tx: txBps / 1024, rxBps: rxBps, txBps: txBps };
        state.rates[name] = rate;
      } else {
        delete state.rates[name];
      }

      state.lastNet[name] = {
        rx: sample.net_rx_bytes, tx: sample.net_tx_bytes, ts: d.ts, at: ts,
      };
    });
  }

  (async function statsLoop() {
    if (!document.hidden) {
      try {
        ingestStats(await getJSON(API + "/stats"));
        state.lastOk = Date.now();
        renderTable();
        renderKpis();
      } catch (_) { /* the live pill shows the problem */ }
    }
    setTimeout(statsLoop, STATS_GAP_MS);
  })();

  /* ---------- monitoring status ---------- */
  async function loadMonitoring() {
    try {
      state.monitoring = await getJSON(API + "/monitoring");
    } catch (_) {
      state.monitoring = {
        ok: false,
        prometheus: { state: "down", up: 0, total: 0 },
        grafana: { state: "down" },
      };
    }
    renderMonitoring();
    renderKpis();
  }

  (function monitoringLoop() {
    const next = () => setTimeout(monitoringLoop, MONITORING_MS);
    if (document.hidden) { next(); return; }
    loadMonitoring().then(next, next);
  })();

  $("historyWindow").addEventListener("change", (event) => {
    historyMinutes = Number(event.target.value) || DEFAULT_HISTORY_MINUTES;
    loadChartHistory();
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadChartHistory();
  });
  scheduleChartHistory();

  /* ---------- actions ---------- */
  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast-i" + (kind === "err" ? " err" : "");
    el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 3800);
  }

  async function doAction(name, action) {
    if (action === "stop" && !window.confirm("Stop " + name + "?")) return;
    state.pending.add(name);
    renderTable();
    try {
      await getJSON(API + "/containers/" + enc(name) + "/" + action, { method: "POST", headers: CSRF });
      toast(name + " " + PAST[action] + " successfully");
    } catch (e) {
      toast(name + ": " + e.message, "err");
    }
    state.pending.delete(name);
    await loadContainers();
  }

  function select(name) {
    state.selected = name;
    renderTable();
    renderDetails();
    refreshChart();
    loadChartHistory();
    syncLogSelect();
    state.logLines = [];
    loadLogs();
  }

  $("tbody").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-name]");
    if (!tr) return;
    const btn = ev.target.closest("button[data-action]");
    if (btn) {
      if (!btn.disabled) doAction(tr.dataset.name, btn.dataset.action);
      return;
    }
    select(tr.dataset.name);
  });

  /* ---------- logs ---------- */
  function syncLogSelect() {
    const sel = $("logContainer");
    const names = state.containers.map((c) => c.name);
    const current = Array.prototype.map.call(sel.options, (o) => o.value);
    if (names.join("|") !== current.join("|")) {
      sel.innerHTML = names.map((n) => '<option value="' + esc(n) + '">' + esc(n) + "</option>").join("");
    }
    if (state.selected) sel.value = state.selected;
  }

  function levelClass(line) {
    if (/\b(error|fatal|crit|critical|emerg|panic)\b/i.test(line)) return "err";
    if (/\b(warn|warning)\b/i.test(line)) return "warn";
    return "";
  }

  function renderLogs(first) {
    const box = $("logBox");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const q = $("logFilter").value.trim().toLowerCase();
    const lines = state.logLines;
    const shown = q ? lines.filter((l) => l.toLowerCase().indexOf(q) !== -1) : lines;
    if (!shown.length) {
      box.textContent = lines.length ? "No lines match the filter." : "No log lines yet.";
    } else {
      const frag = document.createDocumentFragment();
      shown.forEach((l) => {
        const div = document.createElement("div");
        div.className = "ll " + levelClass(l);
        div.textContent = l; // textContent: log lines are never treated as HTML
        frag.appendChild(div);
      });
      box.replaceChildren(frag);
    }
    if (first || nearBottom) box.scrollTop = box.scrollHeight;
    $("logMeta").textContent = shown.length + " of " + lines.length + " lines \u00b7 " +
      (state.selected || "-") + " \u00b7 refreshed " + new Date().toLocaleTimeString([], { hour12: false });
  }

  async function loadLogs() {
    if (!state.selected) return;
    const name = state.selected;
    try {
      const d = await getJSON(API + "/containers/" + enc(name) + "/logs?tail=" + $("logTail").value);
      if (name !== state.selected) return; // selection changed while loading
      const first = state.logLines.length === 0;
      state.logLines = d.lines;
      renderLogs(first);
    } catch (e) {
      $("logBox").textContent = "Could not load logs: " + e.message;
    }
  }

  setInterval(() => { if ($("logFollow").checked && !document.hidden) loadLogs(); }, LOG_MS);
  $("logRefresh").addEventListener("click", loadLogs);
  $("logTail").addEventListener("change", () => { state.logLines = []; loadLogs(); });
  $("logFilter").addEventListener("input", () => renderLogs(false));
  $("logContainer").addEventListener("change", (ev) => select(ev.target.value));

  /* ---------- live indicator ---------- */
  setInterval(() => {
    const pill = $("livePill");
    const text = $("liveText");
    if (!state.lastOk) { pill.className = "pill wait"; text.textContent = "Connecting..."; return; }
    const age = Math.round((Date.now() - state.lastOk) / 1000);
    if (age > 10) { pill.className = "pill bad"; text.textContent = "No data for " + age + " s"; return; }
    pill.className = "pill";
    text.textContent = "Live \u00b7 refreshed " + age + " s ago";
  }, 1000);

  // Compose publishes both monitoring ports only on the local host.
  $("grafanaLink").href = "http://localhost:3000";
  $("prometheusLink").href = "http://localhost:9090/targets";
})();
