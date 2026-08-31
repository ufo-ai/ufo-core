"""RedisStreamHub end to end against a REAL Redis — the XADD/XREAD path
`core/tests/test_ext_redis_hub.py` used to claim was "exercised in integration" when no
such test existed. A container is launched with
`docker run -d --rm -p <port>:6379 redis:7-alpine` (the image
metalcraft's docker-compose already pins for this same live-frame data plane), and the extension's
own `_build_hub` constructs the hub exactly as `serve` would from `config.hub.url` — no hand-rolled
redis client. Every LiveFrame kind is published (XADD) and read back (XREAD) over the wire, a
resumed subscription replays only the frames after a cursor, and STREAM_MAXLEN trimming is asserted
through the hub's own `covers` — a trimmed cursor stops being covered, a retained one still is.

`docker`-gated: missing infrastructure fails the required integration gate and skips an optional
local run."""

import asyncio
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
import ufo_ext_redis_hub.manifest as ext
import ufo_ext_redis_hub.stream_hub as stream_hub
from redis.asyncio import Redis
from redis.exceptions import RedisError
from ufo_ext_redis_hub.stream_hub import STREAM_PREFIX, RedisStreamHub
from ufo_testsupport.plugin import integration_dependency_available

from ufo.harness.models.interface import TextDelta
from ufo.runtime.hub import Activity, CostTick, LiveFrame, Parked, Terminal
from ufo.schema.records import TerminalFrame

pytestmark = pytest.mark.docker

REDIS_IMAGE = "redis:7-alpine"
READY_TIMEOUT_S = 10.0
READY_POLL_S = 0.1

FRAMES: tuple[LiveFrame, ...] = (
    TextDelta(text="hello"),
    Terminal(frame=TerminalFrame(status="done", text="answer", model="claude-opus-4-8")),
    Parked(message="over a spend cap"),
    CostTick(cost_micro_usd=110, tokens=10),
    Activity(text="Listing the workspace."),
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _ping(url: str) -> None:
    client = Redis.from_url(url)
    try:
        await client.ping()
    finally:
        await client.aclose()


@pytest.fixture
def redis_url() -> Iterator[str]:
    if not integration_dependency_available(
        shutil.which("docker") is not None, "Docker executable is not available"
    ):
        pytest.skip("Docker executable is not available")
    port = _free_port()
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", f"{port}:6379", REDIS_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    reason = f"Docker cannot run {REDIS_IMAGE}: {started.stderr.strip()}"
    if not integration_dependency_available(started.returncode == 0, reason):
        pytest.skip(reason)
    container = started.stdout.strip()
    url = f"redis://127.0.0.1:{port}/0"
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            try:
                asyncio.run(_ping(url))
                break
            except RedisError:
                if time.monotonic() >= deadline:
                    reason = f"Redis container ({container}) did not become ready in time"
                    integration_dependency_available(False, reason)
                    pytest.skip(reason)
                time.sleep(READY_POLL_S)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


@pytest.fixture
def hub(redis_url: str) -> RedisStreamHub:
    built = ext._build_hub(redis_url)
    assert isinstance(built, RedisStreamHub)
    return built


async def test_every_frame_kind_round_trips_through_real_redis(hub: RedisStreamHub) -> None:
    turn_id = uuid4()
    for frame in FRAMES:
        await hub.publish(turn_id, frame)

    received: list[tuple[str, LiveFrame]] = []
    async for cursor, frame in hub.subscribe(turn_id):
        received.append((cursor, frame))
        if len(received) == len(FRAMES):
            break

    assert [frame for _, frame in received] == list(FRAMES)
    cursors = [cursor for cursor, _ in received]
    assert cursors == sorted(cursors, key=lambda c: tuple(int(part) for part in c.split("-")))
    assert len(set(cursors)) == len(cursors)


async def test_subscribe_resumes_from_a_cursor_replaying_only_newer_frames(
    hub: RedisStreamHub,
) -> None:
    turn_id = uuid4()
    cursors = [await hub.publish(turn_id, frame) for frame in FRAMES]
    resume_after = cursors[2]

    resumed: list[LiveFrame] = []
    async for _, frame in hub.subscribe(turn_id, resume_after):
        resumed.append(frame)
        if len(resumed) == len(FRAMES) - 3:
            break

    assert resumed == list(FRAMES[3:])


async def test_covers_reports_the_retained_window(hub: RedisStreamHub) -> None:
    turn_id = uuid4()
    first_cursor = await hub.publish(turn_id, TextDelta(text="0"))
    last_cursor = await hub.publish(turn_id, TextDelta(text="1"))

    assert await hub.covers(turn_id, first_cursor) is True
    assert await hub.covers(turn_id, last_cursor) is True
    assert await hub.covers(uuid4(), first_cursor) is False
    assert await hub.covers(turn_id, "") is False


async def test_maxlen_trims_the_stream_dropping_the_earliest_frames(
    hub: RedisStreamHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream_hub, "STREAM_MAXLEN", 5)
    turn_id = uuid4()

    first_cursor = await hub.publish(turn_id, TextDelta(text="0"))
    last_cursor = first_cursor
    for i in range(1, 300):
        last_cursor = await hub.publish(turn_id, TextDelta(text=str(i)))

    length = await hub._client().xlen(f"{STREAM_PREFIX}:{turn_id}")
    assert length < 300, "approximate MAXLEN trimming never removed a full node"
    assert await hub.covers(turn_id, first_cursor) is False
    assert await hub.covers(turn_id, last_cursor) is True
