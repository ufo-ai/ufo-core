"""The Slack source connector, offline over a mock transport.

Slack authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `slack.com` — no live API, no token.
Covered: the `users`/`conversations` full-collection snapshots (cursor-paginated, tombstoning
whatever a run no longer holds), the message streams fanned over the landed channels — one
`conversations.history` walk per channel (a POST, matching Slack's read shape) with a per-channel
JSON watermark — a deleted message tombstoned through `deletes`, the incoming per-channel cursor
sent as `oldest` in the POST body, a capped first backfill checkpointing a `{high, until}` window
and resuming downward with `latest`, and a scope-refusal (`ok=false missing_scope`, or a 403)
surfacing as `StreamSkipped` so the run records a skip, not a failure."""

import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.slack import (
    HISTORY_FETCH_BUDGET,
    HISTORY_PAGE_SIZE,
    HISTORY_PATH,
    REPLIES_PATH,
    THREAD_REREAD_INTERVAL_SECONDS,
    SlackConnector,
    ThreadCheckpoint,
)

from ufo.runtime.access.connectors import (
    Credential,
)
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources.connector import CHECKPOINTS_KEY
from ufo.runtime.sources.sync import (
    SourceAuth,
    StreamSkipped,
    SyncResult,
)
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    Partition,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]


def _channel(channel_id: str, *, archived: bool = False) -> ParentRecord:
    return ParentRecord(
        ref=f"conversations/{channel_id}", fields={"id": channel_id, "is_archived": archived}
    )


ONE_CHANNEL: Landed = {"conversations": (_channel("C1"),)}
TWO_CHANNELS: Landed = {"conversations": (_channel("C1"), _channel("C2"))}


async def _no_parents(stream: str) -> AsyncIterator[ParentRecord]:
    return
    yield


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    self_user_id: str | None = None,
    backfill_after: datetime | None = None,
    parents: ParentPages = _no_parents,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        self_user_id=self_user_id,
        parents=parents,
    )
    return await ConnectorBackend(connector=SlackConnector()).fetch(
        ConnectorSourceConfig(stream=stream, backfill_after=backfill_after),
        cursor,
        auth,
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _ok(body: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, **body})


def _thread(request: httpx.Request, messages: list[dict[str, object]]) -> httpx.Response:
    """`conversations.replies` for a root: the root first, then its replies."""
    root = json.loads(request.content)["ts"]
    return _ok(
        {
            "messages": [
                message for message in messages if message.get("thread_ts", message["ts"]) == root
            ]
        }
    )


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


BOT_MESSAGES: list[dict[str, object]] = [
    {
        "ts": "1700000002.000000",
        "user": "U_UFO",
        "bot_id": "B_UFO",
        "text": "ufo answer",
        "reply_count": 1,
    },
    {
        "ts": "1700000003.000000",
        "user": "U_THIRD",
        "bot_id": "B_THIRD",
        "text": "third-party bot answer",
        "reply_count": 1,
    },
]


def _bot_message_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/users.list":
        return _ok(
            {
                "members": [
                    {
                        "id": "U_UFO",
                        "name": "ufo",
                        "is_bot": True,
                        "profile": {"email": "ufo@app.test"},
                    },
                    {
                        "id": "U_THIRD",
                        "name": "third",
                        "is_bot": True,
                        "profile": {"email": "third@app.test"},
                    },
                ]
            }
        )
    if request.url.path == "/api/conversations.list":
        return _ok({"channels": [{"id": "C1", "name": "general", "is_channel": True}]})
    if request.url.path == "/api/conversations.history":
        return _ok({"messages": BOT_MESSAGES})
    if request.url.path == REPLIES_PATH:
        return _thread(request, BOT_MESSAGES)
    return httpx.Response(404, json={"ok": False, "error": "unknown_method"})


def test_a_channel_walked_under_no_edge_keeps_the_outgoing_images_cursor_entry() -> None:
    """The image being replaced keyed a channel's history walk by the bare channel id. A root
    partition's key is exactly that, so a map it stored keeps matching byte for byte."""
    assert Partition(ref="C1", path=HISTORY_PATH).key == "C1"


