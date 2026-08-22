import functools
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from connector_payload import CONNECTOR_WINDOW_TOKENS, connector_window

from ufo.blob import FilesystemBlobStore
from ufo.loop.compaction import (
    ANCHOR_RETRY_INSTRUCTION,
    AUTOCOMPACT_BUFFER_TOKENS,
    CHARS_PER_TOKEN,
    COMPACTED_CONTEXT_PREFIX,
    COMPACTION_FORMAT_RESTATEMENT,
    COMPACTION_SUMMARY_MAX_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    FILES_HEADING,
    MAX_ANCHORS_PER_KIND,
    MAX_REFERENCE_PATHS,
    Compaction,
    _CompactionRequest,
    harvest_anchors,
    missing_anchors,
)
from ufo.loop.engine import MAX_OUTPUT_TOKENS, OFFLOAD_NOTICE
from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelEvent,
    ModelRequest,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.models.spec import ReasoningSupport
from ufo.schema.records import Agent, Usage
from ufo.skills.runtime import LoadedSkills, RuntimeSkill, SkillRegistry
from ufo.turns.transcript import Anchor, CompactionSummary, FileRef

HEAD_FACT = "the deploy key is rotated every 30 days HEADSECRET"
TAIL_FACT = "the customer prefers Tuesday demos TAILSECRET"
NOTES_PATH = "/workspace/notes.md"
HISTORY_PAD = "x" * 400
REAL_TRIGGER_TOKENS = 300
SMALL_SUMMARY_RESERVE = 100
HEAVY_TAIL_TOKENS = 830
FIXED_SUMMARY = CompactionSummary(
    intent="condensed history",
    current_work="reviewing the deploy",
    next_step="ship the change",
    concepts=("compaction",),
    files=(FileRef(path=NOTES_PATH, why="the running notes"),),
)
PLAIN_SUMMARY = FIXED_SUMMARY.model_copy(update={"files": ()})


@dataclass(frozen=True)
class SummaryModel:
    """Stands in for the summarizing model: it returns a fixed structured summary as JSON so the
    tests can assert the trigger, the before/after/summary records, and the swapped window — never
    the summary text itself."""

    summary: CompactionSummary = field(default_factory=lambda: FIXED_SUMMARY)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=self.summary.model_dump_json())
        yield Usage(input_tokens=11, output_tokens=3)


@dataclass
class CapturingSummaryModel:
    """Records the summarizer input it is handed, so a test can assert what `_prepare` rendered."""

    seen: list[str] = field(default_factory=list)
    seen_conversation_cache_ttl: list[str] = field(default_factory=list)
    seen_reasoning: list[str] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        content = request.messages[0].content
        self.seen.append(content if isinstance(content, str) else "")
        self.seen_conversation_cache_ttl.append(request.conversation_cache_ttl)
        self.seen_reasoning.append(request.reasoning)
        yield TextDelta(text=FIXED_SUMMARY.model_dump_json())
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass
class OverflowingSummaryModel:
    """Raises a provider context-overflow on its first `fails` calls, then returns a valid summary —
    so the summarize call's prompt-too-long recovery (drop oldest head rounds, retry) runs. Records
    each attempt's rendered input length so a test can prove the head shrank between attempts."""

    fails: int
    calls: int = 0
    seen_lengths: list[int] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        content = request.messages[0].content
        self.seen_lengths.append(len(content) if isinstance(content, str) else 0)
        if self.calls <= self.fails:
            raise RuntimeError("input is too long for the context window")
        yield TextDelta(text=FIXED_SUMMARY.model_dump_json())
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass
class CountingSummaryModel:
    """Answers every summarize call with the same summary and keeps each rendered input, so a test
    can prove how many calls a verification spent and what the retry named."""

    summary: CompactionSummary
    seen: list[str] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        content = request.messages[0].content
        self.seen.append(content if isinstance(content, str) else "")
        yield TextDelta(text=self.summary.model_dump_json())
        yield Usage(input_tokens=9, output_tokens=4)


@dataclass
class RecoveringSummaryModel:
    """Drops the head's older offloaded paths on the first call and cites them on the second — a
    summarizer that takes the correction, so the retry's recovery is asserted end to end."""

    paths: tuple[str, ...]
    seen: list[str] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        content = request.messages[0].content
        self.seen.append(content if isinstance(content, str) else "")
        summary = (
            PLAIN_SUMMARY
            if len(self.seen) == 1
            else PLAIN_SUMMARY.model_copy(
                update={
                    "files": tuple(
                        FileRef(path=path, why="the offloaded output") for path in self.paths
                    )
                }
            )
        )
        yield TextDelta(text=summary.model_dump_json())
        yield Usage(input_tokens=9, output_tokens=4)


@dataclass
class FailingAnchorRetryModel:
    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("provider unavailable")
        yield TextDelta(text=PLAIN_SUMMARY.model_dump_json())
        yield Usage(input_tokens=9, output_tokens=4)


