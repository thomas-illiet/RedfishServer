"""Run local verification or package a previously verified release."""

from __future__ import annotations

import argparse
import subprocess

from .packaging import package
from .verification import verify


def main() -> None:
    """Dispatch verification or packaging and report release-gate failures."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("verify", "package"))
    args = cli.parse_args()
    try:
        verify() if args.action == "verify" else package()
    except subprocess.CalledProcessError as exc:
        # Full command arguments can include local paths; reports contain command output.
        raise SystemExit(f"Release gate failed (exit {exc.returncode}); inspect reports/.") from exc


if __name__ == "__main__":
    main()
