import asyncio
import json
import threading
from collections.abc import AsyncIterator
from uuid import uuid4

from ufo.activity import tool_activity
from ufo.hub import (
    SUBSCRIBER_QUEUE_FRAMES,
    Absorbed,
    InProcessHub,
    LiveFrame,
    Parked,
    SkillLoad,
    Terminal,
    ToolCall,
)
from ufo.models.interface import TextDelta, ToolUseBlock
from ufo.schema.records import TerminalFrame


async def _pending_first(
    stream: AsyncIterator[tuple[str, LiveFrame]],
) -> asyncio.Future[tuple[str, LiveFrame]]:
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    return pending


async def _drop(pending: asyncio.Future[tuple[str, LiveFrame]]) -> None:
    """Abandon a read still waiting on the queue, and let the cancellation land before the caller
    closes the generator behind it."""
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)


def test_tool_activity_uses_one_bounded_frame_shape():
    assert tool_activity(ToolUseBlock(id="s", name="load_skill", input={"name": 7})) == SkillLoad(
        skill=""
    )
    command = "é" * 100
    frame = tool_activity(
        ToolUseBlock(id="t", name="bash", input={"command": command, "user_description": 7})
    )
    assert frame == ToolCall(
        tool="bash",
        preview=json.dumps({"command": command, "user_description": 7}, separators=(",", ":"))[:200]
        + "…",
        description="",
    )


async def test_round_trip_delivers_text_and_terminal():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first(stream)
    delta = TextDelta(text="hello")
    terminal = Terminal(frame=TerminalFrame(status="done", text="hello"))
    await hub.publish(turn_id, delta)
    await hub.publish(turn_id, terminal)
    assert (await first)[1] == delta
    assert (await anext(stream))[1] == terminal
    await stream.aclose()


async def test_publish_returns_a_monotonic_cursor():
    hub = InProcessHub()
    turn_id = uuid4()
    first = await hub.publish(turn_id, TextDelta(text="a"))
    second = await hub.publish(turn_id, TextDelta(text="b"))
    assert int(second) > int(first)


async def test_overflow_drops_oldest():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first(stream)
    for index in range(SUBSCRIBER_QUEUE_FRAMES + 1):
        await hub.publish(turn_id, TextDelta(text=str(index)))
    received = [(await first)[1]]
    for _ in range(SUBSCRIBER_QUEUE_FRAMES - 1):
        received.append((await anext(stream))[1])
    assert received == [
        TextDelta(text=str(index)) for index in range(1, SUBSCRIBER_QUEUE_FRAMES + 1)
    ]
    await stream.aclose()


async def test_subscribe_cleans_up_registry_on_exit():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first(stream)
    await hub.publish(turn_id, TextDelta(text="x"))
    assert (await first)[1] == TextDelta(text="x")
    assert turn_id in hub._turns
    await stream.aclose()
    assert hub._turns == {}


async def test_a_late_subscriber_replays_the_buffered_frames():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="early"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="early")
    await hub.publish(turn_id, TextDelta(text="late"))
    assert (await anext(stream))[1] == TextDelta(text="late")
    await stream.aclose()


async def test_resuming_from_a_cursor_skips_already_seen_frames():
    hub = InProcessHub()
    turn_id = uuid4()
    early = await hub.publish(turn_id, TextDelta(text="early"))
    stream = hub.subscribe(turn_id, cursor=early)
    first = await _pending_first(stream)
    await hub.publish(turn_id, TextDelta(text="late"))
    assert (await first)[1] == TextDelta(text="late")
    await stream.aclose()


async def test_covers_reports_whether_a_cursor_is_still_retained():
    hub = InProcessHub()
    turn_id = uuid4()
    cursor = await hub.publish(turn_id, TextDelta(text="x"))
    assert await hub.covers(turn_id, cursor) is True
    assert await hub.covers(turn_id, "") is False
    assert await hub.covers(uuid4(), cursor) is False


