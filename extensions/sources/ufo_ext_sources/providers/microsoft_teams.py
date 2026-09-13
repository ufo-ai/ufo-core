"""The Microsoft Teams connector — teams, channels, chats, and their messages synced as recallable
pages over Microsoft Graph.

Teams speaks Microsoft Graph (OData): every collection returns records under `value` and continues
through the absolute `@odata.nextLink`, walked by the shared `_get_odata_pages`. A grant reaches the
signed-in user's joined teams and chats, and Graph publishes a channel only under its team
(`/teams/{id}/channels`) and a message only under its channel or chat.

Graph documents a message id as "unique within a chat/channel/reply-to-message, but might be
duplicated in other chats/channels/reply-to-messages", so `channel_messages` and `chat_messages`
key `local` and a message page is addressed under the parents that hold it. `channels` keys `local`
for the same reason its id is a `19:…@thread.tacv2` handle scoped to one team. Messages are
incremental on `lastModifiedDateTime`, filtered per channel against that channel's own watermark —
the endpoint takes no time bound. `render` lifts a message's `body.content` (Graph stores it as
HTML) into readable text under its subject; the structural streams (teams, channels, chats) use the
default titled-JSON, so a channel's page names the team it belongs to and the edge carries that.
A per-channel or per-chat `403`/`404` drops that partition and the pass keeps going; a grant that
can't enumerate teams or chats at all (`401`/`403` on the first call) yields `StreamSkipped` so the
run records a skip, not a failure. The credential is resolved through the auth proxy the runner
threads — this connector holds no token. The write path is intentionally absent — the source seam
only reads."""

import re
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    Ordering,
    ParentEdge,
    Partition,
    PartitionBound,
    PartitionSkipped,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
    get_path,
)
from ufo_ext_sources.watermark import text_checkpoint

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_REFUSAL_STATUS = frozenset({401, 403})
_PARTITION_SKIP_STATUS = frozenset({403, 404})
MESSAGE_PAGE_SIZE = 50
LISTING_PAGE_SIZE = 100

TEAMS = StreamSpec(name="teams", source_object="joinedTeams", primary_key="id", canonical=True)
CHANNELS = StreamSpec(
    name="channels",
    source_object="channels",
    primary_key="id",
    canonical=True,
    parents=(
        ParentEdge(
            stream="teams",
            path="/teams/{id}/channels",
            carry={"team_id": "id", "team_name": "displayName"},
        ),
    ),
)
CHANNEL_MESSAGES = StreamSpec(
    name="channel_messages",
    source_object="messages",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    canonical=True,
    ordering=Ordering.ascending,
    parents=(ParentEdge(stream="channels", path="/teams/{team_id}/channels/{id}/messages"),),
)
CHATS = StreamSpec(name="chats", source_object="chats", primary_key="id", canonical=True)
CHAT_MESSAGES = StreamSpec(
    name="chat_messages",
    source_object="messages",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    ordering=Ordering.ascending,
    parents=(ParentEdge(stream="chats", path="/me/chats/{id}/messages"),),
)

ALL_STREAMS = [TEAMS, CHANNELS, CHANNEL_MESSAGES, CHATS, CHAT_MESSAGES]


class MicrosoftTeamsConnector(RestConnector):
    name = "microsoft_teams"
    base_url = "https://graph.microsoft.com/v1.0"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name in {"teams", "chats"}:
                path = "/me/joinedTeams" if stream.name == "teams" else "/me/chats"
                async for records in self._get_odata_pages(
                    client, path, params={"$top": LISTING_PAGE_SIZE}
                ):
                    yield records
                return
            pages = self._channel_pages if stream.name == "channels" else self._message_pages
            async for page in fanned_out(stream, run, partial(pages, client, stream)):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"microsoft_teams: {stream.name!r} refused "
                    f"({error.response.status_code}); the grant lacks the Microsoft Graph scope"
                ) from error
            raise

    async def _channel_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        async for channels in self._partition_pages(client, partition, LISTING_PAGE_SIZE):
            yield WalkPage(records=channels)

    async def _message_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One channel's or chat's messages. Graph takes no time bound on either endpoint, so the
        walk's resume filters the page against that partition's own watermark."""
        field = stream.cursor_field or ""
        async for messages in self._partition_pages(client, partition, MESSAGE_PAGE_SIZE):
            kept = (
                [m for m in messages if str(m.get(field) or "") > bound.after]
                if bound.after
                else messages
            )
            if not kept:
                continue
            stamps = [value for m in kept if isinstance(value := m.get(field), str)]
            yield WalkPage(
                records=kept,
                high=max(stamps) if stamps else None,
                low=min(stamps) if stamps else None,
            )

    async def _partition_pages(
        self, client: httpx.AsyncClient, partition: Partition, page_size: int
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            async for records in self._get_odata_pages(
                client, partition.path, params={"$top": page_size}
            ):
                yield records
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _PARTITION_SKIP_STATUS:
                raise PartitionSkipped(f"microsoft_teams: {partition.path} refused") from error
            raise

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name not in {"channel_messages", "chat_messages"}:
            return super().render(record, stream)
        title = _str(record.get("subject"))
        body = _strip_html(get_path(record, "body.content")) or ""
        heading = f"# microsoft_teams {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _strip_html(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _HTML_TAG_RE.sub(" ", value).strip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
