"""The Facebook Ads connector — ad accounts, campaigns, ad sets, ads, and daily ad insights synced
into recallable pages.

Reads route through the Graph API (`https://graph.facebook.com/v25.0`). Every stream first
enumerates the grant's ad accounts (`/me/adaccounts`) and fans the account-scoped collections out
over them; each collection pages by following the absolute `paging.next` URL the response carries.
`campaigns`, `ad_sets`, and `ads` filter past the stored watermark on `updated_time`; `ads_insights`
requests a per-ad daily breakdown windowed from the cursor date (or the last 90 days on a fresh run)
and synthesises a stable id per row. Auth is the OAuth bearer the resolved `Credential` carries. The
write path is intentionally absent — the source seam only reads."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, records_at, with_context

GRAPH_VERSION = "v25.0"
PAGE_SIZE = 100

FACEBOOK_ADS_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="ad_accounts",
        source_object="adaccounts",
        primary_key="id",
        created_at_field="created_time",
    ),
    StreamSpec(
        name="campaigns",
        source_object="campaigns",
        primary_key="id",
        cursor_field="updated_time",
        created_at_field="created_time",
        updated_at_field="updated_time",
    ),
    StreamSpec(
        name="ad_sets",
        source_object="adsets",
        primary_key="id",
        cursor_field="updated_time",
        created_at_field="created_time",
        updated_at_field="updated_time",
        canonical=False,
    ),
    StreamSpec(
        name="ads",
        source_object="ads",
        primary_key="id",
        cursor_field="updated_time",
        created_at_field="created_time",
        updated_at_field="updated_time",
        canonical=False,
    ),
    StreamSpec(
        name="ads_insights",
        source_object="insights",
        primary_key="id",
        cursor_field="date_stop",
        created_at_field="date_stop",
        updated_at_field=None,
        canonical=False,
    ),
]


class FacebookAdsConnector(RestConnector):
    name = "facebook_ads"
    base_url = f"https://graph.facebook.com/{GRAPH_VERSION}"
    streams_list = FACEBOOK_ADS_STREAMS

    async def _paged(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        next_path: str | None = path
        query = params
        while next_path:
            response = await self._get_raw(client, next_path, params=query)
            data = response.json() if response.content else {}
            records = records_at(data, "data")
            if records:
                yield records
            paging = data.get("paging") if isinstance(data, dict) else {}
            next_path = paging.get("next") if isinstance(paging, dict) else None
            query = None

    async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        fields = "id,name,account_id,account_status,currency,timezone_name,created_time,business"
        out: list[dict[str, Any]] = []
        async for page in self._paged(client, "/me/adaccounts", params={"fields": fields}):
            out.extend(page)
        return out

    async def _account_children(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        fields = {
            "campaigns": "id,name,status,effective_status,objective,created_time,updated_time",
            "ad_sets": (
                "id,name,status,effective_status,campaign_id,optimization_goal,"
                "created_time,updated_time"
            ),
            "ads": "id,name,status,effective_status,campaign_id,adset_id,created_time,updated_time",
        }[stream.name]
        for account in await self._accounts(client):
            account_id = account.get("id")
            if not isinstance(account_id, str) or not account_id:
                continue
            async for page in self._paged(
                client,
                f"/{account_id}/{stream.source_object}",
                params={"fields": fields, "limit": PAGE_SIZE},
            ):
                if cursor and stream.cursor_field:
                    page = [r for r in page if str(r.get(stream.cursor_field) or "") > cursor]
                if page:
                    yield with_context(
                        page, ad_account_id=account_id, ad_account_name=account.get("name")
                    )

    async def _insights(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        fields = (
            "date_start,date_stop,account_id,campaign_id,adset_id,ad_id,"
            "impressions,clicks,spend,reach,cpm,cpc,ctr"
        )
        params: dict[str, Any] = {
            "fields": fields,
            "level": "ad",
            "time_increment": 1,
            "limit": PAGE_SIZE,
        }
        if cursor:
            params["time_range"] = json.dumps(
                {"since": cursor[:10], "until": datetime.now(UTC).date().isoformat()}
            )
        else:
            params["date_preset"] = "last_90d"
        for account in await self._accounts(client):
            account_id = account.get("id")
            if not isinstance(account_id, str) or not account_id:
                continue
            async for page in self._paged(client, f"/{account_id}/insights", params=params):
                rows = [
                    {
                        **row,
                        "id": ":".join(
                            str(part)
                            for part in [
                                account_id,
                                row.get("campaign_id"),
                                row.get("adset_id"),
                                row.get("ad_id"),
                                row.get("date_start"),
                            ]
                            if part
                        ),
                        "ad_account_id": account_id,
                    }
                    for row in page
                ]
                if rows:
                    yield rows

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if stream.name == "ad_accounts":
            accounts = await self._accounts(client)
            if accounts:
                yield accounts
            return
        if stream.name in {"campaigns", "ad_sets", "ads"}:
            async for page in self._account_children(client, stream, cursor=cursor):
                yield page
            return
        if stream.name == "ads_insights":
            async for page in self._insights(client, cursor=cursor):
                yield page
            return
        raise StreamSkipped(f"facebook_ads stream {stream.name!r} is not implemented")

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "campaigns":
            return {
                **record,
                "name": record.get("name"),
                "status": record.get("effective_status") or record.get("status"),
                "created_at": record.get("created_time"),
            }
        return record
