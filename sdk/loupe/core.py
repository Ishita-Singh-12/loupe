"""Bounded, best-effort background export. No prompt/response bodies are captured."""
import atexit
import contextvars
import functools
import inspect
import json
import queue
import secrets
import threading
import time
import urllib.request
from datetime import datetime, timezone

_current = contextvars.ContextVar("loupe_span", default=None)
_client = None

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

class Client:
    def __init__(self, endpoint="http://127.0.0.1:4173", api_key=None,
                 service_name="agent", batch_size=50, flush_interval=1.0,
                 max_queue=2048, timeout=2.0, max_retries=2):
        if batch_size < 1 or max_queue < 1 or flush_interval <= 0 or timeout <= 0:
            raise ValueError("Queue, batch, interval and timeout must be positive")
        self.endpoint = endpoint.rstrip("/") + "/api/spans"
        self.api_key, self.service_name = api_key, service_name
        self.batch_size, self.flush_interval = batch_size, flush_interval
        self.timeout, self.max_retries = timeout, max_retries
        self.queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._pending = 0
        self.exported = self.dropped = self.export_errors = 0
        self.closed = False
        self._worker = threading.Thread(target=self._run, name="loupe-export", daemon=True)
        self._worker.start()
        atexit.register(self.close)

    def submit(self, span):
        try:
            with self._lock:
                if self.closed:
                    self.dropped += 1
                    return
                self.queue.put_nowait(span)
                self._pending += 1
            if self.queue.qsize() >= self.batch_size:
                self._wake.set()
        except Exception:
            with self._lock:
                self.dropped += 1

    def _send(self, batch):
        payload = json.dumps({"spans": batch}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise RuntimeError("Unexpected export status")

    def _run(self):
        while not self._stop.is_set() or not self.queue.empty():
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            batch = []
            while len(batch) < self.batch_size:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                continue
            delivered = False
            for attempt in range(self.max_retries + 1):
                try:
                    self._send(batch)
                    delivered = True
                    break
                except Exception:
                    self.export_errors += 1
                    if attempt < self.max_retries:
                        self._stop.wait(min(.2 * (2 ** attempt), 1))
            with self._lock:
                self._pending -= len(batch)
                if delivered:
                    self.exported += len(batch)
                else:
                    self.dropped += len(batch)
            for _ in batch:
                self.queue.task_done()
            if not self.queue.empty():
                self._wake.set()

    def flush(self, timeout=5.0):
        """Explicit lifecycle operation; may block up to timeout, never raises."""
        deadline = time.monotonic() + timeout
        self._wake.set()
        while time.monotonic() < deadline:
            with self._lock:
                if self._pending == 0:
                    return self.dropped == 0
            time.sleep(.005)
        return False

    def close(self, timeout=2.0):
        with self._lock:
            if self.closed:
                return
            self.closed = True
        self._stop.set()
        self._wake.set()
        self._worker.join(timeout)

    @property
    def diagnostics(self):
        with self._lock:
            return {"pending": self._pending, "exported": self.exported,
                    "dropped": self.dropped, "export_errors": self.export_errors}

def configure(**kwargs):
    global _client
    if _client:
        _client.close()
    _client = Client(**kwargs)
    return _client

def get_client():
    return _client

class Span:
    def __init__(self, name, kind="INTERNAL", attributes=None, parent=None, client=None):
        self.client = client or _client
        parent = parent if parent is not None else _current.get()
        self.data = {"trace_id": parent.data["trace_id"] if parent else secrets.token_hex(16),
                     "span_id": secrets.token_hex(8),
                     "parent_span_id": parent.data["span_id"] if parent else None,
                     "name": name, "kind": kind, "started_at": stamp(),
                     "status": "UNSET", "attributes": dict(attributes or {}),
                     "resource": {"service.name": self.client.service_name if self.client else "agent"}}
        self.ended = False
        self._token = None

    def __enter__(self):
        self._token = _current.set(self)
        return self

    def set_attribute(self, name, value):
        # User-supplied metadata is limited to JSON primitives. Never capture bodies implicitly.
        if isinstance(value, (str, int, float, bool)) or value is None:
            self.data["attributes"][name] = value
        return self

    def end(self, error=None):
        if self.ended:
            return
        self.ended = True
        self.data["ended_at"] = stamp()
        self.data["status"] = "ERROR" if error else "OK"
        if error:
            self.data["attributes"]["error.type"] = type(error).__name__
            # Error messages may contain credentials or prompts: do not capture them.
        if self.client:
            self.client.submit(self.data)

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _current.reset(self._token)
        try:
            self.end(exc)
        except Exception:
            pass  # Telemetry must not replace the application result or exception.
        return False

    @property
    def traceparent(self):
        return f"00-{self.data['trace_id']}-{self.data['span_id']}-01"

def trace(name=None, *, kind="INTERNAL", attributes=None):
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapped(*args, **kwargs):
                with Span(name or fn.__qualname__, kind, attributes):
                    return await fn(*args, **kwargs)
            return async_wrapped
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with Span(name or fn.__qualname__, kind, attributes):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
