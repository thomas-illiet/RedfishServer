"""Shared JSON payload, response and error models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

Json = dict[str, Any]


class RedfishError(Exception):
    """Carry an HTTP status and standard Redfish error code."""

    def __init__(self, status: int, message: str, code: str = "GeneralError") -> None:
        """Initialize the HTTP status, message and namespaced Redfish error code."""
        super().__init__(message)
        self.status = status
        self.code = "Base.1.18." + code

    def payload(self) -> Json:
        """Return the standard JSON error envelope for this exception."""
        return {"error": {"code": self.code, "message": str(self)}}


@dataclass
class Result:
    """Carry a response status, optional JSON body and HTTP headers."""

    status: int = 200
    body: Json | None = None
    headers: dict[str, str] = field(default_factory=dict)


def etag(body: Json) -> str:
    """Hash a canonical JSON snapshot into a weak resource entity tag."""
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    return f'W/"{digest}"'
