from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from redfish_mock.__main__ import main
from redfish_mock.engine import FIXTURES, PROFILES, ROOT, SESSIONS, MockBMC, RedfishError
from redfish_mock.inventory import add_inventory
from redfish_mock.monitor import RedfishClient, snapshot
from redfish_mock.monitoring import SCENARIOS, add_monitoring, paths
from redfish_mock.server import MockHTTPServer

PROJECT = Path(__file__).resolve().parents[1]
AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()


class Client:
    def __init__(self, server, context=None):
        self.server = server
        self.context = context

    def request(self, method, path, body=None, *, auth=True, headers=None, raw=None):
        hdrs = {"Authorization": AUTH} if auth else {}
        if headers:
            hdrs.update(headers)
        if body is not None:
            raw = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if self.context:
            conn = http.client.HTTPSConnection(
                "127.0.0.1", self.server.server_port, context=self.context, timeout=3
            )
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, raw, hdrs)
            response = conn.getresponse()
            payload = response.read()
            return (
                response.status,
                (
                    json.loads(payload)
                    if payload and response.headers.get_content_type() == "application/json"
                    else payload.decode()
                    if payload
                    else None
                ),
                response.headers,
            )
        finally:
            conn.close()


@pytest.fixture
def serve():
    servers = []
    threads = []

    def start(profile="dell", *, tls=None, **kwargs):
        kwargs.setdefault("dynamic_metrics", False)
        server = MockHTTPServer(("127.0.0.1", 0), MockBMC(profile, **kwargs))
        context = None
        if tls:
            cert, key = tls
            server.enable_tls(str(cert), str(key))
            context = ssl.create_default_context(cafile=str(cert))
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        worker.start()
        servers.append(server)
        threads.append(worker)
        return Client(server, context)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
    for worker in threads:
        worker.join(timeout=3)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw", [False, True])
def test_all_imported_resources_over_http(serve, profile, raw):
    client = serve(profile, raw_fixtures=raw)
    for path, expected in client.server.bmc.original.items():
        status, body, headers = client.request("GET", path)
        assert status == 200, path
        assert body == expected, path
        assert headers["OData-Version"] == "4.0"
        assert headers["ETag"].startswith('W/"')
    assert client.request("GET", ROOT + "/", auth=False)[0] == 200


def test_imported_source_checksums():
    # Frozen after checking all 268 files against the upstream import manifest.
    # Provenance and the canonical manifest format are documented in docs/SOURCES.md.
    files = sorted(p for p in FIXTURES.rglob("*") if p.is_file() and p.suffix in (".json", ".py"))
    manifest = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(PROJECT).as_posix()}\n"
        for path in files
    )
    assert len(files) == 268
    assert hashlib.sha256(manifest.encode()).hexdigest() == (
        "9b5908cf07adaf4cab6464e4c2842155ace1c556d9c90aab0c798b382ff36ad7"
    )


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
    status, body, headers = serve().request("GET", ROOT + "/Systems", auth=False, headers=header)
    assert status == 401
    assert "error" in body
    assert "WWW-Authenticate" in headers


def test_sessions_creation_listing_deletion_and_expiry(serve):
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


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro", "lenovo"])
def test_power_patch_etag_reset_and_isolation(serve, profile):
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


def test_fixture_sequences_and_head(serve):
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
        "/../README.md",
        "/redfish/v1/%2e%2e/README.md",
        "/redfish/v1/index.json",
        "/redfish/v1/TaskService/Tasks/JID_001/_steps/01.json",
    ],
)
def test_no_filesystem_exposure(serve, path):
    assert serve().request("GET", path)[0] in (400, 404)


def test_body_validation(serve):
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


def test_no_auth_and_custom_credentials(serve):
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


def test_https(serve, tmp_path):
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


