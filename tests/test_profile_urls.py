"""Namespace boundary and client URL validation regressions."""

from __future__ import annotations

import pytest

from redfish_mock.server.namespace import external_payload, override_body
from redfish_mock.urls import Endpoint


def test_only_uri_fields_are_translated_without_mutation():
    """Keep arbitrary strings, media URLs, external contexts and identifiers intact."""
    payload = {
        "@odata.id": "/redfish/v1/Chassis/1#/PowerSupplies/0",
        "@odata.context": "/redfish/v1/$metadata#Chassis.Chassis",
        "Members@odata.nextLink": "/redfish/v1/Chassis?$skip=1",
        "Actions": [{"target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"}],
        "Image": "https://example.test/redfish/image.iso",
        "Name": "/redfish/v1/Name",
        "Id": "/redfish/v1/id",
        "Password": "/redfish/v1/password",
        "external": {"@odata.id": "https://example.test/redfish/v1/"},
        "control": "/_mock/scenario",
        "v1": "/redfish/v1/",
    }
    translated = external_payload(payload, "/dell")
    assert translated["@odata.id"] == "/dell" + payload["@odata.id"]
    assert translated["@odata.context"] == "/dell" + payload["@odata.context"]
    assert translated["Members@odata.nextLink"] == "/dell" + payload["Members@odata.nextLink"]
    assert translated["Actions"][0]["target"].startswith("/dell/")
    for key in ("Image", "Name", "Id", "Password", "external"):
        assert translated[key] == payload[key]
    assert payload["@odata.id"].startswith("/redfish/")
    assert external_payload(payload, "") == payload
    assert override_body({"Path": "/redfish/v1/Systems/1"}, "") == {"Path": "/redfish/v1/Systems/1"}
    assert override_body({"Path": None}, "/dell") == {"Path": None}


@pytest.mark.parametrize("base", ["https://bmc.example/dell", "https://bmc.example/dell/"])
def test_profile_client_resolution(base):
    """Resolve canonical and discovered URLs within exactly one profile."""
    endpoint = Endpoint(base)
    expected = "https://bmc.example/dell/redfish/v1/Systems?$top=1"
    assert endpoint.url("/redfish/v1/Systems?$top=1", redfish_only=True) == expected
    assert endpoint.url("/dell/redfish/v1/Systems?$top=1", redfish_only=True) == expected
    assert endpoint.url("/") == "https://bmc.example/dell/"
    assert endpoint.path("/dell") == "/dell"
    assert endpoint.path("/dell/") == "/dell/"
    assert endpoint.path("/dell/redfish/v1/%53ystems").startswith("/dell/")


@pytest.mark.parametrize(
    "uri",
    [
        "/hpe/redfish/v1/",
        "/dell/dell/redfish/v1/",
        "/dell/../hpe/redfish/v1/",
        "/dell/%2e%2e/hpe/redfish/v1",
        "/dell/%252e%252e/hpe",
        "/dell%2fredfish/v1",
        "/dell/redfish/v1#fragment",
        "//evil.test/redfish/v1/",
        "https://evil.test/redfish/v1/",
        "/%64ell/redfish/v1/",
        "/redfish/v1/../../hpe/redfish/v1",
        "/_mock",
        "/dell/readyz",
        "/dell/redfish/%0a",
        "/dell/redfish/%zz",
        "/dell/redfish\\v1/",
        "/dell/redfish/%ff",
    ],
)
def test_profile_client_rejects_namespace_escapes(uri):
    """Reject all cross-profile and ambiguous links before sending credentials."""
    with pytest.raises(ValueError, match="outside the configured"):
        Endpoint("https://bmc.example/dell").url(uri, redfish_only=True)


@pytest.mark.parametrize(
    "base",
    [
        "file:///tmp/server",
        "https://user:password@bmc.example/dell",
        "http://bmc.example/path",
        "https://bmc.example/dell?x=1",
        "https://bmc.example/dell#x",
        "http://bmc.example/dell//",
        "https://bmc.example/dell/hpe",
        "https://bmc.example/%64ell",
        "https://bmc.example:99999/dell",
        "https://bmc.example/dell\n",
        "http://bmc.example//",
    ],
)
def test_invalid_base_urls(base):
    """Permit only origins and literal supported profile prefixes."""
    with pytest.raises(ValueError, match=r"URL must be|Port out of range"):
        Endpoint(base)
