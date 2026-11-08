"""Production config regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mock.__main__ import credentials, main, parser

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "args",
    [
        ["--production"],
        ["--production", "--username", "admin", "--password", "admin"],
        ["--production", "--username", "admin", "--password", "test-secret", "--no-auth"],
        ["--production", "--password", "test-secret"],
        ["--username", " ", "--password", "test-secret"],
        ["--password", ""],
        ["--password-file", "/missing/redfish-secret"],
    ],
)
def test_invalid_credentials_fail_before_binding(args):
    """Verify invalid credentials fail before binding."""
    cli = parser()
    with pytest.raises(SystemExit) as exc:
        credentials(cli, cli.parse_args(args))
    assert exc.value.code == 2


@pytest.mark.parametrize("secret", [b"", b" \n", b"\xff", b"x" * 65537, "é".encode() * 32769])
def test_invalid_secret_file(tmp_path, secret, capsys):
    """Verify invalid secret file."""
    path = tmp_path / "password"
    path.write_bytes(secret)
    cli = parser()
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password-file", str(path)]))
    assert "x" * 100 not in capsys.readouterr().err


def test_secret_file_and_environment(tmp_path, monkeypatch):
    """Verify secret file and environment."""
    path = tmp_path / "password"
    path.write_text("test-secret\r\n")
    monkeypatch.setenv("REDFISH_USERNAME", "collector")
    monkeypatch.setenv("REDFISH_PASSWORD_FILE", str(path))
    cli = parser()
    assert credentials(cli, cli.parse_args(["--production"])) == ("collector", "test-secret")
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password", "another"]))
    monkeypatch.delenv("REDFISH_PASSWORD_FILE")
    monkeypatch.setenv("REDFISH_PASSWORD", "from-env")
    assert credentials(cli, cli.parse_args(["--production"])) == ("collector", "from-env")
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password-file", str(path)]))
    with pytest.raises(SystemExit):
        credentials(cli, cli.parse_args(["--password", "from-env"]))


def test_cli_secret_option_and_local_defaults(tmp_path):
    """Verify cli secret option and local defaults."""
    path = tmp_path / "password"
    path.write_text("test-secret")
    cli = parser()
    assert credentials(cli, cli.parse_args([])) == ("admin", "admin")
    assert credentials(
        cli,
        cli.parse_args(["--production", "--username", "collector", "--password-file", str(path)]),
    ) == ("collector", "test-secret")


@pytest.mark.parametrize(
    "args", [["--trusted-proxies", "*"], ["--delay-ms", "nan"], ["--delay-ms", "inf"]]
)
def test_invalid_transport_configuration(args):
    """Verify invalid transport configuration."""
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
