"""Vulnerability and SBOM reports for the exact image archives."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .commands import docker
from .config import ROOT, TOOLS

if TYPE_CHECKING:
    from pathlib import Path


def scan_archive(arch: str, archive: Path, report: Path) -> None:
    """Scan the exact saved image and emit vulnerability and CycloneDX reports."""
    cache = ROOT / ".cache/trivy"
    cache.mkdir(parents=True, exist_ok=True)
    common = [
        "run",
        "--rm",
        "--mount",
        f"type=bind,src={archive.parent},dst=/input,readonly",
        "--mount",
        f"type=bind,src={report},dst=/out",
        "--mount",
        f"type=bind,src={cache},dst=/root/.cache",
        TOOLS["scanner"],
        "image",
        "--input",
        f"/input/{archive.name}",
    ]
    docker(
        *common,
        "--scanners",
        "vuln",
        "--severity",
        "HIGH,CRITICAL",
        "--exit-code",
        "1",
        "--format",
        "json",
        "--output",
        f"/out/{arch}-vulnerabilities.json",
        log=report / f"{arch}-scan.log",
    )
    docker(
        *common,
        "--format",
        "cyclonedx",
        "--output",
        f"/out/{arch}-sbom.cdx.json",
        log=report / f"{arch}-sbom.log",
    )
