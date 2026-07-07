"""Google Ads connector over a mock transport: the accessible-customers listing, the GAQL
`searchStream` POST whose body is a top-level array of result batches, the developer-token gate
(a second credential beyond OAuth, read from the environment), and the `flatten` that derives each
stream's primary key from the nested GAQL object. Offline — a canned transport, no DB, no token."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.google_ads import GoogleAdsConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"
_DEV_TOKEN_VARS = ("UFO_GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN")


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
    return await ConnectorBackend(connector=GoogleAdsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


@contextmanager
def _dev_token(value: str | None) -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in _DEV_TOKEN_VARS}
    for name in _DEV_TOKEN_VARS:
        os.environ.pop(name, None)
    if value is not None:
        os.environ["UFO_GOOGLE_ADS_DEVELOPER_TOKEN"] = value
    try:
        yield
    finally:
        for name, original in saved.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original


def _customers_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "googleads.googleapis.com"
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
    with _dev_token("dev"):
        result = await _fetch("customers", _customers_handler())
    assert {page.source_ref for page in result.pages} == {"customers/123"}
    assert result.snapshot is False
    body = result.pages[0].body
    assert "Acme" in body


async def test_missing_developer_token_skips_the_stream() -> None:
    with _dev_token(None), pytest.raises(StreamSkipped):
        await _fetch("customers", _customers_handler())


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with _dev_token("dev"), pytest.raises(StreamSkipped):
        await _fetch("customers", handle)
