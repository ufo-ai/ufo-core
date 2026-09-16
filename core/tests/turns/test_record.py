import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ufo.harness.models.interface import TextDelta
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SourceRef,
    Sources,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.turns.record import (
    DrainStep,
    ReplyStep,
    ResumedStep,
    TextStep,
    ToolStep,
    TurnRecord,
    fold,
)
from ufo.schema.records import TerminalFrame

TURN_ID = UUID("33333333-3333-4333-8333-333333333333")
ARRIVAL_ID = UUID("88888888-8888-4888-8888-888888888888")
REPLY_ID = UUID("66666666-6666-4666-8666-666666666666")
RUN_ID = UUID("99999999-9999-4999-8999-999999999999")
RUN_CONVERSATION_ID = UUID("77777777-7777-4777-8777-777777777777")
AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PAGE = SourceRef(kind="web", title="Docs", url="https://docs.example/a")


def replay(*frames: LiveFrame) -> TurnRecord:
    record = TurnRecord(id=TURN_ID, steps=(), runs=(), meter=None, end=None)
    for frame in frames:
        record = fold(record, frame, AT)
    return record


def done(text: str) -> Terminal:
    return Terminal(frame=TerminalFrame(status="done", text=text, model="opus"))


def test_the_fold_keeps_words_and_steps_in_the_order_the_stream_told_them() -> None:
    record = replay(
        TextDelta(text="Reading the changelog first."),
        Activity(text="Reading the changelog.", call_id="c1"),
        TextDelta(text="Checking "),
        TextDelta(text="the tags now."),
        Activity(text="Checking the release tags.", call_id="c2"),
        TextDelta(text="It shipped Tuesday."),
    )
    assert record.steps == (
        TextStep(text="Reading the changelog first.", open=False),
        ToolStep(label="Reading the changelog.", call_id="c1", sources=(), open=False),
        TextStep(text="Checking the tags now.", open=False),
        ToolStep(label="Checking the release tags.", call_id="c2", sources=(), open=False),
        TextStep(text="It shipped Tuesday.", open=True),
    )
    assert [step.call_id for step in record.steps if isinstance(step, ToolStep)] == ["c1", "c2"]
    assert record.end is None


def test_a_terminal_closes_every_step_and_ends_the_record() -> None:
    record = replay(TextDelta(text="Words."), done("Words."))
    assert record.steps == (TextStep(text="Words.", open=False),)
    assert record.end is not None
    assert record.end.kind == "terminal"
    assert record.end.at == AT


def test_sources_with_no_step_open_an_unlabelled_one_and_words_close_it() -> None:
    record = replay(Sources(items=(PAGE,)), Sources(items=(PAGE,)), TextDelta(text="Here."))
    assert record.steps == (
        ToolStep(label=None, call_id="", sources=(PAGE,), open=False),
        TextStep(text="Here.", open=True),
    )


def test_a_drain_names_an_arrival_once_and_a_reply_states_itself_once() -> None:
    reply = Reply(id=REPLY_ID, text="Filed it.")
    record = replay(
        TextDelta(text="one "),
        reply,
        reply,
        TextDelta(text="two"),
        Absorbed(arrivals=(ARRIVAL_ID,)),
        Absorbed(arrivals=(ARRIVAL_ID,)),
    )
    assert record.steps == (
        TextStep(text="one two", open=False),
        ReplyStep(id=REPLY_ID, text="Filed it."),
        DrainStep(arrivals=(ARRIVAL_ID,)),
    )


def test_a_resume_closes_the_words_before_it_and_a_park_ends_the_record() -> None:
    record = replay(
        TextDelta(text="Half"),
        Resumed(attempt="a"),
        TextDelta(text="way"),
        Parked(message="Paused."),
    )
    assert record.steps == (
        TextStep(text="Half", open=False),
        ResumedStep(attempt="a"),
        TextStep(text="way", open=False),
    )
    assert record.end is not None
    assert record.end.kind == "parked"


def test_a_runs_frames_nest_under_the_run_they_name_and_end_it() -> None:
    run = SubagentActivity(
        turn_id=RUN_ID,
        parent_turn_id=TURN_ID,
        conversation_id=RUN_CONVERSATION_ID,
        profile="general_purpose",
        name="Lookup",
    )
    record = replay(run, run.model_copy(update={"activity": "Looking"}))
    assert [(held.turn_id, held.running, held.current) for held in record.runs] == [
        (RUN_ID, True, "Looking")
    ]
    ended = fold(record, run.model_copy(update={"status": "done"}), AT)
    assert ended.runs[0].running is False
    assert ended.runs[0].current is None
    assert [event.text for event in ended.runs[0].events] == ["Looking"]


def test_faults_are_logged_and_change_nothing(caplog: pytest.LogCaptureFixture) -> None:
    over = replay(TextDelta(text="Done."), done("Done."))
    orphan = SubagentActivity(
        turn_id=RUN_ID,
        parent_turn_id=UUID("00000000-0000-4000-8000-000000000000"),
        conversation_id=RUN_CONVERSATION_ID,
        profile="general_purpose",
    )
    with caplog.at_level(logging.ERROR):
        assert fold(over, TextDelta(text="more"), AT) == over
        assert fold(over, done("again"), AT) == over
        fresh = TurnRecord(id=TURN_ID, steps=(), runs=(), meter=None, end=None)
        assert fold(fresh, Reply(id=REPLY_ID, text=""), AT) == fresh
        assert len(fold(fresh, orphan, AT).runs) == 1
    assert [record.getMessage() for record in caplog.records] == [
        "record.frame_after_end",
        "record.frame_after_end",
        "record.reply_wordless",
        "record.run_orphan",
    ]
