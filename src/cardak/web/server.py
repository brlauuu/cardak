"""The web view's server: one page and two read-only JSON endpoints, behind a key (M7).

Requests read the snapshot the daemon last published; nothing here polls or writes.
"""

import base64
import contextlib
import hashlib
import hmac
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from cardak.core.agents import SessionState
from cardak.core.snapshot import DeckSnapshot
from cardak.web.view import find_agent, session_json, snapshot_json, trends_json

PAGE = files("cardak.web").joinpath("page.html").read_bytes()
SESSION = re.compile(r"/api/session/([A-Za-z0-9_-]{1,128})")

Session = Callable[[str], SessionState | None]


def _hash(tag: str) -> str:
    """The CSP source for the page's inline <tag> block ('none' when it has none)."""
    found = re.search(rf"<{tag}>(.*?)</{tag}>".encode(), PAGE, re.DOTALL)
    if found is None:
        return "'none'"
    digest = base64.b64encode(hashlib.sha256(found.group(1)).digest()).decode()
    return f"'sha256-{digest}'"


def csp() -> str:
    return (
        f"default-src 'none'; script-src {_hash('script')}; style-src {_hash('style')}; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )


class Latest:
    """The daemon's last snapshot and when it was taken, shared with request threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: tuple[DeckSnapshot, float] | None = None

    def publish(self, snap: DeckSnapshot, ts: float) -> None:
        with self._lock:
            self._value = (snap, ts)

    def get(self) -> tuple[DeckSnapshot, float] | None:
        with self._lock:
            return self._value


def _log(message: str) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"{stamp} cardak daemon: web view: {message}", file=sys.stderr)


def make_server(
    host: str,
    port: int,
    key: str,
    latest: Latest,
    session: Session,
    clock: Callable[[], float] = time.time,
    timeout: float = 10.0,
) -> ThreadingHTTPServer:
    """Bound and ready for `start`; raises OSError when the address can't be bound."""
    expected = key.encode()
    request_timeout = timeout
    policy = csp()

    class Handler(BaseHTTPRequestHandler):
        server_version = "cardak"
        # an idle connection (a browser's preconnect, a slow client) frees its thread
        timeout = request_timeout
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:
            pass  # a line per poll per tab would flood the journal

        def _send(self, status: int, body: bytes = b"", kind: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if kind is not None:
                self.send_header("Content-Type", kind)
            if kind is not None and kind.startswith("text/html"):
                self.send_header("Content-Security-Policy", policy)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self._send(status, body, "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            given = self.headers.get("Authorization", "")
            token = given.removeprefix("Bearer ").encode() if given.startswith("Bearer ") else b""
            return hmac.compare_digest(token, expected)

        def _route(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            found = SESSION.fullmatch(path)
            if path not in ("/api/snapshot", "/api/trends") and found is None:
                return self._send(404)
            if not self._authorized():
                return self._send(401)
            current = latest.get()
            if current is None:
                return self._json(503, {"starting": True})
            snap, ts = current
            if path == "/api/trends":
                asked = "context=1" in self.path.partition("?")[2].split("&")
                return self._json(200, trends_json(snap.usage_trends, snap.usage_context, asked))
            if found is None:
                return self._json(200, snapshot_json(snap, ts, clock()))
            agent = find_agent(snap, found.group(1))
            if agent is None:
                return self._send(404)
            self._json(200, session_json(agent, session(agent.session_id), clock()))

        def do_GET(self) -> None:
            try:
                self._route()
            except Exception as e:  # one bad request never takes the server down
                _log(f"{self.path.split('?', 1)[0]}: {e!r}")
                with contextlib.suppress(OSError):  # the client may be gone already
                    self._send(500)

        do_HEAD = do_GET

        def _refuse(self) -> None:
            self._send(405)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def start(server: ThreadingHTTPServer, poll_interval: float = 0.5) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever, args=(poll_interval,), name="cardak-web", daemon=True
    )
    thread.start()
    return thread
