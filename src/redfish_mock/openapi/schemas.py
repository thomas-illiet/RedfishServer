"""Permissive response schemas for the mock's limited Redfish implementation."""

from __future__ import annotations

from redfish_mock.contracts import hpe_message, supermicro_success
from redfish_mock.models import Json, RedfishError


def schemas(profile: str) -> Json:
    """Return reusable response shapes and synthetic examples without active instance data."""
    message = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "@Message.ExtendedInfo": {"type": "array", "items": {"type": "object"}},
        },
    }
    envelope = {"type": "object", "properties": {"error": message}, "required": ["error"]}
    failure = (
        hpe_message("GeneralError")
        if profile == "hpe"
        else RedfishError(400, "Invalid request.").payload()
    )
    return {
        "Resource": {
            "type": "object",
            "description": "Implemented mock subset; additional vendor fields may be present. "
            "Examples are synthetic illustrations, not live snapshots or full DMTF schemas.",
            "properties": {
                "@odata.id": {"type": "string"},
                "@odata.type": {"type": "string"},
                "Id": {"type": "string"},
                "Name": {"type": "string"},
                "Members": {"type": "array", "items": {"type": "object"}},
                "Members@odata.count": {"type": "integer", "minimum": 0},
                "Members@odata.nextLink": {"type": "string"},
            },
        },
        "Session": {
            "type": "object",
            "properties": {
                "@odata.id": {"type": "string"},
                "@odata.type": {"type": "string"},
                "Id": {"type": "string"},
                "Name": {"type": "string"},
                "UserName": {"type": "string"},
            },
        },
        "Error": {
            **envelope,
            "description": "Redfish error envelope. Transport framing errors may be plain text.",
            "examples": [failure],
        },
        "HpeSuccess": {
            **envelope,
            "description": "HTTP success despite the error key; inspect MessageId.",
            "examples": [hpe_message()],
        },
        "SupermicroSuccess": {
            "type": "object",
            "properties": {"Success": message},
            "required": ["Success"],
            "examples": [supermicro_success()],
        },
        "MockStatus": {
            "type": "object",
            "properties": {
                "profile": {"type": "string"},
                "resources": {"type": "integer"},
                "sessions": {"type": "integer"},
                "storage": {"const": "memory"},
                "fidelity": {"type": "object"},
                "monitoring": {"type": "object"},
            },
        },
    }
