# RedfishServer

![RedfishServer](docs/assets/redfish-banner.png)

A local HTTP/HTTPS server for testing Redfish clients with Dell iDRAC, HPE iLO,
Supermicro, Lenovo and HPE iLO 4 profiles on one shared HTTP listener.
The engine runs on Python 3.14 with a bounded Uvicorn ASGI transport.
Dependencies and container bases are pinned for reproducible releases.

The data is synthetic, and state is kept in memory.

## Quick start

With Docker Compose:

```sh
docker compose up --build -d --wait
```

Or with Python, from the project root:

```sh
uv run --frozen python -m redfish_mock --multi-profile --port 8000
```

Choose one launch method: both use port 8000.

Open the [profile catalogue](http://127.0.0.1:8000/).

| Profile | Interactive documentation | Redfish API |
|---|---|---|
| Dell | [Swagger](http://127.0.0.1:8000/dell/) | `/dell/redfish/v1/` |
| HPE | [Swagger](http://127.0.0.1:8000/hpe/) | `/hpe/redfish/v1/` |
| Supermicro | [Swagger](http://127.0.0.1:8000/supermicro/) | `/supermicro/redfish/v1/` |
| Lenovo | [Swagger](http://127.0.0.1:8000/lenovo/) | `/lenovo/redfish/v1/` |
| HPE iLO 4 | [Swagger](http://127.0.0.1:8000/hpe-ilo4/) | `/hpe-ilo4/redfish/v1/` |

Each profile serves offline Swagger UI at `/<profile>/` and its OpenAPI 3.1
contract at `/<profile>/openapi.json`. The illustrated **Fake** badge identifies
simulated behavior; **Try it out** operates on that profile's in-memory state.
One reverse proxy can publish the catalogue and all profiles on a single HTTPS
domain and port 443; see [production and migration](docs/production.md).

Development credentials: `admin` / `admin`. For internal production, use the
separate [production configuration](docs/production.md), which requires a secret
file and explicit credentials.

```sh
curl -u admin:admin http://127.0.0.1:8000/dell/redfish/v1/Systems
```

To stop: `docker compose down --remove-orphans` or `Ctrl+C` in Python mode.
Changes held in memory are lost on restart.

## Documentation

Full documentation is available in [docs/](docs/readme.md):

- [Internal production and release](docs/production.md)
- [Deployment and configuration](docs/deployment.md)
- [API and examples](docs/api.md)
- [Monitoring and simulation](docs/monitoring.md)
- [CPU and memory inventories](docs/inventory.md)
- [Architecture](docs/architecture.md)
- [Sources and limitations](docs/fidelity.md)
- [Development and checks](docs/development.md)
