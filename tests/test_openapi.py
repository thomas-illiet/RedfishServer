"""Validate profile-specific OpenAPI contracts against the running simulator."""

from __future__ import annotations

import copy
import json
import shutil

import pytest
from openapi_spec_validator import validate

from redfish_mock.constants import FIXTURES, PROFILES, ROOT, SESSIONS
from redfish_mock.engine import MockBMC
from redfish_mock.openapi import build_specification


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw", [False, True])
def test_profile_specification_is_valid_and_covers_resources(serve, profile, raw):
    """Validate all profiles, unique operation identities and documented readable resources."""
    client = serve(profile, raw_fixtures=raw)
    status, spec, _ = client.request("GET", "/openapi.json", auth=False)
    assert status == 200
    validate(spec)
    assert spec["openapi"] == "3.1.0"
    assert spec["servers"] == [{"url": "/", "description": "This mock instance"}]
    assert spec["x-mock-fidelity"]["mode"] == ("raw-fixtures" if raw else "documented-subset")
    paths = spec["paths"]
    for path in client.server.bmc.original:
        assert {"get", "head"} <= paths[path].keys()
        assert "content" not in paths[path]["head"]["responses"]["200"]
    ids = [
        item["operationId"]
        for path in paths.values()
        for method, item in path.items()
        if method != "parameters"
    ]
    assert len(ids) == len(set(ids))
    assert all("#" not in path for path in paths)
    assert "patch" not in paths[ROOT + "/AccountService"]
    assert paths[ROOT]["get"]["security"] == []
    assert paths[SESSIONS]["post"]["security"] == []
    assert spec["security"] == [{"basicAuth": []}, {"sessionToken": []}]
    assert spec["components"]["securitySchemes"]["sessionToken"]["name"] == "X-Auth-Token"
    scenario = paths["/_mock/scenario"]["post"]
    assert ("200" in scenario["responses"]) == client.server.bmc.monitoring_enabled


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw", [False, True])
def test_documented_action_examples_match_http_results(serve, profile, raw):
    """Execute every documented action example and check supported versus 501-only behavior."""
    client = serve(profile, raw_fixtures=raw)
    spec = client.request("GET", "/openapi.json", auth=False)[1]
    for path, methods in spec["paths"].items():
        if "/Actions/" not in path:
            continue
        action = methods["post"]
        body = action["requestBody"]["content"]["application/json"].get("example", {})
        status, _, _ = client.request("POST", path, body)
        assert str(status) in action["responses"], (path, status)
        if action["x-mock-support"] == "not-implemented":
            assert status == 501
            assert not any(code.startswith("2") for code in action["responses"])
        else:
            assert status in (200, 204), (path, status)


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_patch_authentication_and_session_contracts(serve, profile):
    """Verify documented writes, vendor envelopes, ETags and complete session lifecycle."""
    client = serve(profile)
    spec = client.request("GET", "/openapi.json", auth=False)[1]
    system = ROOT + "/Systems/" + ("System.Embedded.1" if profile == "dell" else "1")
    operation = spec["paths"][system]["patch"]
    properties = operation["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert set(properties) == ({"Boot", "HostName"} if profile == "hpe" else {"Boot"})
    assert "PowerState" not in properties
    assert "BootOrder" not in properties["Boot"]["properties"]
    assert (
        client.request(
            "PATCH", system, {"Boot": {"BootSourceOverrideEnabled": "Once"}}, auth=False
        )[0]
        == 401
    )
    status, body, _ = client.request(
        "PATCH", system, {"Boot": {"BootSourceOverrideEnabled": "Once"}}
    )
    assert status == 200
    if profile == "hpe":
        assert body["error"]["@Message.ExtendedInfo"][0]["MessageId"].endswith("Success")
    assert client.request("PATCH", system, {}, headers={"If-Match": 'W/"stale"'})[0] == 412
    status, session, headers = client.request(
        "POST", SESSIONS, {"UserName": "admin", "Password": "admin"}, auth=False
    )
    assert status == 201
    uri = headers["Location"]
    token = {"X-Auth-Token": headers["X-Auth-Token"]}
    assert client.request("GET", uri, auth=False, headers=token)[1] == session
    assert client.request("HEAD", uri, auth=False, headers=token)[1] is None
    status = client.request("DELETE", uri, auth=False, headers=token)[0]
    assert str(status) in spec["paths"][SESSIONS + "/{sessionId}"]["delete"]["responses"]
    assert client.request("GET", system, auth=False, headers=token)[0] == 401


def test_custom_resources_are_discovered_without_exporting_state_or_credentials(tmp_path):
    """Build from custom baselines while keeping secrets and mutable session resources out."""
    custom = tmp_path / "fixtures"
    shutil.copytree(FIXTURES / "dell", custom / "dell")
    resource = custom / "dell" / "Systems" / "Custom" / "index.json"
    resource.parent.mkdir()
    resource.write_text(
        json.dumps(
            {
                "@odata.id": ROOT + "/Systems/Custom",
                "@odata.type": "#ComputerSystem.v1_20_0.ComputerSystem",
                "Name": "private fixture label",
                "Boot": {"BootSourceOverrideEnabled": "Disabled"},
            }
        )
    )
    bmc = MockBMC(
        "dell", mockups_dir=custom, username="secret-user-marker", password="secret-password-marker"
    )
    created = bmc.handle("POST", SESSIONS, {}, {"UserName": bmc.username, "Password": bmc.password})
    spec = build_specification(bmc)
    validate(spec)
    assert ROOT + "/Systems/Custom" in spec["paths"]
    patch = spec["paths"][ROOT + "/Systems/Custom"]["patch"]
    boot = patch["requestBody"]["content"]["application/json"]["schema"]["properties"]["Boot"]
    assert set(boot["properties"]) == {"BootSourceOverrideEnabled"}
    encoded = json.dumps(spec)
    for secret in (
        bmc.username,
        bmc.password,
        created.headers["X-Auth-Token"],
        created.headers["Location"],
        "private fixture label",
    ):
        assert secret not in encoded
    snapshot = copy.deepcopy(spec)
    bmc.reset()
    assert build_specification(bmc) == snapshot


def test_no_auth_is_reflected_but_session_login_still_checks_credentials(serve):
    """Remove API security requirements in no-auth mode without bypassing session login."""
    client = serve(require_auth=False)
    spec = client.request("GET", "/openapi.json", auth=False)[1]
    assert spec["security"] == []
    assert client.request("GET", ROOT + "/Systems", auth=False)[0] == 200
    assert (
        client.request("POST", SESSIONS, {"UserName": "bad", "Password": "bad"}, auth=False)[0]
        == 401
    )
