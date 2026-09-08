import asyncio
import json
import threading
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from ufo.harness.models.interface import ModelRequest, TextDelta, ToolUseBlock
from ufo.runtime.hub import (
    ACTIVITY_PEEK_FRAMES,
    SUBSCRIBER_QUEUE_FRAMES,
    Absorbed,
    Activity,
    InProcessHub,
    LiveFrame,
    Parked,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.turns.activity import (
    ACTIVITY_ARGUMENT_CHARS,
    ACTIVITY_GOAL_CHARS,
    ACTIVITY_RECENT_LABELS,
    ActivitySummarizer,
    activity_line,
)
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


class _ActivityModel:
    model = "gpt-5.6-luna"
    request: ModelRequest | None = None

    async def complete(self, request: ModelRequest) -> str:
        self.request = request
        return "- Reviewing the notes\nthen updating the heading"


async def _check_activity_summarizer_sends_only_bounded_tool_calls():
    model = _ActivityModel()
    line = await ActivitySummarizer(model).summarize(
        ToolUseBlock(id="e", name="edit", input={"content": "x" * 1_000}),
        "Update the release notes" * 100,
    )
    assert line == "Reviewing the notes then updating the heading"
    assert model.request is not None
    payload = json.loads(model.request.messages[0].content)
    assert len(payload["goal"]) == ACTIVITY_GOAL_CHARS
    assert payload["tool_call"]["name"] == "edit"
    assert len(payload["tool_call"]["arguments"]) == ACTIVITY_ARGUMENT_CHARS + 1


async def _check_activity_summarizer_sends_the_labels_already_shown():
    model = _ActivityModel()
    await ActivitySummarizer(model).summarize(
        ToolUseBlock(id="e", name="edit", input={}),
        "Update the release notes",
        tuple(f"Label {index}" for index in range(ACTIVITY_RECENT_LABELS + 2)),
    )
    assert model.request is not None
    payload = json.loads(model.request.messages[0].content)
    assert payload["recent_labels"] == ["Label 2", "Label 3", "Label 4", "Label 5", "Label 6"]


def test_activity_line_normalizes_without_truncating():
    assert activity_line("") is None
    assert activity_line("Checking the release...\n") == "Checking the release"
    assert activity_line("a" * 400) == "a" * 400


async def _check_round_trip_delivers_text_and_terminal():
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


async def _check_publish_returns_a_monotonic_cursor():
    hub = InProcessHub()
    turn_id = uuid4()
    first = await hub.publish(turn_id, TextDelta(text="a"))
    second = await hub.publish(turn_id, TextDelta(text="b"))
    assert int(second) > int(first)


async def _check_overflow_drops_oldest():
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


async def _check_subscribe_cleans_up_registry_once_the_turn_ends():
    hub = InProcessHub()
    turn_id = uuid4()
    stream = hub.subscribe(turn_id)
    first = await _pending_first(stream)
    await hub.publish(turn_id, TextDelta(text="x"))
    assert (await first)[1] == TextDelta(text="x")
    assert turn_id in hub._turns
    await stream.aclose()
    assert turn_id in hub._turns

    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {}


async def _check_latest_activity_peeks_the_newest_activity_frame():
    hub = InProcessHub()
    turn_id = uuid4()
    assert await hub.latest_activity(turn_id) is None
    await hub.publish(turn_id, TextDelta(text="thinking"))
    assert await hub.latest_activity(turn_id) is None
    call = Activity(text="Checking the workspace.")
    await hub.publish(turn_id, call)
    await hub.publish(turn_id, TextDelta(text="more"))
    assert await hub.latest_activity(turn_id) == call
    latest = Activity(text="Saving the result.")
    await hub.publish(turn_id, latest)
    assert await hub.latest_activity(turn_id) == latest


async def _check_latest_activity_reads_back_no_further_than_the_peek_bound():
    """The peek reads the newest ACTIVITY_PEEK_FRAMES and stops: an activity frame that far back is
    still answered, and one frame more of narration puts it out of reach — so a turn only streaming
    text costs the poll a bounded walk rather than the whole ten-thousand-frame ring."""
    hub = InProcessHub()
    turn_id = uuid4()
    call = Activity(text="Checking the workspace.")
    await hub.publish(turn_id, call)
    for index in range(ACTIVITY_PEEK_FRAMES - 1):
        await hub.publish(turn_id, TextDelta(text=f"delta {index}"))
    assert await hub.latest_activity(turn_id) == call
    await hub.publish(turn_id, TextDelta(text="one narration too many"))
    assert await hub.latest_activity(turn_id) is None


async def _check_a_late_subscriber_replays_the_buffered_frames():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="early"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="early")
    await hub.publish(turn_id, TextDelta(text="late"))
    assert (await anext(stream))[1] == TextDelta(text="late")
    await stream.aclose()


async def _check_resuming_from_a_cursor_skips_already_seen_frames():
    hub = InProcessHub()
    turn_id = uuid4()
    early = await hub.publish(turn_id, TextDelta(text="early"))
    stream = hub.subscribe(turn_id, cursor=early)
    first = await _pending_first(stream)
    await hub.publish(turn_id, TextDelta(text="late"))
    assert (await first)[1] == TextDelta(text="late")
    await stream.aclose()


async def _check_covers_reports_whether_a_cursor_is_still_retained():
    hub = InProcessHub()
    turn_id = uuid4()
    cursor = await hub.publish(turn_id, TextDelta(text="x"))
    assert await hub.covers(turn_id, cursor) is True
    assert await hub.covers(turn_id, "") is False
    assert await hub.covers(uuid4(), cursor) is False


