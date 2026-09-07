"""The QuickBooks Online connector — AR/AP and general-ledger entities synced as recallable pages.

QBO has no per-entity list endpoint: every read is a SQL-like query against `/query`
(`SELECT * FROM <Entity> [WHERE MetaData.LastUpdatedTime > '<cursor>' ORDER BY
MetaData.LastUpdatedTime] STARTPOSITION <N> MAXRESULTS 100`), and the response wraps the rows under
`QueryResponse.<Entity>`. `paginate` advances `STARTPOSITION` by the page size until a short page.
An
incremental stream rides the `WHERE` clause on `Metadata.LastUpdatedTime`; the reference entities
without that cursor (`payment_methods`, `tax_agencies`) full-refresh. Because the cursor is nested
(`MetaData.LastUpdatedTime`), `flatten` lifts it to a key so the provider computes the watermark
over it. `Id` is the primary key for every entity. A refusal (401/403) raises `StreamSkipped`. The
credential is resolved through the auth proxy the runner threads; this connector holds no token.

QBO addresses one company file per request: every path sits under `/v3/company/<realmId>`, so the
whole address, company included, is a per-tenant host the source row carries. Intuit issues that
company id on the consent leg and no broker exposes it, so the registering member names it and
`/v3/company/<digits>` is the rule that validates it. The write path is intentionally absent — the
source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, get_path
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
CURSOR_FIELD = "MetaData.LastUpdatedTime"
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(
    name: str,
    *,
    source_object: str,
    cursor_field: str | None = CURSOR_FIELD,
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object,
        primary_key="Id",
        cursor_field=cursor_field,
        created_at_field="MetaData.CreateTime",
        updated_at_field=CURSOR_FIELD,
        canonical=canonical,
    )


QUICKBOOKS_STREAMS: list[StreamSpec] = [
    _stream("accounts", source_object="Account"),
    _stream("customers", source_object="Customer", canonical=True),
    _stream("vendors", source_object="Vendor", canonical=True),
    _stream("invoices", source_object="Invoice", canonical=True),
    _stream("bills", source_object="Bill", canonical=True),
    _stream("payments", source_object="Payment", canonical=True),
    _stream("bill_payments", source_object="BillPayment", canonical=True),
    _stream("journal_entries", source_object="JournalEntry", canonical=True),
    _stream("purchases", source_object="Purchase", canonical=True),
    _stream("budgets", source_object="Budget"),
    _stream("classes", source_object="Class"),
    _stream("credit_memos", source_object="CreditMemo", canonical=True),
    _stream("departments", source_object="Department"),
    _stream("deposits", source_object="Deposit", canonical=True),
    _stream("employees", source_object="Employee"),
    _stream("estimates", source_object="Estimate", canonical=True),
    _stream("items", source_object="Item"),
    _stream("payment_methods", source_object="PaymentMethod", cursor_field=None),
    _stream("purchase_orders", source_object="PurchaseOrder", canonical=True),
    _stream("refund_receipts", source_object="RefundReceipt", canonical=True),
    _stream("sales_receipts", source_object="SalesReceipt", canonical=True),
    _stream("tax_agencies", source_object="TaxAgency", cursor_field=None),
    _stream("tax_codes", source_object="TaxCode"),
    _stream("tax_rates", source_object="TaxRate"),
    _stream("terms", source_object="Term"),
    _stream("time_activities", source_object="TimeActivity", canonical=True),
    _stream("transfers", source_object="Transfer", canonical=True),
    _stream("vendor_credits", source_object="VendorCredit", canonical=True),
]


class QuickBooksConnector(RestConnector):
    name = "quickbooks"
    base_url = ""
    streams_list = QUICKBOOKS_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    @staticmethod
    def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str:
        parts = [f"SELECT * FROM {stream.source_object}"]
        if cursor and stream.cursor_field:
            safe = cursor.replace("'", "''")
            parts.append(f"WHERE {stream.cursor_field} > '{safe}'")
            parts.append(f"ORDER BY {stream.cursor_field}")
        parts.append(f"STARTPOSITION {start_position} MAXRESULTS {PAGE_SIZE}")
        return " ".join(parts)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        entity = stream.source_object
        start_position = 1
        try:
            while True:
                query = self._build_query(stream, cursor=cursor, start_position=start_position)
                data = await self._get(client, "/query", params={"query": query})
                envelope = data.get("QueryResponse") or {}
                records = envelope.get(entity) or []
                if records:
                    yield records
                if len(records) < PAGE_SIZE:
                    return
                start_position += PAGE_SIZE
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"quickbooks: {stream.name!r} refused "
                    f"({error.response.status_code}); the grant lacks scope or the key is invalid"
                ) from error
            raise

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.cursor_field and "." in stream.cursor_field:
            return {**record, stream.cursor_field: get_path(record, stream.cursor_field)}
        return record
