"""The Slack connector — a workspace's users, channels, and messages synced into recallable pages.

Slack's Web API cursor-paginates: a list response carries the next page token at
`response_metadata.next_cursor`, and reports failure as HTTP 200 with `ok=false` (a missing OAuth
scope is `error="missing_scope"`). `users.list` and `conversations.list` enumerate the whole current
collection each run, so they are `delete_missing` snapshots — a member or channel that vanished from
the grant's view is tombstoned, which is how deletes are detected on an API with no delete signal.
The three message-derived streams (`messages`, `conversation_threads`, `message_participants`) come
from one `conversations.history` walk per channel (a POST, matching Slack's own read shape). They
hang under `conversations` by one edge each, so each carries only the channel fields its own records
are recalled by — a message its channel's name and type, a thread its name and privacy — and an
archived channel is excluded by the edge rather than by a walk of its own. A
key already spells its channel (`C1:1700000100.000100`), so all three declare `key_scope="global"`
and a record is addressed by that key alone. Each
channel is a `newest_first` partition: `conversations.history`
returns newest-first, so a first backfill walks a channel downward as a `{high, until}` window
(bounded with `latest`) and a capped run resumes from `until` without the position drift that would
drop messages posted between slices, while steady-state reads only what is newer than the channel
watermark (`oldest`). A busy channel advancing never skips a quiet one.
`conversations.history` returns a thread's root and not its replies, so a root with replies is read
whole through `conversations.replies` and the replies land as messages and participants on the
root's page; the walk's span stays the history page's, since a reply's `ts` is not the channel's
order. A reply to an old thread moves neither the channel nor the root's `ts`, only its
`latest_reply`, so once every `THREAD_REREAD_INTERVAL_SECONDS` a steady-state pass, after the
channel's new messages, re-reads the roots of the last `THREAD_LOOKBACK_DAYS` below the watermark
and reads again each one whose `latest_reply` differs from the partition's `ThreadCheckpoint`. Each
re-read root and each re-read history page lands with no span and its own checkpoint, so a rate
limit resumes the re-read where it stopped and never holds back new messages. A partition with no
checkpoint is due at once, which is how replies reach a channel synced before they were read at all.
A grant that can't enumerate at all (`users.list`/`conversations.list` refused for a
missing scope, `ok=false` or a 403) can read no stream, so the walk raises `StreamSkipped` and the
run records a skip, not a failure; a per-channel refusal deeper in the history walk skips that
channel and the others still sync. Message streams reject the Slack surface's exact live bot-user
id before deriving message, thread, or participant records; another app's `bot_id` remains source
material. The write path is intentionally absent — the source seam only reads."""

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.sdk.sources import (
    CHAT_BACKFILL_WINDOW_DAYS,
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
)

USER_PAGE_SIZE = 200
CONVERSATION_PAGE_SIZE = 200
HISTORY_PAGE_SIZE = 15
HISTORY_FETCH_BUDGET = 20
HISTORY_TYPES = "public_channel,private_channel,mpim,im"
HISTORY_PATH = "/api/conversations.history"
REPLIES_PATH = "/api/conversations.replies"
REPLIES_PAGE_SIZE = 200
REREAD_PAGE_SIZE = 200
THREAD_LOOKBACK_DAYS = 7
THREAD_REREAD_INTERVAL_SECONDS = 3600.0
REPLY_STREAMS = frozenset({"messages", "message_participants"})
SNIPPET_CAP = 240

_SCOPE_REFUSAL_ERRORS = frozenset({"missing_scope", "no_permission", "not_allowed_token_type"})
_SCOPE_REFUSAL_STATUS = frozenset({403})
_CHANNEL_SKIP_ERRORS = frozenset(
    {"missing_scope", "not_in_channel", "channel_not_found", "is_archived"}
)
_CHANNEL_PATH = f"{HISTORY_PATH}?channel={{id}}"
_LIVE = {"is_archived": (False,)}
_UNDER_CHANNEL = ParentEdge(stream="conversations", path=_CHANNEL_PATH, where=_LIVE)
_MESSAGES_UNDER_CHANNEL = ParentEdge(
    stream="conversations",
    path=_CHANNEL_PATH,
    where=_LIVE,
    carry={"channel_name": "name", "channel_type": "conversation_type"},
)
_THREADS_UNDER_CHANNEL = ParentEdge(
    stream="conversations",
    path=_CHANNEL_PATH,
    where=_LIVE,
    carry={
        "channel_name": "name",
        "is_private": "is_private",
        "is_archived": "is_archived",
    },
)

