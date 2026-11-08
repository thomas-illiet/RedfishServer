"""Concurrent collection load with per-profile latency and error summaries."""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import ssl
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from scripts.smoke import Client


def load_test(clients: dict[str, Client], duration: int = 60, concurrency: int = 20) -> dict:
    """Twenty persistent HTTPS clients per profile, all starting together."""
    barrier = threading.Barrier(len(clients) * concurrency)

    def worker(vendor: str, client: Client) -> tuple[str, list[float], list[str]]:
        """Collect timed requests for one load-test client and retain bounded error details."""
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
