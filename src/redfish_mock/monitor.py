"""Collect read-only KPIs through the Redfish HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
from datetime import UTC, datetime

from .collector import RedfishClient, snapshot

__all__ = ["RedfishClient", "main", "snapshot"]


def main() -> None:
    """Collect and print per-server KPIs using the configured credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/dell")
    parser.add_argument(
        "--ca-file", help="CA certificate for HTTPS; TLS verification stays enabled"
    )
    args = parser.parse_args()
    try:
        client = RedfishClient(
            args.url,
            os.environ.get("REDFISH_USERNAME", "admin"),
            os.environ.get("REDFISH_PASSWORD", "admin"),
            ca_file=args.ca_file,
        )
        print(
            json.dumps(
                {"collected_at": datetime.now(UTC).isoformat(), "servers": snapshot(client.get)},
                indent=2,
            )
        )
    except (urllib.error.URLError, ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Collection failed: {error}\n")


if __name__ == "__main__":
    main()