@pytest.mark.parametrize(
    "args", [["--port", "0"], ["--fleet", "--port", "65534"], ["--delay-ms", "-1"], ["--cert", "x"]]
)
def test_cli_rejects_invalid_configuration(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_bad_profiles_and_missing_fixtures(tmp_path):
    with pytest.raises(ValueError, match="Unknown profile"):
        MockBMC("invalid")
    with pytest.raises(ValueError, match="No service root"):
        MockBMC("dell", mockups_dir=tmp_path)
    with pytest.raises(ValueError, match="Credentials"):
        MockBMC("dell", password="")
    with pytest.raises(RedfishError):
        MockBMC("dell").handle("GET", "http://evil.test/redfish/v1", {})


def test_cli_starts_and_stops_a_real_process():
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "redfish_mock",
            "--vendor",
            "supermicro",
            "--port",
            str(port),
            "--quiet",
        ],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.request("GET", ROOT)
                response = connection.getresponse()
                body = json.loads(response.read())
                connection.close()
                assert body["Vendor"] == "Supermicro"
                break
            except OSError:
                assert process.poll() is None
                time.sleep(0.05)
        else:
            pytest.fail("CLI did not become ready")
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert f"http://127.0.0.1:{port}" in stdout


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_documented_collection_and_sensor_contracts(profile):
    # DMTF Power.v1_7_1 / Thermal.v1_7_0 and vendor resource maps; see FIDELITY.md.
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
    bmc = MockBMC(profile)

    def links(node):
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


def test_boot_once_is_consumed_only_on_boot(serve):
    client = serve()
    path = ROOT + "/Systems/System.Embedded.1"
    client.request("PATCH", path, {"Boot": {"BootSourceOverrideEnabled": "Once"}})
    action = client.request("GET", path)[1]["Actions"]["#ComputerSystem.Reset"]["target"]
    client.request("POST", action, {"ResetType": "On"})
    assert client.request("GET", path)[1]["Boot"]["BootSourceOverrideEnabled"] == "Once"
    client.request("POST", action, {"ResetType": "ForceRestart"})
    assert client.request("GET", path)[1]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"


def test_supermicro_sel_and_success_envelope(serve):
    # Supermicro guide revision 6.1 pp. 177, 196, and FAQ 33539.
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
    corrected, raw = MockBMC("supermicro"), MockBMC("supermicro", raw_fixtures=True)
    assert raw.original[ROOT + "/Systems"]["@odata.type"] == "#Collection"
    assert ROOT + "/Managers/1/LogServices/Log1" in raw.original
    assert ROOT + "/Systems/1/LogServices/Log1" in corrected.original
    status = corrected.handle("GET", "/_mock", {"Authorization": AUTH}).body
    assert status["fidelity"]["hardware_verified"] is False
    assert status["fidelity"]["mode"] == "documented-subset"


