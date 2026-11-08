"""Authenticated same-origin Redfish HTTP collection."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.request
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from redfish_mock.models import Json


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent redirects from forwarding credentials to a different origin."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        """Reject redirection and leave urllib to report the redirect response."""
        return


class RedfishClient:
    """Fetch JSON resources from one validated Redfish origin with TLS verification."""

    def __init__(
        self, base_url: str, username: str, password: str, *, ca_file: str | None = None
    ) -> None:
        """Validate the origin and configure same-origin authentication and trusted TLS."""
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("URL must be an HTTP(S) origin, e.g. https://bmc.example:443")
        self.base = base_url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),
        )

    def get(self, path: str) -> Json:
        """Fetch a JSON resource while rejecting links outside the configured origin."""
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not parsed.path.startswith("/redfish/")
        ):
            raise ValueError("Refusing a link outside the configured Redfish origin")
        # The constructor restricts the origin to HTTP(S); links cannot change that origin.
        request = urllib.request.Request(self.base + path, headers={"Authorization": self.auth})  # noqa: S310
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)
