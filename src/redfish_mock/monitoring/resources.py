"""Enrich compatible inventories with linked monitoring resources."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from redfish_mock.inventory import CONFIGURATIONS

from .health import temperature_health, update_health
from .paths import paths
from .power import set_power

if TYPE_CHECKING:
    from redfish_mock.models import Json


def add_monitoring(profile: str, resources: dict[str, Json]) -> bool:
    """Enrich only compatible bundled inventories with linked synthetic sensors."""
    if profile not in CONFIGURATIONS:
        return False
    system_uri, chassis_uri, power_uri, thermal_uri = paths(profile)
    system = resources.get(system_uri, {})
    # Only enrich the known bundled inventory. Custom topologies stay under user control.
    if (
        system.get("Model") != CONFIGURATIONS[profile].system_model
        or "Memory" not in system
        or "Processors" not in system
        or power_uri not in resources
        or thermal_uri not in resources
    ):
        return False
    memory = [
        resources[ref["@odata.id"]] for ref in resources[system["Memory"]["@odata.id"]]["Members"]
    ]
    cpus = [
        resources[ref["@odata.id"]]
        for ref in resources[system["Processors"]["@odata.id"]]["Members"]
    ]
    if (
        len(memory) != CONFIGURATIONS[profile].memory_gib // CONFIGURATIONS[profile].dimm_gib
        or len(cpus) != 2
    ):
        return False
    power, thermal = resources[power_uri], resources[thermal_uri]
    if "Redundancy" in power or any("Metrics" in dimm for dimm in memory):
        return False
    capacity = 750 if profile == "supermicro" else 800
    for psu in power["PowerSupplies"]:
        psu.update(PowerCapacityWatts=capacity, PowerSupplyType="AC", LineInputVoltageType="AC240V")
        psu["RelatedItem"] = [{"@odata.id": chassis_uri}]
    power["Redundancy"] = [
        {
            "@odata.id": power_uri + "#/Redundancy/0",
            "MemberId": "0",
            "Name": "PSU redundancy",
            "Mode": "Failover",
            "MinNumNeeded": 2,
            "MaxNumSupported": 2,
            "RedundancyEnabled": True,
            "RedundancySet": [{"@odata.id": psu["@odata.id"]} for psu in power["PowerSupplies"]],
            "Status": {"State": "Enabled", "Health": "OK"},
        }
    ]
    control = power["PowerControl"][0]
    control.update(PhysicalContext="Chassis", PowerCapacityWatts=capacity)
    control["RelatedItem"] = [{"@odata.id": system_uri}]
    for i, psu in enumerate(power["PowerSupplies"]):
        voltage: Json
        if profile == "dell":
            voltage = power["Voltages"][i]
        else:
            voltage = {"MemberId": f"PSU{i + 1}Input", "Name": f"PSU {i + 1} input voltage"}
            power["Voltages"].append(voltage)
        voltage.update(
            LowerThresholdNonCritical=200,
            LowerThresholdCritical=180,
            UpperThresholdNonCritical=250,
            UpperThresholdCritical=264,
        )
        voltage["RelatedItem"] = [{"@odata.id": psu["@odata.id"]}]
    # Remove HPE's absent placeholder for a memory bank that is now populated.
    thermal["Temperatures"] = [
        s
        for s in thermal["Temperatures"]
        if not (
            s.get("PhysicalContext") == "SystemMemory"
            and s.get("Status", {}).get("State") == "Absent"
        )
    ]
    temperatures = thermal["Temperatures"]
    cpu_sensors = [s for s in temperatures if s.get("PhysicalContext") == "CPU"]
    for cpu, sensor in zip(cpus, cpu_sensors, strict=True):
        sensor["RelatedItem"] = [{"@odata.id": cpu["@odata.id"]}]
        # A zero threshold is a sentinel in the imported HPE data, not a 0 C alert.
        if sensor.get("UpperThresholdCritical") == 0:
            sensor["UpperThresholdCritical"] = 95
            sensor["UpperThresholdNonCritical"] = 90

    def _add_temperature(
        identifier: str,
        name: str,
        context: str,
        reading: int,
        related: str,
        *,
        warning: int,
        critical: int,
    ) -> None:
        """Append a temperature sensor with explicit thresholds and component linkage."""
        temperatures.append(
            {
                "MemberId": identifier,
                "Name": name,
                "PhysicalContext": context,
                "ReadingCelsius": reading,
                "UpperThresholdNonCritical": warning,
                "UpperThresholdCritical": critical,
                "Status": {"State": "Enabled", "Health": "OK"},
                "RelatedItem": [{"@odata.id": related}],
            }
        )

    for context, reading, warning, critical in (
        ("Intake", 24, 35, 40),
        ("Exhaust", 39, 65, 75),
        ("SystemBoard", 33, 75, 85),
    ):
        if not any(s.get("PhysicalContext") == context for s in temperatures):
            _add_temperature(
                context,
                context + " temperature",
                context,
                reading,
                chassis_uri,
                warning=warning,
                critical=critical,
            )
    for i, dimm in enumerate(memory):
        _add_temperature(
            "Memory-" + dimm["Id"],
            dimm["Name"] + " temperature",
            "SystemMemory",
            38 + i % 5,
            dimm["@odata.id"],
            warning=80,
            critical=85,
        )
        uri = dimm["@odata.id"] + "/Metrics"
        dimm["Metrics"] = {"@odata.id": uri}
        resources[uri] = {
            "@odata.id": uri,
            "@odata.type": "#MemoryMetrics.v1_4_0.MemoryMetrics",
            "Id": "Metrics",
            "Name": dimm["Name"] + " metrics",
            "LifeTime": {"CorrectableECCErrorCount": 0, "UncorrectableECCErrorCount": 0},
        }
    drives = [r for r in resources.values() if r.get("@odata.type", "").startswith("#Drive.")]
    for i, drive in enumerate(drives):
        _add_temperature(
            f"Drive-{i}",
            drive["Name"] + " temperature",
            "StorageDevice",
            34 + i,
            drive["@odata.id"],
            warning=55,
            critical=65,
        )
    for i, psu in enumerate(power["PowerSupplies"]):
        _add_temperature(
            f"PSU-{i + 1}",
            f"PSU {i + 1} temperature",
            "PowerSupply",
            34 + i,
            psu["@odata.id"],
            warning=70,
            critical=80,
        )
    for sensor in temperatures:
        sensor.setdefault("RelatedItem", [{"@odata.id": chassis_uri}])
        sensor["Status"].setdefault("Health", temperature_health(sensor))
    for fan in thermal["Fans"]:
        if fan.get("Status", {}).get("State") != "Absent":
            percent = fan["ReadingUnits"] == "Percent"
            fan.update(
                LowerThresholdCritical=5 if percent else 500,
                LowerThresholdNonCritical=10 if percent else 1000,
            )
            fan["RelatedItem"] = [{"@odata.id": chassis_uri}]
    for resource, fields in ((power, ("Voltages",)), (thermal, ("Temperatures",))):
        for field in fields:
            for i, sensor in enumerate(resource[field]):
                sensor["@odata.id"] = f"{resource['@odata.id']}#/{field}/{i}"
    watts = 412 if profile == "dell" else control["PowerConsumedWatts"]
    set_power(power, watts)
    update_health(profile, resources)
    return True


def monitoring_resources(profile: str, resources: dict[str, Json]) -> dict[str, Json]:
    """Copy the power, thermal and memory resources used by monitoring."""
    _, _, power_uri, thermal_uri = paths(profile)
    return {
        u: copy.deepcopy(r)
        for u, r in resources.items()
        if u in (power_uri, thermal_uri)
        or r.get("@odata.type", "").startswith(("#Memory.", "#MemoryMetrics."))
    }
