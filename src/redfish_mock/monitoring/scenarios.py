"""Named monitoring faults, baseline restoration and standby state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .health import temperature_health, update_health
from .paths import paths
from .power import set_power
from .resources import monitoring_resources

if TYPE_CHECKING:
    from redfish_mock.models import Json

SCENARIOS = (
    "baseline",
    "normal",
    "high-load",
    "overheat",
    "psu-failure",
    "fan-failure",
    "memory-errors",
)


def apply_scenario(
    profile: str, resources: dict[str, Json], original: dict[str, Json], scenario: str
) -> None:
    """Restore baseline monitoring resources and apply the selected fault or load."""
    resources.update(monitoring_resources(profile, original))
    _, _, power_uri, thermal_uri = paths(profile)
    power, thermal = resources[power_uri], resources[thermal_uri]
    if scenario != "baseline":
        for psu in power["PowerSupplies"]:
            psu["Status"] = {"Health": "OK", "State": "Enabled"}
        for fan in thermal["Fans"]:
            if fan["Status"]["State"] != "Absent":
                fan["Status"]["Health"] = "OK"
                fan["Reading"] = 34 if fan["ReadingUnits"] == "Percent" else 5000
        for sensor in thermal["Temperatures"]:
            if sensor.get("PhysicalContext") == "CPU":
                sensor["ReadingCelsius"] = 50
        if scenario in ("high-load", "overheat"):
            for sensor in thermal["Temperatures"]:
                if sensor.get("PhysicalContext") != "Intake":
                    sensor["ReadingCelsius"] += 10
            for fan in thermal["Fans"]:
                if "Reading" in fan:
                    fan["Reading"] = 70 if fan["ReadingUnits"] == "Percent" else 10000
        if scenario == "overheat":
            cpu = next(s for s in thermal["Temperatures"] if s.get("PhysicalContext") == "CPU")
            cpu["ReadingCelsius"] = cpu["UpperThresholdCritical"] + 2
        if scenario == "psu-failure":
            power["PowerSupplies"][-1]["Status"]["Health"] = "Critical"
        if scenario == "fan-failure":
            thermal["Fans"][0].update(Reading=0, Status={"State": "Enabled", "Health": "Critical"})
        if scenario == "memory-errors":
            dimm = next(
                r for r in resources.values() if r.get("@odata.type", "").startswith("#Memory.")
            )
            dimm["Status"]["Health"] = "Critical"
            metrics = resources[dimm["Metrics"]["@odata.id"]]
            metrics["LifeTime"].update(CorrectableECCErrorCount=12, UncorrectableECCErrorCount=1)
        set_power(power, 620 if scenario in ("high-load", "overheat") else 300)
        for sensor in thermal["Temperatures"]:
            sensor["Status"]["Health"] = temperature_health(sensor)
    update_health(profile, resources)


def power_off(profile: str, resources: dict[str, Json]) -> None:
    """Model BMC standby power and unavailable host sensors without losing inventory."""
    _, _, power_uri, thermal_uri = paths(profile)
    power, thermal = resources[power_uri], resources[thermal_uri]
    set_power(power, 12)  # Standby BMC consumption; host-off is not AC-disconnected.
    for fan in thermal["Fans"]:
        if fan["Status"]["State"] != "Absent":
            fan.update(Reading=0, Status={"State": "Disabled", "Health": "OK"})
    for sensor in thermal["Temperatures"]:
        if sensor.get("PhysicalContext") in ("CPU", "SystemMemory", "StorageDevice"):
            sensor["ReadingCelsius"] = None
            sensor["Status"] = {"State": "UnavailableOffline"}
    update_health(profile, resources)