def _walked(channel: str) -> str:
    """The cursor entry one channel's history walk checkpoints under: the channel page it fanned out
    from and the collection it asked of it."""
    return f"conversations/{channel}\n{HISTORY_PATH}?channel={channel}"


async def test_messages_are_incremental_with_a_per_channel_watermark_and_tombstone(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch("messages", _message_handler([]), parents=parents_reader(ONE_CHANNEL))
    assert result.snapshot is False
    assert _refs(result) == {"messages/C1:1700000002.000100"}
    assert result.deletes == ("messages/C1:1699999900.000000",)
    assert result.next_cursor == json.dumps({_walked("C1"): "1700000003.000200"}, sort_keys=True)
    assert any("hello world" in page.body for page in result.pages)


async def test_a_message_is_addressed_by_its_key_alone(parents_reader: ParentsReader) -> None:
    """The key already carries the channel, so the streams declare `key_scope="global"` and the
    address stays the record's own key — the page a landed message already sits on."""
    seen: list[tuple[str, dict[str, object]]] = []
    result = await _fetch("messages", _message_handler(seen), parents=parents_reader(ONE_CHANNEL))

    assert [page.source_identity for page in result.pages] == ["messages/C1:1700000002.000100"]
    assert [path for path, _ in seen] == [HISTORY_PATH]
    by_name = {stream.name: stream for stream in SlackConnector().streams()}
    assert by_name["messages"].key_scope == "global"
    assert by_name["conversation_threads"].key_scope == "global"
    assert by_name["message_participants"].key_scope == "global"


CARRY_CHANNEL = {
    "id": "C1",
    "name": "general",
    "is_channel": True,
    "is_private": False,
    "is_archived": False,
    "created": 1700000000,
    "topic": {"value": "t"},
    "purpose": {"value": "p"},
    "creator": "U1",
    "num_members": 3,
}
CARRY_MESSAGES = [
    {
        "ts": "1700000100.000100",
        "user": "U1",
        "text": "hello there",
        "thread_ts": "1700000100.000100",
        "reply_count": 2,
        "latest_reply": "1700000300.000100",
        "reply_users": ["U1"],
    },
    {
        "ts": "1700000090.000100",
        "user": "U1",
        "text": "",
        "thread_ts": "1700000090.000100",
        "reply_count": 1,
        "latest_reply": "1700000095.000100",
        "reply_users": ["U1"],
    },
]
CARRIED_CHANNEL = ParentRecord(
    ref="conversations/C1",
    fields={
        "id": "C1",
        "is_archived": False,
        "is_private": False,
        "name": "general",
        "conversation_type": "public_channel",
    },
)
RENDERED = {
    "messages": {
        "messages/C1:1700000090.000100": (
            "sha256:bec853c3bb6069abf354df134185d0b756738beaf194835f9b86c921af8f5a84"
        ),
        "messages/C1:1700000100.000100": (
            "sha256:0caa42f4730166ef8f50f7aa95d9a2d46ff7367b145cd3e1bcdc7c9ebe8c57b0"
        ),
    },
    "conversation_threads": {
        "conversation_threads/C1:1700000090.000100": (
            "sha256:4171bb321cd9b0e64055de4ca6e034dd129694e3ed6609d815748ca1774719c2"
        ),
        "conversation_threads/C1:1700000100.000100": (
            "sha256:ace772550a29df90f64e10067ebee101e8907f36f91d785146ec907021a678ed"
        ),
    },
    "message_participants": {
        "message_participants/C1:1700000090.000100:from:a@b.co": (
            "sha256:1aceb5f18e2b0e268f2363807e8bbb9456ef510061284aa584252b31d56098f9"
        ),
        "message_participants/C1:1700000100.000100:from:a@b.co": (
            "sha256:623fcdb209148254ba6c2e91d3b6195726276316afc47e31780483af34a1e45a"
        ),
    },
}


def _carry_handler(asked: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "ann", "profile": {"email": "a@b.co"}}]})
        if request.url.path == "/api/conversations.history":
            return _ok({"messages": CARRY_MESSAGES})
        if request.url.path == REPLIES_PATH:
            return _thread(request, CARRY_MESSAGES)
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    return handle


