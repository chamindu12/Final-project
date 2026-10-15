(() => {
  "use strict";

  // Relative URLs so the page also works under the proxy path /dashboard/
  const API = "api";
  const CSRF = { "X-Requested-With": "dashboard-ui" };
  const LIST_MS = 3000;
  const STATS_GAP_MS = 1000;
  const LOG_MS = 2000;
  const HISTORY_POINTS = 60;
  const PAST = { start: "started", stop: "stopped", restart: "restarted" };

  const state = {
    containers: [],
    stats: {},
    rates: {},
    lastNet: {},
    history: {},
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
      const net = run && r ? "&darr; " + r.rx.toFixed(1) + "<br>&uarr; " + r.tx.toFixed(1) : "-";
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
      ? "of " + limit.toFixed(0) + " MB allocated by limits \u00b7 100% CPU = one core"
      : "100% CPU = one core";

    $("kpiRestarts").textContent = list.reduce((a, c) => a + (c.restart_count || 0), 0);

    const box = $("alertBox");
    if (unhealthy.length) {
      box.innerHTML = '<div class="banner bad"><strong>Unhealthy:</strong> ' +
        unhealthy.map((c) => esc(c.name)).join(", ") +
        ". Docker\u2019s health check is failing. Check the logs below.</div>";
    } else if (down.length) {
      box.innerHTML = '<div class="banner warn"><strong>Not running:</strong> ' +
        down.map((c) => esc(c.name)).join(", ") + ".</div>";
    } else {
      box.innerHTML = "";
    }
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

  /* ---------- chart ---------- */
  let chart = null;
  if (window.Chart) {
    chart = new window.Chart($("chartCanvas"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "CPU %", data: [], borderColor: "#2563eb", backgroundColor: "rgba(37,99,235,.10)",
            fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
          { label: "Memory %", data: [], borderColor: "#059669", backgroundColor: "rgba(5,150,105,.08)",
            fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        ],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, suggestedMax: 10, ticks: { callback: (v) => v + "%" } },
          x: { ticks: { maxTicksLimit: 6 } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function refreshChart() {
    $("chartTitle").textContent = state.selected || "-";
    if (!chart) return;
    const h = state.history[state.selected] || { t: [], cpu: [], mem: [] };
    chart.data.labels = h.t.slice();
    chart.data.datasets[0].data = h.cpu.slice();
    chart.data.datasets[1].data = h.mem.slice();
    chart.update("none");
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
    Object.keys(state.stats).forEach((name) => {
      const s = state.stats[name];
      if (!s.running) { delete state.lastNet[name]; delete state.rates[name]; return; }
      const h = state.history[name] || (state.history[name] = { t: [], cpu: [], mem: [] });
      h.t.push(new Date(d.ts * 1000).toLocaleTimeString([], { hour12: false }));
      h.cpu.push(s.cpu_percent);
      h.mem.push(s.mem_percent);
      if (h.t.length > HISTORY_POINTS) { h.t.shift(); h.cpu.shift(); h.mem.shift(); }

      const prev = state.lastNet[name];
      if (prev && s.net_rx_bytes >= prev.rx && s.net_tx_bytes >= prev.tx && d.ts - prev.ts > 0.2) {
        const dt = d.ts - prev.ts;
        state.rates[name] = {
          rx: (s.net_rx_bytes - prev.rx) / 1024 / dt,
          tx: (s.net_tx_bytes - prev.tx) / 1024 / dt,
        };
      }
      state.lastNet[name] = { rx: s.net_rx_bytes, tx: s.net_tx_bytes, ts: d.ts };
    });
  }

  (async function statsLoop() {
    if (!document.hidden) {
      try {
        ingestStats(await getJSON(API + "/stats"));
        state.lastOk = Date.now();
        renderTable();
        renderKpis();
        refreshChart();
      } catch (_) { /* the live pill shows the problem */ }
    }
    setTimeout(statsLoop, STATS_GAP_MS);
  })();

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
})();
