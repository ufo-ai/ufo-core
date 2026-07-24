"""The Google Ads connector — advertiser accounts, campaigns, ad groups, ads, and campaign metrics
synced into recallable pages over the Google Ads Query Language.

Google Ads speaks GAQL over HTTP: every stream is a `SELECT ... FROM <resource>` posted to
`/customers/{id}/googleAds:searchStream`, which answers with a top-level array of result batches.
`paginate` first lists the accessible customer ids (`/customers:listAccessibleCustomers`), then runs
the stream's query against each, stamping `customer_id` onto every row; `flatten` lifts the nested
GAQL object into the flat record the sync keys by (the customer id, a campaign/ad-group
`resource_name`, or the synthesized metrics id). OAuth identifies the advertiser, but Google Ads
additionally requires an approved developer token on every request — read from the environment
(`UFO_GOOGLE_ADS_DEVELOPER_TOKEN`, or `GOOGLE_ADS_DEVELOPER_TOKEN`); absent, the stream is
skipped rather than failed. A refusal (HTTP 401/403) raises `StreamSkipped`. The credential is
resolved through the auth proxy the runner threads — this connector holds no OAuth token. The write
path (campaign/ad mutations) is intentionally absent — the source seam only reads."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, dict_or_empty

GOOGLE_ADS_VERSION = "v24"
_REFUSAL_STATUS = frozenset({401, 403})

CUSTOMERS = StreamSpec(name="customers", source_object="customer", primary_key="id")
CAMPAIGNS = StreamSpec(
    name="campaigns",
    source_object="campaign",
    primary_key="resource_name",
    cursor_field="updated_at",
)
AD_GROUPS = StreamSpec(
    name="ad_groups",
    source_object="ad_group",
    primary_key="resource_name",
    canonical=False,
)
ADS = StreamSpec(
    name="ads",
    source_object="ad_group_ad",
    primary_key="resource_name",
    canonical=False,
)
CAMPAIGN_METRICS = StreamSpec(
    name="campaign_metrics",
    source_object="campaign_metrics",
    primary_key="id",
    cursor_field="date",
    canonical=False,
)
CUSTOMER_CLIENTS = StreamSpec(
    name="customer_clients",
    source_object="customer_client",
    primary_key="resource_name",
    canonical=False,
)

ALL_STREAMS = [CUSTOMERS, CAMPAIGNS, AD_GROUPS, ADS, CAMPAIGN_METRICS, CUSTOMER_CLIENTS]


class GoogleAdsConnector(RestConnector):
    name = "googleads"
    base_url = f"https://googleads.googleapis.com/{GOOGLE_ADS_VERSION}"
    streams_list = ALL_STREAMS

    def _developer_token(self) -> str:
        token = os.getenv("UFO_GOOGLE_ADS_DEVELOPER_TOKEN") or os.getenv(
            "GOOGLE_ADS_DEVELOPER_TOKEN"
        )
        if not token:
            raise StreamSkipped(
                "googleads requires UFO_GOOGLE_ADS_DEVELOPER_TOKEN "
                "(Google Ads OAuth alone is not sufficient)"
            )
        return token

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["developer-token"] = self._developer_token()
        login_customer_id = os.getenv("UFO_GOOGLE_ADS_LOGIN_CUSTOMER_ID") or os.getenv(
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID"
        )
        if login_customer_id:
            client.headers["login-customer-id"] = login_customer_id.replace("-", "")
        return client

    async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]:
        data = await self._get(client, "/customers:listAccessibleCustomers")
        names = data.get("resourceNames") if isinstance(data, dict) else []
        out: list[str] = []
        for name in names if isinstance(names, list) else []:
            if isinstance(name, str) and name.startswith("customers/"):
                out.append(name.rsplit("/", 1)[-1])
        return out

    async def _search_stream(
        self,
        client: httpx.AsyncClient,
        customer_id: str,
        query: str,
    ) -> list[dict[str, Any]]:
        response = await self._post_raw(
            client,
            f"/customers/{customer_id}/googleAds:searchStream",
            json={"query": query},
        )
        batches = response.json() if response.content else []
        rows: list[dict[str, Any]] = []
        for batch in batches if isinstance(batches, list) else []:
            if not isinstance(batch, dict):
                continue
            for row in batch.get("results") or []:
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    async def _query_each_customer(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for customer_id in await self._customer_ids(client):
            rows = await self._search_stream(client, customer_id, query)
            if rows:
                yield [{**row, "customer_id": customer_id} for row in rows]

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "customers":
                query = (
                    "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
                    "customer.time_zone, customer.manager, customer.status FROM customer"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            if stream.name == "campaigns":
                query = (
                    "SELECT campaign.resource_name, campaign.id, campaign.name, "
                    "campaign.status, campaign.advertising_channel_type, "
                    "campaign.start_date, campaign.end_date FROM campaign"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            if stream.name == "ad_groups":
                query = (
                    "SELECT ad_group.resource_name, ad_group.id, ad_group.name, "
                    "ad_group.status, campaign.id, campaign.resource_name FROM ad_group"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            if stream.name == "ads":
                query = (
                    "SELECT ad_group_ad.resource_name, ad_group_ad.ad.id, "
                    "ad_group_ad.status, ad_group.id, campaign.id FROM ad_group_ad"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            if stream.name == "campaign_metrics":
                since = cursor[:10] if cursor else (datetime.now(UTC) - timedelta(days=90)).date()
                query = (
                    "SELECT segments.date, campaign.id, campaign.resource_name, "
                    "metrics.impressions, metrics.clicks, metrics.cost_micros, "
                    "metrics.conversions, metrics.ctr, metrics.average_cpc "
                    f"FROM campaign WHERE segments.date >= '{since}'"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            if stream.name == "customer_clients":
                query = (
                    "SELECT customer_client.resource_name, customer_client.id, "
                    "customer_client.descriptive_name, customer_client.manager, "
                    "customer_client.status FROM customer_client"
                )
                async for page in self._query_each_customer(client, query):
                    yield page
                return
            raise StreamSkipped(f"googleads stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"googleads: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks scope or the developer token is not approved"
                ) from error
            raise

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "customers":
            customer = dict_or_empty(record.get("customer"))
            return {
                **record,
                "id": str(customer.get("id") or record.get("customer_id")),
                "name": customer.get("descriptiveName") or customer.get("descriptive_name"),
            }
        if stream.name == "campaigns":
            campaign = dict_or_empty(record.get("campaign"))
            return {
                **record,
                "resource_name": campaign.get("resourceName") or campaign.get("resource_name"),
                "name": campaign.get("name"),
                "status": campaign.get("status"),
                "created_at": campaign.get("startDate") or campaign.get("start_date"),
            }
        if stream.name == "campaign_metrics":
            campaign = dict_or_empty(record.get("campaign"))
            segments = dict_or_empty(record.get("segments"))
            metrics = dict_or_empty(record.get("metrics"))
            date = segments.get("date")
            return {
                **record,
                "id": f"{record.get('customer_id')}:{campaign.get('id')}:{date}",
                "date": date,
                "campaign_id": campaign.get("id"),
                "impressions": metrics.get("impressions"),
                "clicks": metrics.get("clicks"),
                "cost_micros": metrics.get("costMicros") or metrics.get("cost_micros"),
            }
        return record
