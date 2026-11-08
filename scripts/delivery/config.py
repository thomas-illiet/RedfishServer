"""Repository paths, pinned delivery tools and target architectures."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
TOOLS = json.loads((ROOT / "scripts/release-tools.json").read_text())
ARCHES = ("amd64", "arm64")
PROFILES = ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4")
REPORTS = ROOT / "reports"
VERIFIED = REPORTS / "verification.json"
