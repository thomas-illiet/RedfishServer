"""Start and stop one BMC or an independent three-vendor fleet."""

from __future__ import annotations

import ipaddress
import json
import math
import signal
import threading
import time
from typing import TYPE_CHECKING

from .config import credentials, parser
from .engine import PROFILES, MockBMC
from .server import SHUTDOWN_TIMEOUT, MockHTTPServer, configure_logging

if TYPE_CHECKING:
    from types import FrameType


def main(argv: list[str] | None = None) -> None:
    """Validate options, start all profile workers and coordinate graceful shutdown."""
    cli = parser()
    args = cli.parse_args(argv)
    vendors = PROFILES if args.multi_profile else PROFILES[:3] if args.fleet else (args.vendor,)
    ports = 1 if args.multi_profile else len(vendors)
    if not 1 <= args.port <= 65536 - ports:
        cli.error("Choose a valid port range (1-65535).")
    if not math.isfinite(args.delay_ms) or not 0 <= args.delay_ms <= 60000:
        cli.error("--delay-ms must be between 0 and 60000.")
    if bool(args.cert) != bool(args.key):
        cli.error("--cert and --key must be supplied together.")
    args.username, args.password = credentials(cli, args)
    for network in args.trusted_proxies.split(",") if args.trusted_proxies else ():
        try:
            ipaddress.ip_network(network.strip(), strict=False)
        except ValueError:
            cli.error("--trusted-proxies must contain explicit IP addresses or CIDR networks.")
    configure_logging(quiet=args.quiet)
    servers: list[MockHTTPServer] = []
    workers: list[threading.Thread] = []
    stop = threading.Event()
    previous_signals = {}

    def _request_stop(_signum: int, _frame: FrameType | None) -> None:
        """Request graceful fleet shutdown when an operating-system signal arrives."""
        stop.set()

    try:
        profiles = {
            vendor: MockBMC(
                vendor,
                mockups_dir=args.mockups_dir,
                username=args.username,
                password=args.password,
                require_auth=not args.no_auth,
                session_timeout=args.session_timeout,
                play_sequences=args.play_sequences,
                raw_fixtures=args.raw_fixtures,
                dynamic_metrics=not args.static_metrics,
                metric_interval=args.metric_interval,
                seed=args.seed,
            )
            for vendor in vendors
        }
        groups = [profiles] if args.multi_profile else list(profiles.values())
        for offset, bmc in enumerate(groups):
            server = MockHTTPServer(
                (args.host, args.port + offset),
                bmc,
                delay=args.delay_ms / 1000,
                trusted_proxies=args.trusted_proxies,
            )
            servers.append(server)
            if args.cert:
                server.enable_tls(args.cert, args.key)
        # Bind all ports before starting; a conflict never leaves a partial fleet running.
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signals[signum] = signal.signal(signum, _request_stop)
        for server in servers:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            workers.append(worker)
        for server in servers:
            server.wait_started()
        for server in servers:
            scheme = "https" if args.cert else "http"
            for profile in server.profiles:
                prefix = "/" + profile if args.multi_profile else ""
                print(
                    json.dumps(
                        {
                            "event": "started",
                            "profile": profile,
                            "url": f"{scheme}://{args.host}:{server.server_port}{prefix}/redfish/v1/",
                            "storage": "memory",
                            "production": args.production,
                        }
                    ),
                    flush=True,
                )
        while not stop.wait(0.2):
            if any(not worker.is_alive() for worker in workers):
                raise RuntimeError("A profile server stopped unexpectedly.")
    except (OSError, ValueError, RuntimeError) as exc:
        cli.exit(
            1,
            f"Cannot start Redfish mock ({type(exc).__name__}); "
            "check ports, certificates and fixtures.\n",
        )
    finally:
        for server in servers[: len(workers)]:
            server.shutdown()
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT + 2
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        for server in servers:
            server.server_close()
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
