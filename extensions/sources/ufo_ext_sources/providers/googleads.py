"""The Google Ads connector — advertiser accounts, campaigns, ad groups, ads, and campaign metrics
synced into recallable pages over the Google Ads Query Language.

Google Ads speaks GAQL over HTTP: every stream is a `SELECT ... FROM <resource>` posted to
`/customers/{id}/googleAds:searchStream`, which answers with a top-level array of result batches.
`paginate` first lists the accessible customer ids (`/customers:listAccessibleCustomers`), then runs
the stream's query against each, stamping `customer_id` onto every row; `flatten` lifts the nested
GAQL object into the flat record the sync keys by (the customer id, a campaign/ad-group
`resource_name`, or the synthesized metrics id). OAuth alone authorizes a request: Google Ads
grants API access to the Cloud project that owns the OAuth client, and ignores a `developer-token`
header. A refusal (HTTP 401/403) raises `StreamSkipped`. The credential is
resolved through the auth proxy the runner threads — this connector holds no OAuth token. The write
path (campaign/ad mutations) is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.credentials import deploy_env
from ufo.sdk.sources import (
    RestConnector,
    Run,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

GOOGLE_ADS_VERSION = "v24"
LOGIN_CUSTOMER_ID_ENV = "GOOGLE_ADS_LOGIN_CUSTOMER_ID"
_REFUSAL_STATUS = frozenset({401, 403})

CUSTOMERS = StreamSpec(name="customers", source_object="customer", primary_key="id")
CAMPAIGNS = StreamSpec(
    name="campaigns",
    source_object="campaign",
    primary_key="resource_name",
    canonical=True,
)
AD_GROUPS = StreamSpec(
    name="ad_groups",
    source_object="ad_group",
    primary_key="resource_name",
)
ADS = StreamSpec(
    name="ads",
    source_object="ad_group_ad",
    primary_key="resource_name",
    canonical=True,
)
CAMPAIGN_METRICS = StreamSpec(
    name="campaign_metrics",
    source_object="campaign_metrics",
    primary_key="id",
    cursor_field="date",
    created_at_field="date",
    updated_at_field=None,
)
CUSTOMER_CLIENTS = StreamSpec(
    name="customer_clients",
    source_object="customer_client",
    primary_key="resource_name",
)

ALL_STREAMS = [CUSTOMERS, CAMPAIGNS, AD_GROUPS, ADS, CAMPAIGN_METRICS, CUSTOMER_CLIENTS]


class GoogleAdsConnector(RestConnector):
    name = "googleads"
    base_url = f"https://googleads.googleapis.com/{GOOGLE_ADS_VERSION}"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        login_customer_id = deploy_env(LOGIN_CUSTOMER_ID_ENV)
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
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
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
                since = (
                    run.cursor[:10]
                    if run.cursor
                    else (datetime.now(UTC) - timedelta(days=90)).date()
                )
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
                    "the grant lacks scope or the Cloud project lacks Google Ads API access"
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
