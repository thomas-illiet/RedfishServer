"""Guard documentation coverage and installed-package independence."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from scripts.structure_gate import check, inspect_source


@pytest.mark.parametrize(
    "definition",
    [
        "def public():\n    pass",
        "def _private():\n    pass",
        "async def _async_private():\n    pass",
        "class Record:\n    field: str",
        'def outer():\n    """Documented."""\n    def inner():\n        pass',
        'class Container:\n    """Documented."""\n    def __init__(self):\n        pass',
        'def empty():\n    """   """',
        "callback = lambda: 1",
    ],
)
def test_structure_detects_undocumented_definitions(definition):
    """Require useful docstrings for public, private, nested, async and special definitions."""
    assert inspect_source('"""Documented module."""\n' + definition)


def test_structure_accepts_documented_code_and_limits_file_size():
    """Accept documented code but reject missing module documentation or excess file length."""
    source = '"""Module."""\nclass Record:\n    """Typed record."""\n    value: str\n'
    assert inspect_source(source) == []
    assert inspect_source(source, maximum_lines=3)
    assert inspect_source("value = 1")


def test_structure_checks_imported_code_without_a_documentation_exemption(tmp_path):
    """Require imported docstrings while limiting the size exemption to the original generator."""
    fixtures = tmp_path / "src/redfish_mock/_fixtures"
    fixtures.mkdir(parents=True)
    generator = fixtures / "make_mockups.py"
    generator.write_text('"""Generator."""\ndef _private():\n    pass\n')
    with pytest.raises(SystemExit, match="_private needs a non-empty docstring"):
        check(tmp_path)
    documented = '"""Generator."""\ndef _private():\n    """Generate a fixture."""\n'
    generator.write_text(documented + "\n" * 300)
    check(tmp_path)
    (fixtures / "new_module.py").write_text(documented + "\n" * 300)
    with pytest.raises(SystemExit, match="module has"):
        check(tmp_path)


def test_installed_package_works_outside_checkout(tmp_path):
    """Discover every bundled profile with isolated Python from an unrelated directory."""
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import json; from redfish_mock.engine import MockBMC, PROFILES; "
                "print(json.dumps({p: MockBMC(p).handle('GET', '/healthz', {}).status "
                "for p in PROFILES}))"
            ),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(process.stdout) == dict.fromkeys(
        ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4"), 200
    )
