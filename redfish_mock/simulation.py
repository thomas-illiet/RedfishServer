"""Clock-driven, bounded synthetic telemetry, independent of polling frequency."""

from __future__ import annotations

import random
import secrets
import time
from typing import Any

from .monitoring import monitoring_resources, paths, set_power, temperature_health, update_health

Json = dict[str, Any]


class TelemetrySimulation:
    def __init__(self, profile: str, *, interval: int = 5, seed: int | None = None) -> None:
        if not 1 <= interval <= 60:
            raise ValueError("Metric interval must be between 1 and 60 seconds.")
        self.profile = profile
        self.interval = interval
        self.seed = seed if seed is not None else secrets.randbits(64)

    def reset(
        self, resources: dict[str, Json], scenario: str, *, power_transition: bool = False
    ) -> None:
        system, _, self.power_uri, self.thermal_uri = paths(self.profile)
        self.off = resources[system]["PowerState"] == "Off"
        source = self.running_template if power_transition and not self.off else resources
        self.template = monitoring_resources(self.profile, source)
        if not self.off:
            self.running_template = self.template
        self.scenario = scenario
        self.started = time.monotonic()
        self.tick = 0
        self.base_watts = self.template[self.power_uri]["PowerControl"][0]["PowerConsumedWatts"]
        # Resume the same load range after host power cycles, without cumulative drift.
        if power_transition and not self.off:
            set_power(resources[self.power_uri], self.base_watts)
            for uri, base in self.template.items():
                if "LifeTime" in base:
                    base["LifeTime"] = resources[uri]["LifeTime"].copy()
        self._power_window(resources, 0)

    def _wave(self, key: str, tick: int) -> float:
        """Interpolate seeded random anchors every six samples; start at the fixture."""
        anchor, offset = divmod(tick, 6)

        def value(index: int) -> float:
            if index == 0:
                return 0.0
            # Reproducible simulation noise, never used for authentication or secrets.
            rng = random.Random(  # noqa: S311
                f"{self.seed}:{self.profile}:{self.scenario}:{self.off}:{key}:{index}"
            )
            return rng.uniform(-1, 1)

        left, right = value(anchor), value(anchor + 1)
        return left + (right - left) * offset / 6

    def _watts(self, tick: int) -> float:
        amplitude = 1 if self.off else self.base_watts * 0.1
        return round(self.base_watts + amplitude * self._wave("load", tick), 2)

    def _power_window(self, resources: dict[str, Json], tick: int) -> None:
        # Reconstruct at most five minutes, even after a long idle period. The seed
        # makes skipped samples identical to those seen by a frequently polling client.
        first = max(0, tick - 300 // self.interval)
        watts = [self._watts(i) for i in range(first, tick + 1)]
        completed = watts[:-1]
        resources[self.power_uri]["PowerControl"][0]["PowerMetrics"] = {
            "IntervalInMin": round(len(completed) * self.interval / 60, 6),
            "MinConsumedWatts": min(watts),
            "MaxConsumedWatts": max(watts),
            "AverageConsumedWatts": round(sum(completed) / len(completed), 3)
            if completed
            else watts[0],
        }

    def advance(self, resources: dict[str, Json]) -> None:
        tick = int((time.monotonic() - self.started) // self.interval)
        if tick <= self.tick:
            return
        self.tick = tick
        power, thermal = resources[self.power_uri], resources[self.thermal_uri]
        set_power(power, self._watts(tick))
        self._power_window(resources, tick)
        load = self._wave("load", tick)
        templates = {s["MemberId"]: s for s in self.template[self.thermal_uri]["Temperatures"]}
        overheating = False
        for sensor in thermal["Temperatures"]:
            base = templates.get(sensor["MemberId"], {}).get("ReadingCelsius")
            if not isinstance(base, (int, float)) or sensor["Status"]["State"] != "Enabled":
                continue
            context = sensor.get("PhysicalContext")
            amplitude = {"Intake": 1, "CPU": 4, "StorageDevice": 2}.get(context, 3)
            noise = 0.8 * load + 0.2 * self._wave(sensor["MemberId"], tick)
            reading = base + amplitude * noise
            if self.scenario == "overheat" and context == "CPU" and not overheating:
                reading = sensor["UpperThresholdCritical"] + max(1, 2 + 2 * load)
                overheating = True
            sensor["ReadingCelsius"] = round(reading, 1)
            sensor["Status"]["Health"] = temperature_health(sensor)
        templates = {f["MemberId"]: f for f in self.template[self.thermal_uri]["Fans"]}
        for fan in thermal["Fans"]:
            base = templates.get(fan["MemberId"], {}).get("Reading")
            if (
                not isinstance(base, (int, float))
                or base <= 0
                or fan["Status"]["State"] != "Enabled"
                or fan["Status"].get("Health") == "Critical"
            ):
                continue
            reading = round(base * (1 + 0.1 * load))
            fan["Reading"] = min(100, reading) if fan["ReadingUnits"] == "Percent" else reading
        for psu in power["PowerSupplies"]:
            if psu["PowerInputWatts"]:
                psu["LineInputVoltage"] = round(230 + 2 * self._wave("voltage", tick), 1)
            for voltage in power["Voltages"]:
                if {"@odata.id": psu["@odata.id"]} in voltage.get("RelatedItem", []):
                    voltage["ReadingVolts"] = psu["LineInputVoltage"]
        if self.scenario == "memory-errors" and not self.off:
            for uri, base in self.template.items():
                if base.get("LifeTime", {}).get("UncorrectableECCErrorCount"):
                    resources[uri]["LifeTime"]["CorrectableECCErrorCount"] = (
                        base["LifeTime"]["CorrectableECCErrorCount"] + tick // 6
                    )
        update_health(self.profile, resources)