@pytest.mark.parametrize(
    ("profile", "cpu_model", "cores", "threads", "max_mhz", "tdp", "count", "gib", "mts"),
    [
        ("dell", "6338", 32, 64, 3200, 205, 16, 512, 3200),
        ("hpe", "4314", 16, 32, 3400, 135, 16, 256, 2667),
        ("supermicro", "6230", 20, 40, 3900, 125, 12, 192, 2933),
    ],
)
def test_documented_cpu_and_memory_configuration(
    serve,
    *,
    profile,
    cpu_model,
    cores,
    threads,
    max_mhz,
    tdp,
    count,
    gib,
    mts,
):
    # Independent expected configurations from the source matrix in docs/INVENTORY.md.
    client = serve(profile)
    system_path = client.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    system = client.request("GET", system_path)[1]
    processors_path = system["Processors"]["@odata.id"]
    memory_path = system["Memory"]["@odata.id"]
    processors = client.request("GET", processors_path + "?$expand=.")[1]
    assert processors["Members@odata.count"] == system["ProcessorSummary"]["Count"] == 2
    assert len(processors["Members"]) == 2
    for cpu in processors["Members"]:
        assert cpu_model in cpu["Model"] == system["ProcessorSummary"]["Model"]
        assert (cpu["TotalCores"], cpu["TotalEnabledCores"], cpu["TotalThreads"]) == (
            cores,
            cores,
            threads,
        )
        assert (cpu["MaxSpeedMHz"], cpu["TDPWatts"]) == (max_mhz, tdp)
        assert cpu["ProcessorType"] == "CPU"
        assert cpu["InstructionSet"] == "x86-64"
    memory = client.request("GET", memory_path + "?$expand=.")[1]
    dimms = memory["Members"]
    assert memory["Members@odata.count"] == len(dimms) == count
    assert sum(dimm["CapacityMiB"] for dimm in dimms) == gib * 1024
    assert system["MemorySummary"]["TotalSystemMemoryGiB"] == gib
    assert len({dimm["DeviceLocator"] for dimm in dimms}) == count
    for socket_number in (1, 2):
        local = [dimm for dimm in dimms if dimm["MemoryLocation"]["Socket"] == socket_number]
        assert len(local) == count // 2
        assert sum(dimm["CapacityMiB"] for dimm in local) == gib * 512
    for dimm in dimms:
        assert dimm["OperatingSpeedMhz"] == mts
        assert (dimm["MemoryType"], dimm["MemoryDeviceType"], dimm["BaseModuleType"]) == (
            "DRAM",
            "DDR4",
            "RDIMM",
        )
        assert (dimm["DataWidthBits"], dimm["BusWidthBits"]) == (64, 72)
        assert dimm["Status"] == {"State": "Enabled", "Health": "OK"}
        assert dimm["Links"]["Chassis"] == system["Links"]["Chassis"][0]
    if profile == "dell":
        expected_slots = {f"{bank}{slot}" for bank in "AB" for slot in range(1, 9)}
    elif profile == "hpe":
        expected_slots = {
            f"PROC {socket} DIMM {slot}"
            for socket in (1, 2)
            for slot in (1, 3, 5, 7, 10, 12, 14, 16)
        }
    else:
        expected_slots = {f"P{socket}-DIMM{channel}1" for socket in (1, 2) for channel in "ABCDEF"}
    assert {dimm["DeviceLocator"] for dimm in dimms} == expected_slots
    # Pagination must retain all installed DIMMs, without duplicates or omissions.
    members = []
    page_path = memory_path + "?$top=5"
    while page_path:
        page = client.request("GET", page_path)[1]
        assert page["Members@odata.count"] == count
        members.extend(member["@odata.id"] for member in page["Members"])
        page_path = page.get("Members@odata.nextLink")
    assert len(members) == len(set(members)) == count
    assert set(members) == {dimm["@odata.id"] for dimm in dimms}


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_inventory_readonly_override_reset_and_raw_mode(serve, profile):
    client = serve(profile)
    system_path = client.request("GET", ROOT + "/Systems")[1]["Members"][0]["@odata.id"]
    system = client.request("GET", system_path)[1]
    for collection, patch in (("Processors", {"TotalCores": 1}), ("Memory", {"CapacityMiB": 1})):
        uri = system[collection]["@odata.id"]
        member = client.request("GET", uri)[1]["Members"][0]["@odata.id"]
        original = client.request("GET", member)[1]
        assert client.request("PATCH", member, patch)[0] == 400
        assert client.request("GET", member)[1] == original
        assert (
            client.request(
                "POST",
                "/_mock/overrides",
                {"Path": member, "Patch": {"Status": {"Health": "Warning"}}},
            )[0]
            == 200
        )
        assert client.request("GET", member)[1]["Status"]["Health"] == "Warning"
        client.request("POST", "/_mock/reset", {})
        assert client.request("GET", member)[1] == original
    raw = MockBMC(profile, raw_fixtures=True)
    assert not any(
        item.get("@odata.type", "").startswith(("#Processor.", "#Memory."))
        for item in raw.resources.values()
    )


