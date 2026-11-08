"""Regression coverage for contracts behavior."""

from __future__ import annotations

import ast
import base64
import hashlib
import json

import pytest

from redfish_mock.engine import FIXTURES, ROOT, SESSIONS, MockBMC

AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()


def test_imported_source_checksums():
    # Frozen from the 267 JSON files in the verified upstream import.
    # Provenance and the canonical manifest format are documented in docs/sources.md.
    """Verify imported source checksums."""
    files = sorted(p for p in FIXTURES.rglob("*") if p.is_file() and p.suffix == ".json")
    manifest = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
        f"mockups/{path.relative_to(FIXTURES).as_posix()}\n"
        for path in files
    )
    assert len(files) == 267
    assert hashlib.sha256(manifest.encode()).hexdigest() == (
        "9d1708fdcca9c5b09f10500e3928bb917a4a2012f7d6f06e588bb0337fd10582"
    )


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_documented_collection_and_sensor_contracts(profile):
    # DMTF Power.v1_7_1 / Thermal.v1_7_0 and vendor resource maps; see fidelity.md.
    """Verify documented collection and sensor contracts."""
    bmc = MockBMC(profile)
    expected = {"Systems": "ComputerSystem", "Managers": "Manager", "Chassis": "Chassis"}
    for segment, schema in expected.items():
        assert (
            bmc.original[ROOT + "/" + segment]["@odata.type"]
            == f"#{schema}Collection.{schema}Collection"
        )
    for uri, resource in bmc.original.items():
        assert resource["@odata.type"] != "#Collection"
        if "Members" in resource:
            assert resource["Members@odata.count"] == len(resource["Members"])
        else:
            assert "Id" in resource, uri
        for name in ("Fans", "Temperatures", "PowerControl", "Voltages", "PowerSupplies"):
            members = resource.get(name, [])
            assert len({member["MemberId"] for member in members}) == len(members)
            for index, member in enumerate(members):
                assert member["@odata.id"] == f"{uri}#/{name}/{index}"


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_all_active_resource_links_resolve(profile):
    """Verify all active resource links resolve."""
    bmc = MockBMC(profile)

    def links(node):
        """Yield nested Redfish resource references for integrity checks."""
        if isinstance(node, dict):
            if "@odata.id" in node:
                yield node["@odata.id"].split("#")[0].rstrip("/")
            for value in node.values():
                yield from links(value)
        elif isinstance(node, list):
            for value in node:
                yield from links(value)

    for resource in bmc.original.values():
        for link in links(resource):
            assert link in bmc.original, link


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_readonly_fields_and_mock_overrides_are_separate(serve, profile):
    """Verify readonly fields and mock overrides are separate."""
    client = serve(profile)
    path = client.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    original = client.request("GET", path)[1]
    for patch in (
        {"PowerState": "Off"},
        {"SerialNumber": "FAKE"},
        {"Status": {"Health": "OK"}},
        {"Model": "FAKE"},
    ):
        status, error, _ = client.request("PATCH", path, patch)
        assert status == 400
        assert error["error"]["@Message.ExtendedInfo"][0]["MessageId"].endswith(
            "PropertyNotWritable"
        )
        assert client.request("GET", path)[1] == original
    override = {"Path": path, "Patch": {"Status": {"Health": "Critical"}}}
    assert client.request("POST", "/_mock/overrides", override, auth=False)[0] == 401
    assert client.request("POST", "/_mock/overrides", override)[0] == 200
    assert client.request("GET", path)[1]["Status"]["Health"] == "Critical"
    client.request("POST", "/_mock/reset", {})
    assert client.request("GET", path)[1] == original
    assert client.request("POST", "/_mock/overrides", {"Path": path, "Patch": []})[0] == 400
    assert client.request("POST", "/_mock/overrides", {"Path": ROOT, "Patch": {}})[0] == 400


def test_hpe_documented_success_and_boot_readonly_contract(serve):
    # HPE errorresponses + iLO5 3.06 ComputerSystem resource definition.
    """Verify hpe documented success and boot readonly contract."""
    client = serve("hpe")
    path = ROOT + "/Systems/1"
    status, body, _ = client.request("PATCH", path, {"Boot": {"BootSourceOverrideTarget": "Pxe"}})
    assert status == 200
    assert body == {
        "error": {
            "code": "iLO.0.10.ExtendedInfo",
            "message": "See @Message.ExtendedInfo for more information.",
            "@Message.ExtendedInfo": [{"MessageId": "Base.1.12.Success"}],
        }
    }
    assert client.request("GET", path)[1]["Boot"]["BootSourceOverrideTarget"] == "Pxe"
    status, error, _ = client.request("PATCH", path, {"Boot": {"BootOrder": []}})
    assert status == 400
    assert error["error"]["@Message.ExtendedInfo"][0]["MessageId"].endswith("PropertyNotWritable")
    assert client.request("PATCH", path, {"Boot": {"BootSourceOverrideMode": "invalid"}})[0] == 400
    assert client.request("PATCH", path, {"HostName": "new-name"})[0] == 200
    action = client.request("GET", path)[1]["Actions"]["#ComputerSystem.Reset"]["target"]
    assert client.request("POST", action, {"ResetType": "ForceRestart"})[0] == 200