@dataclass(frozen=True)
class RawTextModel:
    """Returns fixed raw text (not JSON) so a test can prove an unparseable summary fails loud."""

    text: str

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=self.text)
        yield Usage(input_tokens=1, output_tokens=1)


def _compaction(tmp_path: Path, model: object = None, **overrides: object) -> Compaction:
    return Compaction(
        client=model or SummaryModel(),
        model="claude-opus-4-8",
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=uuid4(),
        **overrides,
    )


def _history() -> tuple[Message, ...]:
    """A head that outweighs any summary of it: the budget invariant fails a compaction whose
    replacement window is not smaller than the window it replaced, which a five-line history only
    satisfies when its messages carry more than the rendered sections do. `NOTES_PATH` is in the
    head because `FIXED_SUMMARY` cites it — reference integrity cuts a path the head never named."""
    return (
        Message(role="user", content=f"{HEAD_FACT} read {NOTES_PATH} {HISTORY_PAD}"),
        Message(role="assistant", content="acknowledged " + HISTORY_PAD),
        Message(role="user", content="keep going " + HISTORY_PAD),
        Message(role="assistant", content="working " + HISTORY_PAD),
        Message(role="user", content=TAIL_FACT + " " + HISTORY_PAD),
    )


def _offloaded_history(paths: tuple[str, ...]) -> tuple[Message, ...]:
    """A head that offloaded more tool results than the durable-reference block can carry: with more
    than `MAX_REFERENCE_PATHS` paths, the oldest reach the replacement window only if the summary
    itself cites them."""
    head: list[Message] = [Message(role="user", content="run the batch " + HISTORY_PAD)]
    for index, path in enumerate(paths):
        head.append(Message(role="assistant", content=f"call {index} " + HISTORY_PAD))
        head.append(
            Message(role="user", content="preview…" + OFFLOAD_NOTICE.format(total=9_999, path=path))
        )
    return (
        *head,
        Message(role="assistant", content="ran the last one " + HISTORY_PAD),
        Message(role="user", content="tail " + HISTORY_PAD),
    )


def _heavy_head_history() -> tuple[Message, ...]:
    """A window whose weight is all in the head and whose last round is a few tokens: with room to
    spare under the trigger, the budget invariant is on the summary alone."""
    return (
        Message(role="user", content=f"{HEAD_FACT} {HISTORY_PAD}"),
        Message(role="assistant", content="working " + HISTORY_PAD),
        Message(role="user", content="more " + HISTORY_PAD),
        Message(role="assistant", content="ok"),
        Message(role="user", content="tail"),
    )


def _many_rounds(count: int) -> tuple[Message, ...]:
    messages: list[Message] = [Message(role="user", content="start " + "x" * 40)]
    for index in range(count):
        messages.append(Message(role="assistant", content=f"round {index} " + "x" * 40))
        messages.append(Message(role="user", content=f"result {index} " + "x" * 40))
    return tuple(messages)


async def test_history_under_the_window_is_left_untouched(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, keep_messages=2)
    messages = _history()
    result, usage = await compaction.maybe_compact(messages)
    assert result == messages
    assert usage == ()
    assert await compaction.read_record(1) is None


async def test_force_compacts_below_the_trigger(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=1_000_000, keep_messages=2)
    messages = _history()
    result, usage = await compaction.maybe_compact(messages, force=True)
    assert len(usage) == 1
    assert len(result) == 3
    assert isinstance(result[0].content, str)
    assert result[0].content.startswith(COMPACTED_CONTEXT_PREFIX)
    assert result[-1].content == messages[-1].content


async def test_compaction_preserves_each_active_ref_with_its_exact_request(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=1_000_000, keep_messages=2)
    first_ref = UUID("11111111-1111-1111-1111-111111111111")
    second_ref = UUID("22222222-2222-2222-2222-222222222222")
    first = f"<context>\nmessage_ref: {first_ref}\nsender: Alice\n</context>\napprove access"
    second = f"<context>\nmessage_ref: {second_ref}\nsender: Bob\n</context>\nonly inspect access"

    result, usage = await compaction.maybe_compact(
        _history(),
        force=True,
        active_requests=(first, second),
    )

    assert len(usage) == 1
    rendered = result[0].content
    assert isinstance(rendered, str)
    assert first in rendered
    assert second in rendered
    assert rendered.index(first) < rendered.index(second)


async def test_force_noops_when_the_window_is_within_keep_messages(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=1_000_000, keep_messages=2)
    messages = (Message(role="user", content="only one message"),)
    result, usage = await compaction.maybe_compact(messages, force=True)
    assert result is messages
    assert usage == ()


async def test_force_noops_when_no_assistant_boundary_exists(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=1_000_000, keep_messages=2)
    messages = (
        Message(role="user", content="a"),
        Message(role="user", content="b"),
        Message(role="user", content="c"),
    )
    result, usage = await compaction.maybe_compact(messages, force=True)
    assert result is messages
    assert usage == ()


