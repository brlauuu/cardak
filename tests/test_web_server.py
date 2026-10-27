import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

import pytest
from test_web_view import snap

from cardak.core.agents import SessionState
from cardak.web.server import Latest, csp, make_server, start

KEY = "k" * 43


def session_ok(session_id: str) -> SessionState | None:
    return SessionState(session_id, last_prompt="do the thing")


@pytest.fixture
def serve() -> Iterator[Callable[..., tuple[str, Latest]]]:
    servers = []

    def run(session=session_ok, clock=lambda: 112.0, timeout=10.0) -> tuple[str, Latest]:
        latest = Latest()
        server = make_server("127.0.0.1", 0, KEY, latest, session, clock=clock, timeout=timeout)
        start(server, poll_interval=0.02)
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", latest

    yield run
    for s in servers:
        s.shutdown()
        s.server_close()


def get(url: str, key: str | None = KEY, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    if key is not None:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_page_needs_no_key_and_has_csp(serve) -> None:
    base, _ = serve()
    status, headers, body = get(base + "/", key=None)
    assert status == 200 and body.startswith(b"<!doctype html>")
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Content-Security-Policy"] == csp()
    assert "connect-src 'self'" in csp() and "default-src 'none'" in csp()


def test_snapshot_requires_the_key(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    for key in (None, "wrong", KEY[:-1] + "x"):
        status, _, body = get(base + "/api/snapshot", key=key)
        assert status == 401 and body == b""
    assert get(base + "/api/snapshot")[0] == 200


def test_snapshot_before_first_poll_is_503(serve) -> None:
    base, _ = serve()
    status, _, body = get(base + "/api/snapshot")
    assert status == 503 and json.loads(body) == {"starting": True}


def test_snapshot_ts_stays_while_now_moves(serve) -> None:
    times = iter([105.0, 200.0])
    base, latest = serve(clock=lambda: next(times))
    latest.publish(snap(), 100.0)
    first = json.loads(get(base + "/api/snapshot")[2])
    second = json.loads(get(base + "/api/snapshot")[2])
    assert (first["ts"], first["now"]) == (100.0, 105.0)
    assert (second["ts"], second["now"]) == (100.0, 200.0)


def test_session_unknown_is_404(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    assert get(base + "/api/session/ended")[0] == 404


def test_session_needs_the_key(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    assert get(base + "/api/session/s1", key=None)[0] == 401


def test_session_found(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    status, headers, body = get(base + "/api/session/s1")
    out = json.loads(body)
    assert status == 200 and headers["Content-Type"] == "application/json; charset=utf-8"
    assert out["last_prompt"] == "do the thing" and out["tag"] == "approve"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_writes_are_405(serve, method: str) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    assert get(base + "/api/snapshot", method=method)[0] == 405
    assert get(base + "/", method=method)[0] == 405


def test_head_has_no_body(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    status, _, body = get(base + "/api/snapshot", method="HEAD")
    assert status == 200 and body == b""


def test_unknown_path_404(serve) -> None:
    base, _ = serve()
    for path in ("/x", "/api", "/api/session/", "/api/snapshot/x", "/../etc/passwd"):
        assert get(base + path)[0] == 404


def test_security_headers(serve) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    for path in ("/", "/api/snapshot", "/nope"):
        headers = get(base + path)[1]
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "Access-Control-Allow-Origin" not in headers


def test_handler_error_is_500_and_server_keeps_serving(serve, capsys) -> None:
    def broken(session_id: str) -> SessionState | None:
        raise RuntimeError("database is locked")

    base, latest = serve(session=broken)
    latest.publish(snap(), 100.0)
    assert get(base + "/api/session/s1")[0] == 500
    assert get(base + "/api/snapshot")[0] == 200
    assert "database is locked" in capsys.readouterr().err


def test_no_request_logging(serve, capsys) -> None:
    base, latest = serve()
    latest.publish(snap(), 100.0)
    get(base + "/api/snapshot")
    get(base + "/nope")
    assert capsys.readouterr().err == ""


def test_idle_connection_is_closed(serve) -> None:
    import socket

    base, _ = serve(timeout=0.3)
    port = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        assert s.recv(1) == b""  # the server gave up on a client that never sent a request
