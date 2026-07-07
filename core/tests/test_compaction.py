from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

import pytest

from selfhost.blob import FilesystemBlobStore
from selfhost.loop.compaction import (
    COMPACTED_CONTEXT_PREFIX,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    Compaction,
    CompactionSummary,
    FileRef,
)
from selfhost.loop.engine import OFFLOAD_NOTICE
from selfhost.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelEvent,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)
from selfhost.schema.records import Usage

HEAD_FACT = "the deploy key is rotated every 30 days HEADSECRET"
TAIL_FACT = "the customer prefers Tuesday demos TAILSECRET"
FIXED_SUMMARY = CompactionSummary(
    intent="condensed history",
    current_work="reviewing the deploy",
    next_step="ship the change",
    concepts=("compaction",),
    files=(FileRef(path="/workspace/notes.md", why="the running notes"),),
)


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

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        content = request.messages[0].content
        self.seen.append(content if isinstance(content, str) else "")
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
    return (
        Message(role="user", content=HEAD_FACT + " " + "x" * 60),
        Message(role="assistant", content="acknowledged " + "x" * 60),
        Message(role="user", content="keep going " + "x" * 60),
        Message(role="assistant", content="working " + "x" * 60),
        Message(role="user", content=TAIL_FACT + " " + "x" * 60),
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


async def test_offloaded_tool_output_paths_are_re_referenced_after_compaction(
    tmp_path: Path,
) -> None:
    """Phase 2: a large tool result the engine offloaded to `.tool-output/<id>.txt` in the head is
    re-referenced by path (not re-inlined) after the boundary, so the model can re-read it; a path
    still visible in the kept tail is not duplicated."""
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
    assert head_path in rendered
    assert tail_path not in rendered


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


async def test_trigger_derives_from_the_model_window(tmp_path: Path) -> None:
    """Phase 3: with no explicit trigger the threshold is the model window less the summary reserve
    and buffer — a window just under it is left alone, just over it compacts."""
    compaction = _compaction(tmp_path, keep_messages=2)
    derived = DEFAULT_CONTEXT_WINDOW_TOKENS - compaction.summary_max_tokens - 13_000
    under = (
        Message(role="user", content="a " + "x" * (derived - 200) * 4),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    )
    result, usage = await compaction.maybe_compact(under)
    assert result is under and usage == ()
    over = (
        Message(role="user", content="a " + "x" * (derived + 200) * 4),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    )
    result, usage = await compaction.maybe_compact(over)
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


async def test_compaction_index_is_monotonic(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    await compaction.maybe_compact(_history())
    await compaction.maybe_compact(_history())
    assert await compaction.read_record(1) is not None
    assert await compaction.read_record(2) is not None
    assert await compaction.read_record(3) is None
