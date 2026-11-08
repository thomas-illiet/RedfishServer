"""Consistent chassis and power-supply readings."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from .health import worst

if TYPE_CHECKING:
    from redfish_mock.models import Json


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
