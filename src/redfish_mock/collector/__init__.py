"""Read-only Redfish client and per-server monitoring snapshots."""

from .client import RedfishClient
from .snapshot import snapshot

__all__ = ["RedfishClient", "snapshot"]
