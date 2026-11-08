"""Service URIs, supported profiles and bundled dataset location."""

from __future__ import annotations

from pathlib import Path

ROOT = "/redfish/v1"
SESSIONS = ROOT + "/SessionService/Sessions"
PROFILES = ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4")
FIXTURES = Path(__file__).resolve().parent / "_fixtures"
