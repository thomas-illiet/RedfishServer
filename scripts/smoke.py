"""Exercise explicitly selected disposable BMCs, then reset their state."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import ssl
import urllib.request
from pathlib import Path
from typing import Any


class Client:
    """Drive authenticated HTTP requests against a disposable mock server."""

    def __init__(self, url: str, username: str, password: str, ca_file: str | None = None) -> None:
        """Configure the target origin, credentials and optional trusted CA certificate."""
        self.ca_file = ca_file
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )

    def request(self, method: str, path: str, data: dict | None = None, status: int = 200) -> Any:
        """Send an HTTP request and decode its response while closing the connection."""
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            self.url + path,
            payload,
            {"Authorization": self.auth, "Content-Type": "application/json"},
            method=method,
        )
        with self.opener.open(req, timeout=10) as response:
            if response.status != status:
                raise RuntimeError(f"Unexpected HTTP status: {response.status}; expected {status}")
            raw = response.read()
            return json.loads(raw) if raw else {}


def check_profile(vendor: str, client: Client) -> dict[str, Any]:
    """Exercise discovery, authentication and reset behavior on a disposable profile."""
    if client.request("GET", "/healthz")["profile"] != vendor:
        raise RuntimeError("Unexpected BMC profile")
    if not client.request("GET", "/readyz")["ready"]:
        raise RuntimeError("BMC is not ready")
    client.request("POST", "/_mock/reset", {})
    systems = client.request("GET", "/redfish/v1/Systems")
    path = systems["Members"][0]["@odata.id"]
    original = client.request("GET", path)
    if vendor not in ("dell", "hpe", "supermicro"):
        return {"profile": vendor, "model": original["Model"], "result": "passed"}
    cpus = client.request("GET", original["Processors"]["@odata.id"] + "?$expand=.")
    dimms = client.request("GET", original["Memory"]["@odata.id"] + "?$expand=.")
    expected_dimms = 12 if vendor == "supermicro" else 16
    if len(cpus["Members"]) != 2 or len(dimms["Members"]) != expected_dimms:
        raise RuntimeError("Incomplete CPU/DIMM inventory")
    if any(cpu["Model"] != original["ProcessorSummary"]["Model"] for cpu in cpus["Members"]):
        raise RuntimeError("CPU model does not match summary")
    total_mib = sum(dimm["CapacityMiB"] for dimm in dimms["Members"])
    if total_mib != original["MemorySummary"]["TotalSystemMemoryGiB"] * 1024:
        raise RuntimeError("DIMM capacities do not match summary")
    chassis = client.request("GET", original["Links"]["Chassis"][0]["@odata.id"])
    power_path, thermal_path = chassis["Power"]["@odata.id"], chassis["Thermal"]["@odata.id"]
    power = client.request("GET", power_path)
    watts = power["PowerControl"][0]["PowerConsumedWatts"]
    if not math.isclose(
        watts, sum(psu["PowerInputWatts"] for psu in power["PowerSupplies"]), abs_tol=0.01
    ):
        raise RuntimeError("Chassis power does not match PSU inputs")
    thermal = client.request("GET", thermal_path)
    if (
        sum(t.get("PhysicalContext") == "SystemMemory" for t in thermal["Temperatures"])
        != expected_dimms
    ):
        raise RuntimeError("Missing DIMM temperatures")
    for dimm in dimms["Members"]:
        ecc = client.request("GET", dimm["Metrics"]["@odata.id"])["LifeTime"]
        if ecc["CorrectableECCErrorCount"] != 0 or ecc["UncorrectableECCErrorCount"] != 0:
            raise RuntimeError("Unexpected baseline ECC errors")
    action = original["Actions"]["#ComputerSystem.Reset"]["target"]
    try:
        client.request("POST", "/_mock/scenario", {"Scenario": "overheat"})
        hot = client.request("GET", thermal_path)["Temperatures"]
        if not any(
            t["ReadingCelsius"] > t["UpperThresholdCritical"]
            and t["Status"]["Health"] == "Critical"
            for t in hot
            if t.get("PhysicalContext") == "CPU"
        ):
            raise RuntimeError("Overheat scenario did not raise a thermal fault")
        client.request(
            "POST", action, {"ResetType": "ForceOff"}, status=204 if vendor == "dell" else 200
        )
        if client.request("GET", path)["PowerState"] != "Off":
            raise RuntimeError("Power state did not change")
        if (
            not 11
            <= client.request("GET", power_path)["PowerControl"][0]["PowerConsumedWatts"]
            <= 13
        ):
            raise RuntimeError("Standby power is outside the simulated range")
        if any(
            t["ReadingCelsius"] is not None
            for t in client.request("GET", thermal_path)["Temperatures"]
            if t.get("PhysicalContext") in ("CPU", "SystemMemory", "StorageDevice")
        ):
            raise RuntimeError("Powered-off components expose stale temperatures")
        client.request("PATCH", path, {"Boot": {"BootSourceOverrideTarget": "Pxe"}})
        if client.request("GET", path)["Boot"]["BootSourceOverrideTarget"] != "Pxe":
            raise RuntimeError("PATCH did not persist")
    finally:
        client.request("POST", "/_mock/reset", {})
    restored = client.request("GET", path)
    for field in ("Model", "ProcessorSummary", "MemorySummary", "PowerState", "Boot"):
        if restored[field] != original[field]:
            raise RuntimeError(f"Reset did not restore {field}")
    if client.request("GET", "/_mock")["monitoring"]["scenario"] != "baseline":
        raise RuntimeError("Reset did not restore the baseline scenario")
    return {
        "profile": vendor,
        "model": original["Model"],
        "dimms": expected_dimms,
        "baseline_watts": watts,
        "result": "passed",
    }


def main() -> None:
    """Run explicit reset-authorized smoke checks against disposable targets."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--url",
        action="append",
        required=True,
        help="PROFILE=URL; repeat for each disposable instance",
    )
    cli.add_argument("--ca-file")
    cli.add_argument(
        "--allow-reset",
        action="store_true",
        required=True,
        help="Confirm the selected instances are disposable",
    )
    args = cli.parse_args()
    password_file = os.environ.get("REDFISH_PASSWORD_FILE")
    password = (
        Path(password_file).read_text().removesuffix("\n")
        if password_file
        else os.environ.get("REDFISH_PASSWORD", "admin")
    )
    for item in args.url:
        vendor, separator, url = item.partition("=")
        if not separator:
            cli.error("Use --url PROFILE=URL")
        result = check_profile(
            vendor, Client(url, os.environ.get("REDFISH_USERNAME", "admin"), password, args.ca_file)
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