async def test_a_turn_keeps_its_cursor_across_a_reconnect_so_the_gap_is_neither_lost_nor_repeated():
    """A surface that reconnects between frames leaves no subscriber behind while it is away — the
    terminal client disconnects at every op it hands the member's machine. The ring it left goes,
    but the turn's cursor sequence does not: restarting it would issue numbers the client has
    already passed, and the frames published in the gap would be filtered out as seen. What the
    reconnect finds is exactly the gap — replayed whole to a client with no cursor, and resumed
    from for one that has it."""
    hub = InProcessHub()
    turn_id = uuid4()
    seen = await hub.publish(turn_id, TextDelta(text="printed"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="printed")
    await stream.aclose()
    assert hub._turns == {}

    missed = await hub.publish(turn_id, TextDelta(text="while away"))
    assert int(missed) > int(seen)
    assert await hub.covers(turn_id, seen) is False

    resumed = hub.subscribe(turn_id, cursor=seen)
    assert (await anext(resumed))[1] == TextDelta(text="while away")
    await resumed.aclose()


async def test_a_reconnect_without_a_cursor_replays_only_what_it_missed():
    """The client that has not learned to carry a cursor yet — an installed script the surface
    never replaces — reconnects with none, and must not be handed the turn from its first frame:
    it has printed everything before the gap already."""
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="printed"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="printed")
    await stream.aclose()
    await hub.publish(turn_id, TextDelta(text="while away"))

    resumed = hub.subscribe(turn_id)
    assert (await anext(resumed))[1] == TextDelta(text="while away")
    pending = await _pending_first(resumed)
    assert not pending.done()
    await _drop(pending)
    await resumed.aclose()


async def test_a_turn_tailed_after_it_ended_leaves_nothing_behind():
    """A tail opened on a finished turn — the durable status answers it, so nothing ever publishes
    to the stream that tail registered on. It must not outlive the subscriber that made it, or the
    hub grows one entry per turn read back."""
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="x"))
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {} and hub._marks == {}

    stream = hub.subscribe(turn_id)
    await _drop(await _pending_first(stream))
    await stream.aclose()
    assert hub._turns == {} and hub._marks == {}


async def test_a_parked_turn_resumes_its_sequence_because_it_runs_again_under_one_id():
    """A park is not an end: the fold that resumes it dispatches the same turn id, and a sequence
    restarting there would reissue cursors the client already holds."""
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="before"))
    parked = await hub.publish(turn_id, Parked(message="over the cap"))
    assert hub._turns == {}

    resumed = await hub.publish(turn_id, TextDelta(text="after"))
    assert int(resumed) > int(parked)


async def test_publish_buffers_for_a_later_subscriber_and_does_not_leak_on_terminal():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="x"))
    assert turn_id in hub._turns
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {}


async def test_an_absorbed_frame_neither_ends_the_stream_nor_drops_the_ring():
    hub = InProcessHub()
    turn_id = uuid4()
    absorbed = Absorbed(arrivals=(uuid4(), uuid4()))
    await hub.publish(turn_id, absorbed)
    assert turn_id in hub._turns
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == absorbed
    await stream.aclose()


async def test_two_subscribers_both_receive():
    hub = InProcessHub()
    turn_id = uuid4()
    stream_a = hub.subscribe(turn_id)
    stream_b = hub.subscribe(turn_id)
    first_a = await _pending_first(stream_a)
    first_b = await _pending_first(stream_b)
    await hub.publish(turn_id, TextDelta(text="hi"))
    assert (await first_a)[1] == TextDelta(text="hi")
    assert (await first_b)[1] == TextDelta(text="hi")
    await stream_a.aclose()
    await stream_b.aclose()
    assert hub._turns == {}


async def test_publish_from_another_loop_thread_delivers():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first(stream)

    def publish_from_foreign_loop() -> None:
        asyncio.run(hub.publish(turn_id, TextDelta(text="cross-loop")))

    thread = threading.Thread(target=publish_from_foreign_loop)
    thread.start()
    thread.join()
    assert (await asyncio.wait_for(first, timeout=2))[1] == TextDelta(text="cross-loop")
    await stream.aclose()
