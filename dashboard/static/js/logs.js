/* Live Docker logs, isolated from chart polling and container selection races. */
(function (root) {
  "use strict";

  class NetLabLogViewer {
    constructor(options) {
      this.ui = options.ui;
      this.getJSON = options.getJSON;
      this.api = options.api || "api";
      this.EventSource = options.EventSource || root.EventSource;
      this.onExpired = options.onExpired;
      this.isVisible = options.isVisible || (() => !root.document || !root.document.hidden);
      this.generation = 0;
      this.lines = [];
      this.name = null;
      this.tail = 100;
      this.identity = "";
      this.source = null;
      this.timer = null;
      this.renderTimer = null;
      this.abort = null;
      this.failures = 0;
    }

    active(generation) { return this.generation === generation; }

    status(text) { this.ui.status.textContent = text; }

    closeSource() {
      if (this.source) this.source.close();
      this.source = null;
    }

    stop() {
      this.generation += 1;
      this.closeSource();
      clearTimeout(this.timer);
      clearTimeout(this.renderTimer);
      this.timer = this.renderTimer = null;
      if (this.abort) this.abort.abort();
      this.abort = null;
    }

    open(name, tail) {
      this.stop();
      this.name = name;
      this.tail = Math.max(1, Math.min(Number(tail) || 100, 500));
      this.lines = [];
      this.identity = "";
      this.failures = 0;
      this.render(true);
      if (!name) { this.status("Select a container."); return; }
      if (!this.isVisible()) { this.status("Paused while this tab is hidden."); return; }
      const generation = this.generation;
      if (this.ui.live.checked && this.EventSource) this.connect(generation);
      else this.poll(generation);
    }

    connect(generation) {
      if (!this.active(generation)) return;
      this.status("Connecting to " + this.name + "…");
      let source;
      try {
        source = new this.EventSource(this.api + "/containers/" +
          encodeURIComponent(this.name) + "/logs/stream?tail=" + this.tail);
      } catch (_) { this.poll(generation); return; }
      this.source = source;
      const current = () => this.active(generation) && this.source === source;
      const receive = (handler) => (message) => {
        if (!current()) return;
        try { handler(JSON.parse(message.data)); }
        catch (_) { this.recover(generation, source); }
      };
      source.addEventListener("reset", receive((data) => {
        this.lines = [];
        this.identity = data.container_id || "";
        this.status("Live stream · " + this.name + " · Docker timestamps are UTC");
        this.render(true);
      }));
      source.addEventListener("log", receive((data) => {
        if (typeof data.line !== "string") throw new Error("invalid log event");
        this.failures = 0;
        this.lines.push(data.line); // repeated identical lines are genuine separate records
        if (this.lines.length > this.tail) this.lines.splice(0, this.lines.length - this.tail);
        if (!this.renderTimer) this.renderTimer = setTimeout(() => {
          this.renderTimer = null;
          if (current()) this.render(false);
        }, 50);
      }));
      source.addEventListener("heartbeat", receive(() => {
        this.failures = 0;
        this.status("Live stream · " + this.name + " · Docker timestamps are UTC");
      }));
      source.addEventListener("auth_expired", receive(() => {
        this.stop();
        this.onExpired();
      }));
      source.addEventListener("problem", () => {
        if (current()) this.recover(generation, source);
      });
      source.addEventListener("error", () => {
        if (current()) this.recover(generation, source);
      });
      source.addEventListener("end", receive((data) => {
        clearTimeout(this.renderTimer);
        this.renderTimer = null;
        this.render(false);
        this.closeSource();
        // A fresh bounded tail replaces the view on reconnect, preventing replay duplicates.
        this.status(data.reason === "rotate" ? "Renewing live connection…" :
          "Stream ended; showing retained output. Reconnecting…");
        this.timer = setTimeout(() => this.connect(generation), data.reason === "rotate" ? 100 : 2000);
      }));
    }

    async snapshot(generation) {
      const controller = new AbortController();
      this.abort = controller;
      try {
        const data = await this.getJSON(this.api + "/containers/" +
          encodeURIComponent(this.name) + "/logs?tail=" + this.tail, { signal: controller.signal });
        if (!this.active(generation)) return false;
        if (!Array.isArray(data.lines)) throw new Error("invalid log response");
        this.lines = data.lines.slice(-this.tail);
        this.identity = data.container_id || "";
        this.render(false);
        return true;
      } catch (error) {
        if (this.active(generation) && error.name !== "AbortError") {
          this.status("Could not load logs: " + error.message);
        }
        return false;
      } finally {
        if (this.abort === controller) this.abort = null;
      }
    }

    async recover(generation, source) {
      if (!this.active(generation) || this.source !== source) return;
      this.closeSource();
      this.failures += 1;
      this.status("Live connection interrupted. Checking retained logs…");
      await this.snapshot(generation); // also detects expired authentication via the normal API
      if (!this.active(generation)) return;
      if (this.failures >= 3) { this.poll(generation); return; }
      this.timer = setTimeout(() => this.connect(generation), 2000);
    }

    async poll(generation) {
      if (!this.active(generation)) return;
      const ok = await this.snapshot(generation);
      if (!this.active(generation)) return;
      if (ok) this.status(this.ui.live.checked ?
        "Polling every 1 s · live stream unavailable · Docker timestamps are UTC" :
        "Paused snapshot · use Refresh to reload · Docker timestamps are UTC");
      // Schedule after completion; slow replies can never overlap or arrive out of order.
      if (this.ui.live.checked) this.timer = setTimeout(() => this.poll(generation), 1000);
    }

    level(line) {
      const start = line.indexOf("{");
      if (start >= 0) {
        try {
          const data = JSON.parse(line.slice(start));
          if (data.level) {
            if (/^(error|fatal|critical|panic)$/i.test(data.level)) return "err";
            return /^(warn|warning)$/i.test(data.level) ? "warn" : "";
          }
        } catch (_) { /* other containers write plain text */ }
      }
      if (/\b(error|fatal|crit|critical|emerg|panic)\b/i.test(line)) return "err";
      return /\b(warn|warning)\b/i.test(line) ? "warn" : "";
    }

    render(first) {
      const box = this.ui.box;
      const previousScroll = box.scrollTop;
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      const filter = this.ui.filter.value.trim().toLowerCase();
      const shown = this.lines.filter((line) => !filter || line.toLowerCase().includes(filter));
      if (!shown.length) box.textContent = this.lines.length ?
        "No lines match the filter." : "No output yet. Idle containers may be silent.";
      else {
        const fragment = box.ownerDocument.createDocumentFragment();
        shown.forEach((line) => {
          const div = box.ownerDocument.createElement("div");
          div.className = "ll " + this.level(line);
          div.textContent = line; // log text is never HTML
          fragment.appendChild(div);
        });
        box.replaceChildren(fragment);
      }
      if (this.ui.autoscroll.checked && (first || nearBottom)) box.scrollTop = box.scrollHeight;
      else box.scrollTop = previousScroll;
      this.ui.meta.textContent = shown.length + " of " + this.lines.length +
        " retained lines · " + (this.name || "-") +
        (this.identity ? " · " + this.identity.slice(0, 12) : "") +
        " · original Docker UTC timestamps · maximum " + this.tail + " lines";
    }
  }

  root.NetLabLogViewer = NetLabLogViewer;
  if (typeof module !== "undefined" && module.exports) module.exports = NetLabLogViewer;
})(typeof window !== "undefined" ? window : globalThis);
