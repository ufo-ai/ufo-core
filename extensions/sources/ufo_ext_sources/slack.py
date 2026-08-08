"""The Slack connector — a workspace's users, channels, and messages synced into recallable pages.

Slack's Web API cursor-paginates: a list response carries the next page token at
`response_metadata.next_cursor`, and reports failure as HTTP 200 with `ok=false` (a missing OAuth
scope is `error="missing_scope"`). `users.list` and `conversations.list` enumerate the whole current
collection each run, so they are `delete_missing` snapshots — a member or channel that vanished from
the grant's view is tombstoned, which is how deletes are detected on an API with no delete signal.
The three message-derived streams (`messages`, `conversation_threads`, `message_participants`) come
from one `conversations.history` walk per channel (a POST, matching Slack's own read shape). Each
channel is a `newest_first` partition of the SDK's `PartitionWalk`: `conversations.history`
returns newest-first, so a first backfill walks a channel downward as a `{high, until}` window
(bounded with `latest`) and a capped run resumes from `until` without the position drift that would
drop messages posted between slices, while steady-state reads only what is newer than the channel
watermark (`oldest`). The per-channel cursor map lives in `PartitionWalk`, so a busy channel
advancing never skips a quiet one. A grant that can't enumerate at all (`users.list`/
`conversations.list` refused for a
missing scope, `ok=false` or a 403) can read no stream, so the walk raises `StreamSkipped` and the
run records a skip, not a failure; a per-channel refusal deeper in the history walk skips that
channel and the others still sync. Message streams reject the Slack surface's exact live bot-user
id before deriving message, thread, or participant records; another app's `bot_id` remains source
material. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.sources import (
    CHAT_BACKFILL_WINDOW_DAYS,
    Ordering,
    PartitionBound,
    PartitionSkipped,
    PartitionWalk,
    RestConnector,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
)

USER_PAGE_SIZE = 200
CONVERSATION_PAGE_SIZE = 200
HISTORY_PAGE_SIZE = 15
HISTORY_TYPES = "public_channel,private_channel,mpim,im"
SNIPPET_CAP = 240

_MESSAGE_STREAMS = frozenset({"conversation_threads", "messages", "message_participants"})
_SCOPE_REFUSAL_ERRORS = frozenset({"missing_scope", "no_permission", "not_allowed_token_type"})
_SCOPE_REFUSAL_STATUS = frozenset({403})
_CHANNEL_SKIP_ERRORS = frozenset(
    {"missing_scope", "not_in_channel", "channel_not_found", "is_archived"}
)

ALL_STREAMS: list[StreamSpec] = [
    StreamSpec(name="users", source_object="users.list", primary_key="id", delete_missing=True),
    StreamSpec(
        name="conversations",
        source_object="conversations.list",
        primary_key="id",
        delete_missing=True,
    ),
    StreamSpec(
        name="conversation_threads",
        source_object="conversations.history",
        primary_key="id",
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
    ),
    StreamSpec(
        name="messages",
        source_object="conversations.history",
        primary_key="id",
        created_at_field="sent_at",
        updated_at_field=None,
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
    ),
    StreamSpec(
        name="message_participants",
        source_object="message_participants",
        primary_key="id",
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
    ),
]


class SlackApiError(RuntimeError):
    """Slack reports many failures as HTTP 200 with `ok=false`; this carries the `error` code (and
    the `needed` scope when present) so the walk can decide skip-vs-raise on it."""

    def __init__(self, error: str, *, needed: str | None = None) -> None:
        suffix = f" (needed: {needed})" if needed else ""
        super().__init__(f"slack: {error}{suffix}")
        self.error = error
        self.needed = needed


class SlackConnector(RestConnector):
    name = "slack"
    base_url = "https://slack.com"
    streams_list = ALL_STREAMS

    def paginate_source(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        return self.paginate(
            client,
            stream,
            cursor=cursor,
            self_user_id=self_user_id,
            backfill_after=backfill_after,
        )

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
        self_user_id: str | None = None,
        backfill_after: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.name == "users":
            async for page in self.iter_users(client):
                yield page
            return
        if stream.name == "conversations":
            async for page in self.iter_conversations(client):
                yield page
            return
        if stream.name in _MESSAGE_STREAMS:
            users = await self.user_index(client)
            channels: dict[str, dict[str, Any]] = {}
            async for conversation_page in self.iter_conversations(client):
                for conversation in conversation_page:
                    channel_id = conversation.get("id")
                    if isinstance(channel_id, str) and conversation.get("is_archived") is not True:
                        channels[channel_id] = conversation

            async def partitions() -> AsyncIterator[str]:
                for channel_id in channels:
                    yield channel_id

            def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
                return self._channel_pages(
                    client,
                    stream,
                    channels[channel_id],
                    bound,
                    users,
                    self_user_id,
                )

            walk = PartitionWalk(
                ordering=stream.ordering,
                partitions=partitions,
                pages=channel_pages,
                floor=_slack_ts(backfill_after),
            ).stream(cursor)
            try:
                async for stream_page in walk:
                    yield stream_page
            finally:
                if isinstance(walk, AsyncGenerator):
                    await walk.aclose()
            return
        raise StreamSkipped(f"slack: stream {stream.name!r} is not implemented")

    async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": USER_PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            data = await self._enumerate(client, "/api/users.list", params=params)
            members = [
                _flatten_user(member)
                for member in data.get("members") or []
                if isinstance(member, dict) and isinstance(member.get("id"), str)
            ]
            if members:
                yield members
            cursor = _next_cursor(data)
            if not cursor:
                return

    async def iter_conversations(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": CONVERSATION_PAGE_SIZE,
                "types": HISTORY_TYPES,
                "exclude_archived": "false",
            }
            if cursor:
                params["cursor"] = cursor
            data = await self._enumerate(client, "/api/conversations.list", params=params)
            channels = [
                {
                    "id": channel["id"],
                    "name": channel.get("name") or channel.get("name_normalized") or channel["id"],
                    "conversation_type": _conversation_type(channel),
                    "is_channel": bool(channel.get("is_channel")),
                    "is_group": bool(channel.get("is_group")),
                    "is_im": bool(channel.get("is_im")),
                    "is_mpim": bool(channel.get("is_mpim")),
                    "is_private": bool(channel.get("is_private")),
                    "is_archived": bool(channel.get("is_archived")),
                    "is_shared": bool(channel.get("is_shared")),
                    "is_ext_shared": bool(channel.get("is_ext_shared")),
                    "created_at": _unix_to_iso(channel.get("created")),
                    "creator": channel.get("creator"),
                    "user_id": channel.get("user"),
                    "topic": _nested_value(channel, "topic", "value"),
                    "purpose": _nested_value(channel, "purpose", "value"),
                    "num_members": channel.get("num_members"),
                }
                for channel in data.get("channels") or []
                if isinstance(channel, dict) and isinstance(channel.get("id"), str)
            ]
            if channels:
                yield channels
            cursor = _next_cursor(data)
            if not cursor:
                return

    async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        users: dict[str, dict[str, Any]] = {}
        async for page in self.iter_users(client):
            for user in page:
                user_id = user.get("id")
                if isinstance(user_id, str):
                    users[user_id] = user
        return users

    async def _channel_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        conversation: dict[str, Any],
        bound: PartitionBound,
        users: dict[str, dict[str, Any]],
        self_user_id: str | None,
    ) -> AsyncIterator[WalkPage]:
        """One channel's `conversations.history` slice for `PartitionWalk`: newest-first, bounding
        a steady-state pass above the channel watermark with `oldest` (exclusive — the watermark
        message is already landed) and a backfill below `until` with `latest` (inclusive, so a
        message tied at a capped boundary `ts` is re-fetched and deduped, not dropped). Each page
        reports the raw-message `ts` span so the walk tracks the channel's window, and derives this
        stream's records (threads/messages/participants) from the same page; a channel the grant
        can't read drops out without failing the run."""
        channel_id = conversation["id"]
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"channel": channel_id, "limit": HISTORY_PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            if bound.after:
                params |= {"oldest": bound.after, "inclusive": "false"}
            else:
                if bound.before:
                    params |= {"latest": bound.before, "inclusive": "true"}
                if bound.since:
                    # the pinned floor, bounding the descent server-side; `inclusive` governs both
                    # ends and true is right for each
                    params |= {"oldest": bound.since, "inclusive": "true"}
            try:
                data = await self._slack_post(client, "/api/conversations.history", json=params)
            except SlackApiError as error:
                if error.error in _CHANNEL_SKIP_ERRORS:
                    raise PartitionSkipped(f"slack: channel refused ({error.error})") from error
                raise
            raw_messages = [
                raw
                for raw in data.get("messages") or []
                if isinstance(raw, dict) and isinstance(raw.get("ts"), str)
            ]
            if raw_messages:
                yield self._message_page(
                    stream,
                    conversation,
                    raw_messages,
                    users,
                    self_user_id,
                )
            cursor = _next_cursor(data)
            if not cursor:
                return

    def _message_page(
        self,
        stream: StreamSpec,
        conversation: dict[str, Any],
        raw_messages: list[dict[str, Any]],
        users: dict[str, dict[str, Any]],
        self_user_id: str | None,
    ) -> WalkPage:
        """One history page fanned into this stream's records, carrying the raw-message `ts` span so
        the walk advances the channel's newest-first window over it."""
        channel_id = conversation["id"]
        threads_by_id: dict[str, dict[str, Any]] = {}
        messages: list[dict[str, Any]] = []
        participants: list[dict[str, Any]] = []
        deleted_message_ids: list[str] = []
        for raw in raw_messages:
            if raw.get("subtype") == "message_deleted":
                deleted_ts = raw.get("deleted_ts")
                if isinstance(deleted_ts, str) and deleted_ts:
                    deleted_message_ids.append(f"{channel_id}:{deleted_ts}")
                continue
            row = _flatten_message(
                raw,
                conversation=conversation,
                users=users,
                self_user_id=self_user_id,
            )
            if row is None:
                continue
            thread = _conversation_thread_from_message(row, raw=raw, conversation=conversation)
            if thread is not None:
                existing = threads_by_id.get(thread["id"])
                if existing is None or str(existing.get("updated_at") or "") < str(
                    thread.get("updated_at") or ""
                ):
                    threads_by_id[thread["id"]] = thread
            messages.append(row)
            participant = _participant_for_message(row, users=users)
            if participant is not None:
                participants.append(participant)
        values = [raw["ts"] for raw in raw_messages]
        high, low = max(values), min(values)
        if stream.name == "conversation_threads":
            return WalkPage(records=list(threads_by_id.values()), high=high, low=low)
        if stream.name == "messages":
            return WalkPage(
                records=messages, high=high, low=low, deletes=tuple(deleted_message_ids)
            )
        return WalkPage(records=participants, high=high, low=low)

    async def _enumerate(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]
    ) -> dict[str, Any]:
        """A top-level enumeration GET. A scope refusal here (`ok=false` with a scope error, or a
        403) means the grant can read no record of this stream, so it raises `StreamSkipped` — the
        run records a skip, not a failure — exactly as GitHub's org-enumeration refusal does."""
        try:
            return await self._slack_get(client, path, params=params)
        except SlackApiError as error:
            if error.error in _SCOPE_REFUSAL_ERRORS:
                raise StreamSkipped(
                    f"slack: {path} refused ({error.error}); the grant is missing scope"
                ) from error
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _SCOPE_REFUSAL_STATUS:
                raise StreamSkipped(
                    f"slack: {path} refused (HTTP {error.response.status_code}); "
                    "the grant is missing scope"
                ) from error
            raise

    async def _slack_get(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _ok_or_raise(await self._get(client, path, params=params))

    async def _slack_post(
        self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _ok_or_raise(await self._post(client, path, json=json))


def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("ok") is False:
        error = str(data.get("error") or "unknown_error")
        needed = data.get("needed")
        raise SlackApiError(error, needed=needed if isinstance(needed, str) else None)
    return data


def _next_cursor(data: dict[str, Any]) -> str | None:
    meta = data.get("response_metadata")
    if not isinstance(meta, dict):
        return None
    cursor = meta.get("next_cursor")
    return cursor if isinstance(cursor, str) and cursor else None


def _unix_to_iso(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _slack_ts(value: datetime | None) -> str | None:
    """A pinned instant as the `ts` string slack orders channel history by, zero-padded to the width
    of a live one.

    The walk compares these as strings, so a floor narrower than 10 integer digits would sort ABOVE
    every current `ts` rather than below it — a pin between 1970 and 2001 has 9, and the walk would
    ground itself on the first page and dissolve the channel after one. Padding puts every instant
    from the epoch to 2286 in the same width as the values it is compared against. Below the epoch
    there is nothing to render and nothing to bound, so None."""
    if value is None:
        return None
    seconds = value.timestamp()
    return None if seconds < 0 else f"{seconds:017.6f}"


def _slack_ts_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    except ValueError:
        return None


def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]:
    raw_profile = raw.get("profile")
    profile: dict[str, Any] = raw_profile if isinstance(raw_profile, dict) else {}
    email = profile.get("email")
    email = email.strip().lower() or None if isinstance(email, str) else None
    return {
        "id": raw["id"],
        "slack_user_id": raw["id"],
        "team_id": raw.get("team_id"),
        "name": raw.get("name"),
        "display_name": _first_text(
            profile.get("display_name"),
            profile.get("real_name"),
            raw.get("real_name"),
            raw.get("name"),
        ),
        "real_name": _first_text(profile.get("real_name"), raw.get("real_name")),
        "first_name": profile.get("first_name"),
        "last_name": profile.get("last_name"),
        "email": email,
        "phone": profile.get("phone"),
        "title": profile.get("title"),
        "timezone": raw.get("tz") or raw.get("tz_label"),
        "is_bot": bool(raw.get("is_bot")),
        "is_app_user": bool(raw.get("is_app_user")),
        "deleted": bool(raw.get("deleted")),
        "updated_at": _unix_to_iso(raw.get("updated")),
    }


def _flatten_message(
    raw: dict[str, Any],
    *,
    conversation: dict[str, Any],
    users: dict[str, dict[str, Any]],
    self_user_id: str | None,
) -> dict[str, Any] | None:
    ts = raw.get("ts")
    channel_id = conversation.get("id")
    if not isinstance(ts, str) or not isinstance(channel_id, str):
        return None
    user_id = raw.get("user")
    if isinstance(user_id, str) and user_id == self_user_id:
        return None
    user = users.get(user_id) if isinstance(user_id, str) else None
    text = raw.get("text") if isinstance(raw.get("text"), str) else None
    thread_ts = raw.get("thread_ts")
    if not isinstance(thread_ts, str):
        thread_ts = ts
    return {
        "id": f"{channel_id}:{ts}",
        "slack_ts": ts,
        "channel_id": channel_id,
        "conversation_id": channel_id,
        "channel_name": conversation.get("name"),
        "channel_type": conversation.get("conversation_type"),
        "thread_id": f"{channel_id}:{thread_ts}",
        "thread_ts": thread_ts,
        "sent_at": _slack_ts_to_iso(ts),
        "subject": None,
        "snippet": _snippet(text),
        "text": text,
        "from_handle": _first_text(
            user.get("email") if user else None, user_id, raw.get("bot_id"), raw.get("username")
        ),
        "from_display_name": user.get("display_name") if user else raw.get("username"),
        "user_id": user_id,
        "bot_id": raw.get("bot_id"),
        "subtype": raw.get("subtype"),
        "reply_count": raw.get("reply_count"),
        "latest_reply": raw.get("latest_reply"),
    }


def _conversation_thread_from_message(
    message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]
) -> dict[str, Any] | None:
    thread_id = message.get("thread_id")
    thread_ts = message.get("thread_ts")
    ts = message.get("slack_ts")
    if not isinstance(thread_id, str) or not isinstance(thread_ts, str) or not isinstance(ts, str):
        return None
    reply_count = raw.get("reply_count")
    is_thread_root = thread_ts == ts and isinstance(reply_count, int) and reply_count > 0
    is_thread_reply = thread_ts != ts
    if not is_thread_root and not is_thread_reply:
        return None
    latest_reply = raw.get("latest_reply")
    latest_ts = latest_reply if isinstance(latest_reply, str) else ts
    reply_users = raw.get("reply_users")
    return {
        "id": thread_id,
        "title": message.get("subject") or message.get("snippet") or conversation.get("name"),
        "conversation_type": "thread",
        "snippet": message.get("snippet"),
        "channel_name": conversation.get("name"),
        "is_private": conversation.get("is_private"),
        "is_archived": conversation.get("is_archived"),
        "message_count": reply_count + 1 if isinstance(reply_count, int) else None,
        "participant_count": len(reply_users) if isinstance(reply_users, list) else None,
        "last_message_at": _slack_ts_to_iso(latest_ts),
        "created_at": message.get("sent_at"),
        "updated_at": _slack_ts_to_iso(latest_ts),
        "parent_conversation_id": message.get("channel_id"),
    }


def _participant_for_message(
    message: dict[str, Any], *, users: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    user_id = message.get("user_id")
    user = users.get(user_id) if isinstance(user_id, str) else None
    handle = _first_text(user.get("email") if user else None, user_id)
    if not handle:
        return None
    message_id = message["id"]
    return {
        "id": f"{message_id}:from:{handle}",
        "message_id": message_id,
        "channel_id": message.get("channel_id"),
        "thread_id": message.get("thread_id"),
        "role": "from",
        "handle": handle,
        "email": user.get("email") if user else None,
        "slack_user_id": user_id,
        "display_name": user.get("display_name") if user else message.get("from_display_name"),
        "created_at": message.get("sent_at"),
    }


def _conversation_type(raw: dict[str, Any]) -> str:
    if raw.get("is_im"):
        return "im"
    if raw.get("is_mpim"):
        return "mpim"
    if raw.get("is_group") or raw.get("is_private"):
        return "private_channel"
    return "public_channel"


def _nested_value(raw: dict[str, Any], *path: str) -> Any:
    current: Any = raw
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _snippet(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = " ".join(value.split())
    return collapsed[:SNIPPET_CAP] if collapsed else None
