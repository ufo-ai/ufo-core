from dataclasses import dataclass

import pytest

from ufo.harness.context import CompactionHarness, ContextWindow, is_context_overflow


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    opaque: int = 0
    images: int = 0


def policy(**overrides: int) -> ContextWindow[Message]:
    values = {
        "context_tokens": 100,
        "summary_tokens": 10,
        "buffer_tokens": 20,
        "keep_messages": 2,
        "chars_per_token": 2,
        "image_tokens": 8,
        **overrides,
    }
    return ContextWindow(
        role=lambda message: message.role,
        text=lambda message: message.text,
        opaque_chars=lambda message: message.opaque,
        image_count=lambda message: message.images,
        **values,
    )


def test_selection_keeps_complete_tool_rounds() -> None:
    messages = (
        Message("user", "request"),
        Message("assistant", "call one"),
        Message("user", "result one"),
        Message("assistant", "call two"),
        Message("user", "result two"),
    )

    selection = policy().select(messages)

    assert selection is not None
    assert selection.head == ((messages[0],), (messages[1], messages[2]))
    assert selection.tail == messages[3:]


def test_trigger_counts_text_opaque_content_and_images() -> None:
    window = policy(trigger_tokens=20)
    messages = (Message("user", "abcdefghij", opaque=10, images=1),) * 3

    assert window.tokens(messages) == 60
    assert window.should_compact(messages, force=False, automatic_suppressed=False)
    assert not window.should_compact(messages, force=False, automatic_suppressed=True)


def test_force_still_requires_a_compactible_head() -> None:
    window = policy(keep_messages=2)

    assert not window.should_compact(
        (Message("user", "one"), Message("assistant", "two")),
        force=True,
        automatic_suppressed=False,
    )


def test_budget_rejects_a_replacement_that_could_have_fit() -> None:
    with pytest.raises(RuntimeError, match="stays over the compaction trigger"):
        policy().require_budget(
            before_tokens=100,
            after_tokens=80,
            tail_tokens=10,
            fixed_replacement_tokens=10,
        )


def test_context_overflow_detection_uses_class_and_message() -> None:
    assert is_context_overflow(RuntimeError("prompt is too large"))
    assert not is_context_overflow(RuntimeError("network closed"))


@pytest.mark.asyncio
async def test_compaction_retries_external_overflow_and_missing_anchors() -> None:
    messages = tuple(
        Message("assistant" if index % 2 else "user", str(index)) for index in range(8)
    )
    summaries: list[tuple[int, tuple[str, ...]]] = []
    checkpoints: list[str] = []

    async def summarize(
        head: tuple[tuple[Message, ...], ...], missing: tuple[str, ...]
    ) -> tuple[str, int]:
        summaries.append((len(head), missing))
        if len(summaries) == 1:
            raise RuntimeError("prompt is too large")
        return ("carried" if missing else "first"), 1

    async def open_boundary(
        head: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...], before: int
    ) -> str:
        return "boundary"

    async def checkpoint(candidate: str, boundary: str) -> None:
        checkpoints.append(candidate)

    result = await CompactionHarness(
        window=policy(trigger_tokens=1),
        summarize=summarize,
        open_boundary=open_boundary,
        verify=lambda summary, boundary, retried: f"{summary}:{retried}",
        missing=lambda candidate: ("anchor",) if candidate == "first:False" else (),
        retry_failed=lambda candidate, error: candidate,
        failed_usage=lambda error: (),
        after=lambda candidate: (Message("user", candidate), messages[-2], messages[-1]),
        fixed_replacement_tokens=lambda boundary: 1,
        checkpoint=checkpoint,
        max_context_retries=1,
    ).run(messages)

    assert summaries == [(3, ()), (2, ()), (3, ("anchor",))]
    assert result.usages == (1, 1)
    assert checkpoints == ["carried:True"]
