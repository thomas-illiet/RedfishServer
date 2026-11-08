"""Production transport regression tests."""

from __future__ import annotations

import http.client
import json
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from redfish_mock.engine import MockBMC
from redfish_mock.server import JsonFormatter, MockHTTPServer, RedfishASGI

from .support import invoke, request, running

PROJECT = Path(__file__).resolve().parents[1]


def test_body_timeout_disconnect_and_recovery():
    """Verify body timeout disconnect and recovery."""
    with running() as server:
        server.app.body_timeout = 0.05
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as conn:
            conn.sendall(
                b"POST /redfish/v1/SessionService/Sessions HTTP/1.1\r\n"
                b"Host: localhost\r\nContent-Type: application/json\r\n"
                b"Content-Length: 20\r\n\r\n{"
            )
            response = http.client.HTTPResponse(conn)
            response.begin()
            assert response.status == 408
            assert json.loads(response.read())["error"]
        conn = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        conn.sendall(
            b"POST /redfish/v1/SessionService/Sessions HTTP/1.1\r\n"
            b"Host: localhost\r\nContent-Type: application/json\r\n"
            b"Content-Length: 20\r\n\r\n{"
        )
        conn.close()
        assert request(server)[0] == 200
        assert request(server, "/readyz")[0] == 200


def test_overload_is_bounded_and_recovers():
    """Verify overload is bounded and recovers."""
    with running(limit_concurrency=4) as server:
        held = []
        try:
            for _ in range(3):
                conn = socket.create_connection(("127.0.0.1", server.server_port), timeout=3)
                held.append(conn)
                conn.sendall(
                    b"POST /redfish/v1/SessionService/Sessions HTTP/1.1\r\n"
                    b"Host: localhost\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 20\r\n\r\n{"
                )
                deadline = time.monotonic() + 2
                while len(server.runner.server_state.connections) < len(held):
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
            assert request(server)[0] == 503
        finally:
            for conn in held:
                conn.close()
        deadline = time.monotonic() + 3
        while server.runner.server_state.connections:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert request(server)[0] == 200


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ([(b"content-length", b"2"), (b"content-length", b"2")], b"{}", 400),
        ([(b"content-length", b"no")], b"{}", 400),
        ([(b"content-length", b"-1")], b"{}", 400),
        ([(b"content-length", b"3"), (b"content-type", b"application/json")], b"{}", 400),
        (
            [(b"content-length", b"1"), (b"content-type", b"application/json")],
            b"x" * (1024 * 1024 + 1),
            413,
        ),
        ([(b"authorization", b"a"), (b"authorization", b"b")], b"", 400),
    ],
)
def test_asgi_rejects_invalid_framing(headers, body, status):
    """Verify asgi rejects invalid framing."""
    app = RedfishASGI(MockBMC("dell"))
    assert (
        invoke(app, path="/redfish/v1/SessionService/Sessions", headers=headers, body=body)[0][
            "status"
        ]
        == status
    )


def test_readiness_and_safe_error_logging(caplog, monkeypatch):
    """Verify readiness and safe error logging."""
    app = RedfishASGI(MockBMC("dell"))
    assert invoke(app)[0]["status"] == 503
    app.ready = True
    assert invoke(app)[0]["status"] == 200
    app.ready = False
    assert invoke(app)[0]["status"] == 503

    def broken(*_):
        """Raise a sensitive error to verify that server logging does not expose it."""
        raise ValueError("DO-NOT-LOG-THIS-SECRET")

    monkeypatch.setattr(app.bmc, "handle", broken)
    with caplog.at_level(logging.INFO, logger="redfish_mock"):
        result = invoke(app, path="/redfish/v1/SessionService/Sessions/DO-NOT-LOG-THIS-SECRET")
    assert result[0]["status"] == 500
    formatted = [JsonFormatter().format(record) for record in caplog.records]
    assert "DO-NOT-LOG-THIS-SECRET" not in str(formatted)
    record = json.loads(formatted[-1])
    assert record["path"].endswith("/{id}")
    assert record["status"] == 500
    assert record["duration_ms"] >= 0


def test_startup_failure_and_reserved_ports_are_released():
    # Reserve a consecutive range, then deliberately occupy the second fleet port.
    """Verify startup failure and reserved ports are released."""
    with socket.socket() as first, socket.socket() as second, socket.socket() as third:
        for _ in range(100):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            try:
                second.bind(("127.0.0.1", port + 1))
                third.bind(("127.0.0.1", port + 2))
                break
            except OSError:
                continue
        else:
            pytest.fail("Could not find a consecutive port range")
        second.listen()
        result = subprocess.run(
            [sys.executable, "-m", "redfish_mock", "--fleet", "--port", str(port)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 1
        assert '"event": "started"' not in result.stdout
        first.bind(("127.0.0.1", port))


def test_worker_failure_is_reported(monkeypatch):
    """Verify worker failure is reported."""
    server = MockHTTPServer(("127.0.0.1", 0), MockBMC("dell"))

    def failed(**_):
        """Simulate a Uvicorn startup failure for lifecycle error handling."""
        raise RuntimeError("failed worker")

    monkeypatch.setattr(server.runner, "run", failed)
    server.serve_forever()
    with pytest.raises(RuntimeError, match="ready"):
        server.wait_started()
    server.server_close()
    assert isinstance(server.failure, RuntimeError)


def test_proxy_headers_disabled_by_default():
    """Verify proxy headers disabled by default."""
    server = MockHTTPServer(("127.0.0.1", 0), MockBMC("dell"))
    try:
        assert server.config.workers == 1
        assert server.config.proxy_headers is False
        assert server.config.forwarded_allow_ips == ""
        assert server.config.limit_concurrency == 64
    finally:
        server.server_close()


def test_production_cli_secret_signal_and_proxy(tmp_path):
    """Verify production cli secret signal and proxy."""
    secret = tmp_path / "secret"
    secret.write_text("test-production-secret")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "redfish_mock",
            "--production",
            "--username",
            "collector",
            "--password-file",
            str(secret),
            "--trusted-proxies",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/readyz")
                response = conn.getresponse()
                assert response.status == 200
                response.read()
                conn.close()
                break
            except OSError:
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.02)
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=17)
    assert process.returncode == 0, stderr
    assert "test-production-secret" not in stdout + stderr
    assert all(isinstance(json.loads(line), dict) for line in (stdout + stderr).splitlines())


def test_deeply_nested_json_does_not_crash_server():
    """Verify deeply nested json does not crash server."""
    body = b'{"nested":' + b"[" * 100000 + b"0" + b"]" * 100000 + b"}"
    app = RedfishASGI(MockBMC("dell"))
    result = invoke(
        app,
        path="/redfish/v1/SessionService/Sessions",
        body=body,
        headers=[
            (b"content-length", str(len(body)).encode()),
            (b"content-type", b"application/json"),
        ],
    )
    # Python builds vary in their recursion ceiling; otherwise credentials are rejected.
    assert result[0]["status"] in (400, 401)
