"""Corrections justified by the sources in docs/FIDELITY.md, not hardware captures."""

from __future__ import annotations

import copy
from typing import Any

from .inventory import add_inventory
from .monitoring import add_monitoring

ROOT = "/redfish/v1"
COLLECTION_TYPES = {
    "Systems": "ComputerSystemCollection",
    "Managers": "ManagerCollection",
    "Chassis": "ChassisCollection",
    "LogServices": "LogServiceCollection",
    "VirtualMedia": "VirtualMediaCollection",
    "BootOptions": "BootOptionCollection",
    "Memory": "MemoryCollection",
    "Processors": "ProcessorCollection",
    "FirmwareInventory": "SoftwareInventoryCollection",
    "Sensors": "SensorCollection",
}
BOOT_FIELDS = {
    "BootSourceOverrideTarget",
    "BootSourceOverrideEnabled",
    "BootSourceOverrideMode",
}
READ_ONLY = {
    "Id",
    "Name",
    "Description",
    "Manufacturer",
    "Model",
    "SerialNumber",
    "SKU",
    "PowerState",
    "BiosVersion",
    "FirmwareVersion",
    "Status",
    "MemorySummary",
    "ProcessorSummary",
    "ConnectedVia",
    "MediaTypes",
    "BootOrder",
    "ReadingCelsius",
    "ReadingVolts",
    "PowerConsumedWatts",
    "Reading",
    "Temperatures",
    "Fans",
    "PowerSupplies",
    "Voltages",
    "PowerControl",
    "CapacityBytes",
    "CapacityMiB",
    "TotalCores",
    "TotalEnabledCores",
    "TotalThreads",
    "Socket",
    "ProcessorType",
    "ProcessorArchitecture",
    "InstructionSet",
    "MaxSpeedMHz",
    "TDPWatts",
    "DeviceLocator",
    "MemoryLocation",
    "MemoryType",
    "MemoryDeviceType",
    "BaseModuleType",
    "OperatingSpeedMhz",
    "DataWidthBits",
    "BusWidthBits",
    "LifeTime",
    "PowerMetrics",
    "Redundancy",
    "PowerInputWatts",
    "PowerOutputWatts",
    "LastPowerOutputWatts",
    "EfficiencyPercent",
}


def normalize_resource(uri: str, resource: dict[str, Any]) -> None:
    """Repair common Redfish identity fields without inventing hardware measurements."""
    if resource.get("@odata.type") == "#Collection":
        name = COLLECTION_TYPES.get(uri.rsplit("/", 1)[-1])
        if name is not None:
            resource["@odata.type"] = f"#{name}.{name}"
        # Legacy iLO 4 OEM collections are outside the three-vendor contract audit.
    if "Members" not in resource and ".v" in resource.get("@odata.type", ""):
        resource.setdefault("Id", "RootService" if uri == ROOT else uri.rsplit("/", 1)[-1])
    for field in ("Temperatures", "Fans", "PowerControl", "PowerSupplies", "Voltages"):
        for index, member in enumerate(resource.get(field, [])):
            member.setdefault("MemberId", str(index))
            member.setdefault("@odata.id", f"{uri}#/{field}/{index}")


def apply_corrections(profile: str, resources: dict[str, dict[str, Any]]) -> bool:
    for uri, resource in resources.items():
        normalize_resource(uri, resource)
    if profile == "supermicro":
        # The SEL is under Systems; Managers/LogServices is the maintenance log (SM guide).
        old = ROOT + "/Managers/1/LogServices/Log1"
        new = ROOT + "/Systems/1/LogServices/Log1"

        def relocate(value: Any) -> Any:
            if isinstance(value, str) and (value == old or value.startswith(old + "/")):
                return new + value[len(old) :]
            if isinstance(value, dict):
                return {key: relocate(item) for key, item in value.items()}
            if isinstance(value, list):
                return [relocate(item) for item in value]
            return value

        for uri in list(resources):
            if uri == old or uri.startswith(old + "/"):
                resources[new + uri[len(old) :]] = relocate(resources.pop(uri))
        logs = ROOT + "/Systems/1/LogServices"
        resources[logs] = {
            "@odata.id": logs,
            "@odata.type": "#LogServiceCollection.LogServiceCollection",
            "Name": "System Log Services",
            "Members": [{"@odata.id": new}],
            "Members@odata.count": 1,
        }
        resources[ROOT + "/Systems/1"]["LogServices"] = {"@odata.id": logs}
        manager_logs = resources[ROOT + "/Managers/1/LogServices"]
        manager_logs.update(Members=[], **{"Members@odata.count": 0})
        # Do not mix legacy VM1 links with the standard Redfish >=1.8 VirtualMedia resource.
        media = resources[ROOT + "/Managers/1/VirtualMedia/CD1"]
        media.pop("Oem", None)
        media["Actions"] = {
            "#VirtualMedia." + action: {
                "target": media["@odata.id"] + "/Actions/VirtualMedia." + action,
            }
            for action in ("InsertMedia", "EjectMedia")
        }
        # These actions are advertised, but return 501 until the async task workflow exists.
    if profile == "hpe":
        media = resources[ROOT + "/Managers/1/VirtualMedia/2"]
        media["Actions"] = {
            "#VirtualMedia." + action: {
                "target": media["@odata.id"] + "/Actions/VirtualMedia." + action,
            }
            for action in ("InsertMedia", "EjectMedia")
        }
    for resource in resources.values():
        if resource.get("@odata.type", "").startswith("#VirtualMedia."):
            resource.setdefault("WriteProtected", True)
    add_inventory(profile, resources)
    return add_monitoring(profile, resources)


def patchable_fields(resource: dict[str, Any], profile: str) -> dict[str, Any]:
    """Only implement the documented writable subset; no generic hardware PATCH."""
    datatype = resource.get("@odata.type", "")
    if datatype.startswith("#ComputerSystem."):
        fields: dict[str, Any] = {"Boot": BOOT_FIELDS}
        if profile == "hpe":
            fields["HostName"] = None
        return fields
    if datatype.startswith("#VirtualMedia.") and profile == "hpe":
        return {"Image": None, "Inserted": None, "WriteProtected": None}
    return {}


def hpe_message(message: str = "Success") -> dict[str, Any]:
    return {
        "error": {
            "code": "iLO.0.10.ExtendedInfo",
            "message": "See @Message.ExtendedInfo for more information.",
            "@Message.ExtendedInfo": [{"MessageId": "Base.1.12." + message}],
        }
    }


def supermicro_success() -> dict[str, Any]:
    return {
        "Success": {"code": "Base.v1_10_3.Success", "message": "Successfully Completed Request."}
    }


def contract_summary(profile: str, *, raw: bool) -> dict[str, Any]:
    return copy.deepcopy(
        {
            "mode": "raw-fixtures" if raw else "documented-subset",
            "hardware_verified": False,
            "exact_firmware_verified": False,
            "profile": profile,
            "report": "docs/FIDELITY.md",
        }
    )
