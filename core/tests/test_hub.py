import asyncio
import threading
from collections.abc import AsyncIterator
from uuid import uuid4

from ufo.hub import SUBSCRIBER_QUEUE_FRAMES, InProcessHub, LiveFrame, Terminal
from ufo.models.interface import TextDelta
from ufo.schema.records import TerminalFrame


async def _pending_first(
    stream: AsyncIterator[tuple[str, LiveFrame]],
) -> asyncio.Future[tuple[str, LiveFrame]]:
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    return pending


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


async def test_publish_buffers_for_a_later_subscriber_and_does_not_leak_on_terminal():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="x"))
    assert turn_id in hub._turns
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {}


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
