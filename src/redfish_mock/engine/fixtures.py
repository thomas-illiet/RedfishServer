"""Load immutable resource baselines and optional fixture sequences."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redfish_mock.constants import ROOT

if TYPE_CHECKING:
    from pathlib import Path

    from redfish_mock.models import Json


@dataclass(frozen=True)
class FixtureSet:
    """Group the imported resource baseline and ordered response sequences."""

    resources: dict[str, Json]
    steps: dict[str, list[Json]]


def load_fixtures(directory: Path) -> FixtureSet:
    """Read a profile directory and require its Redfish service root."""
    resources: dict[str, Json] = {}
    sequences: dict[str, list[Json]] = {}
    for filename in sorted(directory.rglob("index.json")):
        suffix = filename.parent.relative_to(directory).as_posix()
        uri = ROOT if suffix == "." else ROOT + "/" + suffix
        resources[uri] = json.loads(filename.read_text())
        steps = sorted((filename.parent / "_steps").glob("*.json"))
        if steps:
            sequences[uri] = [json.loads(step.read_text()) for step in steps]
    if ROOT not in resources:
        raise ValueError(f"No service root found in {directory}")
    return FixtureSet(resources, sequences)
