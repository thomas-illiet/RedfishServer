# Internal production and local releases

[Documentation](readme.md) · [Development](development.md) · [API](api.md)

This delivery runs an in-memory Redfish simulator for a trusted internal team.
It uses Python **3.14.10**, one Uvicorn worker serving five independent profiles, Docker Compose and
an existing HTTPS reverse proxy. Linux x86_64 (`amd64`) and ARM64 (`arm64`) are
built and tested separately. State and sessions are intentionally ephemeral.

## Build and validate a release

On the validation machine, install a recent `uv` capable of obtaining Python
3.14.10, GNU Make, OpenSSL, Docker with Compose v2 and Buildx. Docker must be
able to execute both architectures, natively or through emulation. Network
access is required for pinned registries, PyPI and vulnerability databases.

```sh
uv sync --frozen
make check
make release
```

`make check` runs linting, formatting, typing and tests, including CLI child
processes. It independently requires at least 80% line and 80% branch coverage
of all application modules. `make verify` adds the following release gates;
`make release` runs those gates and packages their successful output:

- Audit locked runtime dependencies; any reported vulnerability blocks release.
- Build, inspect and save both architectures using pinned image digests.
- Scan each exact image archive with Trivy. Any HIGH or CRITICAL finding blocks
  release, including findings without a fix. Scanner/database failures also block.
- Start one disposable production Compose container with five profiles with unique project names,
  temporary secrets and ephemeral localhost ports. Check configuration, vendor
  behavior, inventory, dynamic measurements, readiness and clean SIGTERM exit.
- Test all five profile prefixes on the server's native HTTPS endpoint with certificate
  verification, then run **20 concurrent clients per profile for 60 seconds**.
  Unexpected responses, server errors, timeouts and blocked clients fail the gate.
- Record request counts, throughput, p95 and maximum latency, container resource
  samples, architecture and whether execution used emulation.

The validator checks the default HTTP setup, then recreates the same service with
a temporary certificate and HTTPS health check. No proxy container is required.
It removes its own temporary containers and networks and does not modify the
default development stack or your real proxy. Load results describe
the validation machine, including emulation overhead; they are not a hardware
capacity guarantee. Integrity tests preserve the 267 imported JSON payloads and
the generator's executable syntax; its local additions are docstrings only.

The resulting `dist/redfishserver-0.2.0/` contains Docker archives for both
architectures, `SHA256SUMS`, CycloneDX component inventories, coverage, dependency
and image audit reports, load results, the source snapshot, documentation and
production Compose configuration. `verification.json` identifies the exact
sources and images. Packaging rejects changed sources, images or archives and
refuses to overwrite an existing release directory. Keep previous deliveries
for rollback. Run fresh validation for every new delivery.

## Container base

