"""Verify public offline documentation on a disposable HTTP or HTTPS instance."""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.smoke import Client


def check_documentation(vendor: str, client: Client) -> None:
    """Check the installed page, profile contract and browser assets without authentication."""
    resources = {
        "/": "text/html",
        "/openapi.json": "application/json",
        "/assets/swagger-ui/swagger-ui-bundle.js": "text/javascript",
        "/assets/swagger-ui/swagger-ui.css": "text/css",
        "/assets/swagger-ui/initializer.js": "text/javascript",
        "/assets/swagger-ui/mock.css": "text/css",
        "/assets/swagger-ui/redfish-fake-banner.svg": "image/svg+xml",
        "/assets/swagger-ui/LICENSE": "text/plain",
    }
    for path, content_type in resources.items():
        with client.opener.open(client.endpoint.url(path), timeout=10) as result:
            payload = result.read()
            length = result.headers["Content-Length"]
            if (
                result.status != 200
                or result.headers.get_content_type() != content_type
                or not payload
            ):
                raise RuntimeError(f"Missing or invalid bundled documentation: {path}")
        request = urllib.request.Request(client.endpoint.url(path), method="HEAD")
        with client.opener.open(request, timeout=10) as result:
            if result.status != 200 or result.read() or result.headers["Content-Length"] != length:
                raise RuntimeError(f"Invalid documentation HEAD response: {path}")
        if path == "/" and '<main id="swagger-ui"' not in payload.decode():
            raise RuntimeError("Swagger documentation is missing")
        if path == "/openapi.json":
            specification = json.loads(payload)
            if specification["servers"][0]["url"] != (client.endpoint.prefix or "/"):
                raise RuntimeError("Documentation server does not match the profile prefix")
            if "redfish-fake-banner.svg" not in specification["info"]["description"]:
                raise RuntimeError("Fake banner is missing from the Swagger description")
            if specification["x-mock-fidelity"]["profile"] != vendor:
                raise RuntimeError("Documentation profile does not match the instance")
            if specification["openapi"] != "3.1.0" or "/_mock/reset" not in specification["paths"]:
                raise RuntimeError("Incomplete OpenAPI contract")
