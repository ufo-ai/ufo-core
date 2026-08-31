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
    """One consumed provider stream before the runtime records it durably."""

    text: str = ""
    tool_calls: tuple[ToolCallT, ...] = ()
    reasoning: tuple[ReasoningT, ...] = ()
    usages: tuple[UsageT, ...] = ()
    error_class: str | None = None
    error_message: str | None = None
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
        parts: list[str] = []
        buffer: list[str] = []
        pending = 0
        flush_lock = asyncio.Lock()
        stop = asyncio.Event()
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []
        reasoning: list[ReasoningT] = []
        usages: list[UsageT] = []
        error: Exception | None = None

        async def flush() -> None:
            nonlocal pending
            async with flush_lock:
                if not buffer:
                    return
                visible = text_filter.feed("".join(buffer))
                buffer.clear()
                pending = 0
                if visible:
                    published = self.publish_text(visible)
                    if inspect.isawaitable(published):
                        await published

        async def pace() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), self.flush_seconds)
                except TimeoutError:
                    await flush()

        started = self.monotonic()
        provider_start_ms: int | None = None
        first_visible_event_ms: int | None = None
        pacer = asyncio.create_task(pace())
        try:
            async for event in self.complete(request):
                if isinstance(event, self.events.stream_start):
                    if provider_start_ms is None:
                        provider_start_ms = int((self.monotonic() - started) * 1000)
                        if self.milestone is not None:
                            self.milestone("provider_start")
                    continue
                if isinstance(event, self.events.text):
                    chunk = cast(_TextEvent, event).text
                    if chunk and first_visible_event_ms is None:
                        first_visible_event_ms = int((self.monotonic() - started) * 1000)
                        if self.milestone is not None:
                            self.milestone("first_visible_event")
                    parts.append(chunk)
                    buffer.append(chunk)
                    pending += len(chunk)
                    if pending >= self.flush_bytes:
                        await flush()
                    continue
                if isinstance(event, self.events.tool_start):
                    visible = cast(_ToolStartEvent, event)
                    if first_visible_event_ms is None:
                        first_visible_event_ms = int((self.monotonic() - started) * 1000)
                        if self.milestone is not None:
                            self.milestone("first_visible_event")
                    call_names[visible.id] = visible.name
                    call_json[visible.id] = []
                    call_order.append(visible.id)
                    continue
                if isinstance(event, self.events.tool_delta):
                    delta = cast(_ToolDeltaEvent, event)
                    call_json[delta.id].append(delta.partial_json)
                    continue
                if isinstance(event, self.events.reasoning):
                    reasoning.append(cast(ReasoningT, event))
                    continue
                if isinstance(event, self.events.usage):
                    usages.append(cast(UsageT, event))
                    continue
                raise TypeError(f"unknown model stream event: {type(event).__name__}")
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
        await flush()

        if error is not None:
            partial_calls = tuple(
                f"[tool call: {call_names[call_id]}]\n{''.join(call_json[call_id])}"
                for call_id in call_order
            )
            return CollectedRound(
                usages=tuple(usages),
                error_class=type(error).__name__,
                error_message=str(error),
                partial_output="\n\n".join(
                    segment for segment in ("".join(parts), *partial_calls) if segment
                ),
                wall_ms=wall_ms,
                provider_start_ms=provider_start_ms,
                first_visible_event_ms=first_visible_event_ms,
            )
        if not usages:
            raise RuntimeError("model stream produced no usage")
        tool_calls = tuple(
            self.new_tool_call(
                call_id,
                call_names[call_id],
                cast(
                    dict[str, object],
                    json.loads("".join(call_json[call_id]))
                    if "".join(call_json[call_id]).strip()
                    else {},
                ),
            )
            for call_id in call_order
        )
        return CollectedRound(
            text="".join(parts),
            tool_calls=tool_calls,
            reasoning=tuple(reasoning),
            usages=tuple(usages),
            wall_ms=wall_ms,
            provider_start_ms=provider_start_ms,
            first_visible_event_ms=first_visible_event_ms,
        )