def test_supermicro_sel_and_success_envelope(serve):
    # Supermicro guide revision 6.1 pp. 177, 196, and FAQ 33539.
    """Verify supermicro sel and success envelope."""
    client = serve("supermicro")
    system = client.request("GET", ROOT + "/Systems/1")[1]
    assert system["LogServices"]["@odata.id"] == ROOT + "/Systems/1/LogServices"
    entries = client.request("GET", ROOT + "/Systems/1/LogServices/Log1/Entries")[1]
    assert entries["Members@odata.count"] == 3
    assert all(e["EntryType"] == "SEL" for e in entries["Members"])
    assert client.request("GET", ROOT + "/Managers/1/LogServices/Log1")[0] == 404
    media = client.request("GET", ROOT + "/Managers/1/VirtualMedia/CD1")[1]
    assert "VM1" not in json.dumps(media)
    assert (
        client.request(
            "POST",
            media["Actions"]["#VirtualMedia.InsertMedia"]["target"],
            {"Image": "https://example.test/os.iso"},
        )[0]
        == 501
    )
    status, body, _ = client.request(
        "POST", system["Actions"]["#ComputerSystem.Reset"]["target"], {"ResetType": "ForceOff"}
    )
    assert status == 200
    assert body == {
        "Success": {"code": "Base.v1_10_3.Success", "message": "Successfully Completed Request."}
    }
    _, _, headers = client.request("POST", SESSIONS, {"UserName": "admin", "Password": "admin"})
    assert client.request("DELETE", headers["Location"])[0] == 200


def test_hpe_standard_media_parameters_and_patch(serve):
    """Verify hpe standard media parameters and patch."""
    client = serve("hpe")
    path = ROOT + "/Managers/1/VirtualMedia/2"
    action = client.request("GET", path)[1]["Actions"]["#VirtualMedia.InsertMedia"]["target"]
    image = "https://example.test/os.iso"
    for value in ("no", 1):
        assert client.request("POST", action, {"Image": image, "WriteProtected": value})[0] == 400
    assert client.request("POST", action, {"Image": "https:///missing-host"})[0] == 400
    assert (
        client.request(
            "POST", action, {"Image": image, "Inserted": False, "WriteProtected": False}
        )[0]
        == 200
    )
    media = client.request("GET", path)[1]
    assert media["Inserted"] is False
    assert media["WriteProtected"] is False
    assert media["ConnectedVia"] == "NotConnected"
    assert client.request("PATCH", path, {"Inserted": True})[0] == 200
    assert client.request("GET", path)[1]["ConnectedVia"] == "URI"
    assert client.request("PATCH", path, {"Image": None, "Inserted": False})[0] == 200
    assert client.request("PATCH", path, {"Inserted": True})[0] == 400
    assert client.request("PATCH", path, {"Image": 123})[0] == 400


def test_raw_fixtures_remain_available():
    """Verify raw fixtures remain available."""
    corrected, raw = MockBMC("supermicro"), MockBMC("supermicro", raw_fixtures=True)
    assert raw.original[ROOT + "/Systems"]["@odata.type"] == "#Collection"
    assert ROOT + "/Managers/1/LogServices/Log1" in raw.original
    assert ROOT + "/Systems/1/LogServices/Log1" in corrected.original
    status = corrected.handle("GET", "/_mock", {"Authorization": AUTH}).body
    assert status["fidelity"]["hardware_verified"] is False
    assert status["fidelity"]["mode"] == "documented-subset"


def test_imported_generator_executable_syntax_is_unchanged():
    """Allow generator documentation changes while preserving its original executable syntax."""
    tree = ast.parse((FIXTURES / "make_mockups.py").read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node):
            node.body.pop(0)
    fingerprint = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()
    assert fingerprint == "43d8d7f8a2142f655b38c5eb04125ed821d3ef3ea6b41a5e1002bba384b33a6e"
