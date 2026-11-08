"""Coordinate dependency, image and runtime release gates."""

from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.coverage_gate import check as coverage_check

from .commands import docker, run
from .config import ARCHES, REPORTS, ROOT, TOOLS, VERIFIED, VERSION
from .containers import verify_containers
from .manifest import checksum, source_fingerprint
from .scanning import scan_archive


def verify() -> None:
    """Run all release gates and record evidence for the exact source and image digests."""
    REPORTS.mkdir(exist_ok=True)
    VERIFIED.unlink(missing_ok=True)
    fingerprint = source_fingerprint()
    report = REPORTS / (
        "verify-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    report.mkdir()
    cache = ROOT / ".cache/release-images"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Verification reports: {report.relative_to(ROOT)}", flush=True)
    coverage = coverage_check()
    for name in ("coverage.json", "coverage.xml"):
        shutil.copy2(REPORTS / name, report / name)
    with tempfile.TemporaryDirectory(prefix="redfish-audit-") as temporary:
        requirements = Path(temporary) / "runtime.txt"
        requirements.write_text(
            run(
                ["uv", "export", "--frozen", "--no-dev", "--no-group", "audit", "--no-emit-project"]
            )
        )
        run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--require-hashes",
                "--no-deps",
                "--disable-pip",
                "--strict",
                "-r",
                str(requirements),
                "--format",
                "json",
                "--output",
                str(report / "dependency-audit.json"),
            ],
            log=report / "dependency-audit.log",
        )
    host_arch = docker("info", "--format", "{{.Architecture}}")
    data: dict[str, Any] = {
        "version": VERSION,
        "source_sha256": fingerprint,
        "created": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "coverage_percent": coverage,
        "docker_architecture": host_arch,
        "tools": TOOLS,
        "report_directory": str(report.relative_to(ROOT)),
        "images": {},
    }
    for arch in ARCHES:
        image = f"redfish-mock:{VERSION}-{arch}"
        print(f"[{arch}] Building and auditing {image}", flush=True)
        docker(
            "buildx",
            "build",
            "--platform",
            f"linux/{arch}",
            "--load",
            "--label",
            f"org.opencontainers.image.revision={fingerprint}",
            "-t",
            image,
            ".",
            log=report / f"{arch}-build.log",
        )
        image_info = json.loads(docker("image", "inspect", image))[0]
        if image_info["Architecture"] != arch:
            raise RuntimeError("Built image architecture does not match target")
        runtime = json.loads(
            docker(
                "run",
                "--rm",
                "--platform",
                f"linux/{arch}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--entrypoint",
                "/app/.venv/bin/python",
                image,
                "-c",
                "import importlib.util,json,os,platform,shutil; "
                "print(json.dumps({'python':platform.python_version(),'uid':os.getuid(),"
                "'tools':{name:shutil.which(name) for name in "
                "('sh','bash','busybox','apk','pip','uv')},"
                "'pip':importlib.util.find_spec('pip') is not None,"
                "'ensurepip':importlib.util.find_spec('ensurepip') is not None}))",
            )
        )
        if (
            runtime["python"] != (ROOT / ".python-version").read_text().strip()
            or runtime["uid"] != 10001
            or any(runtime["tools"].values())
            or runtime["pip"]
            or runtime["ensurepip"]
        ):
            raise RuntimeError("Runtime version or distroless checks failed")
        (report / f"{arch}-runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        archive = cache / f"redfishserver-{VERSION}-{arch}.tar"
        docker("image", "save", "--output", str(archive), image)
        scan_archive(arch, archive, report)
        results = verify_containers(arch, image, report)
        data["images"][arch] = {
            "tag": image,
            "id": image_info["Id"],
            "runtime": runtime,
            "archive": str(archive.relative_to(ROOT)),
            "sha256": checksum(archive),
            "emulated": arch != {"aarch64": "arm64", "x86_64": "amd64"}.get(host_arch, host_arch),
            **results,
        }
        (report / "progress.json").write_text(json.dumps(data, indent=2) + "\n")
    if fingerprint != source_fingerprint():
        raise RuntimeError("Sources changed during verification; run make verify again")
    data["status"] = "passed"
    (report / "verification.json").write_text(json.dumps(data, indent=2) + "\n")
    VERIFIED.write_text(json.dumps(data, indent=2) + "\n")
    print("Both architectures passed all release gates.", flush=True)