async def test_history_over_the_window_compacts_and_keeps_the_tail_verbatim(
    tmp_path: Path,
) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    messages = _history()
    result, usage = await compaction.maybe_compact(messages)
    assert len(usage) == 1
    assert len(result) == 3
    assert isinstance(result[0].content, str)
    assert result[0].content.startswith(COMPACTED_CONTEXT_PREFIX)
    assert "condensed history" in result[0].content
    assert result[-1].content == messages[-1].content
    assert TAIL_FACT in str(result[-1].content)
    assert HEAD_FACT not in result[0].content


async def test_the_summary_is_a_validated_structured_object(tmp_path: Path) -> None:
    """Phase 1: one metered call yields a typed CompactionSummary with populated intent/next_step,
    rendered into the swapped window and round-tripped through the durable record."""
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    result, _ = await compaction.maybe_compact(_history())
    record = await compaction.read_record(1)
    assert record is not None
    assert isinstance(record.summary, CompactionSummary)
    assert record.summary.intent == "condensed history"
    assert record.summary.next_step == "ship the change"
    assert record.summary.files[0].path == "/workspace/notes.md"
    rendered = str(result[0].content)
    assert "## Primary request and intent" in rendered
    assert "## Next step" in rendered
    assert "/workspace/notes.md — the running notes" in rendered


async def test_before_record_preserves_a_pre_compaction_fact_verbatim(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    result, _ = await compaction.maybe_compact(_history())
    record = await compaction.read_record(1)
    assert record is not None
    assert record.before == _history()
    assert any(HEAD_FACT in str(message.content) for message in record.before)
    assert record.after == result


async def test_compaction_boundary_never_orphans_a_tool_result_or_repeats_a_role(
    tmp_path: Path,
) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="start " + "x" * 40),
        Message(
            role="assistant",
            content=(TextBlock(text="run"), ToolUseBlock(id="t1", name="bash", input={"c": "ls"})),
        ),
        Message(role="user", content=(ToolResultBlock(tool_use_id="t1", content="a"),)),
        Message(
            role="assistant",
            content=(
                TextBlock(text="run2"),
                ToolUseBlock(id="t2", name="bash", input={"c": "pwd"}),
            ),
        ),
        Message(role="user", content=(ToolResultBlock(tool_use_id="t2", content="/tmp"),)),
        Message(role="assistant", content="done"),
    )
    result, usage = await compaction.maybe_compact(messages)
    assert len(usage) == 1
    assert result[0].role == "user"
    assert result[1].role == "assistant"
    roles = [message.role for message in result]
    assert all(earlier != later for earlier, later in pairwise(roles))
    tool_uses = {
        block.id
        for message in result
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolUseBlock)
    }
    tool_results = {
        block.tool_use_id
        for message in result
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock)
    }
    assert tool_results <= tool_uses


async def test_head_images_become_markers_in_the_summarizer_input(tmp_path: Path) -> None:
    """Phase 2: the summarizer sees an `[image]` marker where an inline image sat, never its
    base64 payload — the image is dropped to a marker in the prepared head, not silently gone."""
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(
            role="user",
            content=(
                TextBlock(text="look at this"),
                ImageBlock(source=ImageSource(media_type="image/png", data="SECRETBASE64")),
            ),
        ),
        Message(role="assistant", content="acknowledged " + "x" * 40),
        Message(role="user", content="keep going " + "x" * 40),
        Message(role="assistant", content="working " + "x" * 40),
        Message(role="user", content="tail " + "x" * 40),
    )
    await compaction.maybe_compact(messages)
    assert model.seen
    assert "[image]" in model.seen[0]
    assert "SECRETBASE64" not in model.seen[0]


async def test_summarizer_requests_the_5m_conversation_cache_ttl(tmp_path: Path) -> None:
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="begin " + "x" * 40),
        Message(role="assistant", content="working " + "x" * 40),
        Message(role="user", content="tail " + "x" * 40),
    )
    await compaction.maybe_compact(messages)
    assert model.seen_conversation_cache_ttl == ["5m"]


async def test_summarizer_uses_required_reasoning_minimum(tmp_path: Path) -> None:
    model = CapturingSummaryModel()
    compaction = _compaction(
        tmp_path,
        model=model,
        trigger_tokens=1,
        keep_messages=2,
        reasoning=ReasoningSupport(
            supported=True, tools_with_reasoning=True, default_on=True, can_disable=False
        ),
    )
    await compaction.maybe_compact(
        (
            Message(role="user", content="begin " + "x" * 40),
            Message(role="assistant", content="working " + "x" * 40),
            Message(role="user", content="tail " + "x" * 40),
        )
    )
    assert model.seen_reasoning == ["low"]


async def test_summarizer_input_closes_with_the_format_restatement(tmp_path: Path) -> None:
    """The rendered head ends with the JSON-format restatement: at a full-scale head the system
    prompt sits too far back to govern the reply, and without a trailing instruction the model can
    continue the conversation instead of summarizing it."""
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="begin " + "x" * 40),
        Message(role="assistant", content="acknowledged " + "x" * 40),
        Message(role="user", content="keep going " + "x" * 40),
        Message(role="assistant", content="working"),
        Message(role="user", content="tail"),
    )
    await compaction.maybe_compact(messages)
    assert model.seen[0].endswith(COMPACTION_FORMAT_RESTATEMENT)


