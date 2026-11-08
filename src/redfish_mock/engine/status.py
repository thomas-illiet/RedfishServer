"""Describe one BMC and its available simulation controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redfish_mock.contracts import contract_summary
from redfish_mock.models import Result
from redfish_mock.monitoring import SCENARIOS

if TYPE_CHECKING:
    from .bmc import MockBMC


def status(bmc: MockBMC) -> Result:
    """Report profile state, fidelity and available monitoring controls."""
    return Result(
        body={
            "profile": bmc.profile,
            "resources": len(bmc.resources),
            "play_sequences": bmc.play_sequences,
            "sessions": len(bmc.sessions),
            "reset": "/_mock/reset",
            "storage": "memory",
            "fidelity": contract_summary(bmc.profile, raw=bmc.raw_fixtures),
            "overrides": "/_mock/overrides",
            "monitoring": {
                "enabled": bmc.monitoring_enabled,
                "scenario": bmc.scenario if bmc.monitoring_enabled else None,
                "scenarios": list(SCENARIOS) if bmc.monitoring_enabled else [],
                "control": "/_mock/scenario" if bmc.monitoring_enabled else None,
                "report": "docs/monitoring.md",
                "dynamic": bmc.simulation is not None,
                "interval_seconds": bmc.simulation.interval if bmc.simulation else None,
                "seed": bmc.simulation.seed if bmc.simulation else None,
            },
        }
    )
