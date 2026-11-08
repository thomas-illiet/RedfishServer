"""Bounded subprocess execution without shell expansion."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from .config import ROOT

if TYPE_CHECKING:
    from pathlib import Path


def run(
    args: list[str],
    *,
    log: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 900,
    both_streams: bool = False,
) -> str:
    """No shell expansion; callers supply only commands and non-secret arguments."""
    if log:
        with log.open("w") as output:
            subprocess.run(
                args,
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
                timeout=timeout,
            )
        return ""
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=timeout
    )
    return (result.stdout + (result.stderr if both_streams else "")).strip()


def docker(*args: str, **kwargs: Any) -> str:
    """Execute Docker with bounded subprocess handling and optional report capture."""
    return run(["docker", *args], **kwargs)
