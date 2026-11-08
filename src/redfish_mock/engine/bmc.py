"""Thread-safe BMC lifecycle and request dispatch."""

from __future__ import annotations

import copy
import threading
from typing import TYPE_CHECKING

from redfish_mock.constants import FIXTURES, PROFILES, ROOT, SESSIONS
from redfish_mock.contracts import apply_corrections, hpe_message, normalize_resource
from redfish_mock.models import Json, RedfishError, Result
from redfish_mock.simulation import TelemetrySimulation

from .actions import execute
from .controls import override_resource, select_scenario
from .fixtures import load_fixtures
from .patching import patch
from .paths import normalize
from .resources import get
from .sessions import authenticate, create_session, delete_session, expire_sessions

if TYPE_CHECKING:
    from pathlib import Path


class MockBMC:
    """Own one profile and serialize all requests against its in-memory state."""

    def __init__(
        self,
        profile: str,
        *,
        mockups_dir: Path = FIXTURES,
        username: str = "admin",
        password: str = "admin",  # noqa: S107 -- Public default for a local mock.
        require_auth: bool = True,
        play_sequences: bool = False,
        session_timeout: int = 1800,
        raw_fixtures: bool = False,
        dynamic_metrics: bool = True,
        metric_interval: int = 5,
        seed: int | None = None,
    ) -> None:
        """Load one vendor profile and initialize authentication, state and telemetry."""
        if profile not in PROFILES:
            raise ValueError(f"Unknown profile: {profile}")
        if not username or not password or session_timeout <= 0:
            raise ValueError("Credentials must be non-empty and session timeout must be positive.")
        if not 1 <= metric_interval <= 60:
            raise ValueError("Metric interval must be between 1 and 60 seconds.")
        self.profile = profile
        self.username = username
        self.password = password
        self.require_auth = require_auth
        self.play_sequences = play_sequences
        self.session_timeout = session_timeout
        self.raw_fixtures = raw_fixtures
        self.lock = threading.RLock()
        fixtures = load_fixtures(mockups_dir / profile)
        self.original = fixtures.resources
        self.steps = fixtures.steps
        self.monitoring_enabled = False
        if not raw_fixtures:
            self.monitoring_enabled = apply_corrections(profile, self.original)
            for uri, snapshots in self.steps.items():
                for snapshot in snapshots:
                    normalize_resource(uri, snapshot)
        self.simulation = (
            TelemetrySimulation(profile, interval=metric_interval, seed=seed)
            if self.monitoring_enabled and dynamic_metrics
            else None
        )
        self.reset()

    def reset(self) -> None:
        """Restore baseline resources, revoke sessions and restart the baseline scenario."""
        with self.lock:
            self.resources = copy.deepcopy(self.original)
            self.positions: dict[str, int] = {}
            self.sessions: dict[str, tuple[str, float]] = {}
            self.scenario = "baseline"
            self.monitoring_on: dict[str, Json] | None = None
            if self.simulation:
                self.simulation.reset(self.resources, self.scenario)

    def error_payload(self, error: RedfishError) -> Json:
        """Encode an error with the active vendor response convention."""
        if self.profile == "hpe":
            payload = hpe_message(error.code.rsplit(".", 1)[-1])
            payload["error"]["@Message.ExtendedInfo"][0]["Message"] = str(error)
            return payload
        payload = error.payload()
        payload["error"]["@Message.ExtendedInfo"] = [
            {"MessageId": error.code, "Message": str(error)}
        ]
        return payload

    def handle(
        self, method: str, raw_path: str, headers: dict[str, str], body: Json | None = None
    ) -> Result:
        """Authenticate and dispatch a request while holding the profile state lock."""
        path, query = normalize(raw_path)
        headers = {k.lower(): v for k, v in headers.items()}
        with self.lock:
            expire_sessions(self)
            public_get = method in ("GET", "HEAD") and path in ("/", "/healthz", "/redfish", ROOT)
            login = method == "POST" and path == SESSIONS
            if not public_get and not login:
                authenticate(self, headers)
            if self.simulation:
                self.simulation.advance(self.resources)
            if method in ("GET", "HEAD"):
                return get(self, path, query, advance=method == "GET")
            if query:
                raise RedfishError(400, "Query options are only supported for GET and HEAD.")
            if login:
                return create_session(self, body or {})
            if method == "POST" and path == "/_mock/reset":
                self.reset()
                return Result(body={"status": "reset", "profile": self.profile})
            if method == "POST" and path == "/_mock/scenario":
                return select_scenario(self, body)
            if method == "POST" and path == "/_mock/overrides":
                return override_resource(self, body)
            if method == "DELETE" and path.startswith(SESSIONS + "/"):
                return delete_session(self, path)
            if method == "PATCH":
                return patch(self, path, body or {}, headers)
            if method == "POST" and "/Actions/" in path:
                return execute(self, path, body or {})
            if path in self.resources:
                raise RedfishError(405, "Method not supported on this resource.")
            raise RedfishError(404, "Resource not found.", "ResourceMissingAtURI")
