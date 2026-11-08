"""Documented synthetic CPU/DIMM configurations; provenance in docs/INVENTORY.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROOT = "/redfish/v1"


@dataclass(frozen=True)
class Configuration:
    system_id: str
    system_model: str
    cpu_model: str
    cores: int
    threads: int
    max_speed_mhz: int
    tdp_watts: int
    memory_gib: int
    dimm_gib: int
    memory_speed_mts: int
    slots_per_cpu: tuple[int, ...]


CONFIGURATIONS = {
    "dell": Configuration(
        "System.Embedded.1",
        "PowerEdge R750",
        "Intel(R) Xeon(R) Gold 6338 CPU @ 2.00GHz",
        32,
        64,
        3200,
        205,
        512,
        32,
        3200,
        tuple(range(1, 9)),
    ),
    "hpe": Configuration(
        "1",
        "ProLiant DL380 Gen10 Plus",
        "Intel(R) Xeon(R) Silver 4314 CPU @ 2.40GHz",
        16,
        32,
        3400,
        135,
        256,
        16,
        2667,
        (1, 3, 5, 7, 10, 12, 14, 16),
    ),
    "supermicro": Configuration(
        "1",
        "SYS-6019U-TR4",
        "Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz",
        20,
        40,
        3900,
        125,
        192,
        16,
        2933,
        tuple(range(1, 7)),
    ),
}


def memory_identity(profile: str, socket: int, slot: int) -> tuple[str, str, dict[str, int]]:
    """Return stable resource ID, board label and physical socket/slot location."""
    if profile == "dell":
        label = f"{'A' if socket == 1 else 'B'}{slot}"
        return f"DIMM.Socket.{label}", label, {"Socket": socket, "Slot": slot}
    if profile == "hpe":
        return (
            str((socket - 1) * 16 + slot),
            f"PROC {socket} DIMM {slot}",
            {"Socket": socket, "Slot": slot},
        )
    # One DIMM per channel A-F on the X11DPU; the other channel slot remains empty.
    label = f"P{socket}-DIMM{'ABCDEF'[slot - 1]}1"
    return label, label, {"Socket": socket, "Channel": slot - 1, "Slot": 1}


def processor_resource(
    config: Configuration,
    profile: str,
    path: str,
    socket: int,
    chassis: str,
) -> dict[str, Any]:
    identifier = f"CPU.Socket.{socket}" if profile == "dell" else str(socket)
    return {
        "@odata.id": f"{path}/{identifier}",
        "@odata.type": "#Processor.v1_7_2.Processor",
        "Id": identifier,
        "Name": f"Processor {socket}",
        "Socket": f"CPU {socket}",
        "Manufacturer": "Intel",
        "Model": config.cpu_model,
        "ProcessorType": "CPU",
        "ProcessorArchitecture": "x86",
        "InstructionSet": "x86-64",
        "TotalCores": config.cores,
        "TotalEnabledCores": config.cores,
        "TotalThreads": config.threads,
        "MaxSpeedMHz": config.max_speed_mhz,
        "TDPWatts": config.tdp_watts,
        "Status": {"State": "Enabled", "Health": "OK"},
        "Links": {"Chassis": {"@odata.id": chassis}},
    }


def memory_resource(
    config: Configuration,
    profile: str,
    path: str,
    socket: int,
    slot: int,
    *,
    chassis: str,
) -> dict[str, Any]:
    identifier, label, location = memory_identity(profile, socket, slot)
    return {
        "@odata.id": f"{path}/{identifier}",
        "@odata.type": "#Memory.v1_7_1.Memory",
        "Id": identifier,
        "Name": label,
        "DeviceLocator": label,
        "MemoryLocation": location,
        "MemoryType": "DRAM",
        "MemoryDeviceType": "DDR4",
        "BaseModuleType": "RDIMM",
        "CapacityMiB": config.dimm_gib * 1024,
        "OperatingSpeedMhz": config.memory_speed_mts,
        "DataWidthBits": 64,
        "BusWidthBits": 72,
        "Status": {"State": "Enabled", "Health": "OK"},
        "Links": {"Chassis": {"@odata.id": chassis}},
    }


def add_inventory(profile: str, resources: dict[str, dict[str, Any]]) -> None:
    """Fill the known rackhands gaps without overwriting customized inventories."""
    config = CONFIGURATIONS.get(profile)
    if config is None:
        return
    system_path = f"{ROOT}/Systems/{config.system_id}"
    system = resources.get(system_path, {})
    if (
        system.get("Model") != config.system_model
        or system.get("ProcessorSummary", {}).get("Model") != config.cpu_model
        or system.get("ProcessorSummary", {}).get("Count") != 2
        or system.get("MemorySummary", {}).get("TotalSystemMemoryGiB") != config.memory_gib
    ):
        return
    paths = [system_path + "/Processors", system_path + "/Memory"]
    if any(
        resources.get(path, {}).get("Members")
        or any(uri.startswith(path + "/") for uri in resources)
        or system.get(field, {}).get("@odata.id", path) != path
        for field, path in zip(("Processors", "Memory"), paths, strict=True)
    ):
        return
    chassis = system["Links"]["Chassis"][0]["@odata.id"]
    processors = [
        processor_resource(config, profile, paths[0], socket, chassis) for socket in (1, 2)
    ]
    memory = [
        memory_resource(config, profile, paths[1], socket, slot, chassis=chassis)
        for socket in (1, 2)
        for slot in config.slots_per_cpu
    ]
    for field, path, members in zip(
        ("Processors", "Memory"),
        paths,
        (processors, memory),
        strict=True,
    ):
        schema = "ProcessorCollection" if field == "Processors" else "MemoryCollection"
        resources[path] = {
            "@odata.id": path,
            "@odata.type": f"#{schema}.{schema}",
            "Name": field,
            "Members": [{"@odata.id": member["@odata.id"]} for member in members],
            "Members@odata.count": len(members),
        }
        resources.update({member["@odata.id"]: member for member in members})
        system[field] = {"@odata.id": path}
