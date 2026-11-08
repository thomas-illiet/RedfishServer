"""Regression coverage for inventory behavior."""

from __future__ import annotations

import copy

import pytest

from redfish_mock.engine import ROOT, MockBMC
from redfish_mock.inventory import add_inventory


@pytest.mark.parametrize(
    ("profile", "cpu_model", "cores", "threads", "max_mhz", "tdp", "count", "gib", "mts"),
    [
        ("dell", "6338", 32, 64, 3200, 205, 16, 512, 3200),
        ("hpe", "4314", 16, 32, 3400, 135, 16, 256, 2667),
        ("supermicro", "6230", 20, 40, 3900, 125, 12, 192, 2933),
    ],
)
def test_documented_cpu_and_memory_configuration(
    serve,
    *,
    profile,
    cpu_model,
    cores,
    threads,
    max_mhz,
    tdp,
    count,
    gib,
    mts,
):
    # Independent expected configurations from the source matrix in docs/inventory.md.
    """Verify documented cpu and memory configuration."""
    client = serve(profile)
    system_path = client.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    system = client.request("GET", system_path)[1]
    processors_path = system["Processors"]["@odata.id"]
    memory_path = system["Memory"]["@odata.id"]
    processors = client.request("GET", processors_path + "?$expand=.")[1]
    assert processors["Members@odata.count"] == system["ProcessorSummary"]["Count"] == 2
    assert len(processors["Members"]) == 2
    for cpu in processors["Members"]:
        assert cpu_model in cpu["Model"] == system["ProcessorSummary"]["Model"]
        assert (cpu["TotalCores"], cpu["TotalEnabledCores"], cpu["TotalThreads"]) == (
            cores,
            cores,
            threads,
        )
        assert (cpu["MaxSpeedMHz"], cpu["TDPWatts"]) == (max_mhz, tdp)
        assert cpu["ProcessorType"] == "CPU"
        assert cpu["InstructionSet"] == "x86-64"
    memory = client.request("GET", memory_path + "?$expand=.")[1]
    dimms = memory["Members"]
    assert memory["Members@odata.count"] == len(dimms) == count
    assert sum(dimm["CapacityMiB"] for dimm in dimms) == gib * 1024
    assert system["MemorySummary"]["TotalSystemMemoryGiB"] == gib
    assert len({dimm["DeviceLocator"] for dimm in dimms}) == count
    for socket_number in (1, 2):
        local = [dimm for dimm in dimms if dimm["MemoryLocation"]["Socket"] == socket_number]
        assert len(local) == count // 2
        assert sum(dimm["CapacityMiB"] for dimm in local) == gib * 512
    for dimm in dimms:
        assert dimm["OperatingSpeedMhz"] == mts
        assert (dimm["MemoryType"], dimm["MemoryDeviceType"], dimm["BaseModuleType"]) == (
            "DRAM",
            "DDR4",
            "RDIMM",
        )
        assert (dimm["DataWidthBits"], dimm["BusWidthBits"]) == (64, 72)
        assert dimm["Status"] == {"State": "Enabled", "Health": "OK"}
        assert dimm["Links"]["Chassis"] == system["Links"]["Chassis"][0]
    if profile == "dell":
        expected_slots = {f"{bank}{slot}" for bank in "AB" for slot in range(1, 9)}
    elif profile == "hpe":
        expected_slots = {
            f"PROC {socket} DIMM {slot}"
            for socket in (1, 2)
            for slot in (1, 3, 5, 7, 10, 12, 14, 16)
        }
    else:
        expected_slots = {f"P{socket}-DIMM{channel}1" for socket in (1, 2) for channel in "ABCDEF"}
    assert {dimm["DeviceLocator"] for dimm in dimms} == expected_slots
    # Pagination must retain all installed DIMMs, without duplicates or omissions.
    members = []
    page_path = memory_path + "?$top=5"
    while page_path:
        page = client.request("GET", page_path)[1]
        assert page["Members@odata.count"] == count
        members.extend(member["@odata.id"] for member in page["Members"])
        page_path = page.get("Members@odata.nextLink")
    assert len(members) == len(set(members)) == count
    assert set(members) == {dimm["@odata.id"] for dimm in dimms}


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_inventory_readonly_override_reset_and_raw_mode(serve, profile):
    """Verify inventory readonly override reset and raw mode."""
    client = serve(profile)
    system_path = client.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    system = client.request("GET", system_path)[1]
    for collection, patch in (("Processors", {"TotalCores": 1}), ("Memory", {"CapacityMiB": 1})):
        uri = system[collection]["@odata.id"]
        member = client.request("GET", uri)[1]["Members"][0]["@odata.id"]
        original = client.request("GET", member)[1]
        assert client.request("PATCH", member, patch)[0] == 400
        assert client.request("GET", member)[1] == original
        assert (
            client.request(
                "POST",
                "/_mock/overrides",
                {"Path": member, "Patch": {"Status": {"Health": "Warning"}}},
            )[0]
            == 200
        )
        assert client.request("GET", member)[1]["Status"]["Health"] == "Warning"
        client.request("POST", "/_mock/reset", {})
        assert client.request("GET", member)[1] == original
    raw = MockBMC(profile, raw_fixtures=True)
    assert not any(
        item.get("@odata.type", "").startswith(("#Processor.", "#Memory."))
        for item in raw.resources.values()
    )


@pytest.mark.parametrize(
    "change", ["model", "cpu", "count", "capacity", "members", "orphan", "link"]
)
def test_inventory_preserves_custom_configurations(change):
    """Verify inventory preserves custom configurations."""
    resources = MockBMC("dell", raw_fixtures=True).resources
    path = ROOT + "/Systems/System.Embedded.1"
    system = resources[path]
    if change == "model":
        system["Model"] = "PowerEdge R650"
    elif change == "cpu":
        system["ProcessorSummary"]["Model"] = "Different CPU"
    elif change == "count":
        system["ProcessorSummary"]["Count"] = 1
    elif change == "capacity":
        system["MemorySummary"]["TotalSystemMemoryGiB"] = 1024
    elif change == "members":
        resources[path + "/Processors"]["Members"] = [{"@odata.id": path + "/Processors/Custom"}]
    elif change == "orphan":
        resources[path + "/Memory/Custom"] = {"Id": "Custom"}
    else:
        system["Memory"] = {"@odata.id": ROOT + "/CustomMemory"}
    original = copy.deepcopy(resources)
    add_inventory("dell", resources)
    assert resources == original
