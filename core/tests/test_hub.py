import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from selfhost.hub import SUBSCRIBER_QUEUE_FRAMES, InProcessHub, LiveFrame, Terminal
from selfhost.models import TextDelta
from selfhost.schema.records import TerminalFrame


async def _pending_first_frame(stream: AsyncIterator[LiveFrame]) -> asyncio.Future[LiveFrame]:
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    return pending


async def test_round_trip_delivers_text_and_terminal():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first_frame(stream)
    delta = TextDelta(text="hello")
    terminal = Terminal(frame=TerminalFrame(status="done", text="hello"))
    await hub.publish(turn_id, delta)
    await hub.publish(turn_id, terminal)
    assert await first == delta
    assert await anext(stream) == terminal
    await stream.aclose()


async def test_overflow_drops_oldest():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first_frame(stream)
    for index in range(SUBSCRIBER_QUEUE_FRAMES + 1):
        await hub.publish(turn_id, TextDelta(text=str(index)))
    received = [await first]
    for _ in range(SUBSCRIBER_QUEUE_FRAMES - 1):
        received.append(await anext(stream))
    assert received == [
        TextDelta(text=str(index)) for index in range(1, SUBSCRIBER_QUEUE_FRAMES + 1)
    ]
    await stream.aclose()


async def test_subscribe_cleans_up_registry_on_exit():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first_frame(stream)
    await hub.publish(turn_id, TextDelta(text="x"))
    assert await first == TextDelta(text="x")
    assert turn_id in hub.queues
    await stream.aclose()
    assert hub.queues == {}


async def test_publish_with_no_subscribers_is_a_noop():
    hub = InProcessHub()
    await hub.publish(uuid4(), TextDelta(text="x"))
    assert hub.queues == {}


async def test_two_subscribers_both_receive():
    hub = InProcessHub()
    turn_id = uuid4()
    stream_a = hub.subscribe(turn_id)
    stream_b = hub.subscribe(turn_id)
    first_a = await _pending_first_frame(stream_a)
    first_b = await _pending_first_frame(stream_b)
    await hub.publish(turn_id, TextDelta(text="hi"))
    assert await first_a == TextDelta(text="hi")
    assert await first_b == TextDelta(text="hi")
    await stream_a.aclose()
    await stream_b.aclose()
    assert hub.queues == {}


async def test_late_subscriber_sees_only_later_frames():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="early"))
    stream = hub.subscribe(turn_id)
    first = await _pending_first_frame(stream)
    await hub.publish(turn_id, TextDelta(text="late"))
    assert await first == TextDelta(text="late")
    await stream.aclose()
