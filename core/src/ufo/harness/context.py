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
class ContextRemaining:
    """Where a window stands: what it has spent, the line a rollover fires at, and the model's hard
    limit. Reported on demand so the number an agent reads never churns the prompt it reads it
    from."""

    used_tokens: int
    rollover_at_tokens: int
    tokens_until_rollover: int
    hard_limit_tokens: int
    tokens_until_hard_limit: int


@dataclass(frozen=True)
class WindowSelection[MessageT]:
    """The whole model rounds summarized as a head and retained as a tail."""

    head: tuple[tuple[MessageT, ...], ...]
    tail: tuple[MessageT, ...]


@dataclass(frozen=True)
class ContextWindow[MessageT]:
    """Estimate an opaque transcript window's cost and the line it crosses its boundary at, without
    owning its message types.

    `reserve_tokens` is what the boundary's own replacement message may cost — the recovery record a
    rollover installs, or the summary a compaction writes — so the line sits that far back from the
    model's real window. `keep_messages` and `head_drop_denominator` serve the compaction
    strategy alone: a rollover keeps no verbatim tail and leaves them at their defaults."""

    role: Callable[[MessageT], str]
    text: Callable[[MessageT], str]
    opaque_chars: Callable[[MessageT], int]
    image_count: Callable[[MessageT], int]
    context_tokens: int
    reserve_tokens: int
    buffer_tokens: int
    chars_per_token: int
    image_tokens: int
    trigger_tokens: int | None = None
    keep_messages: int = 0
    head_drop_denominator: int = 5

    @property
    def trigger(self) -> int:
        """The token count at which the window crosses its boundary: the model's window less the
        reserve the replacement's own first message may cost and the buffer, unless the spec pins a
        line."""
        if self.trigger_tokens is not None:
            return self.trigger_tokens
        return self.context_tokens - self.reserve_tokens - self.buffer_tokens

    def should_compact(
        self,
        messages: tuple[MessageT, ...],
        *,
        force: bool,
        automatic_suppressed: bool,
        automatic_trigger_tokens: int | None = None,
    ) -> bool:
        """Whether this window has a compactible head and has crossed its trigger."""
        if len(messages) <= self.keep_messages:
            return False
        if not force and automatic_suppressed:
            return False
        trigger = self.trigger if automatic_trigger_tokens is None else automatic_trigger_tokens
        return force or self.tokens(messages) > trigger

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
        room = fixed_replacement_tokens + self.reserve_tokens
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
