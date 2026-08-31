from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CONTEXT_OVERFLOW_MARKERS = (
    "too long",
    "context length",
    "maximum context",
    "prompt is too large",
)


def is_context_overflow(error: Exception) -> bool:
    """Whether an external model rejected a request because its context was too large."""
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in CONTEXT_OVERFLOW_MARKERS)


@dataclass(frozen=True)
class WindowSelection[MessageT]:
    """The whole model rounds summarized as a head and retained as a tail."""

    head: tuple[tuple[MessageT, ...], ...]
    tail: tuple[MessageT, ...]


@dataclass(frozen=True)
class ContextWindow[MessageT]:
    """Decide when and how an opaque transcript window is compacted."""

    role: Callable[[MessageT], str]
    text: Callable[[MessageT], str]
    opaque_chars: Callable[[MessageT], int]
    image_count: Callable[[MessageT], int]
    context_tokens: int
    summary_tokens: int
    buffer_tokens: int
    keep_messages: int
    chars_per_token: int
    image_tokens: int
    trigger_tokens: int | None = None
    head_drop_denominator: int = 5

    @property
    def trigger(self) -> int:
        """The token count at which an automatic compaction starts."""
        if self.trigger_tokens is not None:
            return self.trigger_tokens
        return self.context_tokens - self.summary_tokens - self.buffer_tokens

    def should_compact(
        self,
        messages: tuple[MessageT, ...],
        *,
        force: bool,
        automatic_suppressed: bool,
    ) -> bool:
        """Whether this window has a compactible head and has crossed its trigger."""
        if len(messages) <= self.keep_messages:
            return False
        if not force and automatic_suppressed:
            return False
        return force or self.tokens(messages) > self.trigger

    def select(self, messages: tuple[MessageT, ...]) -> WindowSelection[MessageT] | None:
        """Keep the smallest trailing set of whole rounds covering the retention floor."""
        rounds = self.rounds(messages)
        kept: list[tuple[MessageT, ...]] = []
        kept_count = 0
        while rounds and kept_count < self.keep_messages:
            kept.insert(0, rounds[-1])
            kept_count += len(rounds[-1])
            rounds = rounds[:-1]
        if not rounds:
            return None
        return WindowSelection(
            head=rounds,
            tail=tuple(message for round_ in kept for message in round_),
        )

    def rounds(self, messages: tuple[MessageT, ...]) -> tuple[tuple[MessageT, ...], ...]:
        """Group each assistant message with the user tool results that answer it."""
        rounds: list[tuple[MessageT, ...]] = []
        current: list[MessageT] = []
        for message in messages:
            if self.role(message) == "assistant" and current:
                rounds.append(tuple(current))
                current = [message]
            else:
                current.append(message)
        if current:
            rounds.append(tuple(current))
        return tuple(rounds)

    def drop_oldest(
        self, rounds: tuple[tuple[MessageT, ...], ...]
    ) -> tuple[tuple[MessageT, ...], ...]:
        """Drop the oldest bounded fraction before retrying an oversized summary request."""
        return rounds[max(1, len(rounds) // self.head_drop_denominator) :]

    def tokens(self, messages: tuple[MessageT, ...]) -> int:
        """Estimate opaque transcript cost without owning its message types."""
        if self.chars_per_token < 1:
            raise ValueError("chars_per_token must be positive")
        return sum(
            (
                len(self.role(message))
                + len(self.text(message))
                + self.opaque_chars(message)
                + self.chars_per_token
                - 1
            )
            // self.chars_per_token
            + self.image_tokens * self.image_count(message)
            for message in messages
        )

    def require_budget(
        self,
        *,
        before_tokens: int,
        after_tokens: int,
        tail_tokens: int,
        fixed_replacement_tokens: int,
    ) -> None:
        """Reject a replacement that could shrink and fit but does neither."""
        head_tokens = before_tokens - tail_tokens
        room = fixed_replacement_tokens + self.summary_tokens
        if head_tokens > room and after_tokens >= before_tokens:
            raise RuntimeError(
                f"compaction did not shrink the window: {after_tokens} >= {before_tokens} tokens"
            )
        if tail_tokens + room < self.trigger and after_tokens >= self.trigger:
            raise RuntimeError(
                "compacted window stays over the compaction trigger: "
                f"{after_tokens} >= {self.trigger} tokens"
            )


@dataclass(frozen=True)
class CompactionResult[MessageT, UsageT]:
    """The installed transcript window and external usage consumed to produce it."""

    messages: tuple[MessageT, ...]
    usages: tuple[UsageT, ...]


@dataclass(frozen=True)
class CompactionHarness[MessageT, AnchorT, UsageT, SummaryT, CandidateT, BoundaryT]:
    """Run one compaction while callers own transcript types, effects, and checkpoints."""

    window: ContextWindow[MessageT]
    summarize: Callable[
        [tuple[tuple[MessageT, ...], ...], tuple[AnchorT, ...]],
        Awaitable[tuple[SummaryT, UsageT]],
    ]
    open_boundary: Callable[
        [tuple[tuple[MessageT, ...], ...], tuple[MessageT, ...], int],
        Awaitable[BoundaryT],
    ]
    verify: Callable[[SummaryT, BoundaryT, bool], CandidateT]
    missing: Callable[[CandidateT], tuple[AnchorT, ...]]
    retry_failed: Callable[[CandidateT, Exception], CandidateT]
    failed_usage: Callable[[Exception], tuple[UsageT, ...]]
    after: Callable[[CandidateT], tuple[MessageT, ...]]
    fixed_replacement_tokens: Callable[[BoundaryT], int]
    checkpoint: Callable[[CandidateT, BoundaryT], Awaitable[None]]
    max_context_retries: int

    async def run(self, messages: tuple[MessageT, ...]) -> CompactionResult[MessageT, UsageT]:
        """Select, summarize, verify, enforce the budget, and checkpoint one replacement."""
        selection = self.window.select(messages)
        if selection is None:
            return CompactionResult(messages, ())
        before_tokens = self.window.tokens(messages)
        summary, usage = await self._summarize(selection.head, ())
        usages: tuple[UsageT, ...] = (usage,)
        boundary = await self.open_boundary(selection.head, selection.tail, before_tokens)
        candidate = self.verify(summary, boundary, False)
        missing = self.missing(candidate)
        if missing:
            try:
                second, retry_usage = await self._summarize(selection.head, missing)
            except Exception as error:
                usages = (*usages, *self.failed_usage(error))
                candidate = self.retry_failed(candidate, error)
            else:
                usages = (*usages, retry_usage)
                candidate = self.verify(second, boundary, True)
        after = self.after(candidate)
        self.window.require_budget(
            before_tokens=before_tokens,
            after_tokens=self.window.tokens(after),
            tail_tokens=self.window.tokens(selection.tail),
            fixed_replacement_tokens=self.fixed_replacement_tokens(boundary),
        )
        await self.checkpoint(candidate, boundary)
        return CompactionResult(after, usages)

    async def _summarize(
        self,
        head: tuple[tuple[MessageT, ...], ...],
        missing: tuple[AnchorT, ...],
    ) -> tuple[SummaryT, UsageT]:
        rounds = head
        for attempt in range(self.max_context_retries + 1):
            try:
                return await self.summarize(rounds, missing)
            except Exception as error:
                if (
                    not is_context_overflow(error)
                    or attempt == self.max_context_retries
                    or len(rounds) <= 1
                ):
                    raise
                rounds = self.window.drop_oldest(rounds)
        raise RuntimeError("compaction prompt-too-long recovery exhausted")