@pytest.mark.parametrize(
    "change", ["model", "cpu", "count", "capacity", "members", "orphan", "link"]
)
def test_inventory_preserves_custom_configurations(change):
    resources = MockBMC("dell", raw_fixtures=True).resources
    path = ROOT + "/Systems/System.Embedded.1"
    system = resources[path]
    if change == "model":
        system["Model"] = "PowerEdge R650"
    elif change == "cpu":
        system["ProcessorSummary"]["Model"] = "Different CPU"
    elif change == "count":
        system["ProcessorSummary"]["Count"] = 1
    elif change == "capacity":
        system["MemorySummary"]["TotalSystemMemoryGiB"] = 1024
    elif change == "members":
        resources[path + "/Processors"]["Members"] = [{"@odata.id": path + "/Processors/Custom"}]
    elif change == "orphan":
        resources[path + "/Memory/Custom"] = {"Id": "Custom"}
    else:
        system["Memory"] = {"@odata.id": ROOT + "/CustomMemory"}
    original = copy.deepcopy(resources)
    add_inventory("dell", resources)
    assert resources == original


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_complete_monitoring_resources_and_kpis(serve, profile):
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    system_uri, _, power_uri, thermal_uri = paths(profile)
    system = remote.get(system_uri)
    power, thermal = remote.get(power_uri), remote.get(thermal_uri)
    assert power["PowerControl"][0]["PowerConsumedWatts"] == sum(
        p["PowerInputWatts"] for p in power["PowerSupplies"]
    )
    for psu in power["PowerSupplies"]:
        assert psu["PowerCapacityWatts"] == (750 if profile == "supermicro" else 800)
        assert 0 <= psu["PowerOutputWatts"] <= psu["PowerInputWatts"]
        assert psu["PowerOutputWatts"] <= psu["PowerCapacityWatts"]
        if psu["PowerInputWatts"]:
            assert psu["EfficiencyPercent"] == pytest.approx(
                100 * psu["PowerOutputWatts"] / psu["PowerInputWatts"], abs=0.01
            )
    contexts = {s.get("PhysicalContext") for s in thermal["Temperatures"]}
    assert {
        "CPU",
        "SystemMemory",
        "StorageDevice",
        "PowerSupply",
        "Intake",
        "Exhaust",
        "SystemBoard",
    } <= contexts
    memory_refs = {m["@odata.id"] for m in remote.get(system["Memory"]["@odata.id"])["Members"]}
    dimm_sensors = [
        s for s in thermal["Temperatures"] if s.get("PhysicalContext") == "SystemMemory"
    ]
    assert {s["RelatedItem"][0]["@odata.id"] for s in dimm_sensors} == memory_refs
    # Resolve complete JSON pointers, including PSU links, not only base URIs.
    for sensor in thermal["Temperatures"]:
        for ref in sensor["RelatedItem"]:
            uri, _, pointer = ref["@odata.id"].partition("#")
            target = remote.get(uri)
            for part in pointer.lstrip("/").split("/") if pointer else []:
                target = target[int(part)] if isinstance(target, list) else target[part]
            assert target
    report = snapshot(remote.get)[0]
    assert report["power_input_watts"] == {"dell": 412, "hpe": 364, "supermicro": 498}[profile]
    assert report["psu_efficiency_percent"] == 94
    assert report["correctable_ecc_errors"] == report["uncorrectable_ecc_errors"] == 0
    assert report["temperatures"]["unavailable_count"] == 0
    assert report["temperatures"]["exhaust_minus_intake_celsius"] > 0
    if profile == "hpe":
        assert report["ssd_min_remaining_life_percent"] == 6
        assert report["drive_predicted_failure_count"] == 1
        assert all(f["unit"] == "Percent" for f in report["fan_readings"])
    if profile == "supermicro":
        assert report["ssd_min_remaining_life_percent"] is None
        assert report["fan_absent_count"] == 1
        assert report["drive_operations"][0]["PercentageComplete"] == 37
    before = copy.deepcopy(client.server.bmc.original)
    add_monitoring(profile, client.server.bmc.original)
    assert client.server.bmc.original == before


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_monitoring_scenarios_and_reset(serve, profile, scenario):
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    original = copy.deepcopy(client.server.bmc.resources)
    assert client.request("POST", "/_mock/scenario", {"Scenario": scenario})[0] == 200
    report = snapshot(remote.get)[0]
    if scenario != "baseline":
        assert report["power_input_watts"] == (
            620 if scenario in ("overheat", "high-load") else 300
        )
        assert report["psu_redundancy_healthy"] is (scenario != "psu-failure")
        assert report["fan_fault_count"] == (1 if scenario == "fan-failure" else 0)
        assert report["uncorrectable_ecc_errors"] == (1 if scenario == "memory-errors" else 0)
        if profile == "hpe":
            health = remote.get(paths(profile)[0])["Oem"]["Hpe"]["AggregateHealthStatus"]
            assert health["PowerSupplyRedundancy"] == (
                "FailedRedundant" if scenario == "psu-failure" else "Redundant"
            )
            assert health["FanRedundancy"] == (
                "FailedRedundant" if scenario == "fan-failure" else "Redundant"
            )
    if scenario == "overheat":
        assert report["temperatures"]["minimum_critical_headroom_celsius"] == -2
        assert report["health"] == "Critical"
    if scenario == "memory-errors":
        assert report["correctable_ecc_errors"] == 12
        assert report["health"] == "Critical"
    assert client.request("GET", "/_mock")[1]["monitoring"]["scenario"] == scenario
    assert client.request("POST", "/_mock/scenario", {"Scenario": "baseline"})[0] == 200
    assert client.server.bmc.resources == original
    client.request("POST", "/_mock/scenario", {"Scenario": "high-load"})
    client.request("POST", "/_mock/reset", {})
    assert client.server.bmc.resources == original
    assert client.request("GET", "/_mock")[1]["monitoring"]["scenario"] == "baseline"


