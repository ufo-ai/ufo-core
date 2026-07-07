"""HubSpot connector over a mock transport: the CRM search → flatten → watermark path, the
archived-id sweep that tombstones deleted records, the declared-`Pagination` product-API path, and
the `403`-scope refusal that surfaces as `StreamSkipped`. Offline — a canned transport, no DB, no
token, no broker."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.hubspot import HubSpotConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=HubSpotConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _companies_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.hubapi.com"
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/companies":
            return httpx.Response(
                200, json={"results": [{"name": "name"}, {"name": "hs_lastmodifieddate"}]}
            )
        if request.method == "POST" and path == "/crm/v3/objects/companies/search":
            body = json.loads(request.content)
            if not body.get("after"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "c1",
                                "createdAt": "2026-01-01T00:00:00Z",
                                "updatedAt": "2026-02-05T00:00:00Z",
                                "properties": {
                                    "name": "Acme",
                                    "hs_lastmodifieddate": "2026-02-05T00:00:00Z",
                                },
                            }
                        ],
                        "paging": {},
                    },
                )
            return httpx.Response(200, json={"results": [], "paging": {}})
        if request.method == "GET" and path == "/crm/v3/objects/companies":
            return httpx.Response(200, json={"results": [{"id": "c9"}], "paging": {}})
        return httpx.Response(404, json={"path": path})

    return handle


async def test_companies_search_flattens_watermark_and_sweeps_deletes() -> None:
    result = await _fetch("companies", _companies_handler())

    assert {page.source_ref for page in result.pages} == {"companies/c1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T00:00:00Z"
    assert result.deletes == ("companies/c9",)

    body = result.pages[0].body
    assert "Acme" in body
    assert "hs_lastmodifieddate" in body


async def test_companies_incremental_filters_the_boundary_repeat() -> None:
    seen_bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/crm/v3/properties/companies":
            return httpx.Response(200, json={"results": [{"name": "hs_lastmodifieddate"}]})
        if request.method == "POST" and path == "/crm/v3/objects/companies/search":
            seen_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "c1",
                            "properties": {"hs_lastmodifieddate": "2026-02-05T00:00:00Z"},
                        }
                    ],
                    "paging": {},
                },
            )
        if request.method == "GET" and path == "/crm/v3/objects/companies":
            return httpx.Response(200, json={"results": [], "paging": {}})
        return httpx.Response(404, json={"path": path})

    result = await _fetch("companies", handle, cursor="2026-02-01T00:00:00Z")
    assert {page.source_ref for page in result.pages} == {"companies/c1"}
    assert seen_bodies and seen_bodies[0]["filterGroups"][0]["filters"][0]["value"] == (
        "2026-02-01T00:00:00Z"
    )


def _owners_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/crm/v3/owners":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "o1",
                            "email": "ada@example.com",
                            "updatedAt": "2026-03-01T00:00:00Z",
                        }
                    ],
                    "paging": {},
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_owners_strategy_pagination_flattens_product_api() -> None:
    result = await _fetch("owners", _owners_handler())
    assert {page.source_ref for page in result.pages} == {"owners/o1"}
    assert result.next_cursor == "2026-03-01T00:00:00Z"
    assert "ada@example.com" in result.pages[0].body


async def test_stream_skipped_when_the_object_is_scope_gated() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "This app does not have proper permissions to read companies"},
        )

    with pytest.raises(StreamSkipped):
        await _fetch("companies", handle)
