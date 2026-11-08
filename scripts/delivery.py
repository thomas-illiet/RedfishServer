"""Local, fail-closed verification and release of both container architectures."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import http.client
import json
import os
import platform
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scripts.coverage_gate import check as coverage_check
from scripts.smoke import Client, check_profile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
TOOLS = json.loads((ROOT / "scripts/release-tools.json").read_text())
ARCHES = ("amd64", "arm64")
PROFILES = ("dell", "hpe", "supermicro", "lenovo", "hpe-ilo4")
REPORTS = ROOT / "reports"
VERIFIED = REPORTS / "verification.json"


def run(
    args: list[str],
    *,
    log: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 900,
    both_streams: bool = False,
) -> str:
    """No shell expansion; callers supply only commands and non-secret arguments."""
    if log:
        with log.open("w") as output:
            subprocess.run(
                args,
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
                timeout=timeout,
            )
        return ""
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=timeout
    )
    return (result.stdout + (result.stderr if both_streams else "")).strip()


def docker(*args: str, **kwargs: Any) -> str:
    return run(["docker", *args], **kwargs)


def source_files() -> list[Path]:
    files = [
        ROOT / name
        for name in (
            "README.md",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "Dockerfile",
            "Makefile",
            "compose.yaml",
            "compose.production.yaml",
            ".env.example",
            ".env.production.example",
            ".dockerignore",
            ".gitignore",
        )
    ]
    for directory in ("redfish_mock", "mockups", "scripts", "tests", "docs"):
        files.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
        )
    return sorted(files)


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_fingerprint() -> str:
    manifest = "".join(f"{checksum(p)}  {p.relative_to(ROOT).as_posix()}\n" for p in source_files())
    return hashlib.sha256(manifest.encode()).hexdigest()


def wait_ready(client: Client, timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.request("GET", "/readyz")["ready"]:
                return
        except OSError, ValueError:
            time.sleep(0.2)
    raise RuntimeError("Container did not become ready")


def bound_port(container: str, internal: int = 8000) -> int:
    mapping = docker("port", container, f"{internal}/tcp").splitlines()[0]
    return int(mapping.rsplit(":", 1)[1])


def load_test(clients: dict[str, Client], duration: int = 60, concurrency: int = 20) -> dict:
    """Twenty persistent HTTPS clients per profile, all starting together."""
    barrier = threading.Barrier(len(clients) * concurrency)

    def worker(vendor: str, client: Client) -> tuple[str, list[float], list[str]]:
        origin = urlsplit(client.url)
        factory = (
            http.client.HTTPSConnection if origin.scheme == "https" else http.client.HTTPConnection
        )
        kwargs = (
            {"context": ssl.create_default_context(cafile=str(client.ca_file))}
            if origin.scheme == "https"
            else {}
        )
        connection = factory(origin.hostname, origin.port, timeout=10, **kwargs)
        barrier.wait(timeout=30)
        deadline = time.monotonic() + duration
        latencies, errors = [], []
        paths = ("/redfish/v1/Systems", "/redfish/v1/Chassis", "/redfish/v1/Managers")
        try:
            while time.monotonic() < deadline:
                start = time.monotonic()
                try:
                    connection.request(
                        "GET",
                        paths[len(latencies) % len(paths)],
                        headers={"Authorization": client.auth},
                    )
                    response = connection.getresponse()
                    payload = response.read()
                    if response.status != 200:
                        errors.append(f"HTTP {response.status}")
                    elif "Members" not in json.loads(payload):
                        errors.append("Invalid collection")
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    errors.append(type(exc).__name__)
                    connection.close()
                latencies.append((time.monotonic() - start) * 1000)
        finally:
            connection.close()
        return vendor, latencies, errors

    samples: dict[str, list[float]] = {vendor: [] for vendor in clients}
    failures: dict[str, list[str]] = {vendor: [] for vendor in clients}
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(clients) * concurrency) as executor:
        futures = [
            executor.submit(worker, vendor, client)
            for vendor, client in clients.items()
            for _ in range(concurrency)
        ]
        for future in concurrent.futures.as_completed(futures):
            vendor, durations, errors = future.result()
            samples[vendor].extend(durations)
            failures[vendor].extend(errors)
    elapsed = time.monotonic() - started
    result = {}
    for vendor, durations in samples.items():
        durations.sort()
        result[vendor] = {
            "clients": concurrency,
            "requested_duration_seconds": duration,
            "wall_seconds": round(elapsed, 3),
            "requests": len(durations),
            "requests_per_second": round(len(durations) / elapsed, 2),
            "p95_ms": round(durations[int((len(durations) - 1) * 0.95)], 3),
            "max_ms": round(max(durations), 3),
            "errors": len(failures[vendor]),
            "error_examples": failures[vendor][:5],
        }
    return result


def verify_containers(arch: str, image: str, report: Path) -> dict:
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


def scan_archive(arch: str, archive: Path, report: Path) -> None:
    cache = ROOT / ".cache/trivy"
    cache.mkdir(parents=True, exist_ok=True)
    common = [
        "run",
        "--rm",
        "--mount",
        f"type=bind,src={archive.parent},dst=/input,readonly",
        "--mount",
        f"type=bind,src={report},dst=/out",
        "--mount",
        f"type=bind,src={cache},dst=/root/.cache",
        TOOLS["scanner"],
        "image",
        "--input",
        f"/input/{archive.name}",
    ]
    docker(
        *common,
        "--scanners",
        "vuln",
        "--severity",
        "HIGH,CRITICAL",
        "--exit-code",
        "1",
        "--format",
        "json",
        "--output",
        f"/out/{arch}-vulnerabilities.json",
        log=report / f"{arch}-scan.log",
    )
    docker(
        *common,
        "--format",
        "cyclonedx",
        "--output",
        f"/out/{arch}-sbom.cdx.json",
        log=report / f"{arch}-sbom.log",
    )


def verify() -> None:
    REPORTS.mkdir(exist_ok=True)
    VERIFIED.unlink(missing_ok=True)
    fingerprint = source_fingerprint()
    report = REPORTS / (
        "verify-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    report.mkdir()
    cache = ROOT / ".cache/release-images"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Verification reports: {report.relative_to(ROOT)}", flush=True)
    coverage = coverage_check()
    for name in ("coverage.json", "coverage.xml"):
        shutil.copy2(REPORTS / name, report / name)
    with tempfile.TemporaryDirectory(prefix="redfish-audit-") as temporary:
        requirements = Path(temporary) / "runtime.txt"
        requirements.write_text(
            run(
                ["uv", "export", "--frozen", "--no-dev", "--no-group", "audit", "--no-emit-project"]
            )
        )
        run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--require-hashes",
                "--no-deps",
                "--disable-pip",
                "--strict",
                "-r",
                str(requirements),
                "--format",
                "json",
                "--output",
                str(report / "dependency-audit.json"),
            ],
            log=report / "dependency-audit.log",
        )
    host_arch = docker("info", "--format", "{{.Architecture}}")
    data: dict[str, Any] = {
        "version": VERSION,
        "source_sha256": fingerprint,
        "created": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "coverage_percent": coverage,
        "docker_architecture": host_arch,
        "tools": TOOLS,
        "report_directory": str(report.relative_to(ROOT)),
        "images": {},
    }
    for arch in ARCHES:
        image = f"redfish-mock:{VERSION}-{arch}"
        print(f"[{arch}] Building and auditing {image}", flush=True)
        docker(
            "buildx",
            "build",
            "--platform",
            f"linux/{arch}",
            "--load",
            "--label",
            f"org.opencontainers.image.revision={fingerprint}",
            "-t",
            image,
            ".",
            log=report / f"{arch}-build.log",
        )
        image_info = json.loads(docker("image", "inspect", image))[0]
        if image_info["Architecture"] != arch:
            raise RuntimeError("Built image architecture does not match target")
        runtime = json.loads(
            docker(
                "run",
                "--rm",
                "--platform",
                f"linux/{arch}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--entrypoint",
                "/app/.venv/bin/python",
                image,
                "-c",
                "import importlib.util,json,os,platform,shutil; "
                "print(json.dumps({'python':platform.python_version(),'uid':os.getuid(),"
                "'tools':{name:shutil.which(name) for name in "
                "('sh','bash','busybox','apk','pip','uv')},"
                "'pip':importlib.util.find_spec('pip') is not None,"
                "'ensurepip':importlib.util.find_spec('ensurepip') is not None}))",
            )
        )
        if (
            runtime["python"] != (ROOT / ".python-version").read_text().strip()
            or runtime["uid"] != 10001
            or any(runtime["tools"].values())
            or runtime["pip"]
            or runtime["ensurepip"]
        ):
            raise RuntimeError("Runtime version or distroless checks failed")
        (report / f"{arch}-runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        archive = cache / f"redfishserver-{VERSION}-{arch}.tar"
        docker("image", "save", "--output", str(archive), image)
        scan_archive(arch, archive, report)
        results = verify_containers(arch, image, report)
        data["images"][arch] = {
            "tag": image,
            "id": image_info["Id"],
            "runtime": runtime,
            "archive": str(archive.relative_to(ROOT)),
            "sha256": checksum(archive),
            "emulated": arch != {"aarch64": "arm64", "x86_64": "amd64"}.get(host_arch, host_arch),
            **results,
        }
        (report / "progress.json").write_text(json.dumps(data, indent=2) + "\n")
    if fingerprint != source_fingerprint():
        raise RuntimeError("Sources changed during verification; run make verify again")
    data["status"] = "passed"
    (report / "verification.json").write_text(json.dumps(data, indent=2) + "\n")
    VERIFIED.write_text(json.dumps(data, indent=2) + "\n")
    print("Both architectures passed all release gates.", flush=True)


def package() -> None:
    data = json.loads(VERIFIED.read_text())
    if data.get("status") != "passed" or data["source_sha256"] != source_fingerprint():
        raise SystemExit("A successful verification of the current sources is required.")
    destination = ROOT / "dist" / f"redfishserver-{VERSION}"
    if destination.exists():
        raise SystemExit(f"Release directory already exists: {destination}")
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for arch in ARCHES:
            info = data["images"][arch]
            archive = ROOT / info["archive"]
            if (
                checksum(archive) != info["sha256"]
                or json.loads(docker("image", "inspect", info["tag"]))[0]["Id"] != info["id"]
            ):
                raise SystemExit("A verified image or archive changed; run make verify again.")
            shutil.copy2(archive, stage / archive.name)
        shutil.copytree(ROOT / data["report_directory"], stage / "reports")
        for path in source_files():
            target = stage / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (stage / "verification.json").write_text(json.dumps(data, indent=2) + "\n")
        (stage / "SHA256SUMS").write_text(
            "".join(
                f"{checksum(p)}  {p.relative_to(stage).as_posix()}\n"
                for p in sorted(stage.rglob("*"))
                if p.is_file()
            )
        )
        stage.rename(destination)
    print(f"Release ready: {destination}")


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("verify", "package"))
    args = cli.parse_args()
    try:
        verify() if args.action == "verify" else package()
    except subprocess.CalledProcessError as exc:
        # Full command arguments can include local paths; reports contain command output.
        raise SystemExit(f"Release gate failed (exit {exc.returncode}); inspect reports/.") from exc


if __name__ == "__main__":
    main()
