from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from selfhost.blob import FilesystemBlobStore
from selfhost.loop.compaction import COMPACTED_CONTEXT_PREFIX, Compaction
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


@dataclass(frozen=True)
class SummaryModel:
    """Stands in for the summarizing model: it returns a fixed summary so the tests can assert the
    trigger, the before/after records, and the swapped window — never the summary text itself."""

    summary: str = "condensed history"

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=self.summary)
        yield Usage(input_tokens=11, output_tokens=3)


def _compaction(tmp_path: Path, **overrides: int) -> Compaction:
    return Compaction(
        client=SummaryModel(),
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


async def test_before_record_preserves_a_pre_compaction_fact_verbatim(tmp_path: Path) -> None:
    compaction = _compaction(tmp_path, trigger_tokens=10, keep_messages=2)
    messages = _history()
    result, _ = await compaction.maybe_compact(messages)
    record = await compaction.read_record(1)
    assert record is not None
    assert record.before == messages
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
