"""Command-line options and validated credential sources."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .constants import FIXTURES, PROFILES


def parser() -> argparse.ArgumentParser:
    """Build server options with environment-backed defaults."""
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
    """Resolve one password source and enforce production credential requirements."""
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