The runtime is a project-built distroless variant of the official
[Python Alpine image](https://hub.docker.com/_/python), pinned by digest in
`Dockerfile`. It uses Python 3.14.10 and Alpine 3.24. The build removes BusyBox,
the shell, APK tooling, pip and ensurepip, then copies the resulting filesystem
into `scratch`. Required libraries, CA roots, timezone data and installed-package
metadata remain present. This makes scans and component inventories meaningful.
It is not a Google or Chainguard image.

Dependency installation runs in a separate stage with pinned `uv`; build tools
are never copied into the final image. The health check and entry point use
exec-form Python commands. Release validation asserts the interpreter version
and absence of shell/package managers in each final architecture.
Update the pinned upstream digest and rerun the entire verification when
patching the base; do not install packages interactively in a running container.

## Install on a Linux server

Copy the completed delivery to the server and verify its checksums. Choose the
archive matching the host architecture:

```sh
sha256sum -c SHA256SUMS
docker load -i redfishserver-0.2.0-amd64.tar
# ARM64: docker load -i redfishserver-0.2.0-arm64.tar
```

Create `.env.production` from `.env.production.example` only if it does not
already exist. Set `REDFISH_IMAGE` to the loaded architecture-specific version,
set an explicit username and point `REDFISH_PASSWORD_SECRET` at the secret file.
Do not use a floating `latest` image tag. Never overwrite existing secrets or
configuration during an upgrade.

Create the password with a secret manager, or generate one locally in a new
protected directory. For example, on Linux, for an initial installation only:

```sh
install -d -m 0700 secrets
(umask 077; openssl rand -base64 32 > secrets/redfish-password)
sudo chown 10001:10001 secrets/redfish-password
sudo chmod 0400 secrets/redfish-password
```

Compose file secrets retain host permissions: container UID `10001` must be
able to read the file. The parent directory is protected on the host; Compose
mounts the file directly into the container. Restrict access to the Docker
socket and backups as well. Do not put the password itself in `.env.production`
or in command arguments. The secret is never built into the image.

Production mode refuses disabled authentication, absent credentials and the
default `admin` password. `--password-file` and `REDFISH_PASSWORD_FILE` read a
non-empty UTF-8 file of at most 64 KiB, removing one trailing newline. Configuring
more than one source among CLI password, CLI file, environment password and
environment file is an error, even when the values agree.

```sh
docker compose --env-file .env.production -f compose.production.yaml up -d --wait
docker compose --env-file .env.production -f compose.production.yaml ps
```

All five profiles start by default. A single port, 8000, is published on localhost.
Set `REDFISH_PORT` to change that host port. The catalogue is `/`; Swagger and
API routes are `/<profile>/` and `/<profile>/redfish/v1/` respectively.

Each container runs as UID/GID 10001, with a read-only filesystem, no Linux
capabilities, no privilege escalation, a 128-process limit, 1 CPU and 1 GiB
memory. Restart policy is `unless-stopped`. JSON-file logs rotate at 10 MiB,
keeping three files per container. There is no persistent application volume.

## Connect the existing HTTPS proxy

The application binds its published port to `127.0.0.1` by default. Configure
one HTTPS hostname and port 443 to forward **all paths unchanged** to
`http://127.0.0.1:8000`. Proxy configuration is managed outside this project.
No proxy configuration, domain or certificate is provisioned automatically. A containerized proxy
must use the application service's reachable private address, not its own localhost.

The application handles profile routing and URI adaptation. Do not strip
`/dell`, `/hpe`, `/supermicro`, `/lenovo` or `/hpe-ilo4`, rewrite JSON in the proxy,
or strip `Authorization` / `X-Auth-Token`. Authenticate through Basic or sessions.
Swagger and static resources are bundled and served under each profile prefix.

For a remote proxy, set `REDFISH_BIND_ADDRESS` to the server's private interface
address and restrict access to the proxy through your network controls. Backend
HTTP must stay on a trusted private network; use direct TLS if that is unsuitable.
TLS certificates and hostname configuration on the real proxy remain your
operational responsibility.

Forwarded headers are ignored by default. If needed, set
`REDFISH_TRUSTED_PROXIES` to comma-separated explicit proxy IP addresses or CIDRs,
using the source address seen by the container. Wildcard trust is rejected.
Configure the proxy to replace incoming forwarded headers. Proxy header trust
does not grant authentication or alter the shared simulator permissions.
Set proxy limits consistently with the 1 MiB request-body limit and allow the
server's 10-second body deadline and 15-second shutdown grace period.

## Supervise and operate

- `GET /healthz` is a public aggregate liveness check. `GET /readyz` is a public readiness
  check and is healthy only after all five profiles initialize. Per-profile probes
  are available at `/<profile>/healthz` and `/<profile>/readyz`. Readiness is withdrawn when
  shutdown starts; the listener then closes.
- Hardware `Warning` / `Critical` values are simulated dataset states, not
  service health. Do not restart a container merely because a scenario has a fault.
- Request logs contain timestamp, profile, method, sanitized path, status and
  duration. Query strings, bodies, credentials, tokens and session IDs are omitted.
- The shared transport permits at most 320 concurrent connections/tasks;
  overload receives `503` and recovers as capacity frees. Bodies over 1 MiB receive
  `413`; incomplete bodies time out after 10 seconds with `408`.
- SIGTERM drains in-flight requests for up to 15 seconds; Compose allows 20 seconds.
  There is exactly one worker serving all profiles. Do not scale replicas behind a shared
  hostname: each replica would have independent sessions and mutable state.

```sh
docker compose --env-file .env.production -f compose.production.yaml logs --tail=100
curl --fail http://127.0.0.1:8000/readyz
docker stats --no-stream
```

All authenticated users may change scenarios, resources and `/_mock` controls.
AccountService roles are fixture data, not access controls. This is an internal
simulator, not an Internet-facing BMC or a multi-tenant authorization service.
The standalone smoke test resets state and revokes sessions; use only read-only
checks on a live instance you want to preserve.

## Upgrade and roll back

1. Validate and retain a new versioned delivery, then check its SHA-256 sums.
2. Load the appropriate image archive and update only `REDFISH_IMAGE` in the
   existing `.env.production`. Preserve the username, secret and network settings.
3. Run the same Compose `up -d --wait` command and check readiness through HTTPS.
4. For rollback, restore the previous image tag and run `up -d --wait` again.
   Reload the retained previous archive if its image is no longer present.

Every container recreation, restart or rollback loses active sessions and all
in-memory modifications. Clients must authenticate again and replay scenarios
or changes they need. Restarts reload the baseline; `POST /_mock/reset` also
restores the baseline and revokes sessions. There is no state migration or
backup to restore. Rotating a secret requires recreating the containers with
the new secret file and re-authenticating clients.

## Migration from separate profile ports

This changes the Compose topology and default container command. Preserve your
secret, credentials and image/version settings. Stop the old Compose project
using its original configuration, including optional services, before installing
the new configuration:

```sh
# Run with the OLD Compose file still present; state and sessions will be lost.
docker compose --env-file .env.production -f compose.production.yaml --profile extras down
# Install the new delivery, then start its single service.
docker compose --env-file .env.production -f compose.production.yaml up -d --wait --remove-orphans
```

Replace per-profile `REDFISH_*_PORT` settings with `REDFISH_PORT=8000`; the old
port variables no longer configure this Compose file. Remove obsolete proxy
routes and update client targets:

| Old local endpoint | New local base URL |
|---|---|
| `http://127.0.0.1:8001` | `http://127.0.0.1:8000/dell` |
| `http://127.0.0.1:8002` | `http://127.0.0.1:8000/hpe` |
| `http://127.0.0.1:8003` | `http://127.0.0.1:8000/supermicro` |
| `http://127.0.0.1:8004` | `http://127.0.0.1:8000/lenovo` |
| `http://127.0.0.1:8005` | `http://127.0.0.1:8000/hpe-ilo4` |

The bundled collector accepts a profile base URL and rejects links to another
profile, including on the same origin. Clients that hard-code `/redfish/v1/`
at the domain root require adaptation or a legacy `--vendor` instance.
Bare CLI defaults and the `--vendor`/`--fleet` modes retain their earlier behavior.
A process restart now resets all five profiles; a profile's `/_mock/reset` still
resets only that profile. Rollback requires the previous Compose file and image,
followed by restoring the previous proxy/client addresses. No state migration exists.