ALL_STREAMS: list[StreamSpec] = [
    StreamSpec(name="users", source_object="users.list", primary_key="id", delete_missing=True),
    StreamSpec(
        name="conversations",
        source_object="conversations.list",
        primary_key="id",
        delete_missing=True,
        canonical=True,
    ),
    StreamSpec(
        name="conversation_threads",
        source_object="conversations.history",
        primary_key="id",
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
        canonical=True,
        parents=(_THREADS_UNDER_CHANNEL,),
        key_scope="global",
        fetch_budget=HISTORY_FETCH_BUDGET,
    ),
    StreamSpec(
        name="messages",
        source_object="conversations.history",
        primary_key="id",
        created_at_field="sent_at",
        updated_at_field=None,
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
        canonical=True,
        parents=(_MESSAGES_UNDER_CHANNEL,),
        key_scope="global",
        fetch_budget=HISTORY_FETCH_BUDGET,
    ),
    StreamSpec(
        name="message_participants",
        source_object="message_participants",
        primary_key="id",
        ordering=Ordering.newest_first,
        backfill_window_days=CHAT_BACKFILL_WINDOW_DAYS,
        parents=(_UNDER_CHANNEL,),
        key_scope="global",
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

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.name == "users":
            async for page in self.iter_users(client):
                yield page
            return
        if stream.name == "conversations":
            async for page in self.iter_conversations(client):
                yield page
            return
        if not stream.parents:
            raise StreamSkipped(f"slack: stream {stream.name!r} is not implemented")
        users = await self.user_index(client)
        floor = _slack_ts(run.backfill_after)
        window = f"{(datetime.now(UTC) - timedelta(days=THREAD_LOOKBACK_DAYS)).timestamp():017.6f}"
        lookback = max(window, floor or window)

        def channel_pages(partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
            return self._channel_pages(
                client, stream, partition, bound, users, run.self_user_id, lookback
            )

        async for stream_page in fanned_out(stream, run, channel_pages, floor):
            yield stream_page

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Name a thread after the channel it sits in when its first message carries no text. The
        channel's name reaches the record as `channel_name`, carried from the channel this walk
        fanned out from, and the walk writes it after the thread is derived — so the fallback is
        resolved here rather than where the rest of the title is chosen."""
        if stream.name != "conversation_threads" or record.get("title"):
            return record
        return {**record, "title": record.get("channel_name")}

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
        partition: Partition,
        bound: PartitionBound,
        users: dict[str, dict[str, Any]],
        self_user_id: str | None,
        lookback: str,
    ) -> AsyncIterator[WalkPage]:
        """Slack's `oldest` is exclusive and `latest` inclusive, and Slack reads the channel from
        the POST body rather than the query."""
        target = httpx.URL(partition.path)
        channel_id = target.params["channel"]
        state = _thread_checkpoint(bound.checkpoint)
        stored = dict(state.roots)
        state.roots = {ts: latest for ts, latest in stored.items() if ts >= lookback}
        params: dict[str, Any] = {"channel": channel_id}
        if bound.after:
            params["oldest"] = bound.after
        else:
            if bound.before:
                params["latest"] = bound.before
            if bound.since:
                params["oldest"] = bound.since
        async for raw_messages in self._history(
            client, target.path, params, inclusive=not bound.after, limit=HISTORY_PAGE_SIZE
        ):
            threaded = [raw for raw in raw_messages if _is_root(raw)]
            replies = await self._thread_replies(client, stream, channel_id, threaded)
            state.roots |= _root_entries(threaded, lookback)
            yield self._message_page(
                stream,
                channel_id,
                raw_messages,
                replies,
                users,
                self_user_id,
                span=True,
                checkpoint=_changed(state, bound.checkpoint),
            )
        now = time.time()
        if not bound.after or lookback > bound.after:
            return
        if state.below is None and state.due is not None and now < state.due:
            return
        reread = {"channel": channel_id, "oldest": lookback, "latest": state.below or bound.after}
        async for raw_messages in self._history(
            client, target.path, reread, inclusive=True, limit=REREAD_PAGE_SIZE
        ):
            for root in raw_messages:
                if not _is_root(root) or stored.get(root["ts"]) == root.get("latest_reply"):
                    continue
                replies = await self._thread_replies(client, stream, channel_id, [root])
                state.roots |= _root_entries([root], lookback)
                state.below = root["ts"]
                yield self._message_page(
                    stream,
                    channel_id,
                    [root],
                    replies,
                    users,
                    self_user_id,
                    span=False,
                    checkpoint=state.model_dump_json(),
                )
            state.below = min(raw["ts"] for raw in raw_messages)
            yield WalkPage(records=[], checkpoint=state.model_dump_json())
        state.below = None
        state.due = now + THREAD_REREAD_INTERVAL_SECONDS
        yield WalkPage(records=[], checkpoint=state.model_dump_json())

    async def _history(
        self,
        client: httpx.AsyncClient,
        path: str,
        bounds: dict[str, Any],
        *,
        inclusive: bool,
        limit: int,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        while True:
            params = bounds | {
                "limit": limit,
                "inclusive": "true" if inclusive else "false",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                data = await self._slack_post(client, path, json=params)
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
                yield raw_messages
            cursor = _next_cursor(data)
            if not cursor:
                return

    async def _thread_replies(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        channel_id: str,
        roots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Each root's replies, oldest first, without the root `conversations.replies` repeats."""
        if stream.name not in REPLY_STREAMS:
            return []
        replies: list[dict[str, Any]] = []
        for root in roots:
            cursor: str | None = None
            while True:
                params: dict[str, Any] = {
                    "channel": channel_id,
                    "ts": root["ts"],
                    "limit": REPLIES_PAGE_SIZE,
                }
                if cursor:
                    params["cursor"] = cursor
                data = await self._slack_post(client, REPLIES_PATH, json=params)
                replies.extend(
                    raw
                    for raw in data.get("messages") or []
                    if isinstance(raw, dict)
                    and isinstance(raw.get("ts"), str)
                    and raw["ts"] != root["ts"]
                )
                cursor = _next_cursor(data)
                if not cursor:
                    break
        return replies

    def _message_page(
        self,
        stream: StreamSpec,
        channel_id: str,
        raw_messages: list[dict[str, Any]],
        replies: list[dict[str, Any]],
        users: dict[str, dict[str, Any]],
        self_user_id: str | None,
        *,
        span: bool,
        checkpoint: str | None,
    ) -> WalkPage:
        """One history page fanned into this stream's records; a re-read of old roots carries no
        `ts` range, so the channel's newest-first window does not move over it."""
        threads_by_id: dict[str, dict[str, Any]] = {}
        messages: list[dict[str, Any]] = []
        participants: list[dict[str, Any]] = []
        deleted_message_ids: list[str] = []
        for raw in (*raw_messages, *replies):
            if raw.get("subtype") == "message_deleted":
                deleted_ts = raw.get("deleted_ts")
                if isinstance(deleted_ts, str) and deleted_ts:
                    deleted_message_ids.append(f"{channel_id}:{deleted_ts}")
                continue
            row = _flatten_message(
                raw,
                channel_id=channel_id,
                users=users,
                self_user_id=self_user_id,
            )
            if row is None:
                continue
            thread = _conversation_thread_from_message(row, raw=raw)
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
        high, low = (max(values), min(values)) if span else (None, None)
        if stream.name == "conversation_threads":
            return WalkPage(
                records=list(threads_by_id.values()), high=high, low=low, checkpoint=checkpoint
            )
        if stream.name == "messages":
            return WalkPage(
                records=messages,
                high=high,
                low=low,
                deletes=tuple(deleted_message_ids),
                checkpoint=checkpoint,
            )
        return WalkPage(records=participants, high=high, low=low, checkpoint=checkpoint)

    async def _enumerate(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Slack answers a scope refusal as `ok=false` with a scope error, or a 403."""
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


def _is_root(raw: dict[str, Any]) -> bool:
    reply_count = raw.get("reply_count")
    return (
        raw.get("thread_ts", raw["ts"]) == raw["ts"]
        and isinstance(reply_count, int)
        and reply_count > 0
    )


def _root_entries(roots: list[dict[str, Any]], lookback: str) -> dict[str, str]:
    return {raw["ts"]: str(raw.get("latest_reply") or "") for raw in roots if raw["ts"] >= lookback}


class ThreadCheckpoint(BaseModel):
    """What one channel's thread re-read has seen: each root of the window's `latest_reply`, when
    the next re-read is due, and how far down an unfinished one reached."""

    model_config = ConfigDict(extra="forbid")

    roots: dict[str, str] = {}
    due: float | None = None
    below: str | None = None


def _thread_checkpoint(stored: str | None) -> ThreadCheckpoint:
    """An unreadable checkpoint costs one re-read of the window, never a lost reply."""
    if stored is None:
        return ThreadCheckpoint()
    try:
        return ThreadCheckpoint.model_validate_json(stored)
    except ValidationError:
        return ThreadCheckpoint()


def _changed(state: ThreadCheckpoint, stored: str | None) -> str | None:
    encoded = state.model_dump_json()
    return None if encoded == stored or (stored is None and not state.roots) else encoded


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
    """The walk compares `ts` as strings, and a pin before 2001 has 9 integer digits, sorting above
    every current `ts`."""
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
    channel_id: str,
    users: dict[str, dict[str, Any]],
    self_user_id: str | None,
) -> dict[str, Any] | None:
    ts = raw.get("ts")
    if not isinstance(ts, str):
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
    message: dict[str, Any], *, raw: dict[str, Any]
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
        "title": message.get("subject") or message.get("snippet") or None,
        "conversation_type": "thread",
        "snippet": message.get("snippet"),
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
