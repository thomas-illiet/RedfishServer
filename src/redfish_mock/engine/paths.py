"""Validation of resource paths and query parameters."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

from redfish_mock.models import RedfishError


def normalize(raw: str) -> tuple[str, dict[str, list[str]]]:
    """Decode a local resource path and reject traversal or external origins."""
    parsed = urlsplit(raw)
    path = unquote(parsed.path).rstrip("/") or "/"
    if parsed.scheme or parsed.netloc or any(p in (".", "..") for p in path.split("/")):
        raise RedfishError(400, "Invalid resource path.")
    return path, parse_qs(parsed.query, keep_blank_values=True)
