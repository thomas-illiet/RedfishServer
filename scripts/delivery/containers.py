"""Disposable production profiles, HTTPS proxy checks and clean shutdown."""

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
from .config import PROFILES, TOOLS
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


def verify_containers(arch: str, image: str, report: Path) -> dict:
    """Check disposable production profiles, HTTPS load and clean shutdown."""
    project = f"redfish-verify-{uuid.uuid4().hex[:12]}"
    containers: list[str] = []
    network = f"{project}-proxy"
    docker("network", "create", network)
    try:
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
            for name in ("DELL", "HPE", "SUPERMICRO", "LENOVO", "ILO4"):
                env[f"REDFISH_{name}_PORT"] = "0"
            compose = [
                "compose",
                "-p",
                project,
                "-f",
                "compose.production.yaml",
                "--profile",
                "extras",
            ]
            try:
                print(f"[{arch}] Starting five isolated production profiles", flush=True)
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
                clients = {}
                ids = {}
                smoke = []
                for vendor in PROFILES:
                    container = docker(*compose, "ps", "-q", vendor, env=env)
                    ids[vendor] = container
                    docker("network", "connect", "--alias", vendor, network, container)
                    client = Client(
                        f"http://127.0.0.1:{bound_port(container)}", "collector", password
                    )
                    clients[vendor] = client
                    wait_ready(client)
                    smoke.append(check_profile(vendor, client))
                    info = json.loads(docker("inspect", container))[0]
                    if (
                        info["Config"]["User"] != "10001:10001"
                        or not info["HostConfig"]["ReadonlyRootfs"]
                        or info["HostConfig"]["CapDrop"] != ["ALL"]
                        or info["HostConfig"]["Memory"] != 256 * 1024 * 1024
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
                servers = []
                for vendor, port in zip(PROFILES, range(8441, 8446), strict=True):
                    servers.append(f"""server {{
  listen {port} ssl;
  ssl_certificate /test/cert.pem;
  ssl_certificate_key /test/key.pem;
  client_max_body_size 1m;
  client_body_timeout 10s;
  location / {{
    proxy_pass http://{vendor}:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection \"\";
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
  }}
}}""")
                (work / "nginx.conf").write_text(
                    "events {}\nhttp { access_log off; " + "\n".join(servers) + "\n}\n"
                )
                proxy = f"{project}-https"
                docker(
                    "run",
                    "-d",
                    "--name",
                    proxy,
                    "--network",
                    network,
                    "--mount",
                    f"type=bind,src={work},dst=/test,readonly",
                    "-p",
                    "127.0.0.1::8441",
                    "-p",
                    "127.0.0.1::8442",
                    "-p",
                    "127.0.0.1::8443",
                    "-p",
                    "127.0.0.1::8444",
                    "-p",
                    "127.0.0.1::8445",
                    TOOLS["proxy"],
                    "nginx",
                    "-c",
                    "/test/nginx.conf",
                    "-g",
                    "daemon off;",
                )
                containers.append(proxy)
                secure_clients = {}
                for vendor, internal in zip(PROFILES, range(8441, 8446), strict=True):
                    client = Client(
                        f"https://localhost:{bound_port(proxy, internal)}",
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
                    raise RuntimeError("Load test reported failures")
                stats = docker("stats", "--no-stream", "--format", "{{json .}}", *ids.values())
                (report / f"{arch}-container-stats.jsonl").write_text(stats + "\n")
                for vendor, container in ids.items():
                    wait_ready(clients[vendor])
                    docker("stop", "--time", "20", container)
                    state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
                    if state["OOMKilled"] or state["ExitCode"] != 0:
                        raise RuntimeError(f"{vendor}: container did not stop cleanly")
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
                    "proxy_tls_verified": True,
                }
            finally:
                cleanup_errors = []
                for container in containers:
                    try:
                        docker("rm", "-f", container)
                    except subprocess.SubprocessError as exc:
                        cleanup_errors.append(exc)
                try:
                    docker(
                        *compose,
                        "down",
                        "--remove-orphans",
                        env=env,
                        log=report / f"{arch}-cleanup.log",
                    )
                except subprocess.SubprocessError as exc:
                    cleanup_errors.append(exc)
                if cleanup_errors:
                    raise RuntimeError(f"Temporary project cleanup failed: {project}")
    finally:
        docker("network", "rm", network)
