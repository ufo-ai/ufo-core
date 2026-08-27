"""The Redis-hub extension's proof: the frame wire codec round-trips every LiveFrame kind, the
manifest's build wires the backend from `config.hub.url` (a missing URL fails loud), and the live
XADD/XREAD path runs against the compose Redis — a blocking-read timeout is an idle tick the
subscribe survives, and frames cross event loops (the DBOS-loop-publishes, serve-loop-tails
topology) because clients are per running loop."""

import asyncio
import json
import os
import socket
import threading
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_redis_hub.manifest as ext
from pydantic import ValidationError
from redis.asyncio import Redis
from ufo_ext_redis_hub.stream_hub import (
    ACTIVITY_PEEK_FRAMES,
    STREAM_PREFIX,
    RedisStreamHub,
    frame_from_payload,
    frame_payload,
)
from ufo_ext_redis_hub.stream_terminal import RedisTerminals

from ufo.blob import FilesystemBlobStore
from ufo.hub import (
    Absorbed,
    Activity,
    ArrivalQueued,
    CostTick,
    HubFrame,
    Parked,
    SubagentActivity,
    Terminal,
)
from ufo.models.interface import TextDelta
from ufo.schema.records import TerminalFrame

REDIS_TEST_URL = os.environ.get("UFO_TEST_REDIS_URL", "redis://127.0.0.1:5543/0")


def redis_reachable() -> bool:
    host, _, port = REDIS_TEST_URL.removeprefix("redis://").split("/")[0].partition(":")
    try:
        with socket.create_connection((host, int(port or 6379)), timeout=0.5):
            return True
    except OSError:
        return False


needs_redis = pytest.mark.skipif(not redis_reachable(), reason="redis service not reachable")

FRAMES: tuple[HubFrame, ...] = (
    TextDelta(text="hello"),
    Terminal(frame=TerminalFrame(status="done", text="answer", model="claude-opus-4-8")),
    Parked(message="over a spend cap"),
    CostTick(cost_micro_usd=110, tokens=10),
    Activity(text="Listing the workspace."),
    Absorbed(arrivals=(UUID(int=7), UUID(int=8))),
    ArrivalQueued(arrival_id=UUID(int=12)),
    SubagentActivity(
        turn_id=UUID(int=9),
        parent_turn_id=UUID(int=10),
        conversation_id=UUID(int=11),
        profile="general_purpose",
        name="UK sports news",
        activity="Checking the fixtures.",
    ),
)


@pytest.mark.parametrize("frame", FRAMES)
def test_frame_wire_codec_round_trips_every_kind(frame: HubFrame) -> None:
    assert frame_from_payload(frame_payload(frame)) == frame


def test_activity_keeps_the_shared_streams_tool_call_wire_shape() -> None:
    assert frame_payload(Activity(text="Reading the changelog.")) == {
        "kind": "tool_call",
        "data": {
            "tool": "activity",
            "preview": "",
            "description": "Reading the changelog.",
        },
    }


def test_tool_call_and_skill_load_wire_frames_decode_as_activity() -> None:
    assert frame_from_payload(
        {
            "kind": "tool_call",
            "data": {"tool": "bash", "preview": '{"command":"ls"}', "description": ""},
        }
    ) == Activity(text="bash")
    assert frame_from_payload({"kind": "skill_load", "data": {"skill": "calendar"}}) == Activity(
        text="Loading calendar."
    )


def test_manifest_registers_the_redis_hub_backend() -> None:
    manifest = ext.manifest()
    assert {spec.backend for spec in manifest.hubs} == {ext.HUB_BACKEND}


def test_build_requires_a_url() -> None:
    with pytest.raises(RuntimeError, match=r"hub\.url is required"):
        ext._build_hub(None)


def test_build_constructs_a_hub_without_connecting() -> None:
    hub = ext._build_hub("redis://localhost:6379/0")
    assert isinstance(hub, RedisStreamHub)
    assert hub.url == "redis://localhost:6379/0"


async def test_terminal_binding_without_runtime_id_uses_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = uuid4()

    class BindingClient:
        async def get(self, _key: str) -> str:
            return json.dumps({"cwd": "/workspace", "member_id": None})

    monkeypatch.setattr(RedisTerminals, "_client", lambda _self: BindingClient())
    terminals = RedisTerminals(
        url="redis://localhost:6379/0", blob=FilesystemBlobStore(root=tmp_path)
    )

    binding = await terminals._read_binding(conversation_id)

    assert binding is not None
    assert binding.runtime_id == conversation_id.hex


