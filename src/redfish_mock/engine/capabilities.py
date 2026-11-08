"""Shared discovery and classification of advertised action capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from redfish_mock.models import Json

ActionKind = Literal["reset", "insert", "eject", "unsupported"]
ACTION_KINDS: dict[str, ActionKind] = {
    "ComputerSystem.Reset": "reset",
    "Manager.Reset": "reset",
    "VirtualMedia.InsertMedia": "insert",
    "HpeiLOVirtualMedia.InsertVirtualMedia": "insert",
    "VirtualMedia.EjectMedia": "eject",
    "HpeiLOVirtualMedia.EjectVirtualMedia": "eject",
}


def advertised_actions(node: Any) -> dict[str, Json]:
    """Collect advertised targets using the engine's recursive first-match semantics."""
    targets: dict[str, Json] = {}
    if isinstance(node, dict):
        target = node.get("target")
        if isinstance(target, str):
            targets[target.rstrip("/")] = node
        for value in node.values():
            for path, definition in advertised_actions(value).items():
                targets.setdefault(path, definition)
    return targets


def action_kind(profile: str, path: str) -> ActionKind:
    """Classify supported handlers, including the unsimulated Supermicro media workflow."""
    action = path.rsplit("/", 1)[-1]
    if profile == "supermicro" and action.startswith("VirtualMedia."):
        return "unsupported"
    return ACTION_KINDS.get(action, "unsupported")


def reset_types(definition: Json) -> list[str]:
    """Return advertised reset values or the engine's fallback values."""
    return definition.get("ResetType@Redfish.AllowableValues", ["GracefulRestart", "ForceRestart"])
