"""The Slack source connector, offline over a mock transport.

Slack authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `slack.com` — no live API, no token.
Covered: the `users`/`conversations` full-collection snapshots (cursor-paginated, tombstoning
whatever a run no longer holds), the message streams fanned from one `conversations.history` walk
per channel (a POST, matching Slack's read shape) with a per-channel JSON watermark, a deleted
message tombstoned through `deletes`, the incoming per-channel cursor sent as `oldest` in the POST
body, a capped first backfill checkpointing a `{high, until}` window and resuming downward with
`latest`, and a scope-refusal (`ok=false missing_scope`, or a 403) surfacing as `StreamSkipped` so
the run records a skip, not a failure."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.slack import SlackConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources import backend as backend_module
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=SlackConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _ok(body: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, **body})


async def test_users_snapshot_follows_cursor_pagination() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "slack.com"
        assert request.url.path == "/api/users.list"
        if request.url.params.get("cursor") == "page2":
            return _ok({"members": [{"id": "U2", "name": "bob"}]})
        return _ok(
            {
                "members": [
                    {
                        "id": "U1",
                        "name": "alice",
                        "updated": 1_700_000_000,
                        "profile": {"email": "A@X.com"},
                    }
                ],
                "response_metadata": {"next_cursor": "page2"},
            }
        )

    result = await _fetch("users", handle)
    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.deletes == ()
    assert _refs(result) == {"users/U1", "users/U2"}
    assert result.pages[0].updated_at == "2023-11-14T22:13:20.000000+00:00"


async def test_conversations_returns_a_snapshot() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations.list"
        return _ok(
            {
                "channels": [
                    {
                        "id": "C1",
                        "name": "general",
                        "is_channel": True,
                        "created": 1_700_000_001,
                    }
                ]
            }
        )

    result = await _fetch("conversations", handle)
    assert result.snapshot is True
    assert result.next_cursor is None
    assert _refs(result) == {"conversations/C1"}
    assert "general" in result.pages[0].body
    assert result.pages[0].created_at == "2023-11-14T22:13:21.000000+00:00"


def _message_handler(
    seen: list[tuple[str, dict[str, object]]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok(
                {"members": [{"id": "U1", "name": "alice", "profile": {"email": "a@x.com"}}]}
            )
        if path == "/api/conversations.list":
            return _ok({"channels": [{"id": "C1", "name": "general", "is_channel": True}]})
        if path == "/api/conversations.history":
            assert request.method == "POST"
            body = json.loads(request.content) if request.content else {}
            seen.append((path, body))
            return _ok(
                {
                    "messages": [
                        {"ts": "1700000002.000100", "user": "U1", "text": "hello world"},
                        {
                            "ts": "1700000003.000200",
                            "subtype": "message_deleted",
                            "deleted_ts": "1699999900.000000",
                        },
                    ]
                }
            )
        return httpx.Response(404, json={"ok": False, "error": "unknown_method", "path": path})

    return handle


async def test_messages_are_incremental_with_a_per_channel_watermark_and_tombstone() -> None:
    result = await _fetch("messages", _message_handler([]))
    assert result.snapshot is False
    assert _refs(result) == {"messages/C1:1700000002.000100"}
    assert result.deletes == ("messages/C1:1699999900.000000",)
    assert result.next_cursor == json.dumps({"C1": "1700000003.000200"}, sort_keys=True)
    assert any("hello world" in page.body for page in result.pages)


async def test_messages_send_the_stored_channel_cursor_as_oldest_and_advance_it() -> None:
    seen: list[tuple[str, dict[str, object]]] = []
    stored = json.dumps({"C1": "1700000000.000000"})
    result = await _fetch("messages", _message_handler(seen), cursor=stored)
    history = [body for path, body in seen if path == "/api/conversations.history"]
    assert history and history[0].get("oldest") == "1700000000.000000"
    assert history[0].get("inclusive") == "false"
    assert result.next_cursor == json.dumps({"C1": "1700000003.000200"}, sort_keys=True)
    assert {page.created_at for page in result.pages} == {
        "2023-11-14T22:13:22.000100+00:00",
    }


async def test_message_participants_derive_from_the_history_walk() -> None:
    result = await _fetch("message_participants", _message_handler([]))
    assert result.snapshot is False
    assert _refs(result) == {"message_participants/C1:1700000002.000100:from:a@x.com"}
    assert result.pages[0].created_at == "2023-11-14T22:13:22.000100+00:00"


async def test_conversation_threads_derive_a_thread_root() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "alice"}]})
        if path == "/api/conversations.list":
            return _ok({"channels": [{"id": "C1", "name": "general", "is_channel": True}]})
        if path == "/api/conversations.history":
            assert request.method == "POST"
            return _ok(
                {
                    "messages": [
                        {
                            "ts": "1700000002.000100",
                            "user": "U1",
                            "text": "thread root",
                            "reply_count": 2,
                            "latest_reply": "1700000009.000000",
                        }
                    ]
                }
            )
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    result = await _fetch("conversation_threads", handle)
    assert _refs(result) == {"conversation_threads/C1:1700000002.000100"}


async def test_messages_backfill_windows_and_resumes_downward_with_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped first backfill of a channel checkpoints a `{high, until}` window and resumes below
    it with `latest` (inclusive), walking `conversations.history` downward by `ts` — so a message
    posted between slices stays above the frozen `high` for the next steady-state `oldest` pass,
    never lost to a positional skip — and dissolves to the plain `high` watermark once the walk
    exhausts."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    newest = {"ts": "1700000005.000000", "user": "U1", "text": "newest"}
    middle = {"ts": "1700000004.500000", "user": "U1", "text": "middle"}
    older = {"ts": "1700000004.000000", "user": "U1", "text": "older"}
    history_calls: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok(
                {"members": [{"id": "U1", "name": "alice", "profile": {"email": "a@x.com"}}]}
            )
        if path == "/api/conversations.list":
            return _ok({"channels": [{"id": "C1", "name": "general", "is_channel": True}]})
        if path == "/api/conversations.history":
            body = json.loads(request.content) if request.content else {}
            history_calls.append(body)
            if body.get("latest"):
                return _ok({"messages": [older]})
            if body.get("cursor") == "p2":
                return _ok({"messages": [middle]})
            return _ok({"messages": [newest], "response_metadata": {"next_cursor": "p2"}})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    first = await _fetch("messages", handle)
    assert _refs(first) == {
        "messages/C1:1700000005.000000",
        "messages/C1:1700000004.500000",
    }
    assert json.loads(first.next_cursor) == {
        "C1": {"high": "1700000005.000000", "until": "1700000004.500000"}
    }

    second = await _fetch("messages", handle, cursor=first.next_cursor)
    resumed = [call for call in history_calls if call.get("latest")]
    assert resumed and resumed[0]["latest"] == "1700000004.500000"
    assert resumed[0]["inclusive"] == "true"
    assert _refs(second) == {"messages/C1:1700000004.000000"}
    assert json.loads(second.next_cursor) == {"C1": "1700000005.000000"}


async def test_missing_scope_ok_false_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error": "missing_scope", "needed": "users:read"}
        )

    with pytest.raises(StreamSkipped, match="missing scope"):
        await _fetch("users", handle)


async def test_forbidden_status_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "org restriction"})

    with pytest.raises(StreamSkipped, match="missing scope"):
        await _fetch("conversations", handle)


async def test_channel_refusal_mid_walk_skips_only_that_channel() -> None:
    """A channel the grant lost raises `PartitionSkipped` inside its page factory: the walk keeps
    the channel's stored state untouched — its mid-backfill window survives to resume, where a
    silent end would have dissolved it — and its neighbors still sync."""
    fresh = {"ts": "1700000009.000000", "user": "U1", "text": "fresh"}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok(
                {"members": [{"id": "U1", "name": "alice", "profile": {"email": "a@x.com"}}]}
            )
        if path == "/api/conversations.list":
            return _ok(
                {
                    "channels": [
                        {"id": "C1", "name": "general", "is_channel": True},
                        {"id": "C2", "name": "random", "is_channel": True},
                    ]
                }
            )
        if path == "/api/conversations.history":
            body = json.loads(request.content) if request.content else {}
            if body.get("channel") == "C1":
                return _ok({"ok": False, "error": "channel_not_found"})
            return _ok({"messages": [fresh]})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    cursor = json.dumps(
        {
            "C1": {"high": "1700000003.000000", "until": "1700000001.000000"},
            "C2": "1700000002.000000",
        },
        sort_keys=True,
    )
    result = await _fetch("messages", handle, cursor=cursor)
    assert _refs(result) == {"messages/C2:1700000009.000000"}
    assert json.loads(result.next_cursor) == {
        "C1": {"high": "1700000003.000000", "until": "1700000001.000000"},
        "C2": "1700000009.000000",
    }
