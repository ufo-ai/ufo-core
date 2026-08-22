"""The Facebook Ads connector over a mock transport: the ad-account enumeration (`/me/adaccounts`),
the account-scoped fan-out paged by the body's `paging.next`, the `updated_time` watermark
advancing, and the default titled-JSON render. Offline — a canned transport, no DB, no token, no
broker."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.facebook_ads import FacebookAdsConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, SyncResult

ACCOUNT = "acct-1"


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=FacebookAdsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _accounts_only() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.facebook.com"
        assert request.url.path.endswith("/me/adaccounts")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "act_1",
                        "name": "Acme Ads",
                        "created_time": "2026-01-01T00:00:00+0000",
                    }
                ],
                "paging": {},
            },
        )

    return handle


async def test_ad_accounts_list_the_grants_accounts() -> None:
    result = await _fetch("ad_accounts", _accounts_only())
    assert {page.source_ref for page in result.pages} == {"ad_accounts/act_1"}
    assert result.snapshot is False
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert "Acme Ads" in result.pages[0].body


async def test_campaigns_fan_out_per_account_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/adaccounts"):
            return httpx.Response(
                200, json={"data": [{"id": "act_1", "name": "Acme"}], "paging": {}}
            )
        assert request.url.path.endswith("/act_1/campaigns")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "c1", "name": "Launch", "updated_time": "2026-02-05T00:00:00+0000"}
                ],
                "paging": {"next": None},
            },
        )

    result = await _fetch("campaigns", handle)
    assert {page.source_ref for page in result.pages} == {"campaigns/c1"}
    assert result.next_cursor == "2026-02-05T00:00:00+0000"


async def test_campaigns_filter_past_the_stored_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/adaccounts"):
            return httpx.Response(200, json={"data": [{"id": "act_1"}], "paging": {}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "old", "updated_time": "2026-01-01"},
                    {"id": "new", "updated_time": "2026-03-01"},
                ],
                "paging": {"next": None},
            },
        )

    result = await _fetch("campaigns", handle, cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"campaigns/new"}


async def test_campaigns_flatten_derives_status_name_and_created_at() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/adaccounts"):
            return httpx.Response(200, json={"data": [{"id": "act_1"}], "paging": {}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "c1",
                        "name": "Launch",
                        "status": "PAUSED",
                        "effective_status": "ACTIVE",
                        "created_time": "2026-01-01T00:00:00+0000",
                        "updated_time": "2026-02-05T00:00:00+0000",
                    }
                ],
                "paging": {"next": None},
            },
        )

    result = await _fetch("campaigns", handle)
    record = _flat(result, "campaigns/c1")
    assert record["status"] == "ACTIVE"
    assert record["name"] == "Launch"
    assert record["created_at"] == "2026-01-01T00:00:00+0000"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"


@pytest.mark.parametrize(
    ("stream", "path"),
    [("ad_sets", "/act_1/adsets"), ("ads", "/act_1/ads")],
)
async def test_ad_children_preserve_created_and_updated_times(stream: str, path: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/adaccounts"):
            return httpx.Response(200, json={"data": [{"id": "act_1"}], "paging": {}})
        assert request.url.path.endswith(path)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "item-1",
                        "created_time": "2026-01-01T00:00:00+0000",
                        "updated_time": "2026-02-05T00:00:00+0000",
                    }
                ],
                "paging": {},
            },
        )

    result = await _fetch(stream, handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"


async def test_a_forbidden_account_listing_fails_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "permission"}})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("ad_accounts", handle)
