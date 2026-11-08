"""Regression coverage for monitoring behavior."""

from __future__ import annotations

import copy

import pytest

from redfish_mock.monitor import RedfishClient, snapshot
from redfish_mock.monitoring import SCENARIOS, add_monitoring, paths


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_complete_monitoring_resources_and_kpis(serve, profile):
    """Verify complete monitoring resources and kpis."""
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    system_uri, _, power_uri, thermal_uri = paths(profile)
    system = remote.get(system_uri)
    power, thermal = remote.get(power_uri), remote.get(thermal_uri)
    assert power["PowerControl"][0]["PowerConsumedWatts"] == sum(
        p["PowerInputWatts"] for p in power["PowerSupplies"]
    )
    for psu in power["PowerSupplies"]:
        assert psu["PowerCapacityWatts"] == (750 if profile == "supermicro" else 800)
        assert 0 <= psu["PowerOutputWatts"] <= psu["PowerInputWatts"]
        assert psu["PowerOutputWatts"] <= psu["PowerCapacityWatts"]
        if psu["PowerInputWatts"]:
            assert psu["EfficiencyPercent"] == pytest.approx(
                100 * psu["PowerOutputWatts"] / psu["PowerInputWatts"], abs=0.01
            )
    contexts = {s.get("PhysicalContext") for s in thermal["Temperatures"]}
    assert {
        "CPU",
        "SystemMemory",
        "StorageDevice",
        "PowerSupply",
        "Intake",
        "Exhaust",
        "SystemBoard",
    } <= contexts
    memory_refs = {m["@odata.id"] for m in remote.get(system["Memory"]["@odata.id"])["Members"]}
    dimm_sensors = [
        s for s in thermal["Temperatures"] if s.get("PhysicalContext") == "SystemMemory"
    ]
    assert {s["RelatedItem"][0]["@odata.id"] for s in dimm_sensors} == memory_refs
    # Resolve complete JSON pointers, including PSU links, not only base URIs.
    for sensor in thermal["Temperatures"]:
        for ref in sensor["RelatedItem"]:
            uri, _, pointer = ref["@odata.id"].partition("#")
            target = remote.get(uri)
            for part in pointer.lstrip("/").split("/") if pointer else []:
                target = target[int(part)] if isinstance(target, list) else target[part]
            assert target
    report = snapshot(remote.get)[0]
    assert report["power_input_watts"] == {"dell": 412, "hpe": 364, "supermicro": 498}[profile]
    assert report["psu_efficiency_percent"] == 94
    assert report["correctable_ecc_errors"] == report["uncorrectable_ecc_errors"] == 0
    assert report["temperatures"]["unavailable_count"] == 0
    assert report["temperatures"]["exhaust_minus_intake_celsius"] > 0
    if profile == "hpe":
        assert report["ssd_min_remaining_life_percent"] == 6
        assert report["drive_predicted_failure_count"] == 1
        assert all(f["unit"] == "Percent" for f in report["fan_readings"])
    if profile == "supermicro":
        assert report["ssd_min_remaining_life_percent"] is None
        assert report["fan_absent_count"] == 1
        assert report["drive_operations"][0]["PercentageComplete"] == 37
    before = copy.deepcopy(client.server.bmc.original)
    add_monitoring(profile, client.server.bmc.original)
    assert client.server.bmc.original == before


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_monitoring_scenarios_and_reset(serve, profile, scenario):
    """Verify monitoring scenarios and reset."""
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    original = copy.deepcopy(client.server.bmc.resources)
    assert client.request("POST", "/_mock/scenario", {"Scenario": scenario})[0] == 200
    report = snapshot(remote.get)[0]
    if scenario != "baseline":
        assert report["power_input_watts"] == (
            620 if scenario in ("overheat", "high-load") else 300
        )
        assert report["psu_redundancy_healthy"] is (scenario != "psu-failure")
        assert report["fan_fault_count"] == (1 if scenario == "fan-failure" else 0)
        assert report["uncorrectable_ecc_errors"] == (1 if scenario == "memory-errors" else 0)
        if profile == "hpe":
            health = remote.get(paths(profile)[0])["Oem"]["Hpe"]["AggregateHealthStatus"]
            assert health["PowerSupplyRedundancy"] == (
                "FailedRedundant" if scenario == "psu-failure" else "Redundant"
            )
            assert health["FanRedundancy"] == (
                "FailedRedundant" if scenario == "fan-failure" else "Redundant"
            )
    if scenario == "overheat":
        assert report["temperatures"]["minimum_critical_headroom_celsius"] == -2
        assert report["health"] == "Critical"
    if scenario == "memory-errors":
        assert report["correctable_ecc_errors"] == 12
        assert report["health"] == "Critical"
    assert client.request("GET", "/_mock")[1]["monitoring"]["scenario"] == scenario
    assert client.request("POST", "/_mock/scenario", {"Scenario": "baseline"})[0] == 200
    assert client.server.bmc.resources == original
    client.request("POST", "/_mock/scenario", {"Scenario": "high-load"})
    client.request("POST", "/_mock/reset", {})
    assert client.server.bmc.resources == original
    assert client.request("GET", "/_mock")[1]["monitoring"]["scenario"] == "baseline"


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_monitoring_offline_readings_and_restore(serve, profile):
    """Verify monitoring offline readings and restore."""
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    system_uri, _, _, thermal_uri = paths(profile)
    client.request("POST", "/_mock/scenario", {"Scenario": "high-load"})
    original = remote.get(thermal_uri)
    action = remote.get(system_uri)["Actions"]["#ComputerSystem.Reset"]["target"]
    for _ in range(2):
        client.request("POST", action, {"ResetType": "ForceOff"})
    report = snapshot(remote.get)[0]
    assert report["power_input_watts"] == 12
    assert report["power_state"] == "Off"
    assert "CPU" not in report["temperatures"]["max_celsius_by_context"]
    assert report["temperatures"]["unavailable_count"] > 2
    assert client.request("POST", "/_mock/scenario", {"Scenario": "normal"})[0] == 409
    client.request("POST", action, {"ResetType": "On"})
    assert remote.get(thermal_uri) == original
    assert snapshot(remote.get)[0]["power_input_watts"] == 620


