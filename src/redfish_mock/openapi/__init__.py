"""Build an instance-specific OpenAPI contract without reading mutable simulation state."""

from __future__ import annotations

from importlib.metadata import version
from typing import TYPE_CHECKING

from redfish_mock.contracts import contract_summary
from redfish_mock.engine.capabilities import advertised_actions

from .actions import action_operation
from .resources import patch_operation, reads
from .schemas import schemas
from .services import controls, sessions, utilities

if TYPE_CHECKING:
    from redfish_mock.engine import MockBMC
    from redfish_mock.models import Json

__all__ = ["build_specification"]


def build_specification(bmc: MockBMC, *, prefix: str = "") -> Json:
    """Generate documentation from normalized baseline resources and engine capability rules."""
    paths: Json = {}
    for path, resource in sorted(bmc.original.items()):
        paths[path] = reads(path, resource)
        patch = patch_operation(path, resource, bmc.profile)
        if patch is not None:
            paths[path]["patch"] = patch
    for owner, resource in sorted(bmc.original.items()):
        for target, definition in sorted(advertised_actions(resource).items()):
            # The dispatcher resolves the owner before looking up the advertised target.
            if target.split("/Actions/", 1)[0] == owner and "/Actions/" in target:
                paths.setdefault(target, {})["post"] = action_operation(
                    bmc.profile, target, definition
                )
    sessions(paths, bmc.profile)
    utilities(paths, bmc)
    controls(paths, bmc)
    tags = sorted(
        {
            operation["tags"][0]
            for path in paths.values()
            for method, operation in path.items()
            if method != "parameters"
        }
    )
    fidelity = contract_summary(bmc.profile, raw=bmc.raw_fixtures)
    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"Redfish Mock Server — {bmc.profile}",
            "version": version("redfish-mock-server"),
            "description": (
                "![RedfishServer Fake — synthetic test environment]"
                f"({prefix}/assets/swagger-ui/redfish-fake-banner.svg)"
            ),
        },
        "servers": [{"url": prefix or "/", "description": "This mock instance"}],
        "security": [{"basicAuth": []}, {"sessionToken": []}] if bmc.require_auth else [],
        "tags": [{"name": tag} for tag in tags],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "basicAuth": {"type": "http", "scheme": "basic"},
                "sessionToken": {"type": "apiKey", "in": "header", "name": "X-Auth-Token"},
            },
            "schemas": schemas(bmc.profile),
        },
        "x-mock-fidelity": fidelity,
    }
