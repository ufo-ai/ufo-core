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
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.slack import (
    HISTORY_FETCH_BUDGET,
    HISTORY_PATH,
    REPLIES_PATH,
    SlackConnector,
)

from ufo.runtime.access.connectors import (
    Credential,
)
from ufo.runtime.sources import backend as backend_module
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


async def test_a_steady_pass_rereads_only_the_roots_whose_latest_reply_moved(
    parents_reader: ParentsReader,
) -> None:
    moved = {"ts": _days_ago(3), "user": "U1", "text": "vpn token", "reply_count": 2}
    still = {"ts": _days_ago(2), "user": "U1", "text": "font licence", "reply_count": 1}
    late = {"ts": _days_ago(0.5), "thread_ts": moved["ts"], "user": "U1", "text": "new fix"}
    moved["latest_reply"] = late["ts"]
    still["latest_reply"] = _days_ago(1.9)
    watermark = _days_ago(1)
    stored = {moved["ts"]: _days_ago(2.5), still["ts"]: still["latest_reply"]}
    cursor = json.dumps(
        {
            _walked("C1"): watermark,
            "ufo_checkpoints": json.dumps({_walked("C1"): json.dumps(stored)}),
        }
    )
    asked: list[tuple[str, dict[str, object]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == HISTORY_PATH and "latest" not in json.loads(request.content):
            return _ok({"messages": []})
        return _threaded_handler([still, moved], [moved, late], asked)(request)

    result = await _fetch("messages", handle, cursor=cursor, parents=parents_reader(ONE_CHANNEL))

    assert _refs(result) == {f"messages/C1:{moved['ts']}", f"messages/C1:{late['ts']}"}
    assert [body["ts"] for path, body in asked if path == REPLIES_PATH] == [moved["ts"]]
    (reread,) = [body for path, body in asked if path == HISTORY_PATH]
    assert reread["latest"] == watermark and reread["oldest"] < moved["ts"]
    after = json.loads(result.next_cursor or "{}")
    assert after[_walked("C1")] == watermark
    checkpoint = json.loads(json.loads(after["ufo_checkpoints"])[_walked("C1")])
    assert checkpoint == {moved["ts"]: late["ts"], still["ts"]: still["latest_reply"]}