def test_monitoring_controls_and_readonly_metrics(serve):
    """Verify monitoring controls and readonly metrics."""
    client = serve("hpe")
    for body in ({}, {"Scenario": []}, {"Scenario": "invalid"}, {"Scenario": "normal", "Other": 1}):
        original = copy.deepcopy(client.server.bmc.resources)
        assert client.request("POST", "/_mock/scenario", body)[0] == 400
        assert client.server.bmc.resources == original
    assert client.request("POST", "/_mock/scenario", {"Scenario": "normal"}, auth=False)[0] == 401
    assert (
        serve("hpe", raw_fixtures=True).request("POST", "/_mock/scenario", {"Scenario": "normal"})[
            0
        ]
        == 501
    )
    assert serve("lenovo").request("POST", "/_mock/scenario", {"Scenario": "normal"})[0] == 501
    resources = client.server.bmc.resources
    metrics_path = next(
        p for p, r in resources.items() if r.get("@odata.type", "").startswith("#MemoryMetrics.")
    )
    assert (
        client.request("PATCH", metrics_path, {"LifeTime": {"CorrectableECCErrorCount": 5}})[0]
        == 400
    )


def test_monitor_client_rejects_external_links_and_invalid_origins():
    """Verify monitor client rejects external links and invalid origins."""
    for origin in (
        "file:///etc/passwd",
        "https://user:password@example.test",
        "http://example.test/path",
    ):
        with pytest.raises(ValueError, match="URL must be"):
            RedfishClient(origin, "admin", "admin")
    client = RedfishClient("http://127.0.0.1:8001", "admin", "admin")
    for path in (
        "https://example.test/redfish/v1",
        "//example.test/redfish/v1",
        "/_mock",
        "/redfish/v1#x",
    ):
        with pytest.raises(ValueError, match="outside the configured"):
            client.get(path)
