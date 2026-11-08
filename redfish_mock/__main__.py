"""Run one profile or the three primary vendors on independent ports."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import signal
import threading
import time
from pathlib import Path

from .engine import FIXTURES, PROFILES, MockBMC
from .server import SHUTDOWN_TIMEOUT, MockHTTPServer, configure_logging


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        description="Local Redfish BMC mocks for Dell, HPE and Supermicro."
    )
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--vendor", choices=PROFILES, default="dell")
    mode.add_argument(
        "--fleet", action="store_true", help="Dell on port, HPE on port+1, Supermicro on port+2"
    )
    cli.add_argument("--host", default="127.0.0.1")
    cli.add_argument("--port", type=int, default=8001)
    cli.add_argument("--mockups-dir", type=Path, default=FIXTURES)
    cli.add_argument(
        "--raw-fixtures",
        action="store_true",
        help="Serve uncorrected rackhands payloads for regression comparison",
    )
    cli.add_argument("--username", default=os.environ.get("REDFISH_USERNAME"))
    cli.add_argument("--password")
    cli.add_argument("--password-file", type=Path)
    cli.add_argument("--production", action="store_true")
    cli.add_argument("--trusted-proxies", default=os.environ.get("REDFISH_TRUSTED_PROXIES", ""))
    cli.add_argument("--no-auth", action="store_true")
    cli.add_argument("--session-timeout", type=int, default=1800)
    cli.add_argument(
        "--play-sequences", action="store_true", help="Advance fixture _steps on each GET"
    )
    cli.add_argument("--delay-ms", type=float, default=0, help="Add latency to API responses")
    cli.add_argument("--static-metrics", action="store_true", help="Disable evolving telemetry")
    cli.add_argument(
        "--metric-interval",
        type=int,
        default=os.environ.get("REDFISH_METRIC_INTERVAL", "5"),
        help="Telemetry interval, 1-60 seconds",
    )
    cli.add_argument(
        "--seed",
        type=int,
        default=os.environ.get("REDFISH_SEED") or None,
        help="Seed for reproducible telemetry",
    )
    cli.add_argument("--cert", help="TLS certificate (PEM)")
    cli.add_argument("--key", help="TLS private key (PEM)")
    cli.add_argument("--quiet", action="store_true")
    return cli


def credentials(cli: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, str]:
    sources = [
        ("value", args.password),
        ("file", args.password_file),
        ("value", os.environ.get("REDFISH_PASSWORD")),
        ("file", os.environ.get("REDFISH_PASSWORD_FILE")),
    ]
    supplied = [(kind, value) for kind, value in sources if value is not None]
    if len(supplied) > 1:
        cli.error("Configure exactly one password source: option, environment or secret file.")
    password = "admin"
    if supplied:
        kind, value = supplied[0]
        if kind == "file":
            try:
                with Path(value).open("rb") as stream:
                    secret = stream.read(65537)
                if len(secret) > 65536:
                    raise ValueError
                password = secret.decode("utf-8").removesuffix("\n").removesuffix("\r")
            except OSError, UnicodeError, ValueError:
                cli.error("Cannot read a valid password secret file.")
        else:
            password = value
    if args.production and (
        args.no_auth or not args.username or not supplied or password == "admin"
    ):
        cli.error(
            "Production requires authentication, an explicit username and a non-default password."
        )
    username = args.username if args.username is not None else "admin"
    if not username.strip() or not password.strip():
        cli.error("Credentials must not be empty.")
    return username, password


def main(argv: list[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    vendors = PROFILES[:3] if args.fleet else (args.vendor,)
    if not 1 <= args.port <= 65536 - len(vendors):
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
    try:
        for offset, vendor in enumerate(vendors):
            bmc = MockBMC(
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
            previous_signals[signum] = signal.signal(signum, lambda *_: stop.set())
        for server in servers:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            workers.append(worker)
        for server in servers:
            server.wait_started()
        for server in servers:
            scheme = "https" if args.cert else "http"
            print(
                json.dumps(
                    {
                        "event": "started",
                        "profile": server.bmc.profile,
                        "url": f"{scheme}://{args.host}:{server.server_port}/redfish/v1/",
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


if __name__ == "__main__":
    main()