@needs_redis
async def test_a_blocking_read_timeout_is_an_idle_tick_the_subscribe_survives() -> None:
    """A socket timeout tighter than the XREAD block window fires inside every idle blocking read —
    the subscribe must keep reading from its cursor and deliver the frame published after several
    such ticks."""
    hub = RedisStreamHub(url=f"{REDIS_TEST_URL}?socket_timeout=0.2")
    turn_id = uuid4()
    received: list[HubFrame] = []

    async def consume() -> None:
        async for _cursor, frame in hub.subscribe(turn_id):
            received.append(frame)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.5)
    await hub.publish(turn_id, TextDelta(text="alive"))
    await asyncio.wait_for(task, timeout=5)
    assert received == [TextDelta(text="alive")]


@needs_redis
async def test_latest_activity_reads_newest_first_across_pages() -> None:
    """The peek walks the stream newest-first in bounded pages, so the activity frame is found
    even when more than one XREVRANGE batch of deltas was published after it, and a newer summary
    supersedes an older one."""
    hub = RedisStreamHub(url=REDIS_TEST_URL)
    turn_id = uuid4()
    assert await hub.latest_activity(turn_id) is None
    call = Activity(text="Listing the workspace.")
    await hub.publish(turn_id, call)
    for index in range(150):
        await hub.publish(turn_id, TextDelta(text=f"delta {index}"))
    assert await hub.latest_activity(turn_id) == call
    load = Activity(text="Loading saved context.")
    await hub.publish(turn_id, load)
    assert await hub.latest_activity(turn_id) == load


@needs_redis
async def test_latest_activity_reads_back_no_further_than_the_peek_bound() -> None:
    """The peek pages back over the newest ACTIVITY_PEEK_FRAMES entries and stops: an activity
    frame that far back is still answered, and one entry more of narration puts it out of reach —
    so a turn only streaming text costs the poll a bounded walk rather than the whole retained
    stream."""
    hub = RedisStreamHub(url=REDIS_TEST_URL)
    turn_id = uuid4()
    call = Activity(text="Listing the workspace.")
    await hub.publish(turn_id, call)
    for index in range(ACTIVITY_PEEK_FRAMES - 1):
        await hub.publish(turn_id, TextDelta(text=f"delta {index}"))
    assert await hub.latest_activity(turn_id) == call
    await hub.publish(turn_id, TextDelta(text="one narration too many"))
    assert await hub.latest_activity(turn_id) is None


@needs_redis
async def test_latest_activity_reads_an_entrys_kind_before_decoding_it() -> None:
    """Only the frame the peek returns is validated: an entry whose kind is not an activity one is
    skipped on the wire tag alone, so a payload that would fail `frame_from_payload` sits between
    the newest entry and the activity without stopping the walk."""
    hub = RedisStreamHub(url=REDIS_TEST_URL)
    turn_id = uuid4()
    call = Activity(text="Listing the workspace.")
    await hub.publish(turn_id, call)
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.xadd(
            f"{STREAM_PREFIX}:{turn_id}",
            {"frame": json.dumps({"kind": "text_delta", "data": {"not": "a delta"}})},
        )
    finally:
        await client.aclose()
    with pytest.raises(ValidationError):
        frame_from_payload({"kind": "text_delta", "data": {"not": "a delta"}})
    assert await hub.latest_activity(turn_id) == call


@needs_redis
async def test_frames_cross_event_loops() -> None:
    """Publish from another thread's own event loop while the main loop tails — the DBOS-loop
    publishes, serve-loop tails topology — so shared loop-bound client state fails this test."""
    hub = RedisStreamHub(url=REDIS_TEST_URL)
    turn_id = uuid4()
    received: list[HubFrame] = []

    async def consume() -> None:
        async for _cursor, frame in hub.subscribe(turn_id):
            received.append(frame)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    loop = asyncio.get_running_loop()
    published = asyncio.Event()
    failures: list[BaseException] = []

    def publish_from_own_loop() -> None:
        try:
            asyncio.run(hub.publish(turn_id, TextDelta(text="cross-loop")))
        except BaseException as error:
            failures.append(error)
        finally:
            loop.call_soon_threadsafe(published.set)

    threading.Thread(target=publish_from_own_loop).start()
    await asyncio.wait_for(published.wait(), timeout=5)
    assert not failures
    await asyncio.wait_for(task, timeout=5)
    assert received == [TextDelta(text="cross-loop")]
