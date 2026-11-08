"""Discover hardware resources and derive monitoring KPIs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from redfish_mock.models import Json


def snapshot(get: Callable[[str], Json]) -> list[Json]:
    """Collect independent per-server KPIs; missing readings remain unknown."""

    def _members(uri: str) -> list[Json]:
        """Collect paginated resources and reject collection pagination loops."""
        result = []
        seen = set()
        while uri:
            if uri in seen:
                raise ValueError("Collection pagination loop")
            seen.add(uri)
            collection = get(uri)
            result.extend(get(ref["@odata.id"]) for ref in collection["Members"])
            uri = collection.get("Members@odata.nextLink", "")
        return result

    root = get("/redfish/v1/")
    result = []
    for system in _members(root["Systems"]["@odata.id"]):
        chassis = get(system["Links"]["Chassis"][0]["@odata.id"])
        power = get(chassis["Power"]["@odata.id"])
        thermal = get(chassis["Thermal"]["@odata.id"])
        memory = _members(system["Memory"]["@odata.id"])
        drives = {}
        for storage in _members(system["Storage"]["@odata.id"]):
            for ref in storage.get("Drives", []):
                drives[ref["@odata.id"]] = get(ref["@odata.id"])
        ecc = [
            get(dimm["Metrics"]["@odata.id"])["LifeTime"] for dimm in memory if "Metrics" in dimm
        ]
        readings = [
            s
            for s in thermal["Temperatures"]
            if isinstance(s.get("ReadingCelsius"), (int, float))
            and s.get("Status", {}).get("State") == "Enabled"
        ]
        maxima = {}
        for sensor in readings:
            context = sensor.get("PhysicalContext", "Other")
            maxima[context] = max(
                maxima.get(context, sensor["ReadingCelsius"]), sensor["ReadingCelsius"]
            )
        margins = [
            s["UpperThresholdCritical"] - s["ReadingCelsius"]
            for s in readings
            if isinstance(s.get("UpperThresholdCritical"), (int, float))
        ]
        outputs = [p.get("PowerOutputWatts") for p in power["PowerSupplies"]]
        output_watts = sum(outputs) if all(isinstance(v, (int, float)) for v in outputs) else None
        input_watts = power["PowerControl"][0]["PowerConsumedWatts"]
        temperatures = {
            "max_celsius_by_context": maxima,
            "unavailable_count": len(thermal["Temperatures"]) - len(readings),
            "minimum_critical_headroom_celsius": min(margins, default=None),
            "exhaust_minus_intake_celsius": maxima["Exhaust"] - maxima["Intake"]
            if "Exhaust" in maxima and "Intake" in maxima
            else None,
        }
        result.append(
            {
                "system_uri": system["@odata.id"],
                "model": system["Model"],
                "power_state": system["PowerState"],
                "health": system["Status"].get("HealthRollup", system["Status"].get("Health")),
                "power_input_watts": input_watts,
                "power_output_watts": output_watts,
                "psu_efficiency_percent": round(100 * output_watts / input_watts, 2)
                if output_watts is not None and input_watts
                else None,
                "psu_redundancy_healthy": all(
                    g["Status"]["Health"] == "OK" for g in power["Redundancy"]
                )
                if power.get("Redundancy")
                else None,
                "power_window": power["PowerControl"][0].get("PowerMetrics"),
                "temperatures": temperatures,
                "fan_fault_count": sum(
                    f.get("Status", {}).get("Health") in ("Warning", "Critical")
                    for f in thermal["Fans"]
                ),
                "fan_absent_count": sum(
                    f.get("Status", {}).get("State") == "Absent" for f in thermal["Fans"]
                ),
                "fan_readings": [
                    {
                        "id": f["MemberId"],
                        "reading": f.get("Reading"),
                        "unit": f.get("ReadingUnits"),
                        "state": f["Status"]["State"],
                    }
                    for f in thermal["Fans"]
                ],
                "memory_gib": system["MemorySummary"]["TotalSystemMemoryGiB"],
                "correctable_ecc_errors": sum(e["CorrectableECCErrorCount"] for e in ecc)
                if len(ecc) == len(memory)
                else None,
                "uncorrectable_ecc_errors": sum(e["UncorrectableECCErrorCount"] for e in ecc)
                if len(ecc) == len(memory)
                else None,
                "drive_fault_count": sum(
                    d.get("Status", {}).get("Health") in ("Warning", "Critical")
                    for d in drives.values()
                ),
                "drive_predicted_failure_count": sum(
                    bool(d.get("FailurePredicted")) for d in drives.values()
                ),
                "ssd_min_remaining_life_percent": min(
                    (
                        d["PredictedMediaLifeLeftPercent"]
                        for d in drives.values()
                        if d.get("MediaType") == "SSD"
                        and isinstance(d.get("PredictedMediaLifeLeftPercent"), (int, float))
                    ),
                    default=None,
                ),
                "drive_operations": [
                    {"drive_uri": uri, **op}
                    for uri, d in drives.items()
                    for op in d.get("Operations", [])
                ],
            }
        )
    return result
