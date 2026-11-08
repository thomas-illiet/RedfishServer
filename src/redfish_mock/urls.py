"""Validated local paths and profile-scoped client endpoints."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from .constants import PROFILES


def local_uri(value: str, *, fragments: bool = False) -> str:
    """Validate a local URI before decoding, rejecting ambiguous or traversing paths."""
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or (parsed.fragment and not fragments)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c|25)", parsed.path, re.IGNORECASE)
    ):
        raise ValueError("Invalid local URI")
    path = unquote(parsed.path, errors="strict")
    if (
        "\\" in path
        or "//" in path
        or any(part in (".", "..") for part in path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise ValueError("Invalid local path")
    return path


class Endpoint:
    """Resolve canonical and already-prefixed links within a configured profile."""

    def __init__(self, base_url: str) -> None:
        """Accept an HTTP(S) origin with an optional known profile prefix."""
        parsed = urlsplit(base_url)
        prefix = parsed.path.rstrip("/")
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/", prefix, prefix + "/")
            or (prefix and prefix not in {"/" + profile for profile in PROFILES})
            or any(ord(char) <= 32 or ord(char) == 127 for char in base_url)
        ):
            raise ValueError("URL must be an HTTP(S) origin with an optional profile prefix")
        # Accessing port also validates malformed and out-of-range port values.
        _ = parsed.port
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.prefix = prefix
        self.base = self.origin + prefix

    def path(self, uri: str, *, redfish_only: bool = False) -> str:
        """Resolve a local link without double-prefixing or leaving the selected profile."""
        try:
            decoded = local_uri(uri)
            if self.prefix and (decoded == self.prefix or decoded.startswith(self.prefix + "/")):
                internal = decoded[len(self.prefix) :] or "/"
                # Profile selectors must be literal, never percent-encoded aliases.
                if not uri.startswith(self.prefix + "/") and uri != self.prefix:
                    raise ValueError
                result = uri
            else:
                internal = decoded
                result = self.prefix + uri
            if internal.split("/")[1] in PROFILES:
                raise ValueError
            if redfish_only and not internal.startswith("/redfish/"):
                raise ValueError
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Refusing a link outside the configured Redfish profile") from exc
        return result

    def url(self, uri: str, *, redfish_only: bool = False) -> str:
        """Return the absolute URL of a validated profile-local link."""
        return self.origin + self.path(uri, redfish_only=redfish_only)
