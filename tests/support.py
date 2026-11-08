"""Reusable HTTP and ASGI test drivers."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import threading

from redfish_mock.engine import MockBMC
from redfish_mock.server import MockHTTPServer


@contextlib.contextmanager
def running(**kwargs):
    """Start a temporary production-style server and ensure its cleanup."""
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
    """Send an HTTP request and decode its response while closing the connection."""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def invoke(app, *, path="/readyz", headers=(), body=b"", events=None):
    """Drive an ASGI application with explicit events and collect its responses."""

    async def run():
        """Execute the asynchronous ASGI test exchange."""
        sent = []
        queue = list(events) if events is not None else [{"type": "http.request", "body": body}]

        async def receive():
            """Provide the next queued request event to the application."""
            return queue.pop(0)

        async def send(event):
            """Capture an outgoing ASGI event for test assertions."""
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
