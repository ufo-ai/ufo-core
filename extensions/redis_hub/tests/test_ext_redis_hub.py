"""The Redis-hub extension's proof: the frame wire codec round-trips every LiveFrame kind, the
manifest's build wires the backend from `config.hub.url` (a missing URL fails loud), and the live
XADD/XREAD path runs against the compose Redis — a blocking-read timeout is an idle tick the
subscribe survives, and frames cross event loops (the DBOS-loop-publishes, serve-loop-tails
topology) because clients are per running loop."""

import asyncio
import os
import socket
import threading
from uuid import uuid4

import pytest
import ufo_ext_redis_hub.manifest as ext
from ufo_ext_redis_hub.stream_hub import (
    RedisStreamHub,
    frame_from_payload,
    frame_payload,
)

from ufo.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
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

FRAMES: tuple[LiveFrame, ...] = (
    TextDelta(text="hello"),
    Terminal(frame=TerminalFrame(status="done", text="answer", model="claude-opus-4-8")),
    Parked(message="over a spend cap"),
    CostTick(cost_micro_usd=110, tokens=10),
    ToolCall(tool="bash", preview='{"command":"ls"}'),
    SkillLoad(skill="memory"),
)


@pytest.mark.parametrize("frame", FRAMES)
def test_frame_wire_codec_round_trips_every_kind(frame: LiveFrame) -> None:
    assert frame_from_payload(frame_payload(frame)) == frame


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


@needs_redis
async def test_a_blocking_read_timeout_is_an_idle_tick_the_subscribe_survives() -> None:
    """A socket timeout tighter than the XREAD block window fires inside every idle blocking read —
    the subscribe must keep reading from its cursor and deliver the frame published after several
    such ticks."""
    hub = RedisStreamHub(url=f"{REDIS_TEST_URL}?socket_timeout=0.2")
    turn_id = uuid4()
    received: list[LiveFrame] = []

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
async def test_frames_cross_event_loops() -> None:
    """Publish from another thread's own event loop while the main loop tails — the DBOS-loop
    publishes, serve-loop tails topology — so shared loop-bound client state fails this test."""
    hub = RedisStreamHub(url=REDIS_TEST_URL)
    turn_id = uuid4()
    received: list[LiveFrame] = []

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
