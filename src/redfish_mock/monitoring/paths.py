"""Canonical resource paths for the enriched vendor profiles."""

from __future__ import annotations

from redfish_mock.inventory import CONFIGURATIONS


def paths(profile: str) -> tuple[str, str, str, str]:
    """Return the system, chassis, power and thermal URIs for a profile."""
    identifier = CONFIGURATIONS[profile].system_id
    system = f"/redfish/v1/Systems/{identifier}"
    chassis = f"/redfish/v1/Chassis/{identifier}"
    return system, chassis, chassis + "/Power", chassis + "/Thermal"
