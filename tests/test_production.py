from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from redfish_mock.__main__ import credentials, main, parser
from redfish_mock.engine import MockBMC
from redfish_mock.server import JsonFormatter, MockHTTPServer, RedfishASGI

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_credentials(monkeypatch):
    for key in (
        "REDFISH_PASSWORD",
        "REDFISH_PASSWORD_FILE",
        "REDFISH_USERNAME",
        "REDFISH_TRUSTED_PROXIES",
    ):
        monkeypatch.delenv(key, raising=False)


@contextlib.contextmanager
def running(**kwargs):
    server = MockHTTPServer(("127.0.0.1", 0), MockBMC("dell"), **kwargs)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    server.wait_started()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=17)
        server.server_close()
        assert not worker.is_alive()
        assert server.failure is None


def request(server, path="/healthz"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "args",
    [
        ["--production"],
        ["--production", "--username", "admin", "--password", "admin"],
        ["--production", "--username", "admin", "--password", "test-secret", "--no-auth"],
        ["--production", "--password", "test-secret"],
        ["--username", " ", "--password", "test-secret"],
        ["--password", ""],
        ["--password-file", "/missing/redfish-secret"],
    ],
)
def test_invalid_credentials_fail_before_binding(args):
    cli = parser()
    with pytest.raises(SystemExit) as exc:
        credentials(cli, cli.parse_args(args))
    assert exc.value.code == 2


@pytest.mark.parametrize("secret", [b"", b" \n", b"\xff", b"x" * 65537, "é".encode() * 32769])
def test_invalid_secret_file(tmp_path, secret, capsys):
    path = tmp_path / "password"
    path.write_bytes(secret)
    cli = parser()
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password-file", str(path)]))
    assert "x" * 100 not in capsys.readouterr().err


def test_secret_file_and_environment(tmp_path, monkeypatch):
    path = tmp_path / "password"
    path.write_text("test-secret\r\n")
    monkeypatch.setenv("REDFISH_USERNAME", "collector")
    monkeypatch.setenv("REDFISH_PASSWORD_FILE", str(path))
    cli = parser()
    assert credentials(cli, cli.parse_args(["--production"])) == ("collector", "test-secret")
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password", "another"]))
    monkeypatch.delenv("REDFISH_PASSWORD_FILE")
    monkeypatch.setenv("REDFISH_PASSWORD", "from-env")
    assert credentials(cli, cli.parse_args(["--production"])) == ("collector", "from-env")
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password-file", str(path)]))
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password", "from-env"]))


def test_cli_secret_option_and_local_defaults(tmp_path):
    path = tmp_path / "password"
    path.write_text("test-secret")
    cli = parser()
    assert credentials(cli, cli.parse_args([])) == ("admin", "admin")
    assert credentials(
        cli,
        cli.parse_args(["--production", "--username", "collector", "--password-file", str(path)]),
    ) == ("collector", "test-secret")


@pytest.mark.parametrize(
    "args", [["--trusted-proxies", "*"], ["--delay-ms", "nan"], ["--delay-ms", "inf"]]
)
def test_invalid_transport_configuration(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_body_timeout_disconnect_and_recovery():
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


def invoke(app, *, path="/readyz", headers=(), body=b"", events=None):
    async def run():
        sent = []
        queue = list(events) if events is not None else [{"type": "http.request", "body": body}]

        async def receive():
            return queue.pop(0)

        async def send(event):
            sent.append(event)

        await app(
            {
                "type": "http",
                "method": "POST" if path != "/readyz" else "GET",
                "path": path,
                "headers": list(headers),
                "query_string": b"",
            },
            receive,
            send,
        )
        return sent

    return asyncio.run(run())


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
    app = RedfishASGI(MockBMC("dell"))
    assert (
        invoke(app, path="/redfish/v1/SessionService/Sessions", headers=headers, body=body)[0][
            "status"
        ]
        == status
    )


def test_readiness_and_safe_error_logging(caplog, monkeypatch):
    app = RedfishASGI(MockBMC("dell"))
    assert invoke(app)[0]["status"] == 503
    app.ready = True
    assert invoke(app)[0]["status"] == 200
    app.ready = False
    assert invoke(app)[0]["status"] == 503

    def broken(*_):
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
    server = MockHTTPServer(("127.0.0.1", 0), MockBMC("dell"))

    def failed(**_):
        raise RuntimeError("failed worker")

    monkeypatch.setattr(server.runner, "run", failed)
    server.serve_forever()
    with pytest.raises(RuntimeError, match="ready"):
        server.wait_started()
    server.server_close()
    assert isinstance(server.failure, RuntimeError)


def test_proxy_headers_disabled_by_default():
    server = MockHTTPServer(("127.0.0.1", 0), MockBMC("dell"))
    try:
        assert server.config.workers == 1
        assert server.config.proxy_headers is False
        assert server.config.forwarded_allow_ips == ""
        assert server.config.limit_concurrency == 64
    finally:
        server.server_close()


def test_production_cli_secret_signal_and_proxy(tmp_path):
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
