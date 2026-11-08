"""Regression tests for delivery."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts.coverage_gate import check
from scripts.delivery import commands, manifest
from scripts.delivery import packaging as delivery


def test_release_manifest_contains_package_and_fixture_data():
    """Include the src package and every bundled fixture in release fingerprints."""
    paths = {path.relative_to(manifest.ROOT).as_posix() for path in manifest.source_files()}
    assert "src/redfish_mock/engine/bmc.py" in paths
    assert "src/redfish_mock/_fixtures/dell/index.json" in paths
    assert "scripts/delivery/packaging.py" in paths
    assert "docs/sources.md" in paths
    assert not any(path.startswith(("redfish_mock/", "mockups/")) for path in paths)


@pytest.mark.parametrize(("lines", "branches"), [(100, 79), (79, 100), (0, 0)])
def test_coverage_requires_both_thresholds(tmp_path, lines, branches):
    """Verify coverage requires both thresholds."""
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
    """Verify missing branch measurement fails."""
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"num_statements": 100}}))
    with pytest.raises(SystemExit, match="branches"):
        check(report)


def test_exact_threshold_passes(tmp_path):
    """Verify exact threshold passes."""
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
    """Verify log inspection includes stderr."""

    def result(*_, **__):
        """Return a subprocess result containing both output streams for the logging test."""
        return subprocess.CompletedProcess([], 0, stdout="out", stderr="secret")

    monkeypatch.setattr(commands.subprocess, "run", result)
    assert commands.run(["docker", "logs"], both_streams=True) == "outsecret"
    assert commands.run(["docker", "inspect"]) == "out"


@pytest.fixture
def verified_release(tmp_path, monkeypatch):
    """Create isolated source files and matching release-verification evidence."""
    source = tmp_path / "readme.md"
    source.write_text("validated source")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "audit.json").write_text("{}")

    def _source_files():
        """Return the single source file used by the isolated release fixture."""
        return [source]

    def _image_info(*args, **_):
        """Report a matching image identifier for the requested test image tag."""
        return json.dumps([{"Id": args[-1]}])

    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    monkeypatch.setattr(delivery, "VERIFIED", reports / "verification.json")
    monkeypatch.setattr(delivery, "source_files", _source_files)
    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    monkeypatch.setattr(manifest, "source_files", _source_files)
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
    monkeypatch.setattr(delivery, "docker", _image_info)
    return tmp_path, data


@pytest.mark.parametrize("changed", ["source", "archive", "image", "failed"])
def test_package_rejects_unverified_changes(verified_release, monkeypatch, changed):
    """Verify package rejects unverified changes."""

    def _changed_image(*_, **__):
        """Simulate an image whose identifier differs from the verified evidence."""
        return '[{"Id":"changed"}]'

    root, data = verified_release
    if changed == "source":
        (root / "readme.md").write_text("changed")
    elif changed == "archive":
        (root / "amd64.tar").write_text("changed")
    elif changed == "image":
        monkeypatch.setattr(delivery, "docker", _changed_image)
    else:
        data["status"] = "failed"
        delivery.VERIFIED.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match=r"verification|verify"):
        delivery.package()
    assert not (root / "dist" / f"redfishserver-{delivery.VERSION}").exists()


def test_package_preserves_validated_archives_and_checksums(verified_release):
    """Verify package preserves validated archives and checksums."""
    root, _ = verified_release
    delivery.package()
    release = root / "dist" / f"redfishserver-{delivery.VERSION}"
    for line in (release / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert delivery.checksum(release / name) == digest
    assert (release / "arm64.tar").read_bytes() == b"arm64"
    assert (release / "readme.md").read_text() == "validated source"
    with pytest.raises(SystemExit, match="already exists"):
        delivery.package()