@pytest.mark.parametrize("stream", sorted(RENDERED))
async def test_the_rendered_body_is_pinned_byte_for_byte(
    stream: str, parents_reader: ParentsReader
) -> None:
    asked: list[str] = []
    result = await _fetch(
        stream,
        _carry_handler(asked),
        parents=parents_reader({"conversations": (CARRIED_CHANNEL,)}),
    )

    assert {page.source_identity: page.digest for page in result.pages} == RENDERED[stream]
    threads = [REPLIES_PATH] * len(CARRY_MESSAGES) if stream != "conversation_threads" else []
    assert asked == ["/api/users.list", HISTORY_PATH, *threads]


async def test_the_message_walk_enumerates_no_channel_of_its_own(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "alice"}]})
        if request.url.path == "/api/conversations.history":
            return _ok({"messages": [{"ts": "1700000002.000100", "user": "U1", "text": "hi"}]})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    await _fetch("messages", handle, parents=parents_reader(ONE_CHANNEL))
    assert asked == ["/api/users.list", HISTORY_PATH]


async def test_an_archived_channel_is_no_partition_and_costs_no_request(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "alice"}]})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    result = await _fetch(
        "messages",
        handle,
        parents=parents_reader({"conversations": (_channel("C9", archived=True),)}),
    )
    assert result.pages == ()
    assert asked == ["/api/users.list"]


@pytest.mark.parametrize(
    ("stream", "expected_ref"),
    (
        ("messages", "messages/C1:1700000003.000000"),
        ("conversation_threads", "conversation_threads/C1:1700000003.000000"),
        (
            "message_participants",
            "message_participants/C1:1700000003.000000:from:third@app.test",
        ),
    ),
)
async def test_message_streams_exclude_only_ufo_user(
    stream: str, expected_ref: str, parents_reader: ParentsReader
) -> None:
    result = await _fetch(
        stream,
        _bot_message_handler,
        self_user_id="U_UFO",
        parents=parents_reader(ONE_CHANNEL),
    )

    assert _refs(result) == {expected_ref}


