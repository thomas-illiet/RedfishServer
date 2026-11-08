"""Basic authentication and bounded, expiring Redfish sessions."""

from __future__ import annotations

import base64
import binascii
import copy
import hmac
import secrets
import time
from typing import TYPE_CHECKING, Any

from redfish_mock.constants import SESSIONS
from redfish_mock.contracts import supermicro_success
from redfish_mock.models import Json, RedfishError, Result

if TYPE_CHECKING:
    from .bmc import MockBMC


def _credentials_valid(bmc: MockBMC, username: Any, password: Any) -> bool:
    """Compare supplied credentials with constant-time byte comparisons."""
    return (
        isinstance(username, str)
        and isinstance(password, str)
        and hmac.compare_digest(username.encode(), bmc.username.encode())
        and hmac.compare_digest(password.encode(), bmc.password.encode())
    )


def expire_sessions(bmc: MockBMC) -> None:
    """Remove expired tokens and rebuild the Redfish session collection."""
    for token, (uri, expiry) in list(bmc.sessions.items()):
        if expiry <= time.monotonic():
            del bmc.sessions[token]
            bmc.resources.pop(uri, None)
    bmc.resources[SESSIONS]["Members"] = [{"@odata.id": uri} for uri, _ in bmc.sessions.values()]
    bmc.resources[SESSIONS]["Members@odata.count"] = len(bmc.sessions)


def authenticate(bmc: MockBMC, headers: dict[str, str]) -> None:
    """Accept valid Basic credentials or refresh an active session token."""
    if not bmc.require_auth:
        return
    token = headers.get("x-auth-token", "")
    if token:
        if token in bmc.sessions:
            uri, _ = bmc.sessions[token]
            bmc.sessions[token] = (uri, time.monotonic() + bmc.session_timeout)
            return
    else:
        auth = headers.get("authorization", "")
        scheme, _, encoded = auth.partition(" ")
        if scheme.lower() == "basic":
            try:
                username, _, password = (
                    base64.b64decode(encoded, validate=True).decode().partition(":")
                )
                if _credentials_valid(bmc, username, password):
                    return
            except ValueError, UnicodeDecodeError, binascii.Error:
                pass
    raise RedfishError(401, "Valid Basic credentials or X-Auth-Token required.", "NoValidSession")


def create_session(bmc: MockBMC, body: Json) -> Result:
    """Issue a bounded session with a random token and resource identifier."""
    if not _credentials_valid(bmc, body.get("UserName"), body.get("Password")):
        raise RedfishError(401, "Invalid credentials.", "NoValidSession")
    if len(bmc.sessions) >= 1024:
        raise RedfishError(503, "Session limit reached.")
    token = secrets.token_urlsafe(32)
    sid = secrets.token_hex(12)
    uri = SESSIONS + "/" + sid
    session = {
        "@odata.id": uri,
        "@odata.type": "#Session.v1_6_0.Session",
        "Id": sid,
        "Name": "Mock session",
        "UserName": bmc.username,
    }
    bmc.resources[uri] = session
    bmc.sessions[token] = (uri, time.monotonic() + bmc.session_timeout)
    expire_sessions(bmc)
    return Result(201, copy.deepcopy(session), {"X-Auth-Token": token, "Location": uri})


def delete_session(bmc: MockBMC, path: str) -> Result:
    """Revoke the selected session and refresh its collection membership."""
    for token, (uri, _) in list(bmc.sessions.items()):
        if uri == path:
            del bmc.sessions[token]
            bmc.resources.pop(uri, None)
            expire_sessions(bmc)
            if bmc.profile == "supermicro":
                return Result(body=supermicro_success())
            return Result(204)
    raise RedfishError(404, "Session not found.", "ResourceMissingAtURI")