async def test_summarizer_input_folds_verbatim_repetition(tmp_path: Path) -> None:
    """A head dominated by one word-sequence repeated verbatim reaches the summarizer as a single
    copy plus a count marker, with the surrounding instruction intact — rendered verbatim at this
    scale, Anthropic refuses the summarize request outright (stop_reason=refusal)."""
    salad = ("river stone cedar orbit lantern meadow copper harbor velvet winter " * 800)[:49_000]
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="Persistent instruction: retain RETENTION-abc. " + salad),
        Message(role="assistant", content="Continue retaining the instruction. " + salad),
        Message(role="user", content="keep going " + salad),
        Message(role="assistant", content="working"),
        Message(role="user", content="tail"),
    )
    await compaction.maybe_compact(messages)
    seen = model.seen[0]
    assert "Persistent instruction: retain RETENTION-abc." in seen
    assert "[repeated 731 times]" in seen
    assert len(seen) < 2_000


async def test_repetition_below_the_fold_threshold_reaches_the_summarizer_verbatim(
    tmp_path: Path,
) -> None:
    nine = " ".join(["spam"] * 9)
    ten = " ".join(["spam"] * 10)
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="first " + nine),
        Message(role="assistant", content="then " + ten),
        Message(role="user", content="keep going " + "x" * 40),
        Message(role="assistant", content="working"),
        Message(role="user", content="tail"),
    )
    await compaction.maybe_compact(messages)
    seen = model.seen[0]
    assert nine in seen
    assert "spam [repeated 10 times]" in seen


async def test_offloaded_tool_output_paths_are_re_referenced_after_compaction(
    tmp_path: Path,
) -> None:
    """Phase 2: a large tool result the engine offloaded to `.tool-output/<id>.txt` in the head is
    re-referenced by path (not re-inlined) after the boundary, so the model can re-read it; a path
    still visible in the kept tail is not duplicated. The harvested reference must be the path
    exactly: `TOOL_OUTPUT_PATH_RE` ends the match at whitespace, so any wording that puts a
    character straight after `{path}` in the notice harvests a path that opens nothing."""
    head_path = "/workspace/.tool-output/head-call.txt"
    tail_path = "/workspace/.tool-output/tail-call.txt"
    compaction = _compaction(tmp_path, trigger_tokens=1, keep_messages=2)
    messages = (
        Message(role="user", content="run the big command " + "x" * 40),
        Message(role="assistant", content="ran it " + "x" * 40),
        Message(
            role="user",
            content="preview…" + OFFLOAD_NOTICE.format(total=9999, path=head_path),
        ),
        Message(role="assistant", content="ran another " + "x" * 40),
        Message(
            role="user",
            content="tail preview…" + OFFLOAD_NOTICE.format(total=8888, path=tail_path),
        ),
    )
    result, _ = await compaction.maybe_compact(messages)
    rendered = str(result[0].content)
    assert "## Durable references" in rendered
    assert f"- {head_path}\n" in rendered or rendered.endswith(f"- {head_path}")
    assert head_path in rendered
    assert tail_path not in rendered


def test_anchors_are_harvested_by_kind_from_the_head_and_the_pipeline_trackers() -> None:
    """The anchor definition the runtime gate and the eval bars share: paths and error classes come
    out of the head's own text by pattern, skills out of the load tracker, and a member request
    contributes its ref — the authority line — rather than its prose."""
    path = "/workspace/.tool-output/call-1.txt"
    ref = UUID("33333333-3333-3333-3333-333333333333")
    head = f"read {path}, the write raised ValidationError, retried once"
    request = f"<context>\nmessage_ref: {ref}\nsender: Alice\n</context>\napprove access"

    anchors = harvest_anchors(head, ("office-docx",), (request,))

    assert {(anchor.kind, anchor.literal) for anchor in anchors} == {
        ("tool_output", path),
        ("error", "ValidationError"),
        ("skill", "office-docx"),
        ("request", str(ref)),
    }


def test_anchor_harvest_keeps_the_most_recent_of_a_kind() -> None:
    paths = [
        f"/workspace/.tool-output/call-{index}.txt" for index in range(MAX_ANCHORS_PER_KIND + 3)
    ]
    anchors = harvest_anchors(" ".join(paths), (), ())
    assert [anchor.literal for anchor in anchors] == paths[3:]


def test_an_anchor_survives_only_by_being_reproduced() -> None:
    carried = "## Files and outputs\n- /workspace/notes.md — the running notes"
    anchors = (
        Anchor(kind="tool_output", literal="/workspace/notes.md"),
        Anchor(kind="error", literal="ValidationError"),
    )
    assert missing_anchors(anchors, carried) == (anchors[1],)