async def test_a_pinned_floor_bounds_the_channel_walk_and_dissolves_there(
    parents_reader: ParentsReader,
) -> None:
    """A channel with years of history is the volume that actually blows up a workspace sync —
    one walk per channel, all the way down, multiplied by the channel count."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    history_calls: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "alice"}]})
        if path == "/api/conversations.list":
            return _ok({"channels": [{"id": "C1", "name": "general", "is_channel": True}]})
        if path == "/api/conversations.history":
            body = json.loads(request.content) if request.content else {}
            history_calls.append(body)
            return _ok({"messages": [{"ts": "1783000000.000000", "user": "U1", "text": "in"}]})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    result = await _fetch(
        "messages", handle, backfill_after=pinned, parents=parents_reader(ONE_CHANNEL)
    )

    assert history_calls[0]["oldest"] == f"{pinned.timestamp():.6f}"
    assert history_calls[0]["inclusive"] == "true"
    assert "latest" not in history_calls[0]
    assert _refs(result) == {"messages/C1:1783000000.000000"}
    assert json.loads(str(result.next_cursor)) == {_walked("C1"): "1783000000.000000"}


async def test_messages_backfill_windows_and_resumes_downward_with_latest(
    monkeypatch: pytest.MonkeyPatch, parents_reader: ParentsReader
) -> None:
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

    first = await _fetch("messages", handle, parents=parents_reader(ONE_CHANNEL))
    assert _refs(first) == {
        "messages/C1:1700000005.000000",
        "messages/C1:1700000004.500000",
    }
    assert json.loads(first.next_cursor) == {
        _walked("C1"): {"high": "1700000005.000000", "until": "1700000004.500000"}
    }

    second = await _fetch(
        "messages", handle, cursor=first.next_cursor, parents=parents_reader(ONE_CHANNEL)
    )
    resumed = [call for call in history_calls if call.get("latest")]
    assert resumed and resumed[0]["latest"] == "1700000004.500000"
    assert resumed[0]["inclusive"] == "true"
    assert _refs(second) == {"messages/C1:1700000004.000000"}
    assert json.loads(second.next_cursor) == {_walked("C1"): "1700000005.000000"}


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


async def test_channel_refusal_mid_walk_skips_only_that_channel(
    parents_reader: ParentsReader,
) -> None:
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
            _walked("C1"): {"high": "1700000003.000000", "until": "1700000001.000000"},
            _walked("C2"): "1700000002.000000",
        },
        sort_keys=True,
    )
    result = await _fetch("messages", handle, cursor=cursor, parents=parents_reader(TWO_CHANNELS))
    assert _refs(result) == {"messages/C2:1700000009.000000"}
    assert json.loads(result.next_cursor) == {
        _walked("C1"): {"high": "1700000003.000000", "until": "1700000001.000000"},
        _walked("C2"): "1700000009.000000",
    }


@pytest.mark.parametrize("stream", ["messages", "conversation_threads"])
async def test_a_tick_reads_no_more_channels_than_the_history_budget(
    stream: str, parents_reader: ParentsReader
) -> None:
    seen: list[tuple[str, dict[str, object]]] = []
    channels: Landed = {
        "conversations": tuple(_channel(f"C{n}") for n in range(HISTORY_FETCH_BUDGET + 3))
    }

    first = await _fetch(stream, _message_handler(seen), parents=parents_reader(channels))
    assert len(seen) == HISTORY_FETCH_BUDGET

    seen.clear()
    await _fetch(
        stream, _message_handler(seen), cursor=first.next_cursor, parents=parents_reader(channels)
    )
    assert len(seen) == 3


def _threaded_handler(
    history: list[dict[str, object]],
    thread: list[dict[str, object]],
    asked: list[tuple[str, dict[str, object]]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "ann", "profile": {"email": "a@b.co"}}]})
        body = json.loads(request.content)
        asked.append((path, body))
        if path == HISTORY_PATH:
            return _ok({"messages": history})
        if path == REPLIES_PATH:
            return _thread(request, thread)
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    return handle


ROOT = {
    "ts": "1700000100.000100",
    "user": "U1",
    "text": "printer prints gray",
    "reply_count": 2,
    "latest_reply": "1700000300.000100",
}
REPLIES = [
    {"ts": "1700000200.000100", "thread_ts": ROOT["ts"], "user": "U1", "text": "toner ordered"},
    {"ts": "1700000300.000100", "thread_ts": ROOT["ts"], "user": "U1", "text": "paper upstairs"},
]
PLAIN = {"ts": "1700000150.000100", "user": "U1", "text": "lunch?"}


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (
            "messages",
            {
                "messages/C1:1700000100.000100",
                "messages/C1:1700000150.000100",
                "messages/C1:1700000200.000100",
                "messages/C1:1700000300.000100",
            },
        ),
        (
            "message_participants",
            {
                "message_participants/C1:1700000100.000100:from:a@b.co",
                "message_participants/C1:1700000150.000100:from:a@b.co",
                "message_participants/C1:1700000200.000100:from:a@b.co",
                "message_participants/C1:1700000300.000100:from:a@b.co",
            },
        ),
        ("conversation_threads", {"conversation_threads/C1:1700000100.000100"}),
    ],
)
async def test_a_thread_root_lands_its_replies_under_the_history_span(
    stream: str, expected: set[str], parents_reader: ParentsReader
) -> None:
    asked: list[tuple[str, dict[str, object]]] = []
    result = await _fetch(
        stream,
        _threaded_handler([PLAIN, ROOT], [ROOT, *REPLIES], asked),
        parents=parents_reader(ONE_CHANNEL),
    )

    assert _refs(result) == expected
    assert json.loads(result.next_cursor or "{}")[_walked("C1")] == PLAIN["ts"]
    replies = [body["ts"] for path, body in asked if path == REPLIES_PATH]
    assert replies == ([] if stream == "conversation_threads" else [ROOT["ts"]])
    assert any("paper upstairs" in page.body for page in result.pages) == (stream == "messages")


def _days_ago(days: float) -> str:
    return f"{datetime.now(UTC).timestamp() - days * 86400:017.6f}"


def _cursor(watermark: str, state: ThreadCheckpoint | None = None) -> str:
    entries: dict[str, str] = {_walked("C1"): watermark}
    if state is not None:
        entries[CHECKPOINTS_KEY] = json.dumps({_walked("C1"): state.model_dump_json()})
    return json.dumps(entries)


def _state(result: SyncResult) -> ThreadCheckpoint:
    after = json.loads(result.next_cursor or "{}")
    return ThreadCheckpoint.model_validate_json(json.loads(after[CHECKPOINTS_KEY])[_walked("C1")])


def _channel_handler(
    reread: list[dict[str, object]],
    fresh: list[dict[str, object]],
    threads: Mapping[str, list[dict[str, object]]],
    asked: list[tuple[str, dict[str, object]]],
    limited: frozenset[str] = frozenset(),
) -> Callable[[httpx.Request], httpx.Response]:
    """History answers `fresh` above the watermark and `reread` below it; a root in `limited`
    answers its replies with a 429."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/users.list":
            return _ok({"members": [{"id": "U1", "name": "ann", "profile": {"email": "a@b.co"}}]})
        body = json.loads(request.content)
        asked.append((path, body))
        if path == HISTORY_PATH:
            if "latest" not in body:
                return _ok({"messages": fresh})
            return _ok({"messages": [m for m in reread if str(m["ts"]) <= str(body["latest"])]})
        if path == REPLIES_PATH and body["ts"] in limited:
            return httpx.Response(429, headers={"retry-after": "30"}, json={"ok": False})
        if path == REPLIES_PATH:
            return _ok({"messages": threads[str(body["ts"])]})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})

    return handle


