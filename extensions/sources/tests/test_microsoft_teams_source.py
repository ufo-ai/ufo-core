"""Microsoft Teams connector over a mock transport: the Graph OData fan-out
(`/me/joinedTeams` → `/teams/{id}/channels` → `.../messages`), the `lastModifiedDateTime` watermark,
the `render` override that lifts a message's HTML `body.content` into readable text, and the
`StreamSkipped` a refused enumeration raises. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.microsoft_teams import MicrosoftTeamsConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=MicrosoftTeamsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


TEAM = {"id": "team1", "displayName": "Engineering"}
CHANNEL = {"id": "chan1", "displayName": "General"}
MESSAGE = {
    "id": "msg1",
    "subject": "Launch",
    "createdDateTime": "2026-01-01T00:00:00Z",
    "lastModifiedDateTime": "2026-02-01T00:00:00Z",
    "body": {"contentType": "html", "content": "<p>Ship the launch <b>Friday</b></p>"},
}


def _teams_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.microsoft.com"
        path = request.url.path
        if path == "/v1.0/me/joinedTeams":
            return httpx.Response(200, json={"value": [TEAM]})
        if path == "/v1.0/teams/team1/channels":
            return httpx.Response(200, json={"value": [CHANNEL]})
        if path == "/v1.0/teams/team1/channels/chan1/messages":
            return httpx.Response(200, json={"value": [MESSAGE]})
        return httpx.Response(404, json={"path": path})

    return handle


async def test_channel_messages_fan_out_watermark_and_render() -> None:
    result = await _fetch("channel_messages", _teams_handler())

    assert {page.source_ref for page in result.pages} == {"channel_messages/msg1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"

    body = result.pages[0].body
    assert body.startswith("# microsoft_teams channel_messages: Launch")
    assert "Ship the launch" in body
    assert "Friday" in body
    assert "<p>" not in body
    assert "contentType" not in body


async def test_channel_messages_incremental_filters_past_watermark() -> None:
    result = await _fetch("channel_messages", _teams_handler(), cursor="2026-03-01T00:00:00Z")
    assert result.pages == ()


async def test_teams_structural_stream_uses_default_render() -> None:
    result = await _fetch("teams", _teams_handler())
    assert {page.source_ref for page in result.pages} == {"teams/team1"}
    assert "Engineering" in result.pages[0].body


async def test_stream_skipped_when_enumeration_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "Forbidden"}})

    with pytest.raises(StreamSkipped):
        await _fetch("channel_messages", handle)
