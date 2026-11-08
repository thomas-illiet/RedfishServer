"""Atomic resource updates constrained by the supported vendor contracts."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from redfish_mock.constants import ROOT
from redfish_mock.contracts import READ_ONLY, hpe_message, patchable_fields
from redfish_mock.models import Json, RedfishError, Result, etag

from .resources import read

if TYPE_CHECKING:
    from .bmc import MockBMC


def merge_existing(current: Json, patch: Json) -> Json:
    """Apply an atomic mock PATCH; identity, collections and links remain immutable."""
    merged = copy.deepcopy(current)
    immutable = {"Id", "Links", "Actions", "Members", "Members@odata.count", "Password"}
    for key, value in patch.items():
        if key.startswith("@") or key in immutable or "@" in key:
            raise RedfishError(400, f"Property {key} is read-only.", "PropertyNotWritable")
        if key not in current:
            raise RedfishError(400, f"Unknown property: {key}.", "PropertyUnknown")
        old = current[key]
        if isinstance(old, dict) and isinstance(value, dict):
            if "@odata.id" in old:
                raise RedfishError(400, f"Link {key} is read-only.", "PropertyNotWritable")
            merged[key] = merge_existing(old, value)
        else:
            nullable_image = key == "Image" and value is None
            if old is not None and type(value) is not type(old) and not nullable_image:
                raise RedfishError(400, f"Wrong type for {key}.", "PropertyValueTypeError")
            allowed = current.get(key + "@Redfish.AllowableValues")
            if allowed is not None and value not in allowed:
                raise RedfishError(400, f"Unsupported value for {key}.", "PropertyValueNotInList")
            merged[key] = copy.deepcopy(value)
    return merged


def patch(bmc: MockBMC, path: str, body: Json, headers: dict[str, str]) -> Result:
    """Validate writable fields and commit a resource update atomically."""
    current = read(bmc, path)
    if "Members" in current or path.startswith(
        (ROOT + "/AccountService", ROOT + "/SessionService")
    ):
        raise RedfishError(405, "This resource cannot be patched.")
    expected = headers.get("if-match")
    if expected is not None and expected not in ("*", etag(current)):
        raise RedfishError(
            412, "The resource changed; read it again before patching.", "PreconditionFailed"
        )
    updated = merge_existing(current, body)
    allowed = patchable_fields(current, bmc.profile)
    for name, value in body.items():
        if name in READ_ONLY:
            raise RedfishError(400, f"{name} is read-only.", "PropertyNotWritable")
        if name not in allowed:
            raise RedfishError(501, f"PATCH of {name} is not implemented by this mock.")
        if isinstance(allowed[name], set):
            for child in value:
                if child not in allowed[name]:
                    code = "PropertyNotWritable" if child in READ_ONLY else "GeneralError"
                    status = 400 if child in READ_ONLY else 501
                    raise RedfishError(status, f"PATCH of {name}.{child} is not supported.", code)
    boot = updated.get("Boot", {})
    if boot.get("BootSourceOverrideEnabled", "Disabled") not in (
        "Disabled",
        "Once",
        "Continuous",
    ):
        raise RedfishError(400, "Invalid BootSourceOverrideEnabled.", "PropertyValueNotInList")
    if boot.get("BootSourceOverrideMode", "UEFI") not in ("Legacy", "UEFI"):
        raise RedfishError(400, "Invalid BootSourceOverrideMode.", "PropertyValueNotInList")
    if current.get("@odata.type", "").startswith("#VirtualMedia."):
        image = updated.get("Image")
        if image is not None and (not isinstance(image, str) or not urlsplit(image).hostname):
            raise RedfishError(
                400, "Image must be an absolute media URL.", "PropertyValueFormatError"
            )
        if "Image" in body and "Inserted" not in body:
            updated["Inserted"] = image is not None
        if updated.get("Inserted") and image is None:
            raise RedfishError(
                400, "Cannot insert media without an image.", "PropertyValueConflict"
            )
        updated["ConnectedVia"] = "URI" if updated.get("Inserted") else "NotConnected"
    bmc.resources[path] = updated
    bmc.positions[path] = len(bmc.steps.get(path, []))
    payload = hpe_message() if bmc.profile == "hpe" else copy.deepcopy(updated)
    return Result(body=payload, headers={"ETag": etag(updated)})
