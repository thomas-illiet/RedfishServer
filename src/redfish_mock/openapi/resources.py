"""Documentation of baseline resource reads and the supported writable subset."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redfish_mock.constants import ROOT
from redfish_mock.contracts import patchable_fields

from .common import (
    errors,
    etag_header,
    object_schema,
    operation,
    parameter,
    request_body,
    response,
    tag_for,
)

if TYPE_CHECKING:
    from redfish_mock.models import Json

RESOURCE = {"$ref": "#/components/schemas/Resource"}


def reads(path: str, resource: Json) -> Json:
    """Describe GET and HEAD using only synthetic examples and baseline resource metadata."""
    result = {}
    for method in ("get", "head"):
        item = operation(
            method,
            path,
            tag_for(path),
            ("Read " if method == "get" else "Inspect headers for ") + path.rsplit("/", 1)[-1],
            "Synthetic mock resource; this is not a complete DMTF or vendor schema. "
            "GET may advance configured fixture sequences; HEAD does not. Both may advance "
            "time-based telemetry. Accounts and roles are fixtures, not an authentication "
            "directory. "
            "Task sequences are independent of executed actions.",
        )
        example = {
            "@odata.id": path,
            "@odata.type": resource.get("@odata.type", "#Resource.Resource"),
        }
        if "Members" in resource:
            example.update({"Members": [], "Members@odata.count": 0})
            item["parameters"] = [
                parameter("$top", "query", {"type": "integer", "minimum": 0}, "Maximum members."),
                parameter("$skip", "query", {"type": "integer", "minimum": 0}, "Members to skip."),
                parameter(
                    "$expand",
                    "query",
                    {"type": "string", "enum": [".", "*"]},
                    "Expand one level. Recursive expansion, $filter and $select are unsupported.",
                ),
            ]
            item["description"] += (
                " Members@odata.count is the total; Members@odata.nextLink gives the next page."
            )
        success = response(
            "Resource headers." if method == "head" else "Synthetic resource.",
            None if method == "head" else RESOURCE,
            example,
        )
        success["headers"] = etag_header()
        item["responses"] = {"200": success, **errors(400, 401, 404, 503, head=method == "head")}
        if path == ROOT:
            item["security"] = []
        result[method] = item
    return result


def patch_operation(path: str, resource: Json, profile: str) -> Json | None:
    """Document only writable properties present in this profile's baseline resource."""
    if "Members" in resource or path.startswith(
        (ROOT + "/AccountService", ROOT + "/SessionService")
    ):
        return None
    fields = patchable_fields(resource, profile)
    properties: Json = {}
    for name, children in fields.items():
        if name not in resource:
            continue
        if isinstance(children, set):
            nested = {}
            for child in sorted(children):
                if child not in resource[name]:
                    continue
                schema: Json = {"type": "string"}
                choices = resource[name].get(child + "@Redfish.AllowableValues")
                if child == "BootSourceOverrideEnabled":
                    choices = choices or ["Disabled", "Once", "Continuous"]
                elif child == "BootSourceOverrideMode":
                    choices = choices or ["Legacy", "UEFI"]
                if choices is not None:
                    schema["enum"] = choices
                nested[child] = schema
            if nested:
                properties[name] = object_schema(nested)
        else:
            properties[name] = {
                "type": ["string", "null"]
                if name == "Image"
                else "boolean"
                if name in ("Inserted", "WriteProtected")
                else "string"
            }
    if not properties:
        return None
    item = operation(
        "patch",
        path,
        tag_for(path),
        "Update simulated settings",
        "Only the listed properties are writable. Hardware identity, inventory, health "
        "and readings are read-only. Changes are immediate and in memory. Read the "
        "resource again after writing. HPE success uses an error-named iLO envelope. "
        "Media Image accepts null or an absolute URL with a host; inserted media requires "
        "an Image. Images are never downloaded.",
    )
    item["parameters"] = [
        parameter(
            "If-Match",
            "header",
            {"type": "string"},
            "Current weak ETag or *; stale values return 412.",
        )
    ]
    item["requestBody"] = request_body(object_schema(properties), required=False)
    success = response(
        "Settings updated; GET the resource to observe the result.",
        {"$ref": "#/components/schemas/HpeSuccess"} if profile == "hpe" else RESOURCE,
    )
    success["headers"] = etag_header()
    item["responses"] = {"200": success, **errors(400, 401, 404, 408, 412, 413, 415, 501, 503)}
    item["x-mock-support"] = "simulated"
    return item
