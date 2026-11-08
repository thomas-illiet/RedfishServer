"""Real HTTP integration coverage for all profiles sharing one listener."""

from __future__ import annotations

import copy
import json

import pytest
from openapi_spec_validator import validate

from redfish_mock.collector import RedfishClient, snapshot
from redfish_mock.constants import PROFILES, ROOT, SESSIONS
from scripts.smoke import Client as SmokeClient
from scripts.smoke import check_profile


def test_catalog_probes_and_unknown_routes(serve):
    """Serve a public catalogue and aggregate probes without a default BMC."""
    client = serve(multi=True)
    assert client.server.config.limit_concurrency == 320
    status, page, headers = client.request("GET", "/", auth=False)
    assert status == 200
    assert 'lang="en"' in page
    assert "content-security-policy" in headers
    assert client.request("HEAD", "/", auth=False)[2]["Content-Length"] == headers["Content-Length"]
    for profile in PROFILES:
        assert f'href="/{profile}/"' in page
        assert f'href="/{profile}/redfish/v1/"' in page
        status, _, redirect = client.request("GET", f"/{profile}?a=b", auth=False)
        assert status == 308
        assert redirect["Location"] == f"/{profile}/?a=b"
    assert client.request("GET", "/healthz", auth=False)[1]["profiles"] == list(PROFILES)
    assert client.request("GET", "/readyz", auth=False)[1]["ready"] is True
    client.server.app.apps["lenovo"].ready = False
    assert client.request("GET", "/readyz", auth=False)[0] == 503
    assert client.request("GET", "/lenovo/readyz", auth=False)[0] == 503
    assert client.request("GET", "/dell/readyz", auth=False)[0] == 200
    for path in ("/", "/healthz", "/readyz"):
        assert client.request("POST", path, {}, auth=False)[0] == 405
    for path in ("/redfish/v1/", "/openapi.json", "/unknown/", "/dellx/", "/dell/dell/redfish/v1"):
        assert client.request("GET", path)[0] == 404


@pytest.mark.parametrize("raw", [False, True])
def test_all_profile_specs_assets_links_and_actions(serve, raw):
    """Exercise generated contracts and every advertised action through its profile prefix."""
    client = serve(multi=True, raw_fixtures=raw)
    for profile in PROFILES:
        prefix = "/" + profile
        bmc = client.server.profiles[profile]
        before = copy.deepcopy(bmc.original)
        status, spec, _ = client.request("GET", prefix + "/openapi.json", auth=False)
        assert status == 200
        validate(spec)
        assert spec["servers"][0]["url"] == prefix
        assert spec["x-mock-fidelity"]["profile"] == profile
        assert f"({prefix}/assets/" in spec["info"]["description"]
        html = client.request("GET", prefix + "/", auth=False)[1]
        assert f'content="{prefix}"' in html
        assert f'src="{prefix}/assets/swagger-ui/initializer.js"' in html
        script = client.request("GET", prefix + "/assets/swagger-ui/initializer.js", auth=False)[1]
        assert 'target.pathname.startsWith(profilePrefix + "/")' in script
        assert "persistAuthorization: false" in script
        for path in bmc.original:
            status, body, headers = client.request("GET", prefix + path)
            assert status == 200, path
            assert body["@odata.id"].startswith(prefix + "/redfish/")
            assert int(headers["Content-Length"]) == len(
                json.dumps(body, ensure_ascii=False).encode()
            )
        assert bmc.original == before
        for path, methods in spec["paths"].items():
            assert not path.startswith(prefix + "/")
            if "/Actions/" in path:
                action = methods["post"]
                body = action["requestBody"]["content"]["application/json"].get("example", {})
                result = client.request("POST", prefix + path, body)[0]
                assert str(result) in action["responses"], (profile, path, result)