@pytest.mark.parametrize("profile", ["dell", "hpe", "supermicro"])
def test_monitoring_offline_readings_and_restore(serve, profile):
    client = serve(profile)
    remote = RedfishClient(f"http://127.0.0.1:{client.server.server_port}", "admin", "admin")
    system_uri, _, _, thermal_uri = paths(profile)
    client.request("POST", "/_mock/scenario", {"Scenario": "high-load"})
    original = remote.get(thermal_uri)
    action = remote.get(system_uri)["Actions"]["#ComputerSystem.Reset"]["target"]
    for _ in range(2):
        client.request("POST", action, {"ResetType": "ForceOff"})
    report = snapshot(remote.get)[0]
    assert report["power_input_watts"] == 12
    assert report["power_state"] == "Off"
    assert "CPU" not in report["temperatures"]["max_celsius_by_context"]
    assert report["temperatures"]["unavailable_count"] > 2
    assert client.request("POST", "/_mock/scenario", {"Scenario": "normal"})[0] == 409
    client.request("POST", action, {"ResetType": "On"})
    assert remote.get(thermal_uri) == original
    assert snapshot(remote.get)[0]["power_input_watts"] == 620


def test_monitoring_controls_and_readonly_metrics(serve):
    client = serve("hpe")
    for body in ({}, {"Scenario": []}, {"Scenario": "invalid"}, {"Scenario": "normal", "Other": 1}):
        original = copy.deepcopy(client.server.bmc.resources)
        assert client.request("POST", "/_mock/scenario", body)[0] == 400
        assert client.server.bmc.resources == original
    assert client.request("POST", "/_mock/scenario", {"Scenario": "normal"}, auth=False)[0] == 401
    assert (
        serve("hpe", raw_fixtures=True).request("POST", "/_mock/scenario", {"Scenario": "normal"})[
            0
        ]
        == 501
    )
    assert serve("lenovo").request("POST", "/_mock/scenario", {"Scenario": "normal"})[0] == 501
    resources = client.server.bmc.resources
    metrics_path = next(
        p for p, r in resources.items() if r.get("@odata.type", "").startswith("#MemoryMetrics.")
    )
    assert (
        client.request("PATCH", metrics_path, {"LifeTime": {"CorrectableECCErrorCount": 5}})[0]
        == 400
    )


def test_monitor_client_rejects_external_links_and_invalid_origins():
    for origin in (
        "file:///etc/passwd",
        "https://user:password@example.test",
        "http://example.test/path",
    ):
        with pytest.raises(ValueError, match="URL must be"):
            RedfishClient(origin, "admin", "admin")
    client = RedfishClient("http://127.0.0.1:8001", "admin", "admin")
    for path in (
        "https://example.test/redfish/v1",
        "//example.test/redfish/v1",
        "/_mock",
        "/redfish/v1#x",
    ):
        with pytest.raises(ValueError, match="outside the configured"):
            client.get(path)


def test_sessions_are_isolated_across_all_five_profiles(serve):
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
