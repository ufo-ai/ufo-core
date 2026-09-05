"""The DocuSign connector — envelopes and templates synced as recallable pages.

DocuSign publishes no single API host: each account answers on the region host its own base URI
names (`na3.docusign.net`, `eu.docusign.net`, `demo.docusign.net`). So the class host is the
identity host every account shares, and each run resolves the account's REST base for itself — `GET
/oauth/userinfo` returns the grant's `accounts[]`, each with an `account_id` and a `base_uri`, and
the run addresses `{base_uri}/restapi/v2.1/accounts/{account_id}/` from there. The default account
(or the sole one) is the account synced; a grant naming several accounts and no default raises
`StreamFault` rather than guess which company's envelopes land in memory.

Both streams page the same way: `?start_position=N&count=100` until the returned set is short.
`envelopes` is incremental over `statusChangedDateTime` and filtered server-side by the `from_date`
the API requires — a cursor-less first run asks from `EPOCH_FROM_DATE`, so it lands the account's
whole envelope history rather than a silent recent slice. Recipients and custom fields ride the list
response (`include`), so an envelope page recalls with who signed it. An envelope's title is its
`emailSubject`, which no title-like key carries, so `render` names it.

DocuSign Connect would push these events instantly with an HMAC-signed POST. The source seam is
polled — core drives `fetch` on the sync interval and no surface receives provider callbacks — so
this connector polls. A refusal (401/403) raises `StreamSkipped`. The credential is resolved through
the auth proxy the runner threads; this connector holds no token. The write path is intentionally
absent — the source seam only reads."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamFault,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

IDENTITY_HOST = "https://account.docusign.com"
USERINFO_PATH = "/oauth/userinfo"
PAGE_SIZE = 100
EPOCH_FROM_DATE = "2000-01-01T00:00:00Z"
_REFUSAL_STATUS = frozenset({401, 403})
_ENVELOPE_INCLUDE = "recipients,custom_fields"

# Stream-name → (path under the account base, response envelope key).
_LIST_PATHS: dict[str, tuple[str, str]] = {
    "envelopes": ("envelopes", "envelopes"),
    "templates": ("templates", "envelopeTemplates"),
}


DOCUSIGN_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="envelopes",
        source_object="envelopes",
        primary_key="envelopeId",
        cursor_field="statusChangedDateTime",
        created_at_field="createdDateTime",
        updated_at_field="statusChangedDateTime",
        canonical=True,
    ),
    StreamSpec(
        name="templates",
        source_object="templates",
        primary_key="templateId",
        cursor_field=None,
        created_at_field="created",
        updated_at_field="lastModified",
        canonical=False,
    ),
]


class DocuSignConnector(RestConnector):
    name = "docusign"
    base_url = IDENTITY_HOST
    streams_list = DOCUSIGN_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def _account_base(self, client: httpx.AsyncClient) -> str:
        """The account-and-region REST base every data call is issued against, resolved from the
        grant itself. The sole account, else the one flagged default; several accounts and no
        default is a fault, because either choice would silently sync one company's envelopes as
        the workspace's."""
        info = await self._get(client, USERINFO_PATH)
        accounts = list_or_empty(info.get("accounts"))
        usable = [
            account
            for account in accounts
            if isinstance(account.get("account_id"), str)
            and isinstance(account.get("base_uri"), str)
            and account["account_id"]
            and account["base_uri"]
        ]
        if not usable:
            raise StreamFault("docusign: the grant names no account with a base URI")
        if len(usable) == 1:
            chosen = usable[0]
        else:
            defaults = [
                account for account in usable if account.get("is_default") in (True, "true", "True")
            ]
            if len(defaults) != 1:
                named = ", ".join(sorted(account["account_id"] for account in usable))
                raise StreamFault(
                    f"docusign: the grant names {len(usable)} accounts and no single default "
                    f"({named}); reconnect the one account to sync"
                )
            chosen = defaults[0]
        return f"{chosen['base_uri'].rstrip('/')}/restapi/v2.1/accounts/{chosen['account_id']}"

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        route = _LIST_PATHS.get(stream.name)
        if route is None:
            raise NotImplementedError(f"docusign: no list endpoint for stream {stream.name!r}")
        path, key = route
        try:
            base = await self._account_base(client)
            start_position = 0
            while True:
                params: dict[str, Any] = {
                    "count": PAGE_SIZE,
                    "start_position": start_position,
                }
                if stream.name == "envelopes":
                    params["from_date"] = cursor or EPOCH_FROM_DATE
                    params["include"] = _ENVELOPE_INCLUDE
                    params["order"] = "asc"
                data = await self._get(client, f"{base}/{path}", params=params)
                records = list_or_empty(data.get(key))
                if records:
                    yield records
                if len(records) < PAGE_SIZE:
                    return
                start_position += PAGE_SIZE
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"docusign: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the signature scope or the account admits no envelope read"
                ) from error
            raise

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        """An envelope recalls by the subject its recipients saw; DocuSign carries that as
        `emailSubject`, which no title-like key names, so it is titled here."""
        subject = record.get("emailSubject")
        if stream.name != "envelopes" or not isinstance(subject, str) or not subject:
            return super().render(record, stream)
        return (
            subject,
            f"# docusign {stream.name}: {subject}\n\n{json.dumps(record, sort_keys=True)}",
        )
