"""OpenAPI operations for actions classified by the same rules as the engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redfish_mock.engine.capabilities import action_kind, reset_types

from .common import errors, object_schema, operation, request_body, response, tag_for

if TYPE_CHECKING:
    from redfish_mock.models import Json


def action_operation(profile: str, path: str, definition: Json) -> Json:
    """Describe a simulated action or explicitly document its 501-only behavior."""
    kind = action_kind(profile, path)
    name = path.rsplit("/", 1)[-1]
    supported = kind != "unsupported"
    item = operation(
        "post",
        path,
        tag_for(path),
        name + (" — simulated" if supported else " — not simulated (501)"),
    )
    item["x-mock-support"] = "simulated" if supported else "not-implemented"
    item["responses"] = errors(400, 401, 404, 408, 413, 415, 501, 503)
    if not supported:
        item["description"] = (
            "Advertised by the active dataset, but not implemented by this simulator. "
            "Authenticated calls return 501. Firmware processing and asynchronous Supermicro "
            "media workflows are not simulated; no real hardware is contacted."
        )
        item["requestBody"] = request_body({"type": "object"}, {}, required=False)
        return item
    if kind == "reset":
        choices = reset_types(definition)
        item["requestBody"] = request_body(
            {
                "type": "object",
                "properties": {"ResetType": {"type": "string", "enum": choices}},
                "required": ["ResetType"],
            },
            {"ResetType": choices[0]} if choices else None,
        )
        item["description"] = (
            "Instantaneous simulated power transition. Nmi does not change power state. "
            "A Once boot override is consumed on boot or reboot. Shutdown leaves the BMC "
            "reachable; Manager.Reset does not simulate downtime."
        )
    elif kind == "insert":
        item["requestBody"] = request_body(
            {
                "type": "object",
                "properties": {
                    "Image": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^(http|https|nfs|cifs)://",
                        "description": "Absolute URL with a host; stored only, never downloaded.",
                    },
                    "Inserted": {"type": "boolean", "default": True},
                    "WriteProtected": {"type": "boolean", "default": True},
                },
                "required": ["Image"],
            },
            {"Image": "https://example.test/install.iso", "Inserted": True, "WriteProtected": True},
        )
        item["description"] = "Store a media URL in memory. No media is downloaded or executed."
    else:
        item["requestBody"] = request_body(object_schema(), {}, required=False)
        item["description"] = "Clear the simulated media attachment in memory."
    if profile == "hpe":
        item["responses"]["200"] = response(
            "iLO Success message inside the error-named envelope; this is a successful response.",
            {"$ref": "#/components/schemas/HpeSuccess"},
        )
    elif profile == "supermicro" and name == "ComputerSystem.Reset":
        item["responses"]["200"] = response(
            "Simulated reset completed.", {"$ref": "#/components/schemas/SupermicroSuccess"}
        )
    else:
        item["responses"]["204"] = response("Simulated action completed; no body.")
    return item
