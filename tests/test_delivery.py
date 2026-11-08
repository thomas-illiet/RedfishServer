from __future__ import annotations

import json
import subprocess

import pytest

from scripts import delivery
from scripts.coverage_gate import check


@pytest.mark.parametrize(("lines", "branches"), [(100, 79), (79, 100), (0, 0)])
def test_coverage_requires_both_thresholds(tmp_path, lines, branches):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {
                    "num_statements": 100,
                    "num_branches": 100,
                    "covered_lines": lines,
                    "covered_branches": branches,
                }
            }
        )
    )
    with pytest.raises(SystemExit, match="coverage"):
        check(report)


def test_missing_branch_measurement_fails(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"num_statements": 100}}))
    with pytest.raises(SystemExit, match="branches"):
        check(report)


def test_exact_threshold_passes(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {
                    "num_statements": 100,
                    "num_branches": 100,
                    "covered_lines": 80,
                    "covered_branches": 80,
                }
            }
        )
    )
    assert check(report) == {"lines": 80, "branches": 80}


def test_log_inspection_includes_stderr(monkeypatch):
    def result(*_, **__):
        return subprocess.CompletedProcess([], 0, stdout="out", stderr="secret")

    monkeypatch.setattr(delivery.subprocess, "run", result)
    assert delivery.run(["docker", "logs"], both_streams=True) == "outsecret"
    assert delivery.run(["docker", "inspect"]) == "out"


@pytest.fixture
def verified_release(tmp_path, monkeypatch):
    source = tmp_path / "README.md"
    source.write_text("validated source")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "audit.json").write_text("{}")
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    monkeypatch.setattr(delivery, "VERIFIED", reports / "verification.json")
    monkeypatch.setattr(delivery, "source_files", lambda: [source])
    data = {
        "status": "passed",
        "source_sha256": delivery.source_fingerprint(),
        "report_directory": "reports",
        "images": {},
    }
    for arch in delivery.ARCHES:
        archive = tmp_path / f"{arch}.tar"
        archive.write_bytes(arch.encode())
        data["images"][arch] = {
            "tag": arch,
            "id": arch,
            "archive": archive.name,
            "sha256": delivery.checksum(archive),
        }
    delivery.VERIFIED.write_text(json.dumps(data))
    monkeypatch.setattr(delivery, "docker", lambda *args, **_: json.dumps([{"Id": args[-1]}]))
    return tmp_path, data


@pytest.mark.parametrize("changed", ["source", "archive", "image", "failed"])
def test_package_rejects_unverified_changes(verified_release, monkeypatch, changed):
    root, data = verified_release
    if changed == "source":
        (root / "README.md").write_text("changed")
    elif changed == "archive":
        (root / "amd64.tar").write_text("changed")
    elif changed == "image":
        monkeypatch.setattr(delivery, "docker", lambda *_, **__: '[{"Id":"changed"}]')
    else:
        data["status"] = "failed"
        delivery.VERIFIED.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match=r"verification|verify"):
        delivery.package()
    assert not (root / "dist" / f"redfishserver-{delivery.VERSION}").exists()


def test_package_preserves_validated_archives_and_checksums(verified_release):
    root, _ = verified_release
    delivery.package()
    release = root / "dist" / f"redfishserver-{delivery.VERSION}"
    for line in (release / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert delivery.checksum(release / name) == digest
    assert (release / "arm64.tar").read_bytes() == b"arm64"
    assert (release / "README.md").read_text() == "validated source"
    with pytest.raises(SystemExit, match="already exists"):
        delivery.package()
