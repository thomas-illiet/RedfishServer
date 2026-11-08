"""Enforce independent line and branch coverage thresholds."""

from __future__ import annotations

import json
from pathlib import Path


def check(path: Path = Path("reports/coverage.json"), minimum: float = 80) -> dict[str, float]:
    """Require independent minimum line and branch coverage in a coverage JSON report."""
    totals = json.loads(path.read_text())["totals"]
    if not totals.get("num_statements") or not totals.get("num_branches"):
        raise SystemExit("Coverage must include application lines and branches.")
    result = {
        "lines": 100 * totals["covered_lines"] / totals["num_statements"],
        "branches": 100 * totals["covered_branches"] / totals["num_branches"],
    }
    for kind, percent in result.items():
        print(f"{kind}: {percent:.2f}% (required: {minimum:g}%)")
        if percent < minimum:
            raise SystemExit(f"Insufficient {kind} coverage.")
    return result


if __name__ == "__main__":
    check()
