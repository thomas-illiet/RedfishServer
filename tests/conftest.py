"""Shared ephemeral HTTP server fixture and test client."""

from __future__ import annotations

import base64
import http.client
import json
import ssl
import threading

import pytest

from redfish_mock.constants import PROFILES
from redfish_mock.engine import MockBMC
from redfish_mock.server import MockHTTPServer

AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()


class Client:
    """Drive authenticated HTTP requests against a disposable mock server."""

    def __init__(self, server, context=None):
        """Keep the temporary server and optional trusted TLS context."""
        self.server = server
        self.context = context

    def request(self, method, path, body=None, *, auth=True, headers=None, raw=None):
        """Send an HTTP request and decode its response while closing the connection."""
        hdrs = {"Authorization": AUTH} if auth else {}
        if headers:
            hdrs.update(headers)
        if body is not None:
            raw = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if self.context:
            conn = http.client.HTTPSConnection(
                "127.0.0.1", self.server.server_port, context=self.context, timeout=3
            )
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, raw, hdrs)
            response = conn.getresponse()
            payload = response.read()
            return (
                response.status,
                (
                    json.loads(payload)
                    if payload and response.headers.get_content_type() == "application/json"
                    else payload.decode()
                    if payload and not response.headers.get_content_type().startswith("image/")
                    else payload or None
                ),
                response.headers,
            )
        finally:
            conn.close()


@pytest.fixture
def serve():
    """Start ephemeral mock servers and close every worker after the test."""
    servers = []
    threads = []

    def start(profile="dell", *, tls=None, multi=False, **kwargs):
        """Create one test server with optional TLS and static metrics by default."""
        kwargs.setdefault("dynamic_metrics", False)
        bmcs = (
            {name: MockBMC(name, **kwargs) for name in PROFILES}
            if multi
            else MockBMC(profile, **kwargs)
        )
        server = MockHTTPServer(("127.0.0.1", 0), bmcs)
        context = None
        if tls:
            cert, key = tls
            server.enable_tls(str(cert), str(key))
            context = ssl.create_default_context(cafile=str(cert))
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        worker.start()
        servers.append(server)
        threads.append(worker)
        server.wait_started()
        return Client(server, context)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
    for worker in threads:
        worker.join(timeout=3)


@pytest.fixture(autouse=True)
def clean_credentials(monkeypatch):
    """Clear inherited credential sources so each test controls authentication."""
    for key in (
        "REDFISH_PASSWORD",
        "REDFISH_PASSWORD_FILE",
        "REDFISH_USERNAME",
        "REDFISH_TRUSTED_PROXIES",
    ):
        monkeypatch.delenv(key, raising=False)
