"""Regression coverage for sessions behavior."""

from __future__ import annotations

import base64

import pytest

from redfish_mock.engine import ROOT, SESSIONS


@pytest.mark.parametrize(
    "header",
    [
        {},
        {"Authorization": "Basic invalid"},
        {"Authorization": "Basic YTpi"},
        {"X-Auth-Token": "invalid"},
    ],
)
def test_authentication_required(serve, header):
    """Verify authentication required."""
    status, body, headers = serve().request("GET", ROOT + "/Systems", auth=False, headers=header)
    assert status == 401
    assert "error" in body
    assert "WWW-Authenticate" in headers


def test_sessions_creation_listing_deletion_and_expiry(serve):
    """Verify sessions creation listing deletion and expiry."""
    client = serve()
    assert (
        client.request("POST", SESSIONS, {"UserName": "admin", "Password": "bad"}, auth=False)[0]
        == 401
    )
    status, session, headers = client.request(
        "POST", SESSIONS, {"UserName": "admin", "Password": "admin"}, auth=False
    )
    assert status == 201
    assert headers["Location"] == session["@odata.id"]
    token_headers = {"X-Auth-Token": headers["X-Auth-Token"]}
    assert "Password" not in session
    assert (
        client.request("GET", SESSIONS, auth=False, headers=token_headers)[1]["Members@odata.count"]
        == 1
    )
    assert client.request("GET", session["@odata.id"], auth=False, headers=token_headers)[0] == 200
    assert (
        client.request("DELETE", session["@odata.id"], auth=False, headers=token_headers)[0] == 204
    )
    assert client.request("GET", ROOT + "/Systems", auth=False, headers=token_headers)[0] == 401
    assert client.request("DELETE", session["@odata.id"])[0] == 404
    _, session, headers = client.request(
        "POST", SESSIONS, {"UserName": "admin", "Password": "admin"}
    )
    with client.server.bmc.lock:
        client.server.bmc.sessions[headers["X-Auth-Token"]] = (session["@odata.id"], 0)
    assert client.request("GET", session["@odata.id"])[0] == 404
    assert client.request("GET", SESSIONS)[1]["Members@odata.count"] == 0


def test_no_auth_and_custom_credentials(serve):
    """Verify no auth and custom credentials."""
    assert serve(require_auth=False).request("GET", ROOT + "/Systems", auth=False)[0] == 200
    client = serve(username="tester", password="sëcret")
    assert client.request("GET", ROOT + "/Systems")[0] == 401
    credentials = "Basic " + base64.b64encode("tester:sëcret".encode()).decode()
    assert (
        client.request(
            "GET", ROOT + "/Systems", auth=False, headers={"Authorization": credentials}
        )[0]
        == 200
    )


def test_sessions_are_isolated_across_all_five_profiles(serve):
    """Verify sessions are isolated across all five profiles."""
    clients = [serve(profile) for profile in ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4")]
    tokens = []
    for client in clients:
        status, _, headers = client.request(
            "POST", SESSIONS, {"UserName": "admin", "Password": "admin"}, auth=False
        )
        assert status == 201
        tokens.append(headers["X-Auth-Token"])
    for index, client in enumerate(clients):
        for owner, token in enumerate(tokens):
            status = client.request(
                "GET", ROOT + "/Systems", auth=False, headers={"X-Auth-Token": token}
            )[0]
            assert status == (200 if owner == index else 401)
    clients[0].request("POST", "/_mock/reset", {})
    for index, client in enumerate(clients):
        status = client.request(
            "GET", ROOT + "/Systems", auth=False, headers={"X-Auth-Token": tokens[index]}
        )[0]
        assert status == (401 if index == 0 else 200)
