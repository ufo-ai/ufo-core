from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from ufo.harness.rounds import ModelRoundRunner, RoundEventTypes


@dataclass(frozen=True)
class StreamStart:
    pass


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolStart:
    id: str
    name: str


@dataclass(frozen=True)
class ToolDelta:
    id: str
    partial_json: str


@dataclass(frozen=True)
class Reasoning:
    text: str


@dataclass(frozen=True)
class Usage:
    tokens: int


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class Withheld:
    held: str = ""

    def feed(self, chunk: str) -> str:
        self.held += chunk
        if "|" not in self.held:
            return ""
        visible, self.held = self.held.split("|", 1)
        return visible


EVENTS = RoundEventTypes(
    stream_start=StreamStart,
    text=Text,
    tool_start=ToolStart,
    tool_delta=ToolDelta,
    reasoning=(Reasoning,),
    usage=Usage,
)


async def test_collects_one_stream_and_builds_tool_calls_in_provider_order() -> None:
    async def complete(_request: str) -> AsyncIterator[object]:
        for event in (
            StreamStart(),
            Text("shown|held"),
            Reasoning("because"),
            ToolStart("second", "write"),
            ToolDelta("second", '{"path":'),
            ToolDelta("second", '"a.txt"}'),
            ToolStart("first", "read"),
            ToolDelta("first", "{}"),
            Usage(12),
        ):
            yield event

    published: list[str] = []
    milestones: list[str] = []
    runner = ModelRoundRunner(
        complete=complete,
        events=EVENTS,
        new_tool_call=ToolCall,
        publish_text=published.append,
        milestone=milestones.append,
        flush_bytes=1,
    )

    result = await runner.run("request", Withheld())

    assert result.text == "shown|held"
    assert result.tool_calls == (
        ToolCall("second", "write", {"path": "a.txt"}),
        ToolCall("first", "read", {}),
    )
    assert result.reasoning == (Reasoning("because"),)
    assert result.usages == (Usage(12),)
    assert published == ["shown"]
    assert milestones == ["provider_start", "first_visible_event"]


async def test_a_stream_that_ends_on_unclosed_tool_arguments_is_an_interruption() -> None:
    """The provider ended the stream cleanly but the tool call it emitted never closed its JSON,
    so the act it named cannot run. That is the provider's fault, not a parse error of ours: the
    round is discardable exactly like one whose stream died, so it takes the interruption exit the
    engine re-runs once, with the unclosed call kept on the partial output."""

    async def complete(_request: str) -> AsyncIterator[object]:
        yield ToolStart("call", "write")
        yield ToolDelta("call", '{"path": "a.tx')
        yield Usage(7)

    runner = ModelRoundRunner(
        complete=complete,
        events=EVENTS,
        new_tool_call=ToolCall,
        publish_text=lambda _text: None,
    )

    result = await runner.run("request", Withheld())

    assert result.tool_calls == ()
    assert result.usages == (Usage(7),)
    assert result.error_class == "ModelStreamInterrupted"
    assert result.error_kind == "tool_call_json"
    assert result.partial_output == '[tool call: write]\n{"path": "a.tx'


async def test_stream_failure_modes_preserve_partial_usage_and_fail_loud() -> None:
    async def complete(_request: str) -> AsyncIterator[object]:
        yield Text("partial")
        yield ToolStart("call", "write")
        yield ToolDelta("call", '{"path":')
        yield Usage(7)
        raise LookupError("provider stopped")

    runner = ModelRoundRunner(
        complete=complete,
        events=EVENTS,
        new_tool_call=ToolCall,
        publish_text=lambda _text: None,
    )

    result = await runner.run("request", Withheld())

    assert result.text == ""
    assert result.tool_calls == ()
    assert result.reasoning == ()
    assert result.usages == (Usage(7),)
    assert result.error_class == "LookupError"
    assert result.error_message == "provider stopped"
    assert result.partial_output == 'partial\n\n[tool call: write]\n{"path":'

    async def without_usage(_request: str) -> AsyncIterator[object]:
        yield Text("answer")

    runner = ModelRoundRunner(
        complete=without_usage,
        events=EVENTS,
        new_tool_call=ToolCall,
        publish_text=lambda _text: None,
    )

    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        await runner.run("request", Withheld())

    async def unknown(_request: str) -> AsyncIterator[object]:
        yield object()

    runner = ModelRoundRunner(
        complete=unknown,
        events=EVENTS,
        new_tool_call=ToolCall,
        publish_text=lambda _text: None,
    )

    result = await runner.run("request", Withheld())

    assert result.error_class == "TypeError"
    assert result.error_message == "unknown model stream event: object"
