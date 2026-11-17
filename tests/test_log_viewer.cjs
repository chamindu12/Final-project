"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const LogViewer = require("../dashboard/static/js/logs.js");
const turn = () => new Promise((resolve) => setImmediate(resolve));

class Source {
  static instances = [];
  constructor(url) { this.url = url; this.handlers = {}; this.closed = false; Source.instances.push(this); }
  addEventListener(kind, fn) { (this.handlers[kind] ||= []).push(fn); }
  emit(kind, data = {}) { for (const fn of this.handlers[kind] || []) fn({ data: JSON.stringify(data) }); }
  close() { this.closed = true; }
}

function setup(t, options = {}) {
  const document = {
    createElement() { return { className: "", textContent: "" }; },
    createDocumentFragment() { return { children: [], appendChild(x) { this.children.push(x); } }; },
  };
  const box = {
    ownerDocument: document, scrollTop: 0, scrollHeight: 200, clientHeight: 100,
    textContent: "", children: [], replaceChildren(fragment) { this.children = fragment.children; },
  };
  const ui = { box, filter: { value: "" }, live: { checked: true },
    autoscroll: { checked: true }, meta: {}, status: {} };
  let expired = false;
  const viewer = new LogViewer({ ui, EventSource: Source, getJSON: async () => ({ lines: [] }),
    onExpired: () => { expired = true; }, ...options });
  t.after(() => viewer.stop());
  return { viewer, ui, expired: () => expired };
}

test("switching containers rejects late lines, errors and end events from the old stream", (t) => {
  const { viewer } = setup(t);
  viewer.open("staff-portal", 100); const old = viewer.source;
  old.emit("reset", { container_id: "staff-id" });
  old.emit("log", { line: "staff line" });
  viewer.open("guest-portal", 100); const current = viewer.source;
  current.emit("reset", { container_id: "guest-id" });
  current.emit("log", { line: "guest line" });
  for (const event of ["log", "error", "end"]) old.emit(event, { line: "late staff" });
  assert.deepEqual(viewer.lines, ["guest line"]);
  assert.equal(viewer.source, current); assert.equal(old.closed, true);
});

test("retired snapshot responses cannot overwrite the same container's newer tail selection", async (t) => {
  const pending = [];
  const { viewer, ui } = setup(t, { getJSON: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
  ui.live.checked = false;
  viewer.open("staff-portal", 100); viewer.open("staff-portal", 500);
  pending[1].resolve({ lines: ["current response"], container_id: "new-id" }); await turn();
  pending[0].resolve({ lines: ["stale response"], container_id: "old-id" }); await turn();
  assert.deepEqual(viewer.lines, ["current response"]); assert.equal(viewer.identity, "new-id");
});

test("retired snapshot errors cannot replace another container's status", async (t) => {
  const pending = [];
  const { viewer, ui } = setup(t, { getJSON: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
  ui.live.checked = false;
  viewer.open("staff-portal", 100); viewer.open("guest-portal", 100);
  pending[1].resolve({ lines: ["guest"] }); await turn();
  const status = ui.status.textContent;
  pending[0].reject(new Error("stale error")); await turn();
  assert.equal(ui.status.textContent, status); assert.deepEqual(viewer.lines, ["guest"]);
});

test("identical records remain separate and retention is bounded", (t) => {
  const { viewer } = setup(t); viewer.open("staff-portal", 2);
  const source = viewer.source; source.emit("reset", {});
  source.emit("log", { line: "older" });
  source.emit("log", { line: "same timestamp same text" });
  source.emit("log", { line: "same timestamp same text" });
  assert.deepEqual(viewer.lines, ["same timestamp same text", "same timestamp same text"]);
  source.emit("end", { reason: "eof" });
  assert.equal(viewer.ui.box.children.length, 2); // flush final records before EOF/reconnect
});

test("a reconnect replaces the old tail instead of appending replayed history", (t) => {
  const { viewer } = setup(t); viewer.open("nginx-proxy", 100);
  viewer.source.emit("reset", { container_id: "first" });
  viewer.source.emit("log", { line: "old" });
  viewer.source.emit("reset", { container_id: "recreated" });
  viewer.source.emit("log", { line: "new" });
  assert.deepEqual(viewer.lines, ["new"]); assert.equal(viewer.identity, "recreated");
});

test("log content is plain text and structured severity takes precedence over message words", (t) => {
  const { viewer, ui } = setup(t); viewer.open("staff-portal", 100);
  const line = '2026-11-17T10:00:00Z {"level":"info","message":"<script>error()</script>"}';
  viewer.source.emit("reset", {}); viewer.source.emit("log", { line }); viewer.render(true);
  assert.equal(ui.box.children[0].textContent, line); assert.equal(ui.box.children[0].className, "ll ");
  assert.equal(viewer.level('{"level":"warning"}'), "warn");
  assert.equal(viewer.level("plain ERROR message"), "err");
});

test("pause closes the stream and only fetches a snapshot", async (t) => {
  let requests = 0;
  const { viewer, ui } = setup(t, { getJSON: async () => { requests++; return { lines: ["paused"] }; } });
  viewer.open("guest-portal", 100); const source = viewer.source;
  ui.live.checked = false; viewer.open("guest-portal", 100); await turn();
  assert.equal(source.closed, true); assert.equal(viewer.source, null);
  assert.equal(viewer.timer, null); assert.equal(requests, 1); assert.match(ui.status.textContent, /Paused/);
});

test("revoked authentication closes the stream and returns to sign in", (t) => {
  const { viewer, expired } = setup(t); viewer.open("staff-portal", 100);
  const source = viewer.source; source.emit("auth_expired");
  assert.equal(expired(), true); assert.equal(source.closed, true); assert.equal(viewer.timer, null);
});

test("manual scrolling and the filter preserve a paused reading position", (t) => {
  const { viewer, ui } = setup(t); viewer.open("dnsmasq", 100);
  ui.autoscroll.checked = false; ui.box.scrollTop = 17;
  viewer.lines = ["first query", "second response"]; ui.filter.value = "response"; viewer.render(true);
  assert.equal(ui.box.scrollTop, 17); assert.equal(ui.box.children.length, 1);
  assert.equal(ui.box.children[0].textContent, "second response");
});

test("fallback polling has at most one in-flight request and stop ignores its completion", async (t) => {
  let calls = 0; let resolve;
  const { viewer } = setup(t, { EventSource: null,
    getJSON: () => { calls++; return new Promise((r) => { resolve = r; }); } });
  viewer.open("cadvisor", 50); await turn();
  assert.equal(calls, 1); assert.equal(viewer.timer, null);
  viewer.stop(); resolve({ lines: ["late"] }); await turn();
  assert.deepEqual(viewer.lines, []); assert.equal(viewer.timer, null);
});
