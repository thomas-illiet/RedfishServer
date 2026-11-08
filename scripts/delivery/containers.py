"""Disposable production profiles, native HTTPS checks and clean shutdown."""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from scripts.smoke import Client, check_profile

from .commands import docker, run
from .config import PROFILES
from .load import load_test


def wait_ready(client: Client, timeout: int = 45) -> None:
    """Poll a disposable container until readiness succeeds or the deadline expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.request("GET", "/readyz")["ready"]:
                return
        except OSError, ValueError:
            time.sleep(0.2)
    raise RuntimeError("Container did not become ready")


def bound_port(container: str, internal: int = 8000) -> int:
    """Read the ephemeral host port bound to a container service port."""
    mapping = docker("port", container, f"{internal}/tcp").splitlines()[0]
    return int(mapping.rsplit(":", 1)[1])


def tls_override(work: Path, cert: Path, key: Path) -> Path:
    """Enable native TLS only in the disposable Compose project, including its probe."""
    # These short-lived test files must be readable by the unprivileged container UID.
    cert.chmod(0o444)
    key.chmod(0o444)
    certificate = "/run/secrets/redfish_certificate"
    private_key = "/run/secrets/redfish_private_key"
    config = {
        "services": {
            "redfish": {
                "command": [
                    "--production",
                    "--multi-profile",
                    "--host",
                    "0.0.0.0",  # noqa: S104 — Docker publishes only on host loopback.
                    "--port",
                    "8000",
                    "--cert",
                    certificate,
                    "--key",
                    private_key,
                ],
                "secrets": ["redfish_certificate", "redfish_private_key"],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "/app/.venv/bin/python",
                        "-c",
                        (
                            "import ssl, urllib.request; "
                            f"context = ssl.create_default_context(cafile={certificate!r}); "
                            "urllib.request.urlopen('https://localhost:8000/readyz', "
                            "context=context, timeout=2)"
                        ),
                    ],
                },
            },
        },
        "secrets": {
            "redfish_certificate": {"file": str(cert)},
            "redfish_private_key": {"file": str(key)},
        },
    }
    override = work / "compose.https.json"
    override.write_text(json.dumps(config, indent=2) + "\n")
    return override


def verify_containers(arch: str, image: str, report: Path) -> dict:
    """Check disposable production profiles, HTTPS load and clean shutdown."""
    project = f"redfish-verify-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="redfish-delivery-") as temporary:
        work = Path(temporary)
        password = secrets.token_urlsafe(32)
        secret = work / "password"
        secret.write_text(password)
        # Compose file-backed secrets retain host permissions. UID 10001 must read it.
        secret.chmod(0o444)
        env = {
            **os.environ,
            "REDFISH_IMAGE": image,
            "REDFISH_USERNAME": "collector",
            "REDFISH_PASSWORD_SECRET": str(secret),
            "REDFISH_BIND_ADDRESS": "127.0.0.1",
            "REDFISH_TRUSTED_PROXIES": "",
            "REDFISH_SEED": "42",
            "REDFISH_METRIC_INTERVAL": "5",
        }
        env["REDFISH_PORT"] = "0"
        compose = [
            "compose",
            "-p",
            project,
            "-f",
            "compose.production.yaml",
        ]
        try:
            print(f"[{arch}] Starting one container with five isolated profiles", flush=True)
            docker(
                *compose,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "60",
                env=env,
                log=report / f"{arch}-compose.log",
            )
            container = docker(*compose, "ps", "-q", "redfish", env=env)
            origin = f"http://127.0.0.1:{bound_port(container)}"
            global_client = Client(origin, "collector", password)
            wait_ready(global_client)
            smoke = []
            for vendor in PROFILES:
                client = Client(f"{origin}/{vendor}", "collector", password)
                wait_ready(client)
                smoke.append(check_profile(vendor, client))
            info = json.loads(docker("inspect", container))[0]
            if (
                info["Config"]["User"] != "10001:10001"
                or not info["HostConfig"]["ReadonlyRootfs"]
                or info["HostConfig"]["CapDrop"] != ["ALL"]
                or info["HostConfig"]["Memory"] != 1024 * 1024 * 1024
                or info["HostConfig"]["NanoCpus"] != 1_000_000_000
                or "no-new-privileges:true" not in info["HostConfig"]["SecurityOpt"]
            ):
                raise RuntimeError("Production container hardening is missing")
            cert, key = work / "cert.pem", work / "key.pem"
            run(
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
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                ],
                log=work / "certificate.log",
            )
            # Only the validation project is recreated with native TLS enabled.
            secure_compose = [*compose, "-f", str(tls_override(work, cert, key))]
            docker(
                *secure_compose,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "60",
                env=env,
                log=report / f"{arch}-https-compose.log",
            )
            container = docker(*secure_compose, "ps", "-q", "redfish", env=env)
            secure_origin = f"https://localhost:{bound_port(container)}"
            global_client = Client(secure_origin, "collector", password, str(cert))
            wait_ready(global_client)
            secure_clients = {}
            for vendor in PROFILES:
                client = Client(
                    f"{secure_origin}/{vendor}",
                    "collector",
                    password,
                    str(cert),
                )
                wait_ready(client)
                smoke.append(check_profile(vendor, client))
                secure_clients[vendor] = client
            print(f"[{arch}] HTTPS load: 20 clients per profile for 60 seconds", flush=True)
            load = load_test(secure_clients)
            (report / f"{arch}-load.json").write_text(json.dumps(load, indent=2) + "\n")
            if any(value["errors"] for value in load.values()):
                (report / f"{arch}-server-failure.log").write_text(
                    docker("logs", "--tail", "200", container, both_streams=True)
                )
                raise RuntimeError("Load test reported failures")
            stats = docker("stats", "--no-stream", "--format", "{{json .}}", container)
            (report / f"{arch}-container-stats.jsonl").write_text(stats + "\n")
            wait_ready(global_client)
            docker("stop", "--time", "20", container)
            state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
            if state["OOMKilled"] or state["ExitCode"] != 0:
                raise RuntimeError("Multi-profile container did not stop cleanly")
            logs = docker("logs", container, both_streams=True)
            if (
                password in logs
                or base64.b64encode(f"collector:{password}".encode()).decode() in logs
            ):
                raise RuntimeError("Credentials appeared in container logs")
            return {
                "smoke": smoke,
                "load": load,
                "clean_shutdown": True,
                "tls_verified": True,
            }
        finally:
            try:
                docker(
                    *compose,
                    "down",
                    "--remove-orphans",
                    env=env,
                    log=report / f"{arch}-cleanup.log",
                )
            except subprocess.SubprocessError as exc:
                raise RuntimeError(f"Temporary project cleanup failed: {project}") from exc
