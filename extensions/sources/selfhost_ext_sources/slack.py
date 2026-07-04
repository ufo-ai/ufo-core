"""The Slack connector — a workspace's users, channels, and messages synced into recallable pages.

Slack's Web API cursor-paginates: a list response carries the next page token at
`response_metadata.next_cursor`, and reports failure as HTTP 200 with `ok=false` (a missing OAuth
scope is `error="missing_scope"`). `users.list` and `conversations.list` enumerate the whole current
collection each run, so they are `delete_missing` snapshots — a member or channel that vanished from
the grant's view is tombstoned, which is how deletes are detected on an API with no delete signal.
The three message-derived streams (`messages`, `conversation_threads`, `message_participants`) come
from one `conversations.history` walk per channel (a POST, matching Slack's own read shape) and are
incremental: each channel keeps its own monotonic `ts`, so the stream cursor is a JSON map
`{channel_id: last_ts}` rather than one global watermark, and a busy channel advancing never skips a
quiet one. A grant that can't enumerate at all (`users.list`/`conversations.list` refused for a
missing scope, `ok=false` or a 403) can read no stream, so the walk raises `StreamSkipped` and the
run records a skip, not a failure; a per-channel refusal deeper in the history walk skips that
channel and the others still sync. The write path is intentionally absent — the source seam only
reads."""

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from selfhost.sdk.sources import RestConnector, StreamPage, StreamSkipped, StreamSpec

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
        name="conversation_threads", source_object="conversations.history", primary_key="id"
    ),
    StreamSpec(name="messages", source_object="conversations.history", primary_key="id"),
    StreamSpec(name="message_participants", source_object="message_participants", primary_key="id"),
]


class SlackApiError(RuntimeError):
    """Slack reports many failures as HTTP 200 with `ok=false`; this carries the `error` code (and
    the `needed` scope when present) so the walk can decide skip-vs-raise on it."""

    def __init__(self, error: str, *, needed: str | None = None) -> None:
        suffix = f" (needed: {needed})" if needed else ""
        super().__init__(f"slack: {error}{suffix}")
        self.error = error
        self.needed = needed


@dataclass
class SlackMessagePage:
    """One channel's `conversations.history` page fanned into the three derived record shapes, plus
    the highest `ts` seen (the channel's advancing watermark) and the ids of deleted messages."""

    channel_id: str
    threads: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    participants: list[dict[str, Any]]
    deleted_message_ids: list[str]
    latest_ts: str | None


class SlackConnector(RestConnector):
    name = "slack"
    base_url = "https://slack.com"
    streams_list = ALL_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
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
            conversations: list[dict[str, Any]] = []
            async for conversation_page in self.iter_conversations(client):
                conversations.extend(conversation_page)
            channel_cursors = _decode_channel_cursors(cursor)
            merged = dict(channel_cursors)
            async for message_page in self.iter_message_pages(
                client, conversations=conversations, channel_cursors=channel_cursors, users=users
            ):
                if message_page.latest_ts:
                    merged[message_page.channel_id] = message_page.latest_ts
                next_cursor = json.dumps(merged, sort_keys=True)
                if stream.name == "conversation_threads":
                    yield StreamPage(records=message_page.threads, next_cursor=next_cursor)
                elif stream.name == "messages":
                    yield StreamPage(
                        records=message_page.messages,
                        deletes=tuple(message_page.deleted_message_ids),
                        next_cursor=next_cursor,
                    )
                else:
                    yield StreamPage(records=message_page.participants, next_cursor=next_cursor)
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

    async def iter_message_pages(
        self,
        client: httpx.AsyncClient,
        *,
        conversations: Iterable[dict[str, Any]],
        channel_cursors: dict[str, str],
        users: dict[str, dict[str, Any]],
    ) -> AsyncIterator[SlackMessagePage]:
        for conversation in conversations:
            channel_id = conversation.get("id")
            if not isinstance(channel_id, str) or conversation.get("is_archived") is True:
                continue
            oldest = channel_cursors.get(channel_id)
            cursor: str | None = None
            latest_ts: str | None = None
            while True:
                params: dict[str, Any] = {"channel": channel_id, "limit": HISTORY_PAGE_SIZE}
                if cursor:
                    params["cursor"] = cursor
                if oldest:
                    params["oldest"] = oldest
                    params["inclusive"] = "false"
                try:
                    data = await self._slack_post(client, "/api/conversations.history", json=params)
                except SlackApiError as error:
                    if error.error in _CHANNEL_SKIP_ERRORS:
                        break
                    raise
                raw_messages = [
                    raw
                    for raw in data.get("messages") or []
                    if isinstance(raw, dict) and isinstance(raw.get("ts"), str)
                ]
                if raw_messages:
                    threads_by_id: dict[str, dict[str, Any]] = {}
                    messages: list[dict[str, Any]] = []
                    participants: list[dict[str, Any]] = []
                    deleted_message_ids: list[str] = []
                    for raw in raw_messages:
                        ts = raw["ts"]
                        if latest_ts is None or ts > latest_ts:
                            latest_ts = ts
                        if raw.get("subtype") == "message_deleted":
                            deleted_ts = raw.get("deleted_ts")
                            if isinstance(deleted_ts, str) and deleted_ts:
                                deleted_message_ids.append(f"{channel_id}:{deleted_ts}")
                            continue
                        row = _flatten_message(raw, conversation=conversation, users=users)
                        if row is None:
                            continue
                        thread = _conversation_thread_from_message(
                            row, raw=raw, conversation=conversation
                        )
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
                    if threads_by_id or messages or participants or deleted_message_ids:
                        yield SlackMessagePage(
                            channel_id=channel_id,
                            threads=list(threads_by_id.values()),
                            messages=messages,
                            participants=participants,
                            deleted_message_ids=deleted_message_ids,
                            latest_ts=latest_ts,
                        )
                cursor = _next_cursor(data)
                if not cursor:
                    break

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


def _decode_channel_cursors(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items() if isinstance(val, str) and val}


def _unix_to_iso(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


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
    raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    ts = raw.get("ts")
    channel_id = conversation.get("id")
    if not isinstance(ts, str) or not isinstance(channel_id, str):
        return None
    user_id = raw.get("user")
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
