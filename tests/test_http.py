"""Regression coverage for http behavior."""

from __future__ import annotations

import subprocess

import pytest

from redfish_mock.engine import PROFILES, ROOT


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw", [False, True])
def test_all_imported_resources_over_http(serve, profile, raw):
    """Verify all imported resources over http."""
    client = serve(profile, raw_fixtures=raw)
    for path, expected in client.server.bmc.original.items():
        status, body, headers = client.request("GET", path)
        assert status == 200, path
        assert body == expected, path
        assert headers["OData-Version"] == "4.0"
        assert headers["ETag"].startswith('W/"')
    assert client.request("GET", ROOT + "/", auth=False)[0] == 200


def test_fixture_sequences_and_head(serve):
    """Verify fixture sequences and head."""
    path = ROOT + "/TaskService/Tasks/JID_001"
    client = serve(play_sequences=True)
    snapshots = client.server.bmc.steps[path] + [client.server.bmc.original[path]]
    for expected_snapshot in snapshots:
        assert client.request("HEAD", path)[0] == 200
        assert client.request("GET", path)[1] == expected_snapshot
    assert client.request("GET", path)[1] == snapshots[-1]
    client.request("POST", "/_mock/reset", {})
    assert client.request("GET", path)[1] == snapshots[0]
    assert serve().request("GET", path)[1] == snapshots[-1]


def test_collection_queries(serve):
    """Verify collection queries."""
    client = serve()
    path = ROOT + "/UpdateService/FirmwareInventory"
    status, page, _ = client.request("GET", path + "?$top=1&$expand=.")
    assert status == 200
    assert len(page["Members"]) == 1
    assert "Version" in page["Members"][0]
    next_page = client.request("GET", page["Members@odata.nextLink"])[1]
    assert next_page["Members"][0]["@odata.id"] != page["Members"][0]["@odata.id"]
    assert client.request("GET", path + "?$top=0")[1]["Members"] == []
    for query in ("$top=-1", "$skip=bad", "$filter=x", "$expand=bad", "$top=1&$top=2"):
        assert client.request("GET", path + "?" + query)[0] == 400
    assert client.request("GET", ROOT + "?$top=1")[0] == 400
    assert client.request("PATCH", ROOT + "?$top=1", {})[0] == 400


def test_unsupported_actions_and_methods(serve):
    """Verify unsupported actions and methods."""
    client = serve()
    assert (
        client.request(
            "POST",
            ROOT + "/UpdateService/Actions/UpdateService.SimpleUpdate",
            {"ImageURI": "https://example.test/fw"},
        )[0]
        == 501
    )
    assert client.request("POST", ROOT + "/Systems/System.Embedded.1/Actions/Unknown", {})[0] == 404
    assert client.request("POST", ROOT, {})[0] == 405
    assert client.request("PUT", ROOT, {})[0] == 405
    assert client.request("PATCH", ROOT + "/Systems", {})[0] == 405
    assert client.request("PATCH", ROOT + "/AccountService", {})[0] == 405
    assert client.request("DELETE", ROOT + "/Systems")[0] == 405
    assert client.request("POST", "/unknown", {})[0] == 404
    assert client.request("GET", "/healthz", auth=False)[1]["status"] == "ok"
    assert client.request("GET", "/redfish", auth=False)[1] == {"v1": ROOT + "/"}
    assert client.request("GET", "/_mock")[1]["storage"] == "memory"
    assert client.request("GET", "/_mock", auth=False)[0] == 401


@pytest.mark.parametrize(
    "path",
    [
        "/../readme.md",
        "/redfish/v1/%2e%2e/readme.md",
        "/redfish/v1/index.json",
        "/redfish/v1/TaskService/Tasks/JID_001/_steps/01.json",
    ],
)
def test_no_filesystem_exposure(serve, path):
    """Verify no filesystem exposure."""
    assert serve().request("GET", path)[0] in (400, 404)


def test_body_validation(serve):
    """Verify body validation."""
    client = serve()
    for raw in (b"{", b"[]", b"null", b"\xff"):
        assert (
            client.request("PATCH", ROOT, raw=raw, headers={"Content-Type": "application/json"})[0]
            == 400
        )
    assert (
        client.request("PATCH", ROOT, raw=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    )
    assert client.request("PATCH", ROOT, headers={"Content-Length": str(1024 * 1024 + 1)})[0] == 413
    assert client.request("PATCH", ROOT, headers={"Content-Length": "-1"})[0] == 400
    assert client.request("PATCH", ROOT, headers={"Transfer-Encoding": "chunked"})[0] == 501


def test_https(serve, tmp_path):
    """Verify https."""
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    client = serve(tls=(cert, key))
    assert client.request("GET", ROOT + "/Systems")[0] == 200
