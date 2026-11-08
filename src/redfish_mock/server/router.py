"""Single-listener routing and aggregate lifecycle for independent profile applications."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from redfish_mock.constants import PROFILES
from redfish_mock.urls import local_uri

from .asgi import Receive, RedfishASGI, Scope, Send
from .documentation import CSP, STATIC
from .logging import LOGGER

if TYPE_CHECKING:
    from redfish_mock.engine import MockBMC

LABELS = {
    "dell": "Dell iDRAC",
    "hpe": "HPE iLO",
    "supermicro": "Supermicro",
    "lenovo": "Lenovo",
    "hpe-ilo4": "HPE iLO 4",
}
PROFILE_NOTES = {
    "dell": "Give iDRAC a test drive.",
    "hpe": "Let iLO join the experiment.",
    "supermicro": "Put your collector through its paces.",
    "lenovo": "Take the ThinkSystem for a spin.",
    "hpe-ilo4": "Retro iLO. Fresh test cases.",
}


class ProfileRouter:
    """Dispatch exact profile prefixes while keeping simulations and credentials scoped."""

    def __init__(self, profiles: dict[str, MockBMC], *, delay: float = 0) -> None:
        """Initialize all documentation before a listener can become available."""
        if set(profiles) != set(PROFILES) or any(
            name != bmc.profile for name, bmc in profiles.items()
        ):
            raise ValueError("Multi-profile mode requires all five matching profiles")
        self.apps = {
            name: RedfishASGI(bmc, delay=delay, prefix="/" + name) for name, bmc in profiles.items()
        }
        cards = "".join(
            f'<article class="profile-card" aria-labelledby="profile-{name}">'
            '<div class="rack-strip"><span class="rack-vents" aria-hidden="true"></span>'
            f'<span class="unit">UNIT {index:02d}</span>'
            '<span class="mock-label"><i aria-hidden="true"></i> MOCK</span></div>'
            f'<div class="card-content"><code>/{name}</code>'
            f'<h3 id="profile-{name}">{html.escape(LABELS[name])}</h3>'
            f"<p>{html.escape(PROFILE_NOTES[name])}</p>"
            f'<a class="swagger-link" href="/{name}/">Open Swagger '
            '<span aria-hidden="true">↗</span></a>'
            f'<nav class="api-links" aria-label="{html.escape(LABELS[name])} resources">'
            f'<a href="/{name}/redfish/v1/">Redfish API</a><span aria-hidden="true">/</span>'
            f'<a href="/{name}/openapi.json">OpenAPI JSON</a></nav></div></article>'
            for index, name in enumerate(PROFILES, start=1)
        )
        self.page = (STATIC / "catalog.html").read_text().replace("{{cards}}", cards).encode()

    @property
    def ready(self) -> bool:
        """Report readiness only while every profile is initialized and accepting work."""
        return all(app.ready for app in self.apps.values())

    @ready.setter
    def ready(self, value: bool) -> None:
        """Withdraw or publish readiness atomically for the complete shared listener."""
        for app in self.apps.values():
            app.ready = value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route validated raw paths without allowing decoding to change profile identity."""
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        raw = scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        try:
            decoded = local_uri(raw)
        except ValueError, UnicodeError:
            await self._reply(scope, send, 400, {"error": "Invalid request path"})
            return
        if decoded in ("/", "/healthz", "/readyz") and raw == decoded:
            if scope["method"] not in ("GET", "HEAD"):
                await self._reply(
                    scope, send, 405, {"error": "Method not allowed"}, {"allow": "GET, HEAD"}
                )
            elif decoded == "/":
                await self._reply(scope, send, 200, self.page)
            elif decoded == "/healthz":
                await self._reply(scope, send, 200, {"status": "ok", "profiles": list(self.apps)})
            else:
                await self._reply(
                    scope,
                    send,
                    200 if self.ready else 503,
                    {
                        "ready": self.ready,
                        "profiles": {name: app.ready for name, app in self.apps.items()},
                    },
                )
            return
        profile = raw.split("/", 2)[1]
        app = self.apps.get(profile)
        if app is None:
            await self._reply(scope, send, 404, {"error": "Unknown profile or route"})
            return
        prefix = "/" + profile
        if raw == prefix:
            query = scope.get("query_string", b"").decode("latin-1")
            await self._reply(
                scope, send, 308, None, {"location": prefix + "/" + ("?" + query if query else "")}
            )
            return
        nested = {
            **scope,
            "path": decoded[len(prefix) :],
            "raw_path": raw[len(prefix) :].encode("latin-1"),
        }
        await app(nested, receive, send)

    async def _reply(
        self,
        scope: Scope,
        send: Send,
        status: int,
        body: dict | bytes | None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Return bounded catalogue, probe or router responses without logging user paths."""
        encoded = (
            body
            if isinstance(body, bytes)
            else json.dumps(body).encode()
            if body is not None
            else b""
        )
        fields = {
            "content-type": "text/html; charset=utf-8"
            if isinstance(body, bytes)
            else "application/json",
            "content-length": str(len(encoded)),
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            **(headers or {}),
        }
        if isinstance(body, bytes):
            fields["content-security-policy"] = CSP
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(k.encode(), v.encode()) for k, v in fields.items()],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"" if scope["method"] == "HEAD" else encoded,
                }
            )
        except OSError, ConnectionError:
            pass
        LOGGER.info("request", extra={"fields": {"profile": "catalog", "status": status}})

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        """Make startup and shutdown apply to all profiles as one lifecycle."""
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                self.ready = True
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                self.ready = False
                await send({"type": "lifespan.shutdown.complete"})
                return
