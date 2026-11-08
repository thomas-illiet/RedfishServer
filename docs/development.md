# Development and maintenance

[Documentation](readme.md) · [Architecture](architecture.md) · [Diagrams](assets/readme.md)

## Set up the environment

The runtime uses Python 3.14 and Uvicorn. Development uses `uv`, Ruff,
ty, pytest and pytest-cov, with versions pinned in `uv.lock`. The Python patch
version is pinned in `.python-version`; container bases are pinned by digest.

```sh
uv sync --frozen
make check
```

`make check` runs Ruff linting, formatting checks, ty, the structure gate and pytest in sequence.
It requires at least **80% of application lines and 80% of application branches**,
measured independently. Coverage includes CLI subprocesses; no application
modules are omitted. JSON and XML results are written under `reports/`.
`make format` applies automatic fixes and formatting. `make test` runs tests and
the two coverage gates.

`make verify` adds runtime dependency auditing, builds both Linux architectures,
scans the exact image archives, and runs isolated production Compose instances.
It checks all five profiles, HTTPS proxying, clean shutdown and 20 concurrent
clients per profile for 60 seconds. `make release` performs verification
and packages only those exact validated images, source files and reports.
See [production.md](production.md) for prerequisites and release contents.

## Project structure

```text
src/redfish_mock/
  __main__.py         Compatibility entry point for python -m redfish_mock
  cli.py, config.py   Server startup, options and credential validation
  models.py           Shared JSON, response and error types
  constants.py        Service URIs, supported profiles and fixture location
  engine/             BMC coordinator, fixtures, sessions, resources, writes, actions
  server/             ASGI, Uvicorn lifecycle and JSON logging
  monitoring/         Sensor enrichment, power, health and scenarios
  collector/          HTTP client, resource discovery and KPIs
  contracts.py        Vendor-specific corrections and writable fields
  inventory.py        Typed CPU and DIMM topology and resource builders
  simulation.py       Clock-driven seeded telemetry
  monitor.py          Collector CLI and compatibility exports
  _fixtures/          Original JSON fixtures and the locally documented upstream generator
tests/                Tests grouped by behavior; shared fixtures and HTTP/ASGI drivers
scripts/delivery/     Multi-architecture verification and release operations
scripts/smoke.py      Explicit disposable-target checks that reset mock state
scripts/coverage_gate.py   Independent line and branch coverage thresholds
scripts/structure_gate.py  Docstrings and module-size enforcement
docs/                 Guides, provenance, license and editable diagrams
compose.yaml          Three main profiles and two optional profiles
Dockerfile            Installs the same package into the container runtime
```

## Code structure and documentation contract

Keep each maintained Python module at or below **300 lines**, organized around
one responsibility. Every module, class, dataclass, function and method must
have a non-empty docstring describing its purpose. This includes private,
special, nested and asynchronous definitions. Type annotations describe inputs
and outputs; docstrings explain behavior and meaningful constraints.

Use `_name` for implementation helpers. Package `__all__` lists declare the
supported public facades. Keep entry points thin, share data through explicit
types, and keep state ownership and locking in `MockBMC`.

Ruff checks documentation style, and `scripts.structure_gate` also checks
private and nested definitions that public-API lint rules can miss. Both run
in `make check`. Documentation checks cover all Python source, including the
imported generator under `_fixtures/`, with no private or imported-code exception.
Anonymous functions are replaced with named, documented helpers. Only formatting
and the 300-line size limit exempt the original generator, whose executable
syntax is protected by an independent integrity test. Its JSON output fixtures
remain byte-for-byte unchanged. Application source, delivery scripts and tests
are all checked.

## Build and run the installed package

```sh
uv sync --frozen
uv run --frozen redfish-mock --fleet
uv run --frozen redfish-monitor --url http://127.0.0.1:8001
uv run --frozen python -m pytest
make build
```

`make build` produces a source distribution and a wheel under `dist/`.
Both include the five bundled vendor profiles and their upstream license
notice. Installation does not need a `PYTHONPATH` override. Run pytest through
`python -m pytest` so repository-only delivery scripts remain importable while
the application itself is imported from the installed package.


## Verify a change

HTTP tests start servers on ephemeral local ports and cover authentication,
sessions, writes, media, power, ETags, pagination, sequences and HTTPS. Data
tests check imported checksums, links, CPU/DIMM configurations, totals and
the targeted conventions.

Simulation tests control time without waiting in real time: bounds,
power/PSU consistency, faults, counters, standby, seeds, time steps and
aggregates. Tests that expect fixed responses explicitly disable simulation;
do not make an assertion unstable by relying on five seconds elapsing.

After a change that affects containers:

```sh
make verify
```

This uses unique project names, ephemeral host ports and temporary secrets. It
never runs destructive smoke scenarios against the default development ports.
The standalone smoke command requires explicit `--url vendor=URL` targets and
`--allow-reset`; use it only on disposable instances.

Production tests also cover invalid secrets, incomplete fleet startup, occupied
ports, SIGTERM, partial bodies, client disconnects, saturation and recovery.
Integrity tests preserve all 267 JSON fixtures and the generator executable syntax.

## Modify profiles

Do not edit `src/redfish_mock/_fixtures/` to correct the supplied data: checksum tests preserve
its provenance using the canonical manifest described in [sources.md](sources.md).
Apply corrections in memory in the appropriate module, and document their
source and limitations in [fidelity.md](fidelity.md), [inventory.md](inventory.md)
or [monitoring.md](monitoring.md).

For a custom dataset, copy the profiles to another directory, modify that copy
and launch with `--mockups-dir /path/to/copy`. The directory contains the
expected profile subdirectories, such as `dell/index.json`. The upstream
generator recreates its directories: do not run it on fixtures you want to
keep. Custom data is not automatically a validated vendor capture.

## Add behavior

| Change | Areas to review |
|---|---|
| Property or contract correction | `contracts.py`, fidelity documentation and contract test |
| CPU or DIMM | `inventory.py`, configuration and population rules |
| Sensor, scenario or aggregate state | `monitoring/`, then links and availability |
| Changes over time | `simulation.py`, bounds, seed and tests with a controlled clock |
| Action or write | `engine/`, field permissions, status codes and vendor response envelopes |
| Transport or option | `server/`, `config.py` / `cli.py`, deployment guide |
| Collector KPI | `monitor.py`, missing data, units and discovery through links |

A new reading must specify its unit, availability, thresholds, component and
provenance. A `null` or missing reading must not arbitrarily become zero.
A counter must not decrease within a scenario. An unimplemented action must
remain explicitly unimplemented.

## Maintain the documentation

The [root README](../readme.md) remains a short overview with a quick start
and links to the guides. Its banner is the only decorative illustration.
Detailed explanations and additional examples belong in `docs/`.

[`docs/readme.md`](readme.md) is the documentation entry point. Keep examples
consistent with the program options, and distinguish simulated values from
certified equipment capabilities. Simulation parameters are detailed in a
single guide: [monitoring.md](monitoring.md).

The `.drawio` files in [docs/assets](assets/readme.md) are the editable sources.
After editing, re-export the corresponding SVG with Draw.io and visually
check text, arrows and margins. Markdown files use relative links to the SVGs;
no connection to a diagram service is needed to view the documentation.
