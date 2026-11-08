"""Bounded ASGI transport with one stateful Uvicorn worker per BMC."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.message import Message
from typing import Any

import uvicorn

from .engine import MockBMC, RedfishError, Result

LOGGER = logging.getLogger("redfish_mock")
MAX_BODY = 1024 * 1024
BODY_TIMEOUT = 10.0
SHUTDOWN_TIMEOUT = 15
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(*, quiet: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO, handlers=[handler], force=True
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


class RedfishASGI:
    def __init__(
        self, bmc: MockBMC, *, delay: float = 0, body_timeout: float = BODY_TIMEOUT
    ) -> None:
        self.bmc = bmc
        self.delay = delay
        self.body_timeout = body_timeout
        self.ready = False

    async def _body(self, scope: Scope, receive: Receive) -> dict:
        headers = scope["headers"]
        if any(key.lower() == b"transfer-encoding" for key, _ in headers):
            raise RedfishError(501, "Chunked request bodies are not supported.")
        lengths = [value for key, value in headers if key.lower() == b"content-length"]
        try:
            if len(lengths) > 1:
                raise ValueError
            length = int(lengths[0]) if lengths else 0
            if length < 0:
                raise ValueError
        except ValueError as exc:
            raise RedfishError(400, "Invalid Content-Length.") from exc
        if length > MAX_BODY:
            raise RedfishError(413, "Request body exceeds 1 MiB.")
        if length == 0:
            return {}
        content_type = Message()
        for key, value in headers:
            if key.lower() == b"content-type":
                content_type["content-type"] = value.decode("latin-1")
        if content_type.get_content_type() != "application/json":
            raise RedfishError(415, "Use Content-Type: application/json.")
        payload = bytearray()
        async with asyncio.timeout(self.body_timeout):
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    raise ConnectionError
                payload.extend(event.get("body", b""))
                if len(payload) > MAX_BODY:
                    raise RedfishError(413, "Request body exceeds 1 MiB.")
                if not event.get("more_body", False):
                    break
        try:
            if len(payload) != length:
                raise ValueError
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise TypeError
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as exc:
            raise RedfishError(400, "Body must be a valid JSON object.", "MalformedJSON") from exc
        return body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        method = scope["method"]
        raw_path = scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        query = scope.get("query_string", b"").decode("latin-1")
        raw_target = raw_path + ("?" + query if query else "")
        started = time.monotonic()
        try:
            if scope["path"].rstrip("/") == "/readyz" and method in ("GET", "HEAD"):
                result = Result(
                    200 if self.ready else 503, {"ready": self.ready, "profile": self.bmc.profile}
                )
            else:
                if self.delay and scope["path"].rstrip("/") != "/healthz":
                    await asyncio.sleep(self.delay)
                body = (
                    await self._body(scope, receive) if method in ("POST", "PATCH", "PUT") else None
                )
                headers: dict[str, str] = {}
                for key, value in scope["headers"]:
                    name = key.decode("latin-1").lower()
                    if name in headers and name in ("authorization", "x-auth-token"):
                        raise RedfishError(400, "Duplicate authentication header.")
                    headers[name] = value.decode("latin-1")
                # Keep blocking lock acquisition off the event loop (including health probes).
                result = await asyncio.to_thread(self.bmc.handle, method, raw_target, headers, body)
        except RedfishError as exc:
            result = Result(exc.status, self.bmc.error_payload(exc))
            if exc.status == 401:
                result.headers["WWW-Authenticate"] = 'Basic realm="Redfish mock"'
        except TimeoutError:
            result = Result(
                408, self.bmc.error_payload(RedfishError(408, "Request body timed out."))
            )
        except ConnectionError:
            self._log(method, scope["path"], 499, started)
            return
        except Exception:  # noqa: BLE001 -- The transport boundary must return a safe error.
            # Exception messages, request bodies and headers may contain secrets.
            LOGGER.error("request_failed", extra={"fields": {"profile": self.bmc.profile}})  # noqa: TRY400 -- Tracebacks may expose secrets.
            result = Result(
                500, self.bmc.error_payload(RedfishError(500, "Internal mock server error."))
            )
        encoded = (
            json.dumps(result.body, ensure_ascii=False).encode() if result.body is not None else b""
        )
        headers_out = {
            "content-type": "application/json; charset=utf-8",
            "content-length": str(len(encoded)),
            "odata-version": "4.0",
            "cache-control": "no-store",
            **{key.lower(): value for key, value in result.headers.items()},
        }
        if result.status in (408, 413, 415, 501):
            headers_out["connection"] = "close"
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": result.status,
                    "headers": [(k.encode(), v.encode()) for k, v in headers_out.items()],
                }
            )
            await send({"type": "http.response.body", "body": b"" if method == "HEAD" else encoded})
        except OSError, ConnectionError:
            pass
        self._log(method, scope["path"], result.status, started)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                self.ready = True
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                self.ready = False
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _log(self, method: str, path: str, status: int, started: float) -> None:
        # Log route names, never session IDs, arbitrary user paths or query strings.
        if "/SessionService/Sessions/" in path:
            path = "/redfish/v1/SessionService/Sessions/{id}"
        elif path.rstrip("/") not in self.bmc.original and path not in (
            "/",
            "/healthz",
            "/readyz",
            "/redfish",
            "/_mock",
            "/_mock/reset",
            "/_mock/scenario",
            "/_mock/overrides",
        ):
            path = "/{action}" if "/Actions/" in path else "/{unknown}"
        LOGGER.info(
            "request",
            extra={
                "fields": {
                    "profile": self.bmc.profile,
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
            },
        )


class MockHTTPServer:
    """Pre-bind sockets so a fleet either starts completely or releases every port."""

    def __init__(
        self,
        address: tuple[str, int],
        bmc: MockBMC,
        *,
        delay: float = 0,
        limit_concurrency: int = 64,
        trusted_proxies: str = "",
    ) -> None:
        self.bmc = bmc
        self.app = RedfishASGI(bmc, delay=delay)
        self.socket = socket.create_server(address, backlog=64)
        self.server_port = self.socket.getsockname()[1]
        self._finished = threading.Event()
        self._serving = threading.Event()
        self.failure: BaseException | None = None
        self.config = uvicorn.Config(
            self.app,
            host=address[0],
            port=self.server_port,
            workers=1,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            log_config=None,
            access_log=False,
            server_header=False,
            limit_concurrency=limit_concurrency,
            backlog=64,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=SHUTDOWN_TIMEOUT,
            proxy_headers=bool(trusted_proxies),
            forwarded_allow_ips=trusted_proxies,
        )
        self.runner = uvicorn.Server(self.config)

    def enable_tls(self, cert: str, key: str) -> None:
        # Validate before starting any fleet member.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        self.config.ssl_certfile = cert
        self.config.ssl_keyfile = key

    def serve_forever(self, *, poll_interval: float = 0.1) -> None:
        del poll_interval  # Retained for callers of the original transport adapter.
        self._serving.set()
        try:
            self.runner.run(sockets=[self.socket])
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- Report worker startup failures.
            self.failure = exc
        finally:
            self.app.ready = False
            self._finished.set()

    def shutdown(self) -> None:
        self.app.ready = False
        self.runner.should_exit = True

    def server_close(self) -> None:
        if self._serving.is_set():
            self.shutdown()
            self._finished.wait(SHUTDOWN_TIMEOUT + 2)
        self.socket.close()

    def wait_started(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while not self.runner.started:
            if self._finished.wait(0.01) or time.monotonic() >= deadline:
                raise RuntimeError("HTTP server did not become ready.")
