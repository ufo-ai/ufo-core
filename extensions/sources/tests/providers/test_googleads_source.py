"""Google Ads connector over a mock transport: the accessible-customers listing, the GAQL
`searchStream` POST whose body is a top-level array of result batches, OAuth as the only
credential, and the `flatten` that derives each stream's primary key from the nested GAQL object.
Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.googleads import GoogleAdsConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=GoogleAdsConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, _auth(handler)
    )


def _customers_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "googleads.googleapis.com"
        assert "developer-token" not in request.headers
        if request.method == "GET" and request.url.path.endswith(
            "/customers:listAccessibleCustomers"
        ):
            return httpx.Response(200, json={"resourceNames": ["customers/123"]})
        if request.method == "POST" and request.url.path.endswith("googleAds:searchStream"):
            assert "/customers/123/" in request.url.path
            return httpx.Response(
                200,
                json=[
                    {
                        "results": [
                            {
                                "customer": {
                                    "id": "123",
                                    "descriptiveName": "Acme",
                                    "status": "ENABLED",
                                }
                            }
                        ]
                    }
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_customers_lists_and_flatten_derives_id() -> None:
    result = await _fetch("customers", _customers_handler())
    assert {page.source_ref for page in result.pages} == {"customers/123"}
    assert result.snapshot is False
    body = result.pages[0].body
    assert "Acme" in body


async def test_campaign_metrics_project_the_observation_date_as_created_time() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"resourceNames": ["customers/123"]})
        return httpx.Response(
            200,
            json=[
                {
                    "results": [
                        {
                            "campaign": {"id": "9", "resourceName": "customers/123/campaigns/9"},
                            "segments": {"date": "2026-02-01"},
                            "metrics": {"clicks": "4"},
                        }
                    ]
                }
            ],
        )

    result = await _fetch("campaign_metrics", handle)

    assert {page.source_ref for page in result.pages} == {"campaign_metrics/123:9:2026-02-01"}
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("customers", handle)