async def test_a_due_reread_reads_only_the_roots_whose_latest_reply_moved(
    parents_reader: ParentsReader,
) -> None:
    moved = {"ts": _days_ago(3), "user": "U1", "text": "vpn token", "reply_count": 2}
    still = {"ts": _days_ago(2), "user": "U1", "text": "font licence", "reply_count": 1}
    late = {"ts": _days_ago(0.5), "thread_ts": moved["ts"], "user": "U1", "text": "new fix"}
    moved["latest_reply"] = late["ts"]
    still["latest_reply"] = _days_ago(1.9)
    watermark = _days_ago(1)
    stored = ThreadCheckpoint(
        roots={moved["ts"]: _days_ago(2.5), still["ts"]: still["latest_reply"]}
    )
    asked: list[tuple[str, dict[str, object]]] = []

    result = await _fetch(
        "messages",
        _channel_handler([still, moved], [], {moved["ts"]: [moved, late]}, asked),
        cursor=_cursor(watermark, stored),
        parents=parents_reader(ONE_CHANNEL),
    )

    assert _refs(result) == {f"messages/C1:{moved['ts']}", f"messages/C1:{late['ts']}"}
    assert [body["ts"] for path, body in asked if path == REPLIES_PATH] == [moved["ts"]]
    reread = [body for path, body in asked if path == HISTORY_PATH and "latest" in body]
    assert [(body["latest"], body["limit"]) for body in reread] == [(watermark, HISTORY_PAGE_SIZE)]
    assert json.loads(result.next_cursor or "{}")[_walked("C1")] == watermark
    state = _state(result)
    assert state.roots == {moved["ts"]: late["ts"], still["ts"]: still["latest_reply"]}
    assert state.below is None
    assert state.due is not None and state.due > time.time() + THREAD_REREAD_INTERVAL_SECONDS - 60


