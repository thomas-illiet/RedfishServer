"""Single-worker Uvicorn lifecycle with atomic fleet socket binding."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from typing import TYPE_CHECKING

import uvicorn

from .asgi import RedfishASGI
from .router import ProfileRouter

if TYPE_CHECKING:
    from redfish_mock.engine import MockBMC

SHUTDOWN_TIMEOUT = 15


class MockHTTPServer:
    """Pre-bind sockets so a fleet either starts completely or releases every port."""

    def __init__(
        self,
        address: tuple[str, int],
        bmc: MockBMC | dict[str, MockBMC],
        *,
        delay: float = 0,
        limit_concurrency: int | None = None,
        trusted_proxies: str = "",
    ) -> None:
        """Reserve a socket and configure one bounded Uvicorn worker for this BMC."""
        self.bmc = bmc
        self.profiles = bmc if isinstance(bmc, dict) else {bmc.profile: bmc}
        self.app = (
            ProfileRouter(bmc, delay=delay)
            if isinstance(bmc, dict)
            else RedfishASGI(bmc, delay=delay)
        )
        if limit_concurrency is None:
            limit_concurrency = 320 if isinstance(bmc, dict) else 64
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
        """Validate the certificate and key before configuring TLS for this worker."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        self.config.ssl_certfile = cert
        self.config.ssl_keyfile = key

    def serve_forever(self, *, poll_interval: float = 0.1) -> None:
        """Run Uvicorn on the pre-bound socket and record startup failures."""
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
        """Clear readiness and request graceful worker shutdown."""
        self.app.ready = False
        self.runner.should_exit = True

    def server_close(self) -> None:
        """Wait for bounded worker shutdown and release the listening socket."""
        if self._serving.is_set():
            self.shutdown()
            self._finished.wait(SHUTDOWN_TIMEOUT + 2)
        self.socket.close()

    def wait_started(self, timeout: float = 10) -> None:
        """Wait until Uvicorn is ready or raise on failure or startup timeout."""
        deadline = time.monotonic() + timeout
        while not self.runner.started:
            if self._finished.wait(0.01) or time.monotonic() >= deadline:
                raise RuntimeError("HTTP server did not become ready.")
