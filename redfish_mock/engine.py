"""In-memory resources and the deliberately small set of supported BMC actions."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .contracts import (
    READ_ONLY,
    apply_corrections,
    contract_summary,
    hpe_message,
    normalize_resource,
    patchable_fields,
    supermicro_success,
)
from .monitoring import (
    SCENARIOS,
    apply_scenario,
    monitoring_resources,
    paths,
    power_off,
    update_health,
)
from .simulation import TelemetrySimulation

ROOT = "/redfish/v1"
SESSIONS = ROOT + "/SessionService/Sessions"
PROFILES = ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4")
FIXTURES = Path(__file__).resolve().parent.parent / "mockups"
Json = dict[str, Any]


class RedfishError(Exception):
    def __init__(self, status: int, message: str, code: str = "GeneralError") -> None:
        super().__init__(message)
        self.status = status
        self.code = "Base.1.18." + code

    def payload(self) -> Json:
        return {"error": {"code": self.code, "message": str(self)}}


@dataclass
class Result:
    status: int = 200
    body: Json | None = None
    headers: dict[str, str] = field(default_factory=dict)


def etag(body: Json) -> str:
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    return f'W/"{digest}"'


def normalize(raw: str) -> tuple[str, dict[str, list[str]]]:
    parsed = urlsplit(raw)
    path = unquote(parsed.path).rstrip("/") or "/"
    if parsed.scheme or parsed.netloc or any(p in (".", "..") for p in path.split("/")):
        raise RedfishError(400, "Invalid resource path.")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def merge_existing(current: Json, patch: Json) -> Json:
    """Apply an atomic mock PATCH; identity, collections and links remain immutable."""
    merged = copy.deepcopy(current)
    immutable = {"Id", "Links", "Actions", "Members", "Members@odata.count", "Password"}
    for key, value in patch.items():
        if key.startswith("@") or key in immutable or "@" in key:
            raise RedfishError(400, f"Property {key} is read-only.", "PropertyNotWritable")
        if key not in current:
            raise RedfishError(400, f"Unknown property: {key}.", "PropertyUnknown")
        old = current[key]
        if isinstance(old, dict) and isinstance(value, dict):
            if "@odata.id" in old:
                raise RedfishError(400, f"Link {key} is read-only.", "PropertyNotWritable")
            merged[key] = merge_existing(old, value)
        else:
            nullable_image = key == "Image" and value is None
            if old is not None and type(value) is not type(old) and not nullable_image:
                raise RedfishError(400, f"Wrong type for {key}.", "PropertyValueTypeError")
            allowed = current.get(key + "@Redfish.AllowableValues")
            if allowed is not None and value not in allowed:
                raise RedfishError(400, f"Unsupported value for {key}.", "PropertyValueNotInList")
            merged[key] = copy.deepcopy(value)
    return merged


class MockBMC:
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
        self.original: dict[str, Json] = {}
        self.steps: dict[str, list[Json]] = {}
        directory = mockups_dir / profile
        for filename in sorted(directory.rglob("index.json")):
            suffix = filename.parent.relative_to(directory).as_posix()
            uri = ROOT if suffix == "." else ROOT + "/" + suffix
            self.original[uri] = json.loads(filename.read_text())
            steps = sorted((filename.parent / "_steps").glob("*.json"))
            if steps:
                self.steps[uri] = [json.loads(step.read_text()) for step in steps]
        if ROOT not in self.original:
            raise ValueError(f"No service root found in {directory}")
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
        with self.lock:
            self.resources = copy.deepcopy(self.original)
            self.positions: dict[str, int] = {}
            self.sessions: dict[str, tuple[str, float]] = {}
            self.scenario = "baseline"
            self.monitoring_on: dict[str, Json] | None = None
            if self.simulation:
                self.simulation.reset(self.resources, self.scenario)

    def _credentials_valid(self, username: Any, password: Any) -> bool:
        return (
            isinstance(username, str)
            and isinstance(password, str)
            and hmac.compare_digest(username.encode(), self.username.encode())
            and hmac.compare_digest(password.encode(), self.password.encode())
        )

    def _expire_sessions(self) -> None:
        for token, (uri, expiry) in list(self.sessions.items()):
            if expiry <= time.monotonic():
                del self.sessions[token]
                self.resources.pop(uri, None)
        self.resources[SESSIONS]["Members"] = [
            {"@odata.id": uri} for uri, _ in self.sessions.values()
        ]
        self.resources[SESSIONS]["Members@odata.count"] = len(self.sessions)

    def _authenticate(self, headers: dict[str, str]) -> None:
        if not self.require_auth:
            return
        token = headers.get("x-auth-token", "")
        if token:
            if token in self.sessions:
                uri, _ = self.sessions[token]
                self.sessions[token] = (uri, time.monotonic() + self.session_timeout)
                return
        else:
            auth = headers.get("authorization", "")
            scheme, _, encoded = auth.partition(" ")
            if scheme.lower() == "basic":
                try:
                    username, _, password = (
                        base64.b64decode(encoded, validate=True).decode().partition(":")
                    )
                    if self._credentials_valid(username, password):
                        return
                except ValueError, UnicodeDecodeError, binascii.Error:
                    pass
        raise RedfishError(
            401, "Valid Basic credentials or X-Auth-Token required.", "NoValidSession"
        )

    def _read(self, path: str, *, advance: bool = False) -> Json:
        if path not in self.resources:
            raise RedfishError(404, f"Resource not found: {path}.", "ResourceMissingAtURI")
        if self.play_sequences and path in self.steps:
            position = self.positions.get(path, 0)
            if position < len(self.steps[path]):
                body = copy.deepcopy(self.steps[path][position])
                if advance:
                    self.positions[path] = position + 1
                return body
        return copy.deepcopy(self.resources[path])

    def _get(self, path: str, query: dict[str, list[str]], *, advance: bool) -> Result:
        if path in ("/", "/healthz"):
            return Result(body={"status": "ok", "profile": self.profile, "redfish": ROOT + "/"})
        if path == "/redfish":
            return Result(body={"v1": ROOT + "/"})
        if path == "/_mock":
            return Result(
                body={
                    "profile": self.profile,
                    "resources": len(self.resources),
                    "play_sequences": self.play_sequences,
                    "sessions": len(self.sessions),
                    "reset": "/_mock/reset",
                    "storage": "memory",
                    "fidelity": contract_summary(self.profile, raw=self.raw_fixtures),
                    "overrides": "/_mock/overrides",
                    "monitoring": {
                        "enabled": self.monitoring_enabled,
                        "scenario": self.scenario if self.monitoring_enabled else None,
                        "scenarios": list(SCENARIOS) if self.monitoring_enabled else [],
                        "control": "/_mock/scenario" if self.monitoring_enabled else None,
                        "report": "docs/MONITORING.md",
                        "dynamic": self.simulation is not None,
                        "interval_seconds": self.simulation.interval if self.simulation else None,
                        "seed": self.simulation.seed if self.simulation else None,
                    },
                }
            )
        body = self._read(path, advance=advance)
        if query:
            supported = {"$top", "$skip", "$expand"}
            if set(query) - supported or any(len(v) != 1 for v in query.values()):
                raise RedfishError(400, "Supported query options: $top, $skip, $expand=.")
            if "Members" not in body:
                raise RedfishError(400, "Query options are supported on collections only.")
            if query.get("$expand", ["."])[0] not in (".", "*"):
                raise RedfishError(400, "Only $expand=. and $expand=* are supported (one level).")
            try:
                skip = int(query.get("$skip", ["0"])[0])
                top = int(query.get("$top", [str(len(body["Members"]))])[0])
                if skip < 0 or top < 0:
                    raise ValueError
            except ValueError as exc:
                raise RedfishError(400, "$top and $skip must be non-negative integers.") from exc
            members = body["Members"]
            body["Members"] = members[skip : skip + top]
            if "$expand" in query:
                body["Members"] = [
                    self._read(m["@odata.id"].rstrip("/"), advance=advance) for m in body["Members"]
                ]
            if top and skip + top < len(members):
                next_query = {k: v[0] for k, v in query.items()}
                next_query["$skip"] = str(skip + top)
                body["Members@odata.nextLink"] = path + "?" + urlencode(next_query)
        return Result(body=body, headers={"ETag": etag(body)})

    def _create_session(self, body: Json) -> Result:
        if not self._credentials_valid(body.get("UserName"), body.get("Password")):
            raise RedfishError(401, "Invalid credentials.", "NoValidSession")
        if len(self.sessions) >= 1024:
            raise RedfishError(503, "Session limit reached.")
        token = secrets.token_urlsafe(32)
        sid = secrets.token_hex(12)
        uri = SESSIONS + "/" + sid
        session = {
            "@odata.id": uri,
            "@odata.type": "#Session.v1_6_0.Session",
            "Id": sid,
            "Name": "Mock session",
            "UserName": self.username,
        }
        self.resources[uri] = session
        self.sessions[token] = (uri, time.monotonic() + self.session_timeout)
        self._expire_sessions()
        return Result(201, copy.deepcopy(session), {"X-Auth-Token": token, "Location": uri})

    def _patch(self, path: str, body: Json, headers: dict[str, str]) -> Result:
        current = self._read(path)
        if "Members" in current or path.startswith(
            (ROOT + "/AccountService", ROOT + "/SessionService")
        ):
            raise RedfishError(405, "This resource cannot be patched.")
        expected = headers.get("if-match")
        if expected is not None and expected not in ("*", etag(current)):
            raise RedfishError(
                412, "The resource changed; read it again before patching.", "PreconditionFailed"
            )
        updated = merge_existing(current, body)
        allowed = patchable_fields(current, self.profile)
        for name, value in body.items():
            if name in READ_ONLY:
                raise RedfishError(400, f"{name} is read-only.", "PropertyNotWritable")
            if name not in allowed:
                raise RedfishError(501, f"PATCH of {name} is not implemented by this mock.")
            if isinstance(allowed[name], set):
                for child in value:
                    if child not in allowed[name]:
                        code = "PropertyNotWritable" if child in READ_ONLY else "GeneralError"
                        status = 400 if child in READ_ONLY else 501
                        raise RedfishError(
                            status, f"PATCH of {name}.{child} is not supported.", code
                        )
        boot = updated.get("Boot", {})
        if boot.get("BootSourceOverrideEnabled", "Disabled") not in (
            "Disabled",
            "Once",
            "Continuous",
        ):
            raise RedfishError(400, "Invalid BootSourceOverrideEnabled.", "PropertyValueNotInList")
        if boot.get("BootSourceOverrideMode", "UEFI") not in ("Legacy", "UEFI"):
            raise RedfishError(400, "Invalid BootSourceOverrideMode.", "PropertyValueNotInList")
        if current.get("@odata.type", "").startswith("#VirtualMedia."):
            image = updated.get("Image")
            if image is not None and (not isinstance(image, str) or not urlsplit(image).hostname):
                raise RedfishError(
                    400, "Image must be an absolute media URL.", "PropertyValueFormatError"
                )
            if "Image" in body and "Inserted" not in body:
                updated["Inserted"] = image is not None
            if updated.get("Inserted") and image is None:
                raise RedfishError(
                    400, "Cannot insert media without an image.", "PropertyValueConflict"
                )
            updated["ConnectedVia"] = "URI" if updated.get("Inserted") else "NotConnected"
        self.resources[path] = updated
        self.positions[path] = len(self.steps.get(path, []))
        payload = hpe_message() if self.profile == "hpe" else copy.deepcopy(updated)
        return Result(body=payload, headers={"ETag": etag(updated)})

    def _action(self, path: str, body: Json) -> Result:
        owner = path.split("/Actions/", 1)[0]
        resource = self._read(owner)

        def find_target(node: Any) -> Json | None:
            if isinstance(node, dict):
                if node.get("target", "").rstrip("/") == path:
                    return node
                for value in node.values():
                    found = find_target(value)
                    if found is not None:
                        return found
            return None

        definition = find_target(resource)
        if definition is None:
            raise RedfishError(
                404, "Action is not advertised by this profile.", "ActionNotSupported"
            )
        action = path.rsplit("/", 1)[-1]
        if self.profile == "supermicro" and action.startswith("VirtualMedia."):
            raise RedfishError(
                501,
                "Supermicro virtual media requires an async task workflow not simulated here.",
                "ActionNotSupported",
            )
        if action in ("ComputerSystem.Reset", "Manager.Reset"):
            reset_type = body.get("ResetType")
            allowed = definition.get(
                "ResetType@Redfish.AllowableValues", ["GracefulRestart", "ForceRestart"]
            )
            if reset_type not in allowed:
                raise RedfishError(
                    400, "Missing or unsupported ResetType.", "ActionParameterValueNotInList"
                )
            if action == "ComputerSystem.Reset":
                old_power = resource.get("PowerState", "On")
                if reset_type in ("ForceOff", "GracefulShutdown"):
                    resource["PowerState"] = "Off"
                elif reset_type == "PushPowerButton":
                    resource["PowerState"] = "Off" if old_power == "On" else "On"
                elif reset_type != "Nmi":
                    resource["PowerState"] = "On"
                boot = resource.get("Boot", {})
                booted = (
                    old_power != "On" and resource.get("PowerState") == "On"
                ) or reset_type in (
                    "ForceRestart",
                    "GracefulRestart",
                    "PowerCycle",
                )
                if (
                    resource.get("PowerState") == "On"
                    and booted
                    and boot.get("BootSourceOverrideEnabled") == "Once"
                ):
                    boot["BootSourceOverrideEnabled"] = "Disabled"
        elif action in ("VirtualMedia.InsertMedia", "HpeiLOVirtualMedia.InsertVirtualMedia"):
            image = body.get("Image")
            if not isinstance(image, str) or urlsplit(image).scheme not in (
                "http",
                "https",
                "nfs",
                "cifs",
            ):
                raise RedfishError(400, "Image must be an HTTP, HTTPS, NFS or CIFS URL.")
            inserted = body.get("Inserted", True)
            protected = body.get("WriteProtected", True)
            if not isinstance(inserted, bool) or not isinstance(protected, bool):
                raise RedfishError(
                    400,
                    "Inserted and WriteProtected must be booleans.",
                    "ActionParameterValueTypeError",
                )
            if not urlsplit(image).hostname:
                raise RedfishError(
                    400, "Image must include a host.", "ActionParameterValueFormatError"
                )
            resource.update(
                Image=image,
                Inserted=inserted,
                WriteProtected=protected,
                ConnectedVia="URI" if inserted else "NotConnected",
            )
        elif action in ("VirtualMedia.EjectMedia", "HpeiLOVirtualMedia.EjectVirtualMedia"):
            resource.update(Image=None, Inserted=False, ConnectedVia="NotConnected")
        else:
            raise RedfishError(
                501,
                f"{action} is present in the fixture but is not simulated.",
                "ActionNotSupported",
            )
        self.resources[owner] = resource
        if self.monitoring_enabled and action == "ComputerSystem.Reset":
            if old_power != "Off" and resource["PowerState"] == "Off":
                self.monitoring_on = monitoring_resources(self.profile, self.resources)
                power_off(self.profile, self.resources)
            elif old_power == "Off" and resource["PowerState"] == "On" and self.monitoring_on:
                self.resources.update(self.monitoring_on)
                self.monitoring_on = None
                update_health(self.profile, self.resources)
            if self.simulation and old_power != resource["PowerState"]:
                self.simulation.reset(self.resources, self.scenario, power_transition=True)
        self.positions[owner] = len(self.steps.get(owner, []))
        if self.profile == "hpe":
            return Result(body=hpe_message())
        if self.profile == "supermicro" and action == "ComputerSystem.Reset":
            return Result(body=supermicro_success())
        return Result(204)

    def error_payload(self, error: RedfishError) -> Json:
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
        path, query = normalize(raw_path)
        headers = {k.lower(): v for k, v in headers.items()}
        with self.lock:
            self._expire_sessions()
            public_get = method in ("GET", "HEAD") and path in ("/", "/healthz", "/redfish", ROOT)
            login = method == "POST" and path == SESSIONS
            if not public_get and not login:
                self._authenticate(headers)
            if self.simulation:
                self.simulation.advance(self.resources)
            if method in ("GET", "HEAD"):
                return self._get(path, query, advance=method == "GET")
            if query:
                raise RedfishError(400, "Query options are only supported for GET and HEAD.")
            if login:
                return self._create_session(body or {})
            if method == "POST" and path == "/_mock/reset":
                self.reset()
                return Result(body={"status": "reset", "profile": self.profile})
            if method == "POST" and path == "/_mock/scenario":
                payload = body or {}
                scenario = payload.get("Scenario")
                if not self.monitoring_enabled:
                    raise RedfishError(
                        501, "Monitoring scenarios require an enriched vendor profile."
                    )
                if (
                    set(payload) != {"Scenario"}
                    or not isinstance(scenario, str)
                    or scenario not in SCENARIOS
                ):
                    raise RedfishError(400, "Expected Scenario: " + ", ".join(SCENARIOS))
                system_uri, _, _, _ = paths(self.profile)
                if self.resources[system_uri]["PowerState"] != "On":
                    raise RedfishError(
                        409, "Power on the system before selecting a monitoring scenario."
                    )
                apply_scenario(self.profile, self.resources, self.original, scenario)
                self.scenario = scenario
                if self.simulation:
                    self.simulation.reset(self.resources, self.scenario)
                return Result(body={"scenario": scenario, "profile": self.profile})
            if method == "POST" and path == "/_mock/overrides":
                payload = body or {}
                target, patch = payload.get("Path"), payload.get("Patch")
                if not isinstance(target, str) or not isinstance(patch, dict):
                    raise RedfishError(400, "Expected Path (URI) and Patch (object).")
                target, params = normalize(target)
                if (
                    params
                    or target.startswith(SESSIONS)
                    or target in (ROOT, ROOT + "/SessionService")
                ):
                    raise RedfishError(400, "This resource cannot be overridden.")
                updated = merge_existing(self._read(target), patch)
                self.resources[target] = updated
                if self.simulation:
                    self.simulation.reset(self.resources, self.scenario)
                self.positions[target] = len(self.steps.get(target, []))
                return Result(body={"status": "overridden", "resource": target})
            if method == "DELETE" and path.startswith(SESSIONS + "/"):
                for token, (uri, _) in list(self.sessions.items()):
                    if uri == path:
                        del self.sessions[token]
                        self.resources.pop(uri, None)
                        self._expire_sessions()
                        if self.profile == "supermicro":
                            return Result(body=supermicro_success())
                        return Result(204)
                raise RedfishError(404, "Session not found.", "ResourceMissingAtURI")
            if method == "PATCH":
                return self._patch(path, body or {}, headers)
            if method == "POST" and "/Actions/" in path:
                return self._action(path, body or {})
            if path in self.resources:
                raise RedfishError(405, "Method not supported on this resource.")
            raise RedfishError(404, "Resource not found.", "ResourceMissingAtURI")