def test_sessions_reset_and_overrides_are_profile_scoped(serve):
    """Reject cross-profile tokens and overrides while keeping other profiles unchanged."""
    client = serve(multi=True)
    tokens = {}
    locations = {}
    for profile in PROFILES:
        status, body, headers = client.request(
            "POST", f"/{profile}{SESSIONS}", {"UserName": "admin", "Password": "admin"}, auth=False
        )
        assert status == 201
        locations[profile] = headers["Location"]
        tokens[profile] = {"X-Auth-Token": headers["X-Auth-Token"]}
        assert headers["Location"] == body["@odata.id"]
        assert headers["Location"].startswith(f"/{profile}{SESSIONS}/")
        assert (
            client.request("GET", headers["Location"], auth=False, headers=tokens[profile])[0]
            == 200
        )
    for profile in PROFILES[1:]:
        assert (
            client.request("GET", f"/{profile}{ROOT}/Systems", auth=False, headers=tokens["dell"])[
                0
            ]
            == 401
        )
    system = ROOT + "/Systems/System.Embedded.1"
    for target in (system, "/dell" + system):
        status, body, _ = client.request(
            "POST", "/dell/_mock/overrides", {"Path": target, "Patch": {"PowerState": "Off"}}
        )
        assert status == 200
        assert body["resource"] == "/dell" + system
    assert (
        client.request(
            "POST", "/dell/_mock/overrides", {"Path": "/hpe" + ROOT + "/Systems/1", "Patch": {}}
        )[0]
        == 400
    )
    assert (
        client.request("POST", "/dell/_mock/reset", {}, auth=False, headers=tokens["dell"])[0]
        == 200
    )
    assert client.request("GET", locations["dell"], auth=False, headers=tokens["dell"])[0] == 401
    for profile in PROFILES[1:]:
        assert (
            client.request("GET", locations[profile], auth=False, headers=tokens[profile])[0] == 200
        )
        assert client.request("DELETE", locations[profile], auth=False, headers=tokens[profile])[
            0
        ] in (200, 204)
        assert (
            client.request("GET", locations[profile], auth=False, headers=tokens[profile])[0] == 401
        )


def test_pagination_etags_and_scenarios(serve):
    """Follow public links and preserve conditional updates and independent scenarios."""
    client = serve(multi=True)
    system = "/dell" + ROOT + "/Systems/System.Embedded.1"
    _, body, headers = client.request("GET", system)
    assert body["Actions"]["#ComputerSystem.Reset"]["target"].startswith("/dell/")
    assert (
        client.request(
            "PATCH",
            system,
            {"Boot": {"BootSourceOverrideTarget": "Pxe"}},
            headers={"If-Match": headers["ETag"]},
        )[0]
        == 200
    )
    assert client.request("PATCH", system, {}, headers={"If-Match": headers["ETag"]})[0] == 412
    collection = client.request("GET", system + "/Memory?$top=2&$expand=.")[1]
    next_link = collection["Members@odata.nextLink"]
    assert next_link.startswith("/dell/")
    assert client.request("GET", next_link)[0] == 200
    status = client.request("GET", "/dell/_mock")[1]
    assert status["reset"] == "/dell/_mock/reset"
    assert (
        client.request("POST", status["monitoring"]["control"], {"Scenario": "overheat"})[0] == 200
    )
    assert client.request("GET", "/hpe/_mock")[1]["monitoring"]["scenario"] == "baseline"


def test_scoped_collectors_and_smoke_clients(serve):
    """Follow prefixed links once and run delivery checks against all five profiles."""
    client = serve(multi=True)
    base = f"http://127.0.0.1:{client.server.server_port}"
    for profile in PROFILES:
        endpoint = base + "/" + profile
        assert check_profile(profile, SmokeClient(endpoint, "admin", "admin"))["profile"] == profile
        if profile in PROFILES[:3]:
            remote = RedfishClient(endpoint, "admin", "admin")
            assert snapshot(remote.get)[0]["system_uri"].startswith(f"/{profile}/redfish/")


@pytest.mark.parametrize(
    "path",
    [
        "/dell/../hpe/redfish/v1",
        "/dell/%2e%2e/hpe/redfish/v1",
        "/dell/%252e%252e/hpe",
        "/dell%2fhpe/redfish",
        "/dell/\\hpe",
        "/dell//redfish/v1",
        "/dell/%00",
        "/dell/%zz",
        "/dell/%ff",
        "//dell/redfish/v1",
    ],
)
def test_ambiguous_paths_are_rejected(serve, path):
    """Reject traversal and ambiguous encoding before selecting a BMC."""
    client = serve(multi=True)
    assert client.request("GET", path)[0] == 400


def test_safe_encoding_and_literal_profile_selectors(serve):
    """Decode resource paths once while requiring literal profile identifiers."""
    client = serve(multi=True)
    assert client.request("GET", "/dell/redfish/v1/%53ystems")[0] == 200
    assert client.request("GET", "/%64ell/redfish/v1/")[0] == 404
