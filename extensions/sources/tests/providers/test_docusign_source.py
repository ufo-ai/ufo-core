"""The DocuSign connector over a mock transport: the per-account REST base resolved from
`/oauth/userinfo`, the required `from_date` floor, `start_position` paging, the `emailSubject`
title, the multi-account fault, and a refusal as `StreamSkipped`. Offline — a canned transport, no
DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.docusign import EPOCH_FROM_DATE, DocuSignConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"
BASE_URI = "https://na3.docusign.net"
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=DocuSignConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _userinfo(*accounts: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"sub": "user-1", "accounts": list(accounts)})


def _one_account() -> dict[str, object]:
    return {"account_id": ACCOUNT_ID, "base_uri": BASE_URI, "is_default": True}


async def test_envelopes_resolve_the_account_base_and_page_by_start_position() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/oauth/userinfo":
            assert request.url.host == "account.docusign.com"
            return _userinfo(_one_account())
        assert request.url.host == "na3.docusign.net"
        assert request.url.path == f"/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes"
        return httpx.Response(
            200,
            json={
                "envelopes": [
                    {
                        "envelopeId": "env-1",
                        "emailSubject": "Master services agreement",
                        "status": "completed",
                        "createdDateTime": "2026-02-01T09:00:00.0000000Z",
                        "statusChangedDateTime": "2026-02-03T09:00:00.0000000Z",
                    }
                ]
            },
        )

    result = await _fetch("envelopes", handle, cursor="2026-01-15T00:00:00Z")

    assert {page.source_ref for page in result.pages} == {"envelopes/env-1"}
    assert [page.title for page in result.pages] == ["Master services agreement"]
    assert result.next_cursor == "2026-02-03T09:00:00.0000000Z"
    assert "from_date=2026-01-15T00%3A00%3A00Z" in seen[1]
    assert "start_position=0" in seen[1]
    assert "include=recipients%2Ccustom_fields" in seen[1]


async def test_a_first_run_asks_from_the_epoch_floor_the_api_requires() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/userinfo":
            return _userinfo(_one_account())
        seen.append(request.url.params.get("from_date", ""))
        return httpx.Response(200, json={"envelopes": []})

    await _fetch("envelopes", handle)
    assert seen == [EPOCH_FROM_DATE]


async def test_several_accounts_and_no_default_is_a_fault_rather_than_a_guess() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return _userinfo(
            {"account_id": "a-1", "base_uri": BASE_URI},
            {"account_id": "a-2", "base_uri": "https://eu.docusign.net"},
        )

    with pytest.raises(StreamFault, match="2 accounts"):
        await _fetch("envelopes", handle)


async def test_the_default_account_is_the_one_synced() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/userinfo":
            return _userinfo(
                {"account_id": "a-1", "base_uri": "https://eu.docusign.net", "is_default": False},
                {"account_id": ACCOUNT_ID, "base_uri": BASE_URI, "is_default": True},
            )
        assert request.url.path.startswith(f"/restapi/v2.1/accounts/{ACCOUNT_ID}/")
        return httpx.Response(200, json={"envelopes": []})

    result = await _fetch("envelopes", handle)
    assert result.pages == ()


async def test_templates_read_their_own_envelope_key() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/userinfo":
            return _userinfo(_one_account())
        assert request.url.path.endswith("/templates")
        assert "from_date" not in request.url.params
        return httpx.Response(
            200,
            json={"envelopeTemplates": [{"templateId": "tpl-1", "name": "NDA"}]},
        )

    result = await _fetch("templates", handle)
    assert {page.source_ref for page in result.pages} == {"templates/tpl-1"}
    assert [page.title for page in result.pages] == ["NDA"]


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/userinfo":
            return _userinfo(_one_account())
        return httpx.Response(401, json={"errorCode": "USER_AUTHENTICATION_FAILED"})

    with pytest.raises(StreamSkipped):
        await _fetch("envelopes", handle)
