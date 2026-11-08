"""Package only previously verified images and unchanged source files."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .commands import docker
from .config import ARCHES, ROOT, VERIFIED, VERSION
from .manifest import checksum, source_files, source_fingerprint


def package() -> None:
    """Publish a local release directory only from unchanged verified sources and images."""
    data = json.loads(VERIFIED.read_text())
    if data.get("status") != "passed" or data["source_sha256"] != source_fingerprint():
        raise SystemExit("A successful verification of the current sources is required.")
    destination = ROOT / "dist" / f"redfishserver-{VERSION}"
    if destination.exists():
        raise SystemExit(f"Release directory already exists: {destination}")
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for arch in ARCHES:
            info = data["images"][arch]
            archive = ROOT / info["archive"]
            if (
                checksum(archive) != info["sha256"]
                or json.loads(docker("image", "inspect", info["tag"]))[0]["Id"] != info["id"]
            ):
                raise SystemExit("A verified image or archive changed; run make verify again.")
            shutil.copy2(archive, stage / archive.name)
        shutil.copytree(ROOT / data["report_directory"], stage / "reports")
        for path in source_files():
            target = stage / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (stage / "verification.json").write_text(json.dumps(data, indent=2) + "\n")
        (stage / "SHA256SUMS").write_text(
            "".join(
                f"{checksum(p)}  {p.relative_to(stage).as_posix()}\n"
                for p in sorted(stage.rglob("*"))
                if p.is_file()
            )
        )
        stage.rename(destination)
    print(f"Release ready: {destination}")
