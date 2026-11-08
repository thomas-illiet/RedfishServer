"""HTTP, state isolation and distribution checks for the bundled Swagger UI."""

from __future__ import annotations

import copy
import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from redfish_mock.constants import ROOT, SESSIONS
from redfish_mock.server.documentation import ASSET_PREFIX, ASSETS, STATIC
from scripts.documentation_check import check_documentation
from scripts.smoke import Client as SmokeClient


def test_delivery_documentation_check(serve):
    """Exercise the container documentation gate against an ephemeral HTTP instance."""
    client = serve()
    check_documentation(
        "dell", SmokeClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    )


@pytest.mark.parametrize(
    ("path", "content_type"), [("/", "text/html"), ("/openapi.json", "application/json")]
)
def test_public_documentation_and_head(serve, path, content_type):
    """Serve the root page and contract publicly with correct lengths and HEAD semantics."""
    client = serve()
    status, body, headers = client.request("GET", path, auth=False)
    assert status == 200
    assert headers.get_content_type() == content_type
    assert "OData-Version" not in headers
    if path == "/":
        assert 'lang="en"' in body
        assert '<main id="swagger-ui"' in body
        assert "<header" not in body
        assert "<aside" not in body
        assert "connect-src 'self'" in headers["Content-Security-Policy"]
    status, body, head_headers = client.request("HEAD", path, auth=False)
    assert status == 200
    assert body is None
    assert head_headers["Content-Length"] == headers["Content-Length"]
    assert client.request("POST", path, {}, auth=False)[0] == 405
    assert client.request("GET", "/healthz", auth=False)[1]["status"] == "ok"
    assert client.request("GET", "/readyz", auth=False)[0] == 200
    assert client.request("GET", ROOT, auth=False)[0] == 200


def test_bundled_assets_are_offline_and_allowlisted(serve):
    """Deliver only registered assets with correct MIME types and immutable upstream hashes."""
    client = serve()
    manifest = json.loads((STATIC / "swagger-ui/manifest.json").read_text())
    assert manifest["version"] == "5.34.1"
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((STATIC / "swagger-ui" / name).read_bytes()).hexdigest() == expected
    for name, (_, content_type) in ASSETS.items():
        path = ASSET_PREFIX + name
        status, body, headers = client.request("GET", path, auth=False)
        assert status == 200
        assert body
        assert headers["Content-Type"] == content_type
        status, body, head_headers = client.request("HEAD", path, auth=False)
        assert status == 200
        assert body is None
        assert head_headers["Content-Length"] == headers["Content-Length"]
        assert client.request("POST", path, {}, auth=False)[0] == 405
    initializer = client.request("GET", ASSET_PREFIX + "initializer.js", auth=False)[1]
    assert "persistAuthorization: false" in initializer
    assert "validatorUrl: null" in initializer
    assert 'url: profilePrefix + "/openapi.json"' in initializer
    assert 'request.credentials = "omit"' in initializer
    banner = client.request("GET", ASSET_PREFIX + "redfish-fake-banner.svg", auth=False)[1]
    assert banner == (STATIC / "redfish-fake-banner.svg").read_bytes()
    vector = ET.fromstring(banner)  # noqa: S314 -- Trusted local project asset.
    assert vector.tag == "{http://www.w3.org/2000/svg}svg"
    assert vector.get("viewBox")
    assert "Fake" in "".join(vector.itertext())
    assert not vector.findall(".//{http://www.w3.org/2000/svg}image")
    assert not vector.findall(".//{http://www.w3.org/2000/svg}script")
    description = client.request("GET", "/openapi.json", auth=False)[1]["info"]["description"]
    assert description == (
        "![RedfishServer Fake — synthetic test environment]"
        "(/assets/swagger-ui/redfish-fake-banner.svg)"
    )
    for path in (
        "manifest.json",
        "../../pyproject.toml",
        "%2e%2e/index.html",
        "swagger-ui-bundle.js.map",
        "unknown.js",
    ):
        assert client.request("GET", ASSET_PREFIX + path, auth=False)[0] == 404


def test_documentation_does_not_advance_or_expose_simulation_state(serve, monkeypatch):
    """Keep sessions, sequence positions and telemetry untouched even when sessions have expired."""
    client = serve(play_sequences=True, dynamic_metrics=True, seed=42)
    bmc = client.server.bmc
    created = bmc.handle("POST", SESSIONS, {}, {"UserName": "admin", "Password": "admin"})
    token = created.headers["X-Auth-Token"]
    uri = created.headers["Location"]
    bmc.sessions[token] = (uri, 0)
    before = copy.deepcopy((bmc.resources, bmc.sessions, bmc.positions, bmc.simulation.__dict__))

    def forbidden(*_args, **_kwargs):
        """Fail any attempt to dispatch a documentation request into the stateful engine."""
        pytest.fail("Documentation must not dispatch to the engine")

    monkeypatch.setattr(bmc, "handle", forbidden)
    for path in ("/", "/openapi.json", ASSET_PREFIX + "swagger-ui.css", ASSET_PREFIX + "unknown"):
        for method in ("GET", "HEAD"):
            assert client.request(method, path, auth=False)[0] in (200, 404)
    spec = client.request("GET", "/openapi.json", auth=False)[1]
    assert token not in json.dumps(spec)
    assert uri not in spec["paths"]
    assert (bmc.resources, bmc.sessions, bmc.positions, bmc.simulation.__dict__) == before


@pytest.mark.parametrize(
    ("username", "password", "require_auth", "enabled"),
    [
        ("admin", "admin", True, True),
        ("admin", "private-password-marker", True, False),
        ("private-username-marker", "admin", True, False),
        ("admin", "admin", False, False),
    ],
)
def test_swagger_preauthorizes_only_valid_public_defaults(
    serve, username, password, require_auth, enabled
):
    """Enable demo authorization only when valid, without publishing custom credentials."""
    client = serve(multi=True, username=username, password=password, require_auth=require_auth)
    for profile in client.server.profiles:
        prefix = "/" + profile
        _, page, _ = client.request("GET", prefix + "/", auth=False)
        flag = "true" if enabled else "false"
        assert f'<meta name="redfish-demo-auth" content="{flag}">' in page
        spec = client.request("GET", prefix + "/openapi.json", auth=False)[1]
        script = client.request("GET", prefix + ASSET_PREFIX + "initializer.js", auth=False)[1]
        public_content = page + json.dumps(spec) + script
        assert "private-password-marker" not in public_content
        assert "private-username-marker" not in public_content
        if enabled:
            # The public pair activated by Swagger must authenticate against this profile.
            assert client.request("GET", prefix + ROOT + "/Systems")[0] == 200
