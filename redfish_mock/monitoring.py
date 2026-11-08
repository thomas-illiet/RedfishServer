"""Synthetic hardware monitoring on standard Redfish resources (docs/MONITORING.md)."""

from __future__ import annotations

import copy
from typing import Any

from .inventory import CONFIGURATIONS

Json = dict[str, Any]
SCENARIOS = (
    "baseline",
    "normal",
    "high-load",
    "overheat",
    "psu-failure",
    "fan-failure",
    "memory-errors",
)
SEVERITY = {"OK": 0, "Warning": 1, "Critical": 2}


def paths(profile: str) -> tuple[str, str, str, str]:
    identifier = CONFIGURATIONS[profile].system_id
    system = f"/redfish/v1/Systems/{identifier}"
    chassis = f"/redfish/v1/Chassis/{identifier}"
    return system, chassis, chassis + "/Power", chassis + "/Thermal"


def worst(items: list[Json]) -> str:
    return max(
        (
            item.get("Status", {}).get(field, "OK")
            for item in items
            for field in ("Health", "HealthRollup")
        ),
        key=lambda health: SEVERITY.get(health, 0),
        default="OK",
    )


def temperature_health(sensor: Json) -> str:
    reading = sensor.get("ReadingCelsius")
    if reading is None:
        return "OK"
    for suffix, health in (("Critical", "Critical"), ("NonCritical", "Warning")):
        threshold = sensor.get("UpperThreshold" + suffix)
        if threshold is not None and reading >= threshold:
            return health
    return "OK"


def set_power(power: Json, watts: float) -> None:
    """Treat chassis watts as AC input; synthesize a constant five-minute window."""
    psus = power["PowerSupplies"]
    working = [p for p in psus if p["Status"]["Health"] != "Critical"]
    for psu in psus:
        input_watts = round(watts / len(working), 3) if psu in working else 0
        psu.update(
            PowerInputWatts=input_watts,
            PowerOutputWatts=round(input_watts * 0.94, 3),
            LastPowerOutputWatts=round(input_watts * 0.94, 3),
            LineInputVoltage=230 if input_watts else 0,
            EfficiencyPercent=94 if input_watts else None,
        )
    control = power["PowerControl"][0]
    control["PowerConsumedWatts"] = sum(p["PowerInputWatts"] for p in psus)
    control["PowerMetrics"] = {
        "IntervalInMin": 5,
        "MinConsumedWatts": control["PowerConsumedWatts"],
        "MaxConsumedWatts": control["PowerConsumedWatts"],
        "AverageConsumedWatts": control["PowerConsumedWatts"],
    }
    control["Status"] = {"State": "Enabled", "Health": worst(psus)}
    power["Redundancy"][0]["Status"] = {
        "State": "Enabled" if len(working) == len(psus) else "Degraded",
        "Health": "OK" if len(working) == len(psus) else "Warning",
    }
    for voltage in power["Voltages"]:
        related = {item["@odata.id"] for item in voltage.get("RelatedItem", [])}
        for psu in psus:
            if psu["@odata.id"] in related:
                voltage["ReadingVolts"] = psu["LineInputVoltage"]
                voltage["Status"] = copy.deepcopy(psu["Status"])


def add_monitoring(profile: str, resources: dict[str, Json]) -> bool:
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

    def add_temperature(
        identifier: str,
        name: str,
        context: str,
        reading: int,
        related: str,
        *,
        warning: int,
        critical: int,
    ) -> None:
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
            add_temperature(
                context,
                context + " temperature",
                context,
                reading,
                chassis_uri,
                warning=warning,
                critical=critical,
            )
    for i, dimm in enumerate(memory):
        add_temperature(
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
        add_temperature(
            f"Drive-{i}",
            drive["Name"] + " temperature",
            "StorageDevice",
            34 + i,
            drive["@odata.id"],
            warning=55,
            critical=65,
        )
    for i, psu in enumerate(power["PowerSupplies"]):
        add_temperature(
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


def update_health(profile: str, resources: dict[str, Json]) -> None:
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


def monitoring_resources(profile: str, resources: dict[str, Json]) -> dict[str, Json]:
    _, _, power_uri, thermal_uri = paths(profile)
    return {
        u: copy.deepcopy(r)
        for u, r in resources.items()
        if u in (power_uri, thermal_uri)
        or r.get("@odata.type", "").startswith(("#Memory.", "#MemoryMetrics."))
    }


def apply_scenario(
    profile: str, resources: dict[str, Json], original: dict[str, Json], scenario: str
) -> None:
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
