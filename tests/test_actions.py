"""Regression coverage for actions behavior."""

from __future__ import annotations

import pytest

from redfish_mock.engine import ROOT


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro", "lenovo"])
def test_power_patch_etag_reset_and_isolation(serve, profile):
    """Verify power patch etag reset and isolation."""
    first, second = serve(profile), serve(profile)
    path = first.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    _, system, original_headers = first.request("GET", path)
    action = system["Actions"]["#ComputerSystem.Reset"]["target"]
    assert first.request("POST", action, {"ResetType": "Bogus"})[0] == 400
    expected_status = 200 if profile in ("hpe", "supermicro") else 204
    assert first.request("POST", action, {"ResetType": "ForceOff"})[0] == expected_status
    assert first.request("GET", path)[1]["PowerState"] == "Off"
    assert second.request("GET", path)[1]["PowerState"] == "On"
    patch = {"Boot": {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}}
    assert (
        first.request("PATCH", path, patch, headers={"If-Match": original_headers["ETag"]})[0]
        == 412
    )
    status, modified, headers = first.request("PATCH", path, patch)
    assert status == 200
    modified = first.request("GET", path)[1]
    assert modified["Boot"]["BootSourceOverrideTarget"] == "Pxe"
    assert headers["ETag"] != original_headers["ETag"]
    assert first.request("POST", action, {"ResetType": "On"})[0] == expected_status
    assert first.request("GET", path)[1]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
    assert first.request("POST", "/_mock/reset", {})[0] == 200
    assert first.request("GET", path)[1] == system


@pytest.mark.parametrize(
    "patch",
    [
        {"@odata.id": "/evil"},
        {"Id": "changed"},
        {"Absent": True},
        {"PowerState": 1},
        {"Boot": {"BootSourceOverrideTarget": "Invalid"}},
        {"Boot": {"BootSourceOverrideEnabled": "Forever"}},
        {"Memory": {"other": 1}},
        {"PowerState": "Off", "Id": "cannot-change"},
    ],
)
def test_invalid_patch_is_atomic(serve, patch):
    """Verify invalid patch is atomic."""
    client = serve()
    path = ROOT + "/Systems/System.Embedded.1"
    original = client.request("GET", path)[1]
    assert client.request("PATCH", path, patch)[0] == 400
    assert client.request("GET", path)[1] == original


@pytest.mark.parametrize(
    ("profile", "uri", "insert", "eject"),
    [
        (
            "dell",
            "/Managers/iDRAC.Embedded.1/VirtualMedia/CD",
            "VirtualMedia.InsertMedia",
            "VirtualMedia.EjectMedia",
        ),
        (
            "hpe",
            "/Managers/1/VirtualMedia/2",
            "Oem/Hpe/HpeiLOVirtualMedia.InsertVirtualMedia",
            "Oem/Hpe/HpeiLOVirtualMedia.EjectVirtualMedia",
        ),
        (
            "lenovo",
            "/Managers/1/VirtualMedia/EXT1",
            "VirtualMedia.InsertMedia",
            "VirtualMedia.EjectMedia",
        ),
    ],
)
def test_virtual_media(serve, profile, uri, insert, eject):
    """Verify virtual media."""
    client = serve(profile)
    path = ROOT + uri
    assert client.request("POST", path + "/Actions/" + insert, {"Image": "bad"})[0] == 400
    expected_status = 200 if profile == "hpe" else 204
    assert (
        client.request(
            "POST", path + "/Actions/" + insert, {"Image": "https://example.test/os.iso"}
        )[0]
        == expected_status
    )
    assert client.request("GET", path)[1]["Inserted"] is True
    assert client.request("POST", path + "/Actions/" + eject, {})[0] == expected_status
    assert client.request("GET", path)[1]["Image"] is None


def test_boot_once_is_consumed_only_on_boot(serve):
    """Verify boot once is consumed only on boot."""
    client = serve()
    path = ROOT + "/Systems/System.Embedded.1"
    client.request("PATCH", path, {"Boot": {"BootSourceOverrideEnabled": "Once"}})
    action = client.request("GET", path)[1]["Actions"]["#ComputerSystem.Reset"]["target"]
    client.request("POST", action, {"ResetType": "On"})
    assert client.request("GET", path)[1]["Boot"]["BootSourceOverrideEnabled"] == "Once"
    client.request("POST", action, {"ResetType": "ForceRestart"})
    assert client.request("GET", path)[1]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
