"""Regression coverage for cli behavior."""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from redfish_mock.__main__ import main
from redfish_mock.engine import ROOT, MockBMC, RedfishError

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "args",
    [
        ["--port", "0"],
        ["--fleet", "--port", "65534"],
        ["--delay-ms", "-1"],
        ["--cert", "x"],
        ["--multi-profile", "--fleet"],
        ["--multi-profile", "--vendor", "dell"],
    ],
)
def test_cli_rejects_invalid_configuration(args):
    """Verify cli rejects invalid configuration."""
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_bad_profiles_and_missing_fixtures(tmp_path):
    """Verify bad profiles and missing fixtures."""
    with pytest.raises(ValueError, match="Unknown profile"):
        MockBMC("invalid")
    with pytest.raises(ValueError, match="No service root"):
        MockBMC("dell", mockups_dir=tmp_path)
    with pytest.raises(ValueError, match="Credentials"):
        MockBMC("dell", password="")
    with pytest.raises(RedfishError):
        MockBMC("dell").handle("GET", "http://evil.test/redfish/v1", {})


@pytest.mark.parametrize("multi", [False, True])
def test_cli_starts_and_stops_a_real_process(multi):
    """Verify cli starts and stops a real process."""
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "redfish_mock",
            *(["--multi-profile"] if multi else ["--vendor", "supermicro"]),
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
                connection.request("GET", "/supermicro" + ROOT if multi else ROOT)
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
    if multi:
        assert len(stdout.splitlines()) == 5
