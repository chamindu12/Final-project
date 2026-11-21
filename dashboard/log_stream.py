"""Bounded, cancellable Docker log subscriptions for the admin SSE endpoint."""
import json
import queue
import threading
import time
from datetime import datetime, timezone


# Gunicorn has four threads per worker. Reserve at least two for ordinary APIs.
STREAM_SLOTS = threading.BoundedSemaphore(2)
HEARTBEAT_SECONDS = 2
STREAM_SECONDS = 25
MAX_LINE_BYTES = 16384


class LogCapacityError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_lines(chunks):
    """Reassemble split UTF-8 and newlines without deduplicating repeated messages."""
    pending = bytearray()
    truncated = False
    for chunk in chunks:
        parts = chunk.split(b"\n")
        for i, part in enumerate(parts):
            room = MAX_LINE_BYTES - len(pending)
            pending.extend(part[:room])
            truncated = truncated or len(part) > room
            if i < len(parts) - 1:
                yield pending.decode("utf-8", errors="replace").rstrip("\r") + (
                    " [line truncated at 16384 bytes]" if truncated else "")
                pending.clear()
                truncated = False
    if pending or truncated:
        yield pending.decode("utf-8", errors="replace") + (
            " [line truncated at 16384 bytes]" if truncated else "")


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class LogSubscription:
    def __init__(self, source, metadata, authorized, release):
        self.source = source
        self.metadata = metadata
        self.authorized = authorized
        self.release = release
        self.messages = queue.Queue(maxsize=256)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.closed = False
        self.reader = None

    def put(self, kind, value=None):
        while not self.stopped.is_set():
            try:
                self.messages.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue  # backpressure, never silently discard an individual line

    def read(self):
        try:
            for line in log_lines(self.source):
                if self.stopped.is_set():
                    return
                self.put("log", line)
            self.put("end", "eof")
        except Exception:
            if not self.stopped.is_set():
                self.put("problem", "Docker log stream interrupted")

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stopped.set()
            try:
                self.source.close()  # Docker CancellableStream shuts down the socket
            except Exception:
                pass  # already closed/disconnected; always release the bounded slot
            finally:
                self.release()
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(timeout=0.5)

    def events(self):
        self.reader = threading.Thread(target=self.read, daemon=True, name="docker-log-reader")
        self.reader.start()
        deadline = time.monotonic() + STREAM_SECONDS
        next_auth = 0
        try:
            yield event("reset", self.metadata)
            while not self.stopped.is_set():
                now = time.monotonic()
                if now >= next_auth:
                    if not self.authorized():
                        yield event("auth_expired", {})
                        return
                    next_auth = now + HEARTBEAT_SECONDS
                if now >= deadline:
                    yield event("end", {"reason": "rotate"})
                    return
                try:
                    kind, value = self.messages.get(
                        timeout=min(HEARTBEAT_SECONDS, deadline - now))
                except queue.Empty:
                    yield event("heartbeat", {"server_time": utc_now()})
                    continue
                if kind == "log":
                    yield event("log", {"line": value})
                elif kind == "end":
                    yield event("end", {"reason": value})
                    return
                else:
                    yield event("problem", {"message": value})
                    return
        except Exception:
            yield event("problem", {"message": "Live logs temporarily unavailable"})
        finally:
            self.close()


def subscribe(container, tail, authorized):
    if not STREAM_SLOTS.acquire(blocking=False):
        raise LogCapacityError("Live log capacity is busy; retry or use Refresh")
    try:
        source = container.logs(tail=tail, timestamps=True, stdout=True, stderr=True,
                                stream=True, follow=True)
    except Exception:
        STREAM_SLOTS.release()
        raise
    return LogSubscription(source, {
        "name": container.name,
        "container_id": container.attrs.get("Id", container.short_id),
        "tail": tail, "timestamp_basis": "Docker UTC", "connected_at": utc_now(),
    }, authorized, STREAM_SLOTS.release)
