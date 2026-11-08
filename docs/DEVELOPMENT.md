# Development and maintenance

[Documentation](README.md) · [Architecture](ARCHITECTURE.md) · [Diagrams](assets/README.md)

## Set up the environment

The runtime uses Python 3.14 and Uvicorn. Development uses `uv`, Ruff,
ty, pytest and pytest-cov, with versions pinned in `uv.lock`. The Python patch
version is pinned in `.python-version`; container bases are pinned by digest.

```sh
uv sync --frozen
make check
```

`make check` runs Ruff linting, formatting checks, ty and pytest in sequence.
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
See [PRODUCTION.md](PRODUCTION.md) for prerequisites and release contents.

## Project structure

```text
redfish_mock/          Engine, transport, inventories, simulation, collector
mockups/              Imported rackhands files, kept unchanged
tests/                HTTP, contract and simulation tests with a controlled clock
scripts/smoke.py       Checks explicit disposable targets; resets their state
scripts/delivery.py    Isolated multi-architecture verification and release
scripts/coverage_gate.py Independent line and branch coverage gates
docs/                 Guides and references
docs/SOURCES.md        Provenance, fixture license and integrity checks
docs/assets/          Banner, Draw.io sources and SVG exports embedded in guides
compose.yaml          Three main profiles and two optional profiles
Dockerfile            Python runtime and health check
```

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
The fixture checksum test preserves all 268 imported files.

## Modify profiles

Do not edit `mockups/` to correct the supplied data: checksum tests preserve
its provenance using the canonical manifest described in [SOURCES.md](SOURCES.md).
Apply corrections in memory in the appropriate module, and document their
source and limitations in [FIDELITY.md](FIDELITY.md), [INVENTORY.md](INVENTORY.md)
or [MONITORING.md](MONITORING.md).

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
| Sensor, scenario or aggregate state | `monitoring.py`, then links and availability |
| Changes over time | `simulation.py`, bounds, seed and tests with a controlled clock |
| Action or write | `engine.py`, field permissions, status codes and vendor response envelopes |
| Transport or option | `server.py` / `__main__.py`, deployment guide |
| Collector KPI | `monitor.py`, missing data, units and discovery through links |

A new reading must specify its unit, availability, thresholds, component and
provenance. A `null` or missing reading must not arbitrarily become zero.
A counter must not decrease within a scenario. An unimplemented action must
remain explicitly unimplemented.

## Maintain the documentation

The [root README](../README.md) remains a short overview with a quick start
and links to the guides. Its banner is the only decorative illustration.
Detailed explanations and additional examples belong in `docs/`.

[`docs/README.md`](README.md) is the documentation entry point. Keep examples
consistent with the program options, and distinguish simulated values from
certified equipment capabilities. Simulation parameters are detailed in a
single guide: [MONITORING.md](MONITORING.md).

The `.drawio` files in [docs/assets](assets/README.md) are the editable sources.
After editing, re-export the corresponding SVG with Draw.io and visually
check text, arrows and margins. Markdown files use relative links to the SVGs;
no connection to a diagram service is needed to view the documentation.
