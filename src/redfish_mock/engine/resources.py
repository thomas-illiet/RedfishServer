"""Resource reads, fixture progression and collection queries."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from redfish_mock.constants import ROOT
from redfish_mock.models import Json, RedfishError, Result, etag

from .status import status

if TYPE_CHECKING:
    from .bmc import MockBMC


def read(bmc: MockBMC, path: str, *, advance: bool = False) -> Json:
    """Return an isolated resource snapshot and optionally advance its fixture sequence."""
    if path not in bmc.resources:
        raise RedfishError(404, f"Resource not found: {path}.", "ResourceMissingAtURI")
    if bmc.play_sequences and path in bmc.steps:
        position = bmc.positions.get(path, 0)
        if position < len(bmc.steps[path]):
            body = copy.deepcopy(bmc.steps[path][position])
            if advance:
                bmc.positions[path] = position + 1
            return body
    return copy.deepcopy(bmc.resources[path])


def get(bmc: MockBMC, path: str, query: dict[str, list[str]], *, advance: bool) -> Result:
    """Serve health, metadata or resource reads with collection query support."""
    if path in ("/", "/healthz"):
        return Result(body={"status": "ok", "profile": bmc.profile, "redfish": ROOT + "/"})
    if path == "/redfish":
        return Result(body={"v1": ROOT + "/"})
    if path == "/_mock":
        return status(bmc)
    body = read(bmc, path, advance=advance)
    if query:
        supported = {"$top", "$skip", "$expand"}
        if set(query) - supported or any(len(v) != 1 for v in query.values()):
            raise RedfishError(400, "Supported query options: $top, $skip, $expand=.")
        if "Members" not in body:
            raise RedfishError(400, "Query options are supported on collections only.")
        if query.get("$expand", ["."])[0] not in (".", "*"):
            raise RedfishError(400, "Only $expand=. and $expand=* are supported (one level).")
        try:
            skip = int(query.get("$skip", ["0"])[0])
            top = int(query.get("$top", [str(len(body["Members"]))])[0])
            if skip < 0 or top < 0:
                raise ValueError
        except ValueError as exc:
            raise RedfishError(400, "$top and $skip must be non-negative integers.") from exc
        members = body["Members"]
        body["Members"] = members[skip : skip + top]
        if "$expand" in query:
            body["Members"] = [
                read(bmc, m["@odata.id"].rstrip("/"), advance=advance) for m in body["Members"]
            ]
        if top and skip + top < len(members):
            next_query = {k: v[0] for k, v in query.items()}
            next_query["$skip"] = str(skip + top)
            body["Members@odata.nextLink"] = path + "?" + urlencode(next_query)
    return Result(body=body, headers={"ETag": etag(body)})