async def test_a_reread_that_is_not_due_reads_nothing_below_the_watermark(
    parents_reader: ParentsReader,
) -> None:
    moved = {"ts": _days_ago(3), "user": "U1", "text": "x", "reply_count": 1, "latest_reply": "9"}
    asked: list[tuple[str, dict[str, object]]] = []

    await _fetch(
        "messages",
        _channel_handler([moved], [], {}, asked),
        cursor=_cursor(_days_ago(1), ThreadCheckpoint(due=time.time() + 600)),
        parents=parents_reader(ONE_CHANNEL),
    )

    assert [body for path, body in asked if "latest" in body or path == REPLIES_PATH] == []


async def test_a_rate_limited_reply_read_keeps_the_root_owed_for_the_next_pass(
    parents_reader: ParentsReader,
) -> None:
    newer = {"ts": _days_ago(3), "user": "U1", "text": "one", "reply_count": 1}
    older = {"ts": _days_ago(4), "user": "U1", "text": "two", "reply_count": 1}
    reply = {"ts": _days_ago(0.4), "thread_ts": newer["ts"], "user": "U1", "text": "r1"}
    older_reply = {"ts": _days_ago(0.3), "thread_ts": older["ts"], "user": "U1", "text": "r2"}
    newer["latest_reply"] = reply["ts"]
    older["latest_reply"] = older_reply["ts"]
    fresh = {"ts": _days_ago(0.2), "user": "U1", "text": "new today"}
    threads = {newer["ts"]: [newer, reply], older["ts"]: [older, older_reply]}
    asked: list[tuple[str, dict[str, object]]] = []

    limited = await _fetch(
        "messages",
        _channel_handler([newer, older], [fresh], threads, asked, frozenset({older["ts"]})),
        cursor=_cursor(_days_ago(1)),
        parents=parents_reader(ONE_CHANNEL),
    )

    assert limited.retry_after_seconds is None
    assert _refs(limited) == {
        f"messages/C1:{fresh['ts']}",
        f"messages/C1:{newer['ts']}",
        f"messages/C1:{older['ts']}",
        f"messages/C1:{reply['ts']}",
    }
    assert json.loads(limited.next_cursor or "{}")[_walked("C1")] == fresh["ts"]
    assert _state(limited).owed == [older["ts"]]

    asked.clear()
    resumed = await _fetch(
        "messages",
        _channel_handler([newer, older], [], threads, asked),
        cursor=limited.next_cursor,
        parents=parents_reader(ONE_CHANNEL),
    )

    assert _refs(resumed) == {f"messages/C1:{older_reply['ts']}"}
    assert [body for path, body in asked if "latest" in body] == []
    assert [body["ts"] for path, body in asked if path == REPLIES_PATH] == [older["ts"]]
    assert _state(resumed).owed == []


async def test_a_reply_rate_limit_closes_reply_reads_but_not_the_next_channels_history(
    parents_reader: ParentsReader,
) -> None:
    first = {"ts": "1700000100.000100", "user": "U1", "text": "a", "reply_count": 1}
    second = {"ts": "1700000200.000100", "user": "U1", "text": "b", "reply_count": 1}
    asked: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users.list":
            return _ok({"members": []})
        body = json.loads(request.content)
        asked.append((request.url.path, body["channel"]))
        if request.url.path == HISTORY_PATH:
            return _ok({"messages": [first if body["channel"] == "C1" else second]})
        return httpx.Response(429, headers={"retry-after": "60"}, json={"ok": False})

    result = await _fetch("messages", handle, parents=parents_reader(TWO_CHANNELS))

    assert result.retry_after_seconds is None
    assert asked == [(HISTORY_PATH, "C1"), (REPLIES_PATH, "C1"), (HISTORY_PATH, "C2")]
    assert _refs(result) == {f"messages/C1:{first['ts']}", f"messages/C2:{second['ts']}"}


