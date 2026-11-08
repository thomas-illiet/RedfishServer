"""Small OpenAPI constructors for the implemented mock contract."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redfish_mock.models import Json

ERRORS = {
    400: "Invalid JSON, parameter, type, value or read-only property.",
    401: "Missing or invalid credentials, or expired session.",
    404: "Resource, session or advertised action not found.",
    405: "Method not supported on this resource.",
    408: "Request body did not arrive within 10 seconds.",
    409: "The host must be powered on to select a scenario.",
    412: "The supplied If-Match does not match the resource ETag.",
    413: "Request body exceeds 1 MiB.",
    415: "Use Content-Type: application/json.",
    501: "This operation, property or transfer encoding is not simulated.",
    503: "Readiness, session capacity or transport concurrency limit reached.",
}


def object_schema(properties: Json | None = None, *, required: tuple[str, ...] = ()) -> Json:
    """Describe a request object with explicitly supported fields."""
    schema: Json = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def response(description: str, schema: Json | None = None, example: Json | None = None) -> Json:
    """Describe a response, optionally including its JSON schema and synthetic example."""
    result: Json = {"description": description}
    if schema is not None:
        media = {"schema": schema}
        if example is not None:
            media["example"] = example
        result["content"] = {"application/json": media}
    return result


def errors(*codes: int, head: bool = False) -> Json:
    """Describe applicable failures, omitting bodies for HEAD requests."""
    return {
        str(code): response(ERRORS[code], None if head else {"$ref": "#/components/schemas/Error"})
        for code in codes
    }


def operation(method: str, path: str, tag: str, summary: str, description: str = "") -> Json:
    """Build a stable operation identity independent of punctuation in custom resource paths."""
    identifier = hashlib.sha256(f"{method} {path}".encode()).hexdigest()[:20]
    return {
        "operationId": f"{method}_{identifier}",
        "tags": [tag],
        "summary": summary,
        "description": description,
        "responses": {},
    }


def request_body(schema: Json, example: Json | None = None, *, required: bool = True) -> Json:
    """Describe a JSON body without using configured credentials or live instance state."""
    media = {"schema": schema}
    if example is not None:
        media["example"] = example
    return {"required": required, "content": {"application/json": media}}


def parameter(name: str, location: str, schema: Json, description: str) -> Json:
    """Describe a header, query or required path parameter."""
    return {
        "name": name,
        "in": location,
        "required": location == "path",
        "schema": schema,
        "description": description,
    }


def etag_header() -> Json:
    """Describe the weak content-derived entity tag returned by resource operations."""
    return {"ETag": {"description": "Weak ETag of this resource.", "schema": {"type": "string"}}}


def tag_for(path: str) -> str:
    """Group concrete resource paths by their most specific function."""
    groups = (
        ("SessionService", "Sessions"),
        ("AccountService", "Accounts (fixtures)"),
        ("UpdateService", "Firmware"),
        ("TaskService", "Tasks"),
        ("LogServices", "Logs"),
        ("VirtualMedia", "Virtual media"),
        ("Storage", "Storage"),
        ("Drives", "Storage"),
        ("Power", "Power"),
        ("Thermal", "Thermal and sensors"),
        ("Sensors", "Thermal and sensors"),
        ("Processors", "Inventory"),
        ("Memory", "Inventory"),
        ("Systems", "Systems"),
        ("Managers", "Managers"),
        ("Chassis", "Chassis"),
    )
    for segment, tag in groups:
        if segment in path or (segment == "Power" and path.endswith("ComputerSystem.Reset")):
            return tag
    return "Discovery"
