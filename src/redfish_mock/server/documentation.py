"""Serve bundled Swagger assets and an immutable, instance-specific API description."""

from __future__ import annotations

import html
import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from redfish_mock.openapi import build_specification

from .namespace import external_payload

if TYPE_CHECKING:
    from redfish_mock.engine import MockBMC

STATIC = Path(__file__).resolve().parents[1] / "_static"
ASSET_PREFIX = "/assets/swagger-ui/"
ASSETS = {
    "swagger-ui-bundle.js": ("swagger-ui/swagger-ui-bundle.js", "text/javascript; charset=utf-8"),
    "swagger-ui.css": ("swagger-ui/swagger-ui.css", "text/css; charset=utf-8"),
    "initializer.js": ("initializer.js", "text/javascript; charset=utf-8"),
    "mock.css": ("mock.css", "text/css; charset=utf-8"),
    "redfish-fake-banner.svg": ("redfish-fake-banner.svg", "image/svg+xml"),
    "LICENSE": ("swagger-ui/LICENSE", "text/plain; charset=utf-8"),
    "NOTICE": ("swagger-ui/NOTICE", "text/plain; charset=utf-8"),
    "swagger-ui-bundle.js.LICENSE.txt": (
        "swagger-ui/swagger-ui-bundle.js.LICENSE.txt",
        "text/plain; charset=utf-8",
    ),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


@cache
def asset(name: str) -> tuple[bytes, str]:
    """Load only explicitly registered package assets, never a request-derived filesystem path."""
    filename, content_type = ASSETS[name]
    return (STATIC / filename).read_bytes(), content_type


class Documentation:
    """Cache baseline documentation without advancing sessions, metrics or sequences."""

    def __init__(self, bmc: MockBMC, *, prefix: str = "") -> None:
        """Render the page and contract once from this instance's immutable configuration."""
        specification = external_payload(build_specification(bmc, prefix=prefix), prefix)
        self.specification = json.dumps(specification, ensure_ascii=False).encode()
        page = (STATIC / "index.html").read_text()
        page = page.replace("{{profile}}", html.escape(bmc.profile))
        page = page.replace("{{prefix}}", html.escape(prefix, quote=True))
        # Advertise only the already-public development pair, never configured secrets.
        demo_auth = bmc.require_auth and bmc.username == "admin" and bmc.password == "admin"
        page = page.replace("{{demo_auth}}", "true" if demo_auth else "false")
        self.page = page.encode()

    def lookup(self, path: str, method: str) -> tuple[int, bytes, dict[str, str]] | None:
        """Resolve public documentation routes and reject unregistered assets or write methods."""
        if path not in ("/", "/openapi.json") and not path.startswith(ASSET_PREFIX):
            return None
        status = 200
        headers = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
        if path.startswith(ASSET_PREFIX) and path.removeprefix(ASSET_PREFIX) not in ASSETS:
            status, body, content_type = 404, b"Asset not found.", "text/plain; charset=utf-8"
        elif method not in ("GET", "HEAD"):
            status, body, content_type = 405, b"Method not allowed.", "text/plain; charset=utf-8"
            headers["allow"] = "GET, HEAD"
        elif path == "/":
            body, content_type = self.page, "text/html; charset=utf-8"
            headers["content-security-policy"] = CSP
        elif path == "/openapi.json":
            body, content_type = self.specification, "application/json; charset=utf-8"
        else:
            body, content_type = asset(path.removeprefix(ASSET_PREFIX))
        headers.update({"content-type": content_type, "content-length": str(len(body))})
        return status, body, headers
