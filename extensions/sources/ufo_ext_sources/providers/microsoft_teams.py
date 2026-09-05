"""The Microsoft Teams connector — teams, channels, chats, and their messages synced as recallable
pages over Microsoft Graph.

Teams speaks Microsoft Graph (OData): every collection returns records under `value` and continues
through the absolute `@odata.nextLink`, walked by the shared `_get_odata_pages`. A grant reaches the
signed-in user's joined teams and chats, so the connector fans out — `/me/joinedTeams` then each
team's `/channels` and each channel's `/messages`, and `/me/chats` then each chat's `/messages`.
Channel and chat messages are incremental: each record's `lastModifiedDateTime` is filtered past the
stored watermark, which the provider computes. `render` lifts a message's `body.content` (Graph
stores it as HTML) into readable text under its subject; the structural streams (teams, channels,
chats) use the default titled-JSON. A per-team or per-chat `403`/`404` is skipped so the other
parents still sync; a grant that can't enumerate teams/chats at all (`401`/`403` on the first call)
yields `StreamSkipped` so the run records a skip, not a failure. The credential is resolved through
the auth proxy the runner threads — this connector holds no token. The write path is intentionally
absent — the source seam only reads."""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, get_path, with_context
from ufo_ext_sources.watermark import text_checkpoint

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_REFUSAL_STATUS = frozenset({401, 403})
_PARENT_SKIP_STATUS = frozenset({403, 404})

TEAMS = StreamSpec(name="teams", source_object="joinedTeams", primary_key="id")
CHANNELS = StreamSpec(name="channels", source_object="channels", primary_key="id")
CHANNEL_MESSAGES = StreamSpec(
    name="channel_messages",
    source_object="messages",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
)
CHATS = StreamSpec(name="chats", source_object="chats", primary_key="id", canonical=False)
CHAT_MESSAGES = StreamSpec(
    name="chat_messages",
    source_object="messages",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    canonical=False,
)

ALL_STREAMS = [TEAMS, CHANNELS, CHANNEL_MESSAGES, CHATS, CHAT_MESSAGES]


class MicrosoftTeamsConnector(RestConnector):
    name = "microsoft_teams"
    base_url = "https://graph.microsoft.com/v1.0"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self._get_odata_pages(client, "/me/joinedTeams", params={"$top": 100}):
            out.extend(page)
        return out

    async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        for team in await self._teams(client):
            team_id = team.get("id")
            if not isinstance(team_id, str) or not team_id:
                continue
            try:
                async for channels in self._get_odata_pages(
                    client, f"/teams/{team_id}/channels", params={"$top": 100}
                ):
                    yield with_context(channels, team_id=team_id, team_name=team.get("displayName"))
            except httpx.HTTPStatusError as error:
                if error.response.status_code in _PARENT_SKIP_STATUS:
                    continue
                raise

    async def _channel_messages(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for channels in self._channels(client):
            for channel in channels:
                team_id = channel.get("team_id")
                channel_id = channel.get("id")
                if not isinstance(team_id, str) or not isinstance(channel_id, str):
                    continue
                try:
                    async for messages in self._get_odata_pages(
                        client,
                        f"/teams/{team_id}/channels/{channel_id}/messages",
                        params={"$top": 50},
                    ):
                        if cursor:
                            messages = [
                                m
                                for m in messages
                                if str(m.get("lastModifiedDateTime") or "") > cursor
                            ]
                        if messages:
                            yield with_context(
                                messages,
                                team_id=team_id,
                                channel_id=channel_id,
                                thread_id=channel_id,
                            )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in _PARENT_SKIP_STATUS:
                        continue
                    raise

    async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self._get_odata_pages(client, "/me/chats", params={"$top": 100}):
            out.extend(page)
        return out

    async def _chat_messages(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for chat in await self._chats(client):
            chat_id = chat.get("id")
            if not isinstance(chat_id, str) or not chat_id:
                continue
            try:
                async for messages in self._get_odata_pages(
                    client, f"/me/chats/{chat_id}/messages", params={"$top": 50}
                ):
                    if cursor:
                        messages = [
                            m for m in messages if str(m.get("lastModifiedDateTime") or "") > cursor
                        ]
                    if messages:
                        yield with_context(messages, chat_id=chat_id, thread_id=chat_id)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in _PARENT_SKIP_STATUS:
                    continue
                raise

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "teams":
                teams = await self._teams(client)
                if teams:
                    yield teams
                return
            if stream.name == "channels":
                async for page in self._channels(client):
                    yield page
                return
            if stream.name == "channel_messages":
                async for page in self._channel_messages(client, cursor=cursor):
                    yield page
                return
            if stream.name == "chats":
                chats = await self._chats(client)
                if chats:
                    yield chats
                return
            if stream.name == "chat_messages":
                async for page in self._chat_messages(client, cursor=cursor):
                    yield page
                return
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"microsoft_teams: {stream.name!r} refused "
                    f"({error.response.status_code}); the grant lacks the Microsoft Graph scope"
                ) from error
            raise
        raise StreamSkipped(f"microsoft_teams stream {stream.name!r} is not implemented")

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
