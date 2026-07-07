"""The Salesforce connector — the standard CRM SObject surface synced as recallable pages.

Salesforce reads through SOQL Query REST: `paginate` describes the SObject to learn its full field
list (no hand-listed schemas — the sync lands whatever the org exposes), builds
`SELECT <fields> FROM <SObject> [WHERE SystemModstamp > <cursor>] ORDER BY SystemModstamp ASC LIMIT
200`, GETs `/services/data/<v>/query`, and follows the absolute `nextRecordsUrl` while `done` is
false. Once a cursor exists, a second call to `/sobjects/<obj>/deleted/` names the records
hard-deleted since the cursor and carries them as a delete-tombstone page whose `next_cursor` is the
window end — so a vanished record is tombstoned without a full snapshot. `flatten` drops the SObject
`attributes` envelope so only field columns remain. The base URL is per-instance (each org sits on a
different host), so the class default is empty and a run without a resolved instance URL fails loud.
A refusal (401/403) raises `StreamSkipped`. The credential is resolved through the auth proxy the
runner threads; this connector holds no token. The write path is intentionally absent — the source
seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamPage, StreamSkipped, StreamSpec

API_VERSION = "v60.0"
PAGE_LIMIT = 200
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(name: str, *, sobject: str, canonical: bool = True) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=sobject,
        primary_key="Id",
        cursor_field="SystemModstamp",
        canonical=canonical,
    )


SALESFORCE_STREAMS: list[StreamSpec] = [
    _stream("accounts", sobject="Account"),
    _stream("contacts", sobject="Contact"),
    _stream("opportunities", sobject="Opportunity"),
    _stream("tasks", sobject="Task"),
    _stream("leads", sobject="Lead", canonical=False),
    _stream("users", sobject="User", canonical=False),
    _stream("opportunity_line_items", sobject="OpportunityLineItem", canonical=False),
    _stream("opportunity_contact_roles", sobject="OpportunityContactRole", canonical=False),
    _stream("products", sobject="Product2", canonical=False),
    _stream("pricebooks", sobject="Pricebook2", canonical=False),
    _stream("pricebook_entries", sobject="PricebookEntry", canonical=False),
    _stream("quotes", sobject="Quote", canonical=False),
    _stream("quote_line_items", sobject="QuoteLineItem", canonical=False),
    _stream("orders", sobject="Order", canonical=False),
    _stream("order_items", sobject="OrderItem", canonical=False),
    _stream("contracts", sobject="Contract", canonical=False),
    _stream("assets", sobject="Asset", canonical=False),
    _stream("cases", sobject="Case", canonical=False),
    _stream("case_comments", sobject="CaseComment", canonical=False),
    _stream("solutions", sobject="Solution", canonical=False),
    _stream("campaigns", sobject="Campaign", canonical=False),
    _stream("campaign_members", sobject="CampaignMember", canonical=False),
    _stream("events", sobject="Event", canonical=False),
    _stream("email_messages", sobject="EmailMessage", canonical=False),
    _stream("content_notes", sobject="ContentNote", canonical=False),
    _stream("content_documents", sobject="ContentDocument", canonical=False),
    _stream("content_versions", sobject="ContentVersion", canonical=False),
]


class SalesforceConnector(RestConnector):
    name = "salesforce"
    base_url = ""
    streams_list = SALESFORCE_STREAMS

    @staticmethod
    def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str:
        cols = ", ".join(fields)
        where = f" WHERE {stream.cursor_field} > {cursor}" if stream.cursor_field and cursor else ""
        order = f" ORDER BY {stream.cursor_field} ASC" if stream.cursor_field else ""
        return f"SELECT {cols} FROM {stream.source_object}{where}{order} LIMIT {PAGE_LIMIT}"

    async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]:
        data = await self._get(client, f"/services/data/{API_VERSION}/sobjects/{sobject}/describe")
        return [
            str(field["name"])
            for field in (data.get("fields") or [])
            if isinstance(field, dict) and field.get("name")
        ]

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            fields = await self._describe_fields(client, stream.source_object)
            soql = self._build_soql(stream, fields, cursor)
            path: str | None = f"/services/data/{API_VERSION}/query"
            params: dict[str, Any] | None = {"q": soql}
            while path:
                data = await self._get(client, path, params=params)
                records = data.get("records", []) or []
                if records:
                    yield records
                if data.get("done", True):
                    break
                path = data.get("nextRecordsUrl")
                params = None
            if cursor:
                delete_page = await self._deleted_page(client, stream, cursor=cursor)
                if delete_page is not None:
                    yield delete_page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"salesforce: {stream.name!r} refused "
                    f"({error.response.status_code}); the grant lacks scope or the key is invalid"
                ) from error
            raise

    async def _deleted_page(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str
    ) -> StreamPage | None:
        end = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        data = await self._get(
            client,
            f"/services/data/{API_VERSION}/sobjects/{stream.source_object}/deleted/",
            params={"start": cursor, "end": end},
        )
        deletes = tuple(
            str(record["id"])
            for record in data.get("deletedRecords") or []
            if isinstance(record, dict) and record.get("id")
        )
        latest = data.get("latestDateCovered")
        next_cursor = latest if isinstance(latest, str) and latest else end
        if not deletes and not next_cursor:
            return None
        return StreamPage(deletes=deletes, next_cursor=next_cursor)

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if "attributes" in record:
            return {key: value for key, value in record.items() if key != "attributes"}
        return record
