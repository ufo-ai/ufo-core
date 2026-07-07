"""The Xero connector — the accounting resource surface synced as recallable pages.

Xero wraps each list response in a resource-plural envelope (`/Accounts` → `{"Accounts": [...]}`)
and pages the resources that support it by `?page=N` (100 rows a page) until a short page; a handful
of small collections ignore `page` and ship in one shot. Incremental sync rides an
`If-Modified-Since` (RFC 1123) request header, not a query param, so `paginate` issues each GET
directly with that header set. Each record carries its id under a typed key (`AccountID`,
`InvoiceID`, …), so `flatten` lifts it to the `id` the streams key on. A refusal (401/403) raises
`StreamSkipped`. Xero requires a `xero-tenant-id` header (one org within a grant); `_make_client`
sets it when the connector was built with a tenant. The credential is resolved through the proxy
the runner threads; this connector holds no token. The write path is intentionally absent — the
source seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

_ID_FIELD_OVERRIDES: dict[str, str] = {
    "accounts": "AccountID",
    "bank_transactions": "BankTransactionID",
    "bank_transfers": "BankTransferID",
    "branding_themes": "BrandingThemeID",
    "contact_groups": "ContactGroupID",
    "contacts": "ContactID",
    "credit_notes": "CreditNoteID",
    "currencies": "Code",
    "employees": "EmployeeID",
    "invoices": "InvoiceID",
    "items": "ItemID",
    "manual_journals": "ManualJournalID",
    "organisations": "OrganisationID",
    "overpayments": "OverpaymentID",
    "payments": "PaymentID",
    "prepayments": "PrepaymentID",
    "purchase_orders": "PurchaseOrderID",
    "repeating_invoices": "RepeatingInvoiceID",
    "tax_rates": "TaxType",
    "tracking_categories": "TrackingCategoryID",
    "users": "UserID",
}

_NON_PAGED_STREAMS = frozenset(
    {
        "branding_themes",
        "contact_groups",
        "currencies",
        "organisations",
        "tax_rates",
        "tracking_categories",
        "users",
        "repeating_invoices",
    }
)


def _stream(
    name: str,
    *,
    source_object: str,
    cursor_field: str | None = "UpdatedDateUTC",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object,
        primary_key="id",
        cursor_field=cursor_field,
        canonical=canonical,
    )


XERO_STREAMS: list[StreamSpec] = [
    _stream("accounts", source_object="Accounts", canonical=True),
    _stream("contacts", source_object="Contacts", canonical=True),
    _stream("invoices", source_object="Invoices", canonical=True),
    _stream("payments", source_object="Payments", canonical=True),
    _stream("manual_journals", source_object="ManualJournals", canonical=True),
    _stream("bank_transactions", source_object="BankTransactions"),
    _stream("bank_transfers", source_object="BankTransfers"),
    _stream("branding_themes", source_object="BrandingThemes", cursor_field=None),
    _stream("contact_groups", source_object="ContactGroups", cursor_field=None),
    _stream("credit_notes", source_object="CreditNotes"),
    _stream("currencies", source_object="Currencies", cursor_field=None),
    _stream("employees", source_object="Employees"),
    _stream("items", source_object="Items"),
    _stream("organisations", source_object="Organisation", cursor_field=None),
    _stream("overpayments", source_object="Overpayments"),
    _stream("prepayments", source_object="Prepayments"),
    _stream("purchase_orders", source_object="PurchaseOrders"),
    _stream("repeating_invoices", source_object="RepeatingInvoices", cursor_field=None),
    _stream("tax_rates", source_object="TaxRates", cursor_field=None),
    _stream("tracking_categories", source_object="TrackingCategories", cursor_field=None),
    _stream("users", source_object="Users"),
]


def _cursor_to_rfc1123(cursor: str | None) -> str | None:
    if not cursor:
        return None
    text = str(cursor).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
        except (ValueError, OverflowError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


class XeroConnector(RestConnector):
    name = "xero"
    base_url = "https://api.xero.com/api.xro/2.0"
    streams_list = XERO_STREAMS

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        if self._tenant_id:
            client.headers["xero-tenant-id"] = self._tenant_id
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/{stream.source_object}"
        envelope = stream.source_object
        headers: dict[str, str] = {}
        if stream.cursor_field and cursor:
            modified_since = _cursor_to_rfc1123(cursor)
            if modified_since:
                headers["If-Modified-Since"] = modified_since
        try:
            if stream.name in _NON_PAGED_STREAMS:
                response = await client.get(path, headers=headers or None)
                response.raise_for_status()
                records = (response.json() or {}).get(envelope) or []
                if records:
                    yield records
                return
            page = 1
            while True:
                response = await client.get(path, params={"page": page}, headers=headers or None)
                response.raise_for_status()
                records = (response.json() or {}).get(envelope) or []
                if not records:
                    return
                yield records
                if len(records) < PAGE_SIZE:
                    return
                page += 1
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"xero: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if "id" in record:
            return record
        id_field = _ID_FIELD_OVERRIDES.get(stream.name)
        if id_field is None:
            return record
        value = record.get(id_field)
        if value is None:
            return record
        return {**record, "id": str(value)}
