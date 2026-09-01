from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

DEFAULT_FLUSH_BYTES = 2048
DEFAULT_FLUSH_SECONDS = 0.04

RequestT = TypeVar("RequestT")
ToolCallT = TypeVar("ToolCallT")
ReasoningT = TypeVar("ReasoningT")
UsageT = TypeVar("UsageT")


class TextFilter(Protocol):
    """Incrementally remove text that must not reach the live stream."""

    def feed(self, chunk: str) -> str: ...


class ModelStreamInterrupted(RuntimeError):
    """A provider stream died mid-round to a transient provider or transport fault — an error frame
    injected into the live stream, a dropped connection, a usage ledger that never indexed the
    generation. Any model client raises it, whether or not the round has already yielded visible
    output: the partial round is discardable because the engine commits a round only when its
    stream completes. The runner records `kind` on the collected round, and the engine re-runs the
    round once on it; a second interruption of the same round fails the turn."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _TextEvent(Protocol):
    text: str


class _ToolStartEvent(Protocol):
    id: str
    name: str


class _ToolDeltaEvent(Protocol):
    id: str
    partial_json: str


@dataclass(frozen=True)
class RoundEventTypes:
    """The caller-owned event classes emitted by one model provider stream."""

    stream_start: type[object]
    text: type[object]
    tool_start: type[object]
    tool_delta: type[object]
    reasoning: tuple[type[object], ...]
    usage: type[object]


@dataclass(frozen=True)
class CollectedRound[ToolCallT, ReasoningT, UsageT]:
    """One consumed provider stream before the runtime records it durably. `error_kind` is set
    exactly when the stream died to a `ModelStreamInterrupted` — the caller's signal to discard
    this round and re-run it."""

    text: str = ""
    tool_calls: tuple[ToolCallT, ...] = ()
    reasoning: tuple[ReasoningT, ...] = ()
    usages: tuple[UsageT, ...] = ()
    error_class: str | None = None
    error_message: str | None = None
    error_kind: str | None = None
    partial_output: str = ""
    wall_ms: int = 0
    provider_start_ms: int | None = None
    first_visible_event_ms: int | None = None


@dataclass(frozen=True)
class ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT]:
    """Consume one model stream, publish safe text, and assemble its ordered result."""

    complete: Callable[[RequestT], AsyncIterator[object]]
    events: RoundEventTypes
    new_tool_call: Callable[[str, str, dict[str, object]], ToolCallT]
    publish_text: Callable[[str], Awaitable[object] | None]
    milestone: Callable[[str], None] | None = None
    flush_bytes: int = DEFAULT_FLUSH_BYTES
    flush_seconds: float = DEFAULT_FLUSH_SECONDS
    monotonic: Callable[[], float] = time.monotonic

    async def run(
        self, request: RequestT, text_filter: TextFilter
    ) -> CollectedRound[ToolCallT, ReasoningT, UsageT]:
        """Run exactly one provider stream; failures retain consumed usage and partial output."""
        started = self.monotonic()
        state = _RoundState(self, text_filter, started)
        stop = asyncio.Event()
        error: Exception | None = None

        async def pace() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), self.flush_seconds)
                except TimeoutError:
                    await state.flush()

        pacer = asyncio.create_task(pace())
        try:
            async for event in self.complete(request):
                await state.accept(event)
        except Exception as caught:
            error = caught
        finally:
            stop.set()
            try:
                await pacer
            except Exception as caught:
                if error is None:
                    error = caught
        wall_ms = int((self.monotonic() - started) * 1000)
        await state.flush()
        return state.result(error, wall_ms)


class _RoundState[RequestT, ToolCallT, ReasoningT, UsageT]:
    def __init__(
        self,
        runner: ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT],
        text_filter: TextFilter,
        started: float,
    ) -> None:
        self.runner = runner
        self.text_filter = text_filter
        self.started = started
        self.parts: list[str] = []
        self.buffer: list[str] = []
        self.pending = 0
        self.flush_lock = asyncio.Lock()
        self.call_names: dict[str, str] = {}
        self.call_json: dict[str, list[str]] = {}
        self.call_order: list[str] = []
        self.reasoning: list[ReasoningT] = []
        self.usages: list[UsageT] = []
        self.provider_start_ms: int | None = None
        self.first_visible_event_ms: int | None = None

    async def accept(self, event: object) -> None:
        if isinstance(event, self.runner.events.stream_start):
            if self.provider_start_ms is None:
                self.provider_start_ms = self._elapsed_ms()
                if self.runner.milestone is not None:
                    self.runner.milestone("provider_start")
            return
        if isinstance(event, self.runner.events.text):
            chunk = cast(_TextEvent, event).text
            if chunk:
                self._mark_visible()
            self.parts.append(chunk)
            self.buffer.append(chunk)
            self.pending += len(chunk)
            if self.pending >= self.runner.flush_bytes:
                await self.flush()
            return
        if isinstance(event, self.runner.events.tool_start):
            visible = cast(_ToolStartEvent, event)
            self._mark_visible()
            self.call_names[visible.id] = visible.name
            self.call_json[visible.id] = []
            self.call_order.append(visible.id)
            return
        if isinstance(event, self.runner.events.tool_delta):
            delta = cast(_ToolDeltaEvent, event)
            self.call_json[delta.id].append(delta.partial_json)
            return
        if isinstance(event, self.runner.events.reasoning):
            self.reasoning.append(cast(ReasoningT, event))
            return
        if isinstance(event, self.runner.events.usage):
            self.usages.append(cast(UsageT, event))
            return
        raise TypeError(f"unknown model stream event: {type(event).__name__}")

    async def flush(self) -> None:
        async with self.flush_lock:
            if not self.buffer:
                return
            visible = self.text_filter.feed("".join(self.buffer))
            self.buffer.clear()
            self.pending = 0
            if visible:
                published = self.runner.publish_text(visible)
                if inspect.isawaitable(published):
                    await published

    def result(
        self, error: Exception | None, wall_ms: int
    ) -> CollectedRound[ToolCallT, ReasoningT, UsageT]:
        if error is not None:
            partial_calls = tuple(
                f"[tool call: {self.call_names[call_id]}]\n{''.join(self.call_json[call_id])}"
                for call_id in self.call_order
            )
            return CollectedRound(
                usages=tuple(self.usages),
                error_class=type(error).__name__,
                error_message=str(error),
                error_kind=error.kind if isinstance(error, ModelStreamInterrupted) else None,
                partial_output="\n\n".join(
                    segment for segment in ("".join(self.parts), *partial_calls) if segment
                ),
                wall_ms=wall_ms,
                provider_start_ms=self.provider_start_ms,
                first_visible_event_ms=self.first_visible_event_ms,
            )
        if not self.usages:
            raise RuntimeError("model stream produced no usage")
        tool_calls = tuple(
            self.runner.new_tool_call(
                call_id,
                self.call_names[call_id],
                cast(
                    dict[str, object],
                    json.loads("".join(self.call_json[call_id]))
                    if "".join(self.call_json[call_id]).strip()
                    else {},
                ),
            )
            for call_id in self.call_order
        )
        return CollectedRound(
            text="".join(self.parts),
            tool_calls=tool_calls,
            reasoning=tuple(self.reasoning),
            usages=tuple(self.usages),
            wall_ms=wall_ms,
            provider_start_ms=self.provider_start_ms,
            first_visible_event_ms=self.first_visible_event_ms,
        )

    def _mark_visible(self) -> None:
        if self.first_visible_event_ms is not None:
            return
        self.first_visible_event_ms = self._elapsed_ms()
        if self.runner.milestone is not None:
            self.runner.milestone("first_visible_event")

    def _elapsed_ms(self) -> int:
        return int((self.runner.monotonic() - self.started) * 1000)
