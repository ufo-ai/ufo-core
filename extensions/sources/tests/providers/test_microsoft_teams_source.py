"""Microsoft Teams connector over a mock transport: the channels and messages fanned out over the
pages their parent row landed, the `lastModifiedDateTime` watermark per channel, the `render`
override that lifts a message's HTML `body.content` into readable text, and the `StreamSkipped` a
refused enumeration raises. Graph documents a message id as unique only inside its channel, so both
streams key `local` and a page is addressed under the team and channel that hold it. Offline — a
canned transport, no DB, no token."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.microsoft_teams import MicrosoftTeamsConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]
TEAM = {"id": "team1", "displayName": "Engineering"}
CHANNEL = {"id": "chan1", "displayName": "General"}
MESSAGE = {
    "id": "msg1",
    "subject": "Launch",
    "createdDateTime": "2026-01-01T00:00:00Z",
    "lastModifiedDateTime": "2026-02-01T00:00:00Z",
    "body": {"contentType": "html", "content": "<p>Ship the launch <b>Friday</b></p>"},
}
LANDED: Landed = {
    "teams": (
        ParentRecord(ref="teams/team1", fields={"id": "team1", "displayName": "Engineering"}),
    ),
    "channels": (
        ParentRecord(ref="channels/team1/chan1", fields={"team_id": "team1", "id": "chan1"}),
    ),
}
MESSAGES_KEY = "channels/team1/chan1\n/teams/team1/channels/chan1/messages"
CHANNEL_DIGEST = "sha256:a2066ab4e95cf9e6aebd42ee0d36dea5aa352b03e45d668ef7dd55e6d1e39e1e"
MESSAGE_DIGEST = "sha256:cd0ac22d91e2ea4b173b287a0255e75ae1929ab67b5f8525cc17a25a921f79bf"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    reader: ParentsReader,
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    landed: Landed = LANDED,
):
    auth = SourceAuth(
        workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=reader(landed)
    )
    return await ConnectorBackend(connector=MicrosoftTeamsConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


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


async def test_channel_messages_read_only_the_channel_that_landed(
    parents_reader: ParentsReader,
) -> None:
    """The team and channel listings belong to their own rows, so a messages tick asks one endpoint
    and the message is addressed under both."""
    calls: list[str] = []
    handler = _teams_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    result = await _fetch(parents_reader, "channel_messages", handle)

    assert calls == ["/v1.0/teams/team1/channels/chan1/messages"]
    assert {page.source_ref for page in result.pages} == {"channel_messages/team1/chan1/msg1"}
    assert result.snapshot is False
    assert json.loads(result.next_cursor) == {MESSAGES_KEY: "2026-02-01T00:00:00Z"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"

    body = result.pages[0].body
    assert body.startswith("# microsoft_teams channel_messages: Launch")
    assert "Ship the launch" in body
    assert "Friday" in body
    assert "<p>" not in body
    assert "contentType" not in body


async def test_one_message_id_in_two_channels_lands_two_pages(
    parents_reader: ParentsReader,
) -> None:
    """Graph states a message id is "unique within a chat/channel/reply-to-message, but might be
    duplicated in other chats/channels". Addressed by the id alone, the second channel's message
    overwrites the first and one of them is lost."""
    shared = "1616990032035"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            channel = request.url.path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            **MESSAGE,
                            "id": shared,
                            "subject": f"from {channel}",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    two = {
        "channels": (
            ParentRecord(ref="channels/team1/chan1", fields={"team_id": "team1", "id": "chan1"}),
            ParentRecord(ref="channels/team1/chan2", fields={"team_id": "team1", "id": "chan2"}),
        )
    }
    result = await _fetch(parents_reader, "channel_messages", handle, landed=two)

    assert {page.source_identity for page in result.pages} == {
        f"channel_messages/team1/chan1/{shared}",
        f"channel_messages/team1/chan2/{shared}",
    }


async def test_a_message_page_renders_what_it_rendered_before_the_tree(
    parents_reader: ParentsReader,
) -> None:
    """A message body is its subject and its text, so nothing the old walk stamped reached it and
    the re-keyed page says exactly what it said. The digest pins that."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/teams/team1/channels/chan1/messages":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "1616990032035",
                            "subject": "Deploy window",
                            "body": {"content": "<p>rolling at 4</p>"},
                            "createdDateTime": "2026-09-01T00:00:00Z",
                            "lastModifiedDateTime": "2026-09-01T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "channel_messages", handle)

    assert result.pages[0].digest == MESSAGE_DIGEST


