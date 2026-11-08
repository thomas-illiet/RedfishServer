"""Regression tests for simulation."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from redfish_mock import simulation
from redfish_mock.__main__ import parser
from redfish_mock.engine import MockBMC
from redfish_mock.monitoring import SCENARIOS, paths


@pytest.fixture
def clock(monkeypatch):
    """Provide a controllable monotonic clock for deterministic telemetry tests."""
    now = [1000.0]

    def _monotonic():
        """Read the test-controlled time without consulting the system clock."""
        return now[0]

    monkeypatch.setattr(simulation, "time", SimpleNamespace(monotonic=_monotonic))
    return now


def get(bmc, path):
    """Read a resource from the simulator and require a successful response."""
    return bmc.handle("GET", path, {}).body


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_dynamic_ranges_coherence_and_faults(clock, profile, scenario):
    """Verify dynamic ranges coherence and faults."""
    bmc = MockBMC(profile, require_auth=False, seed=42)
    bmc.handle("POST", "/_mock/scenario", {}, {"Scenario": scenario})
    system, _, power_uri, thermal_uri = paths(profile)
    initial = copy.deepcopy(bmc.resources)
    base_watts = initial[power_uri]["PowerControl"][0]["PowerConsumedWatts"]
    seen = set()
    previous_ecc = 0
    for step in range(1, 101):
        clock[0] = 1000 + step * 5
        power = get(bmc, power_uri)
        thermal = get(bmc, thermal_uri)
        watts = power["PowerControl"][0]["PowerConsumedWatts"]
        seen.add(watts)
        assert 0.9 * base_watts <= watts <= 1.1 * base_watts
        assert watts == pytest.approx(sum(p["PowerInputWatts"] for p in power["PowerSupplies"]))
        for psu in power["PowerSupplies"]:
            assert 0 <= psu["PowerOutputWatts"] <= psu["PowerCapacityWatts"]
            assert psu["PowerOutputWatts"] == pytest.approx(
                psu["PowerInputWatts"] * 0.94, abs=0.001
            )
        for old, sensor in zip(
            initial[thermal_uri]["Temperatures"], thermal["Temperatures"], strict=True
        ):
            if (
                scenario == "overheat"
                and sensor.get("PhysicalContext") == "CPU"
                and old["Status"]["Health"] == "Critical"
            ):
                assert (
                    sensor["UpperThresholdCritical"]
                    < sensor["ReadingCelsius"]
                    <= sensor["UpperThresholdCritical"] + 4
                )
                assert sensor["Status"]["Health"] == "Critical"
            else:
                amplitude = {"Intake": 1, "CPU": 4, "StorageDevice": 2}.get(
                    sensor.get("PhysicalContext"), 3
                )
                assert abs(old["ReadingCelsius"] - sensor["ReadingCelsius"]) <= amplitude + 0.01
        for old, fan in zip(initial[thermal_uri]["Fans"], thermal["Fans"], strict=True):
            if old.get("Reading", 0) == 0:
                assert fan == old
            else:
                assert abs(fan["Reading"] - old["Reading"]) <= old["Reading"] * 0.1 + 1
        if scenario == "psu-failure":
            assert power["PowerSupplies"][-1]["PowerInputWatts"] == 0
            assert power["Redundancy"][0]["Status"]["Health"] == "Warning"
        if scenario == "memory-errors":
            ecc = sum(
                r.get("LifeTime", {}).get("CorrectableECCErrorCount", 0)
                for r in bmc.resources.values()
            )
            assert ecc >= previous_ecc
            previous_ecc = ecc
            assert get(bmc, system)["MemorySummary"]["Status"]["Health"] == "Critical"
        metrics = power["PowerControl"][0]["PowerMetrics"]
        assert (
            metrics["MinConsumedWatts"]
            <= metrics["AverageConsumedWatts"]
            <= metrics["MaxConsumedWatts"]
        )
        assert metrics["IntervalInMin"] <= 5
    assert len(seen) > 50
    # Dynamic readings never mutate the original fixture or inventory.
    for field in ("Model", "Count"):
        assert (
            bmc.original[system]["ProcessorSummary"][field]
            == initial[system]["ProcessorSummary"][field]
        )
    bmc.handle("POST", "/_mock/reset", {})
    assert (
        get(bmc, power_uri)["PowerControl"][0]["PowerConsumedWatts"]
        == bmc.original[power_uri]["PowerControl"][0]["PowerConsumedWatts"]
    )


def test_sampling_seed_history_and_etag(clock):
    """Verify sampling seed history and etag."""
    frequent = MockBMC("hpe", require_auth=False, seed=12)
    sparse = MockBMC("hpe", require_auth=False, seed=12)
    different = MockBMC("hpe", require_auth=False, seed=13)
    power_uri = paths("hpe")[2]
    start = frequent.handle("GET", power_uri, {})
    clock[0] += 4
    assert frequent.handle("HEAD", power_uri, {}).headers["ETag"] == start.headers["ETag"]
    history = [start.body["PowerControl"][0]["PowerConsumedWatts"]]
    for tick in range(1, 81):
        clock[0] = 1000 + tick * 5
        history.append(get(frequent, power_uri)["PowerControl"][0]["PowerConsumedWatts"])
    assert get(frequent, power_uri) == get(sparse, power_uri)
    assert get(frequent, power_uri) != get(different, power_uri)
    result = frequent.handle("GET", power_uri, {})
    assert result.headers["ETag"] != start.headers["ETag"]
    metrics = result.body["PowerControl"][0]["PowerMetrics"]
    assert metrics["IntervalInMin"] == 5
    assert metrics["AverageConsumedWatts"] == pytest.approx(sum(history[-61:-1]) / 60, abs=0.001)
    assert metrics["MinConsumedWatts"] == min(history[-61:])
    assert metrics["MaxConsumedWatts"] == max(history[-61:])
    assert get(frequent, "/_mock")["monitoring"]["seed"] == 12
    # A month without polling does not replay or retain an unbounded sample history.
    clock[0] += 30 * 86400
    assert get(frequent, power_uri) == get(sparse, power_uri)


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_off_standby_and_repeated_power_cycles(clock, profile):
    """Verify off standby and repeated power cycles."""
    bmc = MockBMC(profile, require_auth=False, seed=5)
    system, _, power_uri, thermal_uri = paths(profile)
    action = get(bmc, system)["Actions"]["#ComputerSystem.Reset"]["target"]
    baseline = get(bmc, power_uri)["PowerControl"][0]["PowerConsumedWatts"]
    for _ in range(5):
        bmc.handle("POST", action, {}, {"ResetType": "ForceOff"})
        clock[0] += 90
        power = get(bmc, power_uri)
        assert 11 <= power["PowerControl"][0]["PowerConsumedWatts"] <= 13
        thermal = get(bmc, thermal_uri)
        assert all(f.get("Reading", 0) == 0 for f in thermal["Fans"])
        assert all(
            s["ReadingCelsius"] is None
            for s in thermal["Temperatures"]
            if s.get("PhysicalContext") in ("CPU", "SystemMemory", "StorageDevice")
        )
        bmc.handle("POST", action, {}, {"ResetType": "On"})
        clock[0] += 90
        assert (
            0.9 * baseline
            <= get(bmc, power_uri)["PowerControl"][0]["PowerConsumedWatts"]
            <= 1.1 * baseline
        )


@pytest.mark.parametrize("options", [{"dynamic_metrics": False}, {"raw_fixtures": True}])
def test_static_and_raw_unchanged(clock, options):
    """Verify static and raw unchanged."""
    bmc = MockBMC("dell", require_auth=False, **options)
    power_uri = paths("dell")[2]
    initial = get(bmc, power_uri)
    clock[0] += 86400
    assert get(bmc, power_uri) == initial
    assert get(bmc, "/_mock")["monitoring"]["dynamic"] is False


@pytest.mark.parametrize("interval", [0, -1, 61])
def test_invalid_metric_interval(interval):
    """Verify invalid metric interval."""
    with pytest.raises(ValueError, match="Metric interval"):
        MockBMC("dell", metric_interval=interval)


@pytest.mark.parametrize("interval", [1, 7, 60])
def test_configurable_interval_and_replay(clock, interval):
    """Verify configurable interval and replay."""
    bmc = MockBMC("hpe", require_auth=False, seed=42, metric_interval=interval)
    power_uri = paths("hpe")[2]
    initial = get(bmc, power_uri)
    clock[0] += interval - 0.01
    assert get(bmc, power_uri) == initial
    clock[0] = 1000 + interval * 10
    changed = get(bmc, power_uri)
    assert changed != initial
    bmc.handle("POST", "/_mock/reset", {})
    clock[0] += interval * 10
    assert get(bmc, power_uri) == changed


def test_cli_environment_and_explicit_options(monkeypatch):
    """Verify cli environment and explicit options."""
    monkeypatch.setenv("REDFISH_METRIC_INTERVAL", "10")
    monkeypatch.setenv("REDFISH_SEED", "42")
    args = parser().parse_args([])
    assert args.metric_interval == 10
    assert args.seed == 42
    assert args.static_metrics is False
    args = parser().parse_args(["--metric-interval", "2", "--seed", "13", "--static-metrics"])
    assert args.metric_interval == 2
    assert args.seed == 13
    assert args.static_metrics is True
    monkeypatch.setenv("REDFISH_SEED", "")
    assert parser().parse_args([]).seed is None
