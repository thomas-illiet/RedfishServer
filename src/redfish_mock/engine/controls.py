"""Mock metadata and explicit scenario and override controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redfish_mock.constants import ROOT, SESSIONS
from redfish_mock.models import Json, RedfishError, Result
from redfish_mock.monitoring import SCENARIOS, apply_scenario, paths

from .patching import merge_existing
from .paths import normalize
from .resources import read

if TYPE_CHECKING:
    from .bmc import MockBMC


def select_scenario(bmc: MockBMC, body: Json | None) -> Result:
    """Apply a supported monitoring scenario only while the host is powered on."""
    payload = body or {}
    scenario = payload.get("Scenario")
    if not bmc.monitoring_enabled:
        raise RedfishError(501, "Monitoring scenarios require an enriched vendor profile.")
    if set(payload) != {"Scenario"} or not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise RedfishError(400, "Expected Scenario: " + ", ".join(SCENARIOS))
    system_uri, _, _, _ = paths(bmc.profile)
    if bmc.resources[system_uri]["PowerState"] != "On":
        raise RedfishError(409, "Power on the system before selecting a monitoring scenario.")
    apply_scenario(bmc.profile, bmc.resources, bmc.original, scenario)
    bmc.scenario = scenario
    if bmc.simulation:
        bmc.simulation.reset(bmc.resources, bmc.scenario)
    return Result(body={"scenario": scenario, "profile": bmc.profile})


def override_resource(bmc: MockBMC, body: Json | None) -> Result:
    """Apply an explicit mock override without changing protected service resources."""
    payload = body or {}
    target, patch = payload.get("Path"), payload.get("Patch")
    if not isinstance(target, str) or not isinstance(patch, dict):
        raise RedfishError(400, "Expected Path (URI) and Patch (object).")
    target, params = normalize(target)
    if params or target.startswith(SESSIONS) or target in (ROOT, ROOT + "/SessionService"):
        raise RedfishError(400, "This resource cannot be overridden.")
    updated = merge_existing(read(bmc, target), patch)
    bmc.resources[target] = updated
    if bmc.simulation:
        bmc.simulation.reset(bmc.resources, bmc.scenario)
    bmc.positions[target] = len(bmc.steps.get(target, []))
    return Result(body={"status": "overridden", "resource": target})
