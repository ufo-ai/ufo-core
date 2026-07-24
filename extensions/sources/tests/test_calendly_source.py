"""The Calendly connector over a mock transport: the org-scoped fan-out (read `/users/me` for
`current_organization`, then filter every collection by it), the `collection` /
`pagination.next_page_token` envelope, the incremental `updated_since` filter, and the
`StreamSkipped` raised when the account exposes no organization. Offline — a canned transport, no
DB, no token, no broker."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.calendly import CalendlyConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
ORG = "https://api.calendly.com/organizations/ORG1"


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
    return await ConnectorBackend(connector=CalendlyConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _me() -> dict[str, object]:
    return {"resource": {"uri": "u1", "name": "Ada", "current_organization": ORG}}


async def test_api_user_reads_the_current_user() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.calendly.com"
        assert request.url.path == "/users/me"
        return httpx.Response(200, json=_me())

    result = await _fetch("api_user", handle)
    assert {page.source_ref for page in result.pages} == {"api_user/u1"}
    assert "Ada" in result.pages[0].body


async def test_event_types_scope_to_org_thread_updated_since_and_advance_watermark() -> None:
    seen_updated_since: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        assert request.url.path == "/event_types"
        assert request.url.params.get("organization") == ORG
        seen_updated_since.append(request.url.params.get("updated_since"))
        return httpx.Response(
            200,
            json={
                "collection": [{"uri": "et1", "name": "Intro", "updated_at": "2026-02-05"}],
                "pagination": {"next_page_token": None},
            },
        )

    result = await _fetch("event_types", handle, cursor="2026-02-01")
    assert seen_updated_since == ["2026-02-01"]
    assert {page.source_ref for page in result.pages} == {"event_types/et1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05"


async def test_scheduled_events_flatten_derives_title_times_and_location() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        assert request.url.path == "/scheduled_events"
        return httpx.Response(
            200,
            json={
                "collection": [
                    {
                        "uri": "ev1",
                        "name": "Standup",
                        "description": "Daily sync",
                        "start_time": "2026-02-05T09:00:00Z",
                        "end_time": "2026-02-05T09:15:00Z",
                        "location": {"type": "zoom", "location": "https://zoom.us/j/1"},
                    }
                ],
                "pagination": {"next_page_token": None},
            },
        )

    record = _flat(await _fetch("scheduled_events", handle), "scheduled_events/ev1")
    assert record["title"] == "Standup"
    assert record["start_at"] == "2026-02-05T09:00:00Z"
    assert record["end_at"] == "2026-02-05T09:15:00Z"
    assert record["location"] == "https://zoom.us/j/1"


async def test_event_types_flatten_derives_api_url() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        return httpx.Response(
            200,
            json={
                "collection": [{"uri": "et1", "name": "Intro", "created_at": "2026-01-01"}],
                "pagination": {"next_page_token": None},
            },
        )

    record = _flat(await _fetch("event_types", handle), "event_types/et1")
    assert record["api_url"] == "et1"
    assert record["name"] == "Intro"


async def test_organization_memberships_keep_only_user_name_and_email() -> None:
    async def fetch(user_updated_at: str) -> SyncResult:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/users/me":
                return httpx.Response(200, json=_me())
            assert request.url.path == "/organization_memberships"
            return httpx.Response(
                200,
                json={
                    "collection": [
                        {
                            "uri": "om1",
                            "created_at": "2026-01-03",
                            "user": {
                                "name": "Ada Lovelace",
                                "email": "ada@example.com",
                                "avatar_url": "https://example.com/avatar.png",
                                "updated_at": user_updated_at,
                            },
                        }
                    ],
                    "pagination": {"next_page_token": None},
                },
            )

        return await _fetch("organization_memberships", handle)

    first = await fetch("2026-01-01")
    profile_changed = await fetch("2026-01-02")
    record = _flat(first, "organization_memberships/om1")
    assert record["name"] == "Ada Lovelace"
    assert record["email"] == "ada@example.com"
    assert "user" not in record
    assert first.pages[0].digest == profile_changed.pages[0].digest


async def test_missing_organization_skips_the_stream() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/me"
        return httpx.Response(200, json={"resource": {"uri": "u1", "name": "Ada"}})

    with pytest.raises(StreamSkipped):
        await _fetch("scheduled_events", handle)