async def test_a_dropped_anchor_is_retried_once_then_recorded_as_a_lossy_compaction(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The harvester keeps only the most recent `MAX_REFERENCE_PATHS` paths, so an older offload can
    only cross the boundary in the summary's own files field. A summary that carries neither buys
    exactly one re-summarize naming the misses, and a second summary that still drops them is
    accepted — recorded lossy in the compaction record and in one log record, never a dead turn."""
    paths = tuple(f"/workspace/.tool-output/call-{index}.txt" for index in range(7))
    model = CountingSummaryModel(summary=PLAIN_SUMMARY)
    compaction = _compaction(tmp_path, model=model, trigger_tokens=10, keep_messages=2)

    with caplog.at_level(logging.INFO, logger="ufo"):
        result, usage = await compaction.maybe_compact(_offloaded_history(paths))

    assert len(model.seen) == 2
    assert len(usage) == 2
    dropped = list(paths[: len(paths) - MAX_REFERENCE_PATHS])
    assert ANCHOR_RETRY_INSTRUCTION not in model.seen[0]
    assert ANCHOR_RETRY_INSTRUCTION in model.seen[1]
    assert all(path in model.seen[1].split(ANCHOR_RETRY_INSTRUCTION)[1] for path in dropped)
    record = await compaction.read_record(1)
    assert record is not None
    verification = record.summary.verification
    assert verification is not None
    assert verification.retried
    assert [anchor.literal for anchor in verification.missing] == dropped
    assert {anchor.kind for anchor in verification.missing} == {"tool_output"}
    assert verification.anchors == len(paths)
    assert verification.after_tokens < verification.before_tokens
    assert all(path not in str(result[0].content) for path in dropped)
    logged = next(record for record in caplog.records if record.message == "compaction.verified")
    assert logged.ufo["missing"] == [f"tool_output:{path}" for path in dropped]
    assert logged.ufo["anchors"] == len(paths)
    assert logged.ufo["retried"] is True
    assert logged.ufo["reason"] == "auto"


async def test_a_retry_that_carries_the_named_anchors_records_a_clean_compaction(
    tmp_path: Path,
) -> None:
    """The retry is worth its call: the second summary cites the paths the instruction named, they
    render under the files heading, and the recorded verification carries no miss."""
    paths = tuple(f"/workspace/.tool-output/call-{index}.txt" for index in range(7))
    dropped = paths[: len(paths) - MAX_REFERENCE_PATHS]
    model = RecoveringSummaryModel(paths=dropped)
    compaction = _compaction(tmp_path, model=model, trigger_tokens=10, keep_messages=2)

    result, usage = await compaction.maybe_compact(_offloaded_history(paths))

    assert len(model.seen) == 2
    assert len(usage) == 2
    rendered = str(result[0].content)
    assert FILES_HEADING in rendered
    assert all(path in rendered for path in dropped)
    record = await compaction.read_record(1)
    assert record is not None
    verification = record.summary.verification
    assert verification is not None
    assert verification.missing == ()
    assert verification.retried


async def test_a_failed_anchor_retry_installs_the_verified_first_summary(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = tuple(f"/workspace/.tool-output/call-{index}.txt" for index in range(7))
    model = FailingAnchorRetryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=10, keep_messages=2)

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result, usage = await compaction.maybe_compact(_offloaded_history(paths))

    assert model.calls == 2
    assert len(usage) == 1
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)
    record = await compaction.read_record(1)
    assert record is not None
    verification = record.summary.verification
    assert verification is not None
    assert verification.retried
    assert [anchor.literal for anchor in verification.missing] == list(paths[:2])
    failure = next(
        record for record in caplog.records if record.message == "compaction.anchor_retry_failed"
    )
    assert failure.ufo["error_class"] == "RuntimeError"


async def test_a_summary_path_the_window_never_mentioned_never_reaches_the_render(
    tmp_path: Path,
) -> None:
    """Reference integrity: `files` is model-authored, and a path rendered under the files heading
    is read as fact on the next round. A path absent from the pre-compaction window is cut from the
    render and from the persisted summary, and named in the verification instead."""
    invented = "/workspace/invented-by-the-model.md"
    model = SummaryModel(
        summary=FIXED_SUMMARY.model_copy(
            update={"files": (FileRef(path=invented, why="the plan"),)}
        )
    )
    compaction = _compaction(tmp_path, model=model, trigger_tokens=10, keep_messages=2)

    result, _ = await compaction.maybe_compact(_history())

    rendered = str(result[0].content)
    assert invented not in rendered
    assert FILES_HEADING not in rendered
    record = await compaction.read_record(1)
    assert record is not None
    assert record.summary.files == ()
    assert record.summary.verification is not None
    assert record.summary.verification.dropped_paths == (invented,)


async def test_a_summary_that_outweighs_the_window_fails_the_turn_loud(tmp_path: Path) -> None:
    """The budget invariant: with a head heavier than the render's floor plus the summary's own
    output ceiling, a replacement at or above the window it replaces is the pipeline's own doing, so
    it never installs and nothing is persisted."""
    bloated = PLAIN_SUMMARY.model_copy(update={"intent": "restated at length " * 1_000})
    compaction = _compaction(
        tmp_path,
        model=SummaryModel(summary=bloated),
        trigger_tokens=REAL_TRIGGER_TOKENS,
        summary_max_tokens=SMALL_SUMMARY_RESERVE,
        keep_messages=1,
    )

    with pytest.raises(RuntimeError, match="did not shrink the window"):
        await compaction.maybe_compact(_heavy_head_history())

    assert await compaction.read_record(1) is None


async def test_a_replacement_still_over_the_trigger_fails_the_turn_loud(tmp_path: Path) -> None:
    """The other half of the invariant: a window that comes back over the trigger compacts again on
    the very next round, spending a summarize call per round for as long as the turn lives. Asserted
    here because the tail plus the ceiling left room under the trigger, so the summary overspent."""
    middling = PLAIN_SUMMARY.model_copy(update={"intent": "restated at length " * 40})
    compaction = _compaction(
        tmp_path,
        model=SummaryModel(summary=middling),
        trigger_tokens=REAL_TRIGGER_TOKENS,
        summary_max_tokens=SMALL_SUMMARY_RESERVE,
        keep_messages=1,
    )

    with pytest.raises(RuntimeError, match="stays over the compaction trigger"):
        await compaction.maybe_compact(_heavy_head_history())


@pytest.mark.parametrize(
    "trigger",
    [
        pytest.param(HEAVY_TAIL_TOKENS - 100, id="tail_over_the_trigger"),
        pytest.param(HEAVY_TAIL_TOKENS + 50, id="tail_with_no_room_left"),
    ],
)
async def test_a_tail_that_leaves_no_room_installs_the_window_and_records_staying_over(
    tmp_path: Path, trigger: int
) -> None:
    """The head is the only thing a compaction compresses. `_select` keeps whole rounds, so one
    round of parallel results or inline images can carry the verbatim tail past the trigger — or to
    just under it, which is the same thing, since the summary still has to fit somewhere. No summary
    brings either window under the trigger and no retry on the same transcript lands differently, so
    the invariant is not asserted and the turn lives. The record says the window stayed over, and
    `tail_tokens` says why."""
    heavy_tail = (
        Message(role="user", content=f"{HEAD_FACT} {HISTORY_PAD}"),
        Message(role="assistant", content="ran the batch"),
        Message(
            role="user",
            content=tuple(
                ToolResultBlock(tool_use_id=f"t{index}", content="result " + HISTORY_PAD)
                for index in range(4)
            ),
        ),
    )
    compaction = _compaction(
        tmp_path,
        trigger_tokens=trigger,
        summary_max_tokens=SMALL_SUMMARY_RESERVE,
        keep_messages=2,
    )

    result, usage = await compaction.maybe_compact(heavy_tail)

    assert len(usage) == 1
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)
    record = await compaction.read_record(1)
    assert record is not None
    verification = record.summary.verification
    assert verification is not None
    assert verification.after_tokens >= trigger
    assert verification.tail_tokens + SMALL_SUMMARY_RESERVE >= trigger
    assert verification.missing == ()
    assert verification.retried is False


async def test_a_clean_compaction_records_its_own_counts(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)

    result, _ = await compaction.maybe_compact(_history())

    record = await compaction.read_record(1)
    assert record is not None
    verification = record.summary.verification
    assert verification is not None
    assert verification.after_tokens < verification.before_tokens
    assert verification.tail_tokens < verification.after_tokens
    assert verification.missing == ()
    assert verification.dropped_paths == ()
    assert verification.retried is False
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)


async def test_loaded_skills_come_from_the_tracker_and_the_tracker_ends_empty(
    tmp_path: Path,
) -> None:
    """The one summary field the pipeline fills: the tracker knows which workflows the head held, so
    the model's answer for it is ignored, and draining it is what tells the rest of the turn those
    bodies are gone from the window."""
    invented = CompactionSummary(
        intent="condensed history",
        current_work="mid-turn",
        next_step="answer",
        loaded_skills=("invented-by-the-model",),
    )
    docx = RuntimeSkill(name="office-docx", description="d", instructions="BODY", depends=("base",))
    base = RuntimeSkill(name="base", description="d", instructions="B")
    tracker = LoadedSkills()
    tracker.reseed((SkillRegistry({"office-docx": docx, "base": base}).closure("office-docx"),))
    compaction = _compaction(
        tmp_path,
        model=SummaryModel(summary=invented),
        trigger_tokens=10,
        keep_messages=2,
        loaded_skills=tracker,
    )

    result, _ = await compaction.maybe_compact(_history())

    record = await compaction.read_record(1)
    assert record is not None
    assert record.summary.loaded_skills == ("office-docx",)
    assert "## Loaded skills\n- office-docx" in str(result[0].content)
    assert "invented-by-the-model" not in str(result[0].content)
    assert tracker.in_context == set()
    assert tracker.asked_for == set()


async def test_a_turn_that_loaded_no_skill_renders_no_loaded_skills_section(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    result, _ = await compaction.maybe_compact(_history())
    assert "## Loaded skills" not in str(result[0].content)


async def test_summarize_recovers_from_a_prompt_too_long_overflow(tmp_path: Path) -> None:
    """Phase 3: when the summarize request itself overflows, the oldest head rounds are dropped and
    the call retried, up to the bound — the retry meters, and the retried request is smaller."""
    model = OverflowingSummaryModel(fails=2)
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    result, usage = await compaction.maybe_compact(_many_rounds(8))
    assert model.calls == 3
    assert len(usage) == 1
    assert model.seen_lengths[-1] < model.seen_lengths[0]
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)


async def test_summarize_gives_up_loud_when_the_overflow_never_clears(tmp_path: Path) -> None:
    """Phase 3: an unshrinkable summarize request fails the turn loud after the bounded retries,
    never loops."""
    model = OverflowingSummaryModel(fails=99)
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    with pytest.raises(RuntimeError, match="too long"):
        await compaction.maybe_compact(_many_rounds(8))
    assert model.calls == 4


async def test_an_empty_summary_fails_loud(tmp_path: Path) -> None:
    model = SummaryModel(summary=CompactionSummary(intent="  ", current_work="w", next_step="n"))
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    with pytest.raises(RuntimeError, match="empty summary"):
        await compaction.maybe_compact(_history())


async def test_an_unparseable_summary_fails_loud(tmp_path: Path) -> None:
    compaction = _compaction(
        tmp_path, model=RawTextModel(text="not json at all"), trigger_tokens=1, keep_messages=2
    )
    with pytest.raises(RuntimeError, match="no JSON summary"):
        await compaction.maybe_compact(_history())


async def test_a_summary_missing_required_fields_fails_loud(tmp_path: Path) -> None:
    """A model that answers with well-formed JSON that isn't the CompactionSummary shape (e.g. it
    echoed a tool call's arguments instead of the requested schema) must fail with the module's own
    RuntimeError, not a raw pydantic ValidationError leaking past this boundary."""
    model = RawTextModel(text='{"query": "create issue in the repo", "source_id": "github"}')
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    with pytest.raises(RuntimeError, match="invalid summary"):
        await compaction.maybe_compact(_history())


async def test_a_summary_with_trailing_characters_still_parses(tmp_path: Path) -> None:
    """The live failure behind #467: the model emits a valid summary object then keeps going — a
    fence, prose, more JSON. The first balanced object is what validates; the tail (which itself
    contains braces, so a last-brace slice would swallow it) must not break the parse."""
    text = (
        f"```json\n{FIXED_SUMMARY.model_dump_json()}\n```\n"
        'Trailing note: {"delegation": ["a}b"]}'
    )
    compaction = _compaction(
        tmp_path, model=RawTextModel(text=text), trigger_tokens=1, keep_messages=2
    )
    result, _ = await compaction.maybe_compact(_history())
    rendered = str(result[0].content)
    assert rendered.startswith(COMPACTED_CONTEXT_PREFIX)
    assert FIXED_SUMMARY.intent in rendered


def test_round_output_budget_fits_under_the_compaction_trigger() -> None:
    """Providers require input + max_tokens <= window and the derived trigger is window less the
    summary reserve and buffer, so with the window cancelled the reserve plus buffer must cover a
    round's full output budget — for every model's context window (the smallest being the base)."""
    assert MAX_OUTPUT_TOKENS <= COMPACTION_SUMMARY_MAX_TOKENS + AUTOCOMPACT_BUFFER_TOKENS


async def test_trigger_derives_from_the_model_window(tmp_path: Path) -> None:
    """Phase 3: with no explicit trigger the threshold is the model window less the summary reserve
    and buffer — a window just under it is left alone, just over it compacts."""
    compaction = _compaction(tmp_path, keep_messages=2)
    derived = (
        DEFAULT_CONTEXT_WINDOW_TOKENS - compaction.summary_max_tokens - AUTOCOMPACT_BUFFER_TOKENS
    )
    under = (
        Message(role="user", content="a " + "x" * (derived - 200) * CHARS_PER_TOKEN),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    )
    result, usage = await compaction.maybe_compact(under)
    assert result is under and usage == ()
    over = (
        Message(role="user", content="a " + "x" * (derived + 200) * CHARS_PER_TOKEN),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    )
    result, usage = await compaction.maybe_compact(over)
    assert len(usage) == 1
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)


async def test_a_connector_heavy_window_over_the_real_trigger_compacts(tmp_path: Path) -> None:
    """The live failure behind #282: a turn re-ingested a connector search result every round and
    never compacted, because URL-dense connector JSON tokenizes at 2.2 characters per token — read
    the window as any sparser and the trigger lands past the provider's own limit, so the provider,
    not the guard, ends the turn. This window really costs CONNECTOR_WINDOW_TOKENS (Anthropic's
    `count_tokens` on `claude-opus-4-8`), which is past the derived trigger, so the guard must fire;
    and the estimate must land near the measured cost in both directions, since a wildly high
    estimate compacts a turn that had room to run."""
    compaction = _compaction(tmp_path)
    derived = (
        DEFAULT_CONTEXT_WINDOW_TOKENS - compaction.summary_max_tokens - AUTOCOMPACT_BUFFER_TOKENS
    )
    window = connector_window()
    assert CONNECTOR_WINDOW_TOKENS > derived
    assert 0.9 <= compaction._tokens(window) / CONNECTOR_WINDOW_TOKENS <= 1.2
    result, usage = await compaction.maybe_compact(window)
    assert len(usage) == 1
    assert str(result[0].content).startswith(COMPACTED_CONTEXT_PREFIX)


async def test_images_count_toward_the_compaction_budget(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=100, keep_messages=2)
    short_text = (
        Message(role="user", content="hi"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="more"),
        Message(role="assistant", content="sure"),
        Message(role="user", content="tail"),
    )
    _, text_usage = await compaction.maybe_compact(short_text)
    assert text_usage == ()
    with_image = (
        Message(
            role="user",
            content=(
                TextBlock(text="hi"),
                ImageBlock(source=ImageSource(media_type="image/png", data="AAAA")),
            ),
        ),
        *short_text[1:],
    )
    _, image_usage = await compaction.maybe_compact(with_image)
    assert len(image_usage) == 1


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(ThinkingBlock(thinking="", signature="s" * 200), id="signature"),
        pytest.param(RedactedThinkingBlock(data="e" * 200), id="encrypted"),
        pytest.param(
            ReasoningItemBlock(id="rs_1", encrypted_content="e" * 200), id="reasoning_item"
        ),
    ],
)
async def test_each_reasoning_kind_counts_toward_the_compaction_budget(
    tmp_path: Path, block: ThinkingBlock | RedactedThinkingBlock | ReasoningItemBlock
) -> None:
    """A reasoning round is mostly opaque bytes — an empty thinking text under `display: omitted`,
    a signature, an encrypted block, a reasoning item whose request asked for no summary — so a
    window the summarizer would render as almost nothing must still trip the trigger on what it
    re-sends to the provider every round. Each kind carries its own window: a redacted block has no
    signature to ride along with, so it must trip on `data`."""
    compaction = _compaction(tmp_path, trigger_tokens=100, keep_messages=2)
    short_text = (
        Message(role="user", content="hi"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="more"),
        Message(role="assistant", content="sure"),
        Message(role="user", content="tail"),
    )
    _, text_usage = await compaction.maybe_compact(short_text)
    assert text_usage == ()
    with_reasoning = (
        short_text[0],
        Message(role="assistant", content=(block, ToolUseBlock(id="t1", name="bash", input={}))),
        *short_text[2:],
    )
    _, reasoning_usage = await compaction.maybe_compact(with_reasoning)
    assert len(reasoning_usage) == 1


async def test_head_reasoning_reaches_the_summarizer_as_text_without_its_signature(
    tmp_path: Path,
) -> None:
    model = CapturingSummaryModel()
    compaction = _compaction(tmp_path, model=model, trigger_tokens=1, keep_messages=2)
    await compaction.maybe_compact(
        (
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content=(
                    ThinkingBlock(thinking="weigh the options", signature="SECRETSIGNATURE"),
                    RedactedThinkingBlock(data="SECRETDATA"),
                    ReasoningItemBlock(
                        id="rs_1",
                        encrypted_content="SECRETENCRYPTEDITEM",
                        summary=("weigh the item",),
                    ),
                    TextBlock(text="done"),
                ),
            ),
            Message(role="user", content="keep going"),
            Message(role="assistant", content="working"),
            Message(role="user", content="tail"),
        )
    )
    assert "weigh the options" in model.seen[0]
    assert "weigh the item" in model.seen[0]
    assert "[redacted reasoning]" in model.seen[0]
    assert "SECRETSIGNATURE" not in model.seen[0]
    assert "SECRETDATA" not in model.seen[0]
    assert "SECRETENCRYPTEDITEM" not in model.seen[0]


def test_the_compact_step_renders_no_window_into_a_cancellation_log(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, agent=Agent(prompt=HEAD_FACT, model="claude-opus-4-8"))
    request = _CompactionRequest(messages=_history(), reason="auto", active_requests=())
    step = functools.partial(Compaction._compact, compaction, request)

    assert HEAD_FACT not in repr(step)
    assert TAIL_FACT not in repr(step)
    assert repr(compaction) == (
        f"Compaction(conversation_id={compaction.conversation_id}, model=claude-opus-4-8)"
    )
    assert repr(request) == "_CompactionRequest(messages=5, reason=auto, active_requests=0)"


async def test_compaction_index_is_monotonic(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    await compaction.maybe_compact(_history())
    await compaction.maybe_compact(_history())
    assert await compaction.read_record(1) is not None
    assert await compaction.read_record(2) is not None
    assert await compaction.read_record(3) is None