async def test_a_reread_reads_one_page_a_pass_and_resumes_below_it(
    parents_reader: ParentsReader,
) -> None:
    upper = {"ts": _days_ago(2), "user": "U1", "text": "u", "reply_count": 1, "latest_reply": "1"}
    lower = {"ts": _days_ago(3), "user": "U1", "text": "l", "reply_count": 1, "latest_reply": "2"}
    watermark = _days_ago(1)
    asked: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users.list":
            return _ok({"members": []})
        body = json.loads(request.content)
        if request.url.path == REPLIES_PATH:
            return _thread(request, [upper, lower])
        if "latest" not in body:
            return _ok({"messages": []})
        asked.append(body)
        if body["latest"] == watermark:
            return _ok({"messages": [upper], "response_metadata": {"next_cursor": "more"}})
        return _ok({"messages": [lower]})

    first = await _fetch(
        "messages", handle, cursor=_cursor(watermark), parents=parents_reader(ONE_CHANNEL)
    )
    assert [body["latest"] for body in asked] == [watermark]
    assert _state(first).below == upper["ts"]
    assert _state(first).due is None

    second = await _fetch(
        "messages", handle, cursor=first.next_cursor, parents=parents_reader(ONE_CHANNEL)
    )
    assert [body["latest"] for body in asked] == [watermark, upper["ts"]]
    assert _state(second).below is None
    assert _state(second).due is not None
    assert set(_state(second).roots) == {upper["ts"], lower["ts"]}


async def test_an_owed_thread_slack_no_longer_serves_leaves_the_queue(
    parents_reader: ParentsReader,
) -> None:
    gone = {"ts": "1700000100.000100", "user": "U1", "text": "x", "reply_count": 1}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users.list":
            return _ok({"members": []})
        if request.url.path == HISTORY_PATH:
            return _ok({"messages": [gone]})
        return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})

    result = await _fetch("messages", handle, parents=parents_reader(ONE_CHANNEL))

    assert _refs(result) == {f"messages/C1:{gone['ts']}"}
    assert _state(result).owed == []


async def test_a_thread_longer_than_a_page_resumes_after_its_last_read_reply(
    parents_reader: ParentsReader,
) -> None:
    root = {"ts": "1700000100.000100", "user": "U1", "text": "long", "reply_count": 3}
    replies = [
        {"ts": f"17000002{i}0.000100", "thread_ts": root["ts"], "user": "U1", "text": f"r{i}"}
        for i in range(3)
    ]
    asked: list[dict[str, object]] = []
    limit_second = [True]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users.list":
            return _ok({"members": []})
        body = json.loads(request.content)
        if request.url.path == HISTORY_PATH:
            return _ok({"messages": [root]} if "oldest" not in body else {"messages": []})
        asked.append(body)
        if "oldest" not in body:
            return _ok(
                {"messages": [root, *replies[:2]], "response_metadata": {"next_cursor": "c"}}
            )
        if limit_second[0]:
            return httpx.Response(429, headers={"retry-after": "60"}, json={"ok": False})
        later = [reply for reply in replies if reply["ts"] > body["oldest"]]
        return _ok({"messages": [root, *later]})

    first = await _fetch("messages", handle, parents=parents_reader(ONE_CHANNEL))

    assert _refs(first) == {f"messages/C1:{m['ts']}" for m in (root, *replies[:2])}
    assert _state(first).owed == [root["ts"]]
    assert _state(first).reading == {root["ts"]: replies[1]["ts"]}

    limit_second[0] = False
    asked.clear()
    second = await _fetch(
        "messages", handle, cursor=first.next_cursor, parents=parents_reader(ONE_CHANNEL)
    )

    assert [body.get("oldest") for body in asked] == [replies[1]["ts"]]
    assert _refs(second) == {f"messages/C1:{replies[2]['ts']}"}
    assert _state(second).owed == []
    assert _state(second).reading == {}
