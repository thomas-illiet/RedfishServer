"""Threshold evaluation and propagation of component health."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .paths import paths

if TYPE_CHECKING:
    from redfish_mock.models import Json

SEVERITY = {"OK": 0, "Warning": 1, "Critical": 2}


def _severity(health: str) -> int:
    """Rank known health states while treating unknown values as the lowest severity."""
    return SEVERITY.get(health, 0)


def worst(items: list[Json]) -> str:
    """Return the most severe health or rollup value among components."""
    return max(
        (
            item.get("Status", {}).get(field, "OK")
            for item in items
            for field in ("Health", "HealthRollup")
        ),
        key=_severity,
        default="OK",
    )


def temperature_health(sensor: Json) -> str:
    """Evaluate a temperature reading against its warning and critical limits."""
    reading = sensor.get("ReadingCelsius")
    if reading is None:
        return "OK"
    for suffix, health in (("Critical", "Critical"), ("NonCritical", "Warning")):
        threshold = sensor.get("UpperThreshold" + suffix)
        if threshold is not None and reading >= threshold:
            return health
    return "OK"


def update_health(profile: str, resources: dict[str, Json]) -> None:
    """Propagate component health into standard and vendor aggregate fields."""
    system_uri, chassis_uri, power_uri, thermal_uri = paths(profile)
    system, chassis = resources[system_uri], resources[chassis_uri]
    power, thermal = resources[power_uri], resources[thermal_uri]
    cpu_health = worst([s for s in thermal["Temperatures"] if s.get("PhysicalContext") == "CPU"])
    memory_resources = [
        r for r in resources.values() if r.get("@odata.type", "").startswith("#Memory.")
    ]
    memory_health = worst(
        memory_resources
        + [s for s in thermal["Temperatures"] if s.get("PhysicalContext") == "SystemMemory"]
    )
    system["ProcessorSummary"]["Status"].update(Health=cpu_health, HealthRollup=cpu_health)
    system["MemorySummary"]["Status"].update(Health=memory_health, HealthRollup=memory_health)
    groups = {
        "CPURollupStatus": cpu_health,
        "TempRollupStatus": worst(thermal["Temperatures"]),
        "FanRollupStatus": worst(thermal["Fans"]),
        "PSRollupStatus": worst(power["PowerSupplies"]),
    }
    external = [
        r
        for r in resources.values()
        if r.get("@odata.type", "").startswith(("#Drive.", "#Storage.", "#Volume."))
    ]
    aggregate = worst(
        external
        + memory_resources
        + thermal["Temperatures"]
        + thermal["Fans"]
        + power["PowerSupplies"]
        + power["Voltages"]
    )
    system["Status"]["HealthRollup"] = aggregate
    chassis["Status"]["HealthRollup"] = aggregate
    if profile == "dell":
        system["Oem"]["Dell"]["DellSystem"].update(groups, CurrentRollupStatus=aggregate)
    elif profile == "hpe":
        health = system["Oem"]["Hpe"]["AggregateHealthStatus"]
        for key, value in (
            ("Processors", cpu_health),
            ("Memory", memory_health),
            ("Fans", groups["FanRollupStatus"]),
            ("Temperatures", groups["TempRollupStatus"]),
            ("PowerSupplies", groups["PSRollupStatus"]),
        ):
            health[key]["Status"]["Health"] = value
        health["PowerSupplyRedundancy"] = (
            "Redundant" if groups["PSRollupStatus"] == "OK" else "FailedRedundant"
        )
        health["FanRedundancy"] = (
            "Redundant" if groups["FanRollupStatus"] == "OK" else "FailedRedundant"
        )