async def test_a_channel_page_renders_what_it_rendered_before_the_tree(
    parents_reader: ParentsReader,
) -> None:
    """A channel renders as titled JSON, so the team id and name the old walk stamped are its body.
    The edge carries both, and the digest pins that it still does."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/teams/t1/channels":
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "c1", "displayName": "General", "webUrl": "https://teams/c1"}]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    landed = {
        "teams": (ParentRecord(ref="teams/t1", fields={"id": "t1", "displayName": "Acme Eng"}),)
    }
    result = await _fetch(parents_reader, "channels", handle, landed=landed)

    assert {page.source_identity for page in result.pages} == {"channels/t1/c1"}
    assert result.pages[0].digest == CHANNEL_DIGEST


async def test_channel_messages_incremental_filters_past_watermark(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch(
        parents_reader,
        "channel_messages",
        _teams_handler(),
        cursor=json.dumps({MESSAGES_KEY: "2026-03-01T00:00:00Z"}),
    )
    assert result.pages == ()


async def test_teams_structural_stream_uses_default_render(parents_reader: ParentsReader) -> None:
    result = await _fetch(parents_reader, "teams", _teams_handler())
    assert {page.source_ref for page in result.pages} == {"teams/team1"}
    assert "Engineering" in result.pages[0].body


async def test_a_channel_the_grant_cannot_read_leaves_the_others_syncing(
    parents_reader: ParentsReader,
) -> None:
    """A per-channel 403 or 404 drops that partition and the pass keeps going, where a raise would
    lose every channel behind it."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chan1/messages"):
            return httpx.Response(404, json={"error": {"code": "NotFound"}})
        if request.url.path.endswith("/chan2/messages"):
            return httpx.Response(200, json={"value": [MESSAGE]})
        return httpx.Response(404, json={"path": request.url.path})

    two = {
        "channels": (
            ParentRecord(ref="channels/team1/chan1", fields={"team_id": "team1", "id": "chan1"}),
            ParentRecord(ref="channels/team1/chan2", fields={"team_id": "team1", "id": "chan2"}),
        )
    }
    result = await _fetch(parents_reader, "channel_messages", handle, landed=two)

    assert {page.source_ref for page in result.pages} == {"channel_messages/team1/chan2/msg1"}


@pytest.mark.parametrize("stream", ["teams", "chats"])
async def test_a_refused_root_listing_is_skipped_rather_than_failed(
    stream: str, parents_reader: ParentsReader
) -> None:
    """A grant with no Teams scope at all is refused on the listing the whole catalog hangs off, so
    that row records a skip. Every stream below it then enumerates no partition and spends no
    request, rather than each raising its own refusal."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "Forbidden"}})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, stream, handle)


def test_the_catalog_hangs_every_message_under_the_channel_that_holds_it() -> None:
    streams = {stream.name: stream for stream in MicrosoftTeamsConnector().streams()}

    assert syncing_streams(list(streams.values())) == {
        "teams",
        "channels",
        "channel_messages",
        "chats",
    }
    assert streams["teams"].parents == ()
    assert streams["channels"].key_scope == "local"
    assert streams["channel_messages"].key_scope == "local"
    assert [(edge.stream, edge.path) for edge in streams["channel_messages"].parents] == [
        ("channels", "/teams/{team_id}/channels/{id}/messages")
    ]
