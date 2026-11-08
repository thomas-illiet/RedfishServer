"""Advertised system reset and virtual media actions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from redfish_mock.contracts import hpe_message, supermicro_success
from redfish_mock.models import Json, RedfishError, Result
from redfish_mock.monitoring import monitoring_resources, power_off, update_health

from .capabilities import action_kind, advertised_actions, reset_types
from .resources import read

if TYPE_CHECKING:
    from .bmc import MockBMC


def execute(bmc: MockBMC, path: str, body: Json) -> Result:
    """Validate and execute an advertised reset or virtual media action."""
    owner = path.split("/Actions/", 1)[0]
    resource = read(bmc, owner)

    definition = advertised_actions(resource).get(path)
    if definition is None:
        raise RedfishError(404, "Action is not advertised by this profile.", "ActionNotSupported")
    action = path.rsplit("/", 1)[-1]
    kind = action_kind(bmc.profile, path)
    if bmc.profile == "supermicro" and action.startswith("VirtualMedia."):
        raise RedfishError(
            501,
            "Supermicro virtual media requires an async task workflow not simulated here.",
            "ActionNotSupported",
        )
    if kind == "reset":
        reset_type = body.get("ResetType")
        allowed = reset_types(definition)
        if reset_type not in allowed:
            raise RedfishError(
                400, "Missing or unsupported ResetType.", "ActionParameterValueNotInList"
            )
        if action == "ComputerSystem.Reset":
            old_power = resource.get("PowerState", "On")
            if reset_type in ("ForceOff", "GracefulShutdown"):
                resource["PowerState"] = "Off"
            elif reset_type == "PushPowerButton":
                resource["PowerState"] = "Off" if old_power == "On" else "On"
            elif reset_type != "Nmi":
                resource["PowerState"] = "On"
            boot = resource.get("Boot", {})
            booted = (old_power != "On" and resource.get("PowerState") == "On") or reset_type in (
                "ForceRestart",
                "GracefulRestart",
                "PowerCycle",
            )
            if (
                resource.get("PowerState") == "On"
                and booted
                and boot.get("BootSourceOverrideEnabled") == "Once"
            ):
                boot["BootSourceOverrideEnabled"] = "Disabled"
    elif kind == "insert":
        image = body.get("Image")
        if not isinstance(image, str) or urlsplit(image).scheme not in (
            "http",
            "https",
            "nfs",
            "cifs",
        ):
            raise RedfishError(400, "Image must be an HTTP, HTTPS, NFS or CIFS URL.")
        inserted = body.get("Inserted", True)
        protected = body.get("WriteProtected", True)
        if not isinstance(inserted, bool) or not isinstance(protected, bool):
            raise RedfishError(
                400,
                "Inserted and WriteProtected must be booleans.",
                "ActionParameterValueTypeError",
            )
        if not urlsplit(image).hostname:
            raise RedfishError(400, "Image must include a host.", "ActionParameterValueFormatError")
        resource.update(
            Image=image,
            Inserted=inserted,
            WriteProtected=protected,
            ConnectedVia="URI" if inserted else "NotConnected",
        )
    elif kind == "eject":
        resource.update(Image=None, Inserted=False, ConnectedVia="NotConnected")
    else:
        raise RedfishError(
            501,
            f"{action} is present in the fixture but is not simulated.",
            "ActionNotSupported",
        )
    bmc.resources[owner] = resource
    if bmc.monitoring_enabled and action == "ComputerSystem.Reset":
        if old_power != "Off" and resource["PowerState"] == "Off":
            bmc.monitoring_on = monitoring_resources(bmc.profile, bmc.resources)
            power_off(bmc.profile, bmc.resources)
        elif old_power == "Off" and resource["PowerState"] == "On" and bmc.monitoring_on:
            bmc.resources.update(bmc.monitoring_on)
            bmc.monitoring_on = None
            update_health(bmc.profile, bmc.resources)
        if bmc.simulation and old_power != resource["PowerState"]:
            bmc.simulation.reset(bmc.resources, bmc.scenario, power_transition=True)
    bmc.positions[owner] = len(bmc.steps.get(owner, []))
    if bmc.profile == "hpe":
        return Result(body=hpe_message())
    if bmc.profile == "supermicro" and action == "ComputerSystem.Reset":
        return Result(body=supermicro_success())
    return Result(204)
