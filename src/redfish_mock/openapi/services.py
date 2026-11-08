"""OpenAPI contracts for authentication, discovery, probes and test controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redfish_mock.constants import ROOT, SESSIONS
from redfish_mock.monitoring import SCENARIOS

from .common import errors, object_schema, operation, parameter, request_body, response
from .resources import reads

if TYPE_CHECKING:
    from redfish_mock.engine import MockBMC
    from redfish_mock.models import Json


def sessions(paths: Json, profile: str) -> None:
    """Add credential-based session creation and dynamic session read/delete operations."""
    item = operation(
        "post",
        SESSIONS,
        "Sessions",
        "Create a mock session",
        "Credentials in the JSON body are always checked, including in --no-auth mode. "
        "Copy X-Auth-Token from the response into Authorize. Tokens belong to one instance; "
        "reset/restart revokes them. Idle expiry is configurable (default 1800 seconds); "
        "valid token use renews it. At most 1024 sessions may exist.",
    )
    item["security"] = []
    item["requestBody"] = request_body(
        object_schema(
            {
                "UserName": {"type": "string", "minLength": 1},
                "Password": {
                    "type": "string",
                    "format": "password",
                    "writeOnly": True,
                    "minLength": 1,
                },
            },
            required=("UserName", "Password"),
        )
    )
    success = response("Session created.", {"$ref": "#/components/schemas/Session"})
    success["headers"] = {
        "X-Auth-Token": {
            "description": "Secret token for subsequent requests.",
            "schema": {"type": "string"},
        },
        "Location": {
            "description": "URI of the session; DELETE it to log out.",
            "schema": {"type": "string"},
        },
    }
    item["responses"] = {"201": success, **errors(400, 401, 408, 413, 415, 503)}
    paths.setdefault(SESSIONS, {})["post"] = item
    path = SESSIONS + "/{sessionId}"
    paths[path] = reads(path, {"@odata.type": "#Session.v1_6_0.Session"})
    paths[path]["parameters"] = [
        parameter(
            "sessionId",
            "path",
            {"type": "string"},
            "Identifier from the session creation Location header.",
        )
    ]
    item = operation(
        "delete",
        path,
        "Sessions",
        "Revoke a session",
        "The selected token immediately becomes invalid.",
    )
    item["responses"] = errors(401, 404, 503)
    if profile == "supermicro":
        item["responses"]["200"] = response(
            "Session revoked.", {"$ref": "#/components/schemas/SupermicroSuccess"}
        )
    else:
        item["responses"]["204"] = response("Session revoked; no body.")
    paths[path]["delete"] = item


def utilities(paths: Json, bmc: MockBMC) -> None:
    """Describe public discovery/probes and the authenticated mock metadata endpoint."""
    definitions = {
        "/redfish": ("Discovery", "Discover the Redfish service", {"v1": ROOT + "/"}),
        "/healthz": (
            "Service health",
            "Check HTTP availability",
            {"status": "ok", "profile": bmc.profile, "redfish": ROOT + "/"},
        ),
        "/readyz": (
            "Service health",
            "Check server readiness",
            {"ready": True, "profile": bmc.profile},
        ),
        "/_mock": ("Mock controls", "Inspect simulator metadata", None),
    }
    for path, (tag, summary, example) in definitions.items():
        paths[path] = {}
        for method in ("get", "head"):
            item = operation(
                method,
                path,
                tag,
                summary,
                "Utility endpoint, separate from simulated hardware health. "
                "Mock metadata reports fidelity, scenario, sessions and telemetry mode.",
            )
            schema = (
                {"type": "object"}
                if path != "/_mock"
                else {"$ref": "#/components/schemas/MockStatus"}
            )
            item["responses"] = {
                "200": response("Current status.", None if method == "head" else schema, example)
            }
            item["responses"].update(errors(503, head=method == "head"))
            if path != "/_mock":
                item["security"] = []
            else:
                item["responses"].update(errors(401, head=method == "head"))
            if path == "/readyz":
                item["responses"]["503"] = response(
                    "Server not ready.",
                    None if method == "head" else schema,
                    {"ready": False, "profile": bmc.profile},
                )
            paths[path][method] = item


def controls(paths: Json, bmc: MockBMC) -> None:
    """Document test-only reset, scenarios and explicit resource overrides."""
    definitions = {
        "reset": (
            "Reset the mock",
            (
                "Restore baseline resources, sequences and telemetry. "
                "All sessions are revoked, including the token used for this request."
            ),
            object_schema(),
            {},
        ),
        "scenario": (
            "Select a scenario",
            (
                "Requires enriched monitoring and a powered-on host. "
                "Returns 409 when the host is off, or 501 when monitoring is unavailable."
            ),
            object_schema(
                {"Scenario": {"type": "string", "enum": list(SCENARIOS)}}, required=("Scenario",)
            ),
            {"Scenario": "baseline"},
        ),
        "overrides": (
            "Inject a test state",
            (
                "Change existing properties while preserving identity and links. "
                "Accounts and sessions are protected. Aggregates may not be recalculated. "
                "Dynamic telemetry uses overrides as starting values; static mode preserves them."
            ),
            object_schema(
                {"Path": {"type": "string"}, "Patch": {"type": "object"}},
                required=("Path", "Patch"),
            ),
            None,
        ),
    }
    for name, (summary, description, schema, example) in definitions.items():
        path = "/_mock/" + name
        item = operation(
            "post",
            path,
            "Mock controls",
            summary,
            "Test-only, non-Redfish operation. " + description,
        )
        item["requestBody"] = request_body(schema, example, required=name != "reset")
        item["responses"] = errors(400, 401, 404, 408, 413, 415, 501, 503)
        if name == "scenario":
            item["responses"].update(errors(409))
        supported = name != "scenario" or bmc.monitoring_enabled
        item["x-mock-support"] = "simulated" if supported else "not-implemented"
        if supported:
            item["responses"]["200"] = response("Simulator state updated.", {"type": "object"})
        else:
            item["summary"] += " — unavailable (501)"
        paths[path] = {"post": item}
