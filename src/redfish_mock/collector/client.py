"""Authenticated same-origin Redfish HTTP collection."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.request
from typing import TYPE_CHECKING, Any

from redfish_mock.urls import Endpoint

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
        self.endpoint = Endpoint(base_url)
        self.base = self.endpoint.base
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),
        )

    def get(self, path: str) -> Json:
        """Fetch a JSON resource while rejecting links outside the configured origin."""
        # The constructor restricts the origin to HTTP(S); links cannot change that origin.
        request = urllib.request.Request(  # noqa: S310 -- Endpoint restricts scheme and profile.
            self.endpoint.url(path, redfish_only=True), headers={"Authorization": self.auth}
        )
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)