async def _check_reconnect_cursor_has_no_gap_or_repeat() -> None:
    """A surface that reconnects between frames leaves no subscriber behind while it is away — the
    terminal client disconnects at every op it hands the member's machine. The in-flight turn keeps
    its ring and its cursor sequence across that gap: restarting the sequence would issue numbers
    the client has already passed, and the frames published in the gap would be filtered out as
    seen. What the reconnect resumes from its cursor is exactly the gap."""
    hub = InProcessHub()
    turn_id = uuid4()
    seen = await hub.publish(turn_id, TextDelta(text="printed"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="printed")
    await stream.aclose()

    missed = await hub.publish(turn_id, TextDelta(text="while away"))
    assert int(missed) > int(seen)
    assert await hub.covers(turn_id, missed) is True

    resumed = hub.subscribe(turn_id, cursor=seen)
    assert (await anext(resumed))[1] == TextDelta(text="while away")
    await resumed.aclose()


async def _check_a_frame_the_dying_stream_never_rendered_replays_on_the_cursor_reconnect():
    """The op race: a tool note lands on the held stream's subscription in the same breath the op
    directive ends it, so the note is taken off the queue but never rendered and never advances the
    client's cursor. The reply's reconnect carries the cursor from before the note, and the ring —
    which must survive the gap with no subscriber attached — replays it."""
    hub = InProcessHub()
    turn_id = uuid4()
    rendered = await hub.publish(turn_id, TextDelta(text="cost tick"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="cost tick")
    note = Activity(text="Checking the workspace.")
    await hub.publish(turn_id, note)
    await stream.aclose()

    resumed = hub.subscribe(turn_id, cursor=rendered)
    assert (await anext(resumed))[1] == note
    await resumed.aclose()


async def _check_a_subscriber_without_a_cursor_replays_the_whole_retained_ring():
    """A tail opened with no cursor — the portal loading mid-turn — wants everything retained,
    across any gap a terminal client's op reconnects left; the Redis hub replays its stream the
    same way. Every wire client carries a cursor, so nothing is double-printed by replaying
    whole."""
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="printed"))
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == TextDelta(text="printed")
    await stream.aclose()
    await hub.publish(turn_id, TextDelta(text="while away"))

    resumed = hub.subscribe(turn_id)
    assert (await anext(resumed))[1] == TextDelta(text="printed")
    assert (await anext(resumed))[1] == TextDelta(text="while away")
    pending = await _pending_first(resumed)
    assert not pending.done()
    await _drop(pending)
    await resumed.aclose()


async def _check_a_turn_tailed_after_it_ended_leaves_nothing_behind():
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


async def _check_a_parked_turn_resumes_its_sequence_because_it_runs_again_under_one_id():
    """A park is not an end: the fold that resumes it dispatches the same turn id, and a sequence
    restarting there would reissue cursors the client already holds."""
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="before"))
    parked = await hub.publish(turn_id, Parked(message="over the cap"))
    assert hub._turns == {}

    resumed = await hub.publish(turn_id, TextDelta(text="after"))
    assert int(resumed) > int(parked)


async def _check_publish_buffers_for_a_later_subscriber_and_does_not_leak_on_terminal():
    hub = InProcessHub()
    turn_id = uuid4()
    await hub.publish(turn_id, TextDelta(text="x"))
    assert turn_id in hub._turns
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {}


def _run_frame(root_turn_id: UUID) -> SubagentActivity:
    return SubagentActivity(
        turn_id=uuid4(),
        parent_turn_id=root_turn_id,
        conversation_id=uuid4(),
        profile="general_purpose",
        activity="Still going.",
    )


async def _check_a_run_outliving_its_root_neither_revives_nor_founds_the_ring():
    """A background child can publish after its root committed its terminal and the ring was
    dropped. The frame has no tail it could reach, so it drops instead of rebuilding a stream
    nothing will ever release — one pinned ring per background spawn for the process's life."""
    hub = InProcessHub()
    root = uuid4()
    await hub.publish(root, TextDelta(text="x"))
    await hub.publish(root, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {} and hub._marks == {}

    assert await hub.publish(root, _run_frame(root)) == ""
    assert hub._turns == {} and hub._marks == {}

    never_ran = uuid4()
    assert await hub.publish(never_ran, _run_frame(never_ran)) == ""
    assert hub._turns == {} and hub._marks == {}


async def _check_a_run_frame_reaches_the_ring_of_a_root_still_in_flight():
    hub = InProcessHub()
    root = uuid4()
    await hub.publish(root, TextDelta(text="x"))
    cursor = await hub.publish(root, _run_frame(root))
    assert cursor == "2"
    stream = hub.subscribe(root)
    assert isinstance((await anext(stream))[1], TextDelta)
    assert isinstance((await anext(stream))[1], SubagentActivity)
    await stream.aclose()


async def _check_an_absorbed_frame_neither_ends_the_stream_nor_drops_the_ring():
    hub = InProcessHub()
    turn_id = uuid4()
    absorbed = Absorbed(arrivals=(uuid4(), uuid4()))
    await hub.publish(turn_id, absorbed)
    assert turn_id in hub._turns
    stream = hub.subscribe(turn_id)
    assert (await anext(stream))[1] == absorbed
    await stream.aclose()


async def _check_two_subscribers_both_receive():
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
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    assert hub._turns == {}


async def _check_publish_from_another_loop_thread_delivers():
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


async def test_hub_async_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 22
    for check in checks:
        await check()
