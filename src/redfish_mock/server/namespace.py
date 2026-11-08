"""Translate only local URI fields at the HTTP boundary, preserving engine state."""

from __future__ import annotations

from typing import Any

from redfish_mock.models import RedfishError
from redfish_mock.urls import Endpoint

URI_FIELDS = frozenset(
    {
        "@odata.id",
        "@odata.context",
        "@odata.nextLink",
        "@Redfish.ActionInfo",
        "target",
        "TaskMonitor",
        "v1",
        "redfish",
        "reset",
        "overrides",
        "control",
        "resource",
    }
)


def external_uri(uri: str, prefix: str) -> str:
    """Prefix local service URIs while preserving query strings and JSON pointers."""
    if prefix and any(
        uri == root or uri.startswith((root + "/", root + "?", root + "#"))
        for root in ("/redfish", "/_mock", "/healthz", "/readyz")
    ):
        return prefix + uri
    return uri


def external_payload(value: Any, prefix: str) -> Any:
    """Copy JSON and translate known URI fields, never arbitrary strings or media URLs."""
    if isinstance(value, list):
        return [external_payload(item, prefix) for item in value]
    if isinstance(value, dict):
        return {
            key: external_uri(item, prefix)
            if isinstance(item, str) and (key in URI_FIELDS or key.endswith("@odata.nextLink"))
            else external_payload(item, prefix)
            for key, item in value.items()
        }
    return value


def override_body(body: dict, prefix: str) -> dict:
    """Resolve override targets within one profile without transforming patch values."""
    target = body.get("Path")
    if not prefix or not isinstance(target, str):
        return body
    try:
        resolved = Endpoint("http://profile.invalid" + prefix).path(target, redfish_only=True)
    except ValueError as exc:
        raise RedfishError(400, "Override target must belong to this profile.") from exc
    return {**body, "Path": resolved.removeprefix(prefix)}
