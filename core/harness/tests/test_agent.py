from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from ufo.harness.agent import (
    AgentDefinition,
    AgentEngine,
    Finished,
    Message,
    ModelRequest,
    ModelRound,
    PreparedRound,
    Reasoning,
    RecoverableModelError,
    RoundMode,
    StructuredOutput,
    Text,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from ufo.harness.replies import MarkedReply


@dataclass
class ScriptedModel:
    rounds: list[ModelRound | Exception]
    requests: list[ModelRequest] = field(default_factory=list)

    async def stream(self, request: ModelRequest, round_index: int) -> ModelRound:
        self.requests.append(request)
        scripted = self.rounds.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        return ModelRound(request.messages, scripted.text, scripted.calls, scripted.reasoning)


@dataclass
class Tools:
    definitions_: tuple[ToolDefinition, ...] = (
        ToolDefinition("echo", "Echo text.", {"type": "object"}),
    )
    safe: frozenset[str] = frozenset({"echo"})
    calls: list[ToolCall] = field(default_factory=list)
    rounds: list[tuple[tuple[ToolCall, ...], tuple[ToolResult, ...]]] = field(default_factory=list)
    interruptions: int = 0

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self.definitions_

    def parallel_safe(self, call: ToolCall) -> bool:
        return call.name in self.safe

    async def prepare(self, calls: tuple[ToolCall, ...]) -> None:
        return None

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(call.id, str(call.input["value"]))

    async def after_round(
        self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]
    ) -> None:
        self.rounds.append((calls, results))

    def interrupted(self) -> None:
        self.interruptions += 1


@dataclass
class Conversation:
    interrupted: bool = False
    exchanges: list[tuple[Message, ...]] = field(default_factory=list)

    async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound:
        interrupted = self.interrupted
        self.interrupted = False
        return PreparedRound(messages, interrupted)

    async def checkpoint(self, messages: tuple[Message, ...]) -> None:
        self.exchanges.append(messages)

    async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]:
        return messages


@dataclass
class Events:
    spoken: list[MarkedReply] = field(default_factory=list)
    exhausted_count: int = 0

    async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None:
        self.spoken.extend(replies)

    async def closing(self, replies: tuple[MarkedReply, ...]) -> None:
        self.spoken.extend(replies)

    def exhausted(self) -> None:
        self.exhausted_count += 1


def definition(max_rounds: int = 3) -> AgentDefinition:
    return AgentDefinition(
        system_prompt="You are concise.",
        max_rounds=max_rounds,
        max_parallel_calls=4,
        force_final_prompt="finish without tools",
    )


def round_(text: str = "", *calls: ToolCall) -> ModelRound:
    return ModelRound((), text, calls)


def output_contract(blocked: Callable[[], bool] = lambda: False) -> StructuredOutput:
    def validate(call: ToolCall) -> str:
        value = call.input.get("result")
        if not isinstance(value, str) or not value:
            raise ValueError("result is empty")
        return value

    return StructuredOutput(
        finish_tool=ToolDefinition(
            "finish", "Return the result.", {"type": "object", "required": ["result"]}
        ),
        finish_prompt="call finish",
        force_finish_prompt="force finish",
        coexisting_error="finish alone",
        blocked=blocked,
        validate=validate,
        invalid_error=lambda error: f"invalid: {error}",
        accept_prose=lambda text: text if text.startswith("direct:") else None,
    )


@pytest.mark.asyncio
async def test_engine_runs_a_tool_and_returns_the_model_answer() -> None:
    call = ToolCall("one", "echo", {"value": "hello"})
    model = ScriptedModel([round_("", call), round_("hello")])
    tools = Tools()
    conversation = Conversation()

    result = await AgentEngine(definition(), model, tools, conversation).run(
        (Message("user", "say hello"),)
    )

    exchange = (
        Message("user", "say hello"),
        Message("assistant", (call,)),
        Message("user", (ToolResult("one", "hello"),)),
    )
    assert result == Finished(exchange, "hello")
    assert tools.calls == [call]
    assert conversation.exchanges == [exchange]
    assert model.requests[0].system_prompt == "You are concise."
    assert model.requests[0].tools == tools.definitions()


@pytest.mark.asyncio
async def test_engine_preserves_reasoning_text_calls_and_results_in_order() -> None:
    call = ToolCall("one", "echo", {"value": "hello"})
    reasoning = Reasoning("thinking", {"type": "thinking", "thinking": "x"})
    model = ScriptedModel([ModelRound((), "working", (call,), (reasoning,)), round_("done")])

    result = await AgentEngine(definition(), model, Tools()).run(())

    assert result.messages == (
        Message("assistant", (reasoning, Text("working"), call)),
        Message("user", (ToolResult("one", "hello"),)),
    )


@pytest.mark.asyncio
async def test_empty_round_is_nudged_once() -> None:
    model = ScriptedModel([round_(), round_("answer")])

    result = await AgentEngine(definition(), model, Tools()).run(())

    assert result.messages == (Message("user", definition().empty_response_feedback),)
    assert result.answer == "answer"


@pytest.mark.asyncio
async def test_two_empty_rounds_fail() -> None:
    model = ScriptedModel([round_(), round_()])

    with pytest.raises(RuntimeError, match="model returned an empty response twice"):
        await AgentEngine(definition(), model, Tools()).run(())


@pytest.mark.asyncio
async def test_round_budget_runs_one_toolless_final_round() -> None:
    call = ToolCall("one", "echo", {"value": "one"})
    model = ScriptedModel([round_("", call), round_("forced")])
    events = Events()

    result = await AgentEngine(definition(max_rounds=1), model, Tools(), events=events).run(())

    assert result == Finished(
        (
            Message("assistant", (call,)),
            Message("user", (ToolResult("one", "one"),)),
            Message("user", "finish without tools"),
        ),
        "forced",
        exhausted=True,
    )
    assert [request.mode for request in model.requests] == [
        RoundMode.NORMAL,
        RoundMode.NO_TOOLS,
    ]
    assert model.requests[-1].tools == ()
    assert events.exhausted_count == 1


@pytest.mark.asyncio
async def test_recoverable_model_error_consumes_the_same_round_budget() -> None:
    model = ScriptedModel([RecoverableModelError("recovered"), round_("forced")])

    result = await AgentEngine(definition(max_rounds=1), model, Tools()).run(())

    assert result == Finished(
        (Message("user", "recovered"), Message("user", "finish without tools")),
        "forced",
        exhausted=True,
    )


@pytest.mark.asyncio
async def test_a_lone_finish_call_returns_the_structured_result() -> None:
    call = ToolCall("done", "finish", {"result": "result"})
    model = ScriptedModel([round_("", call)])

    result = await AgentEngine(definition(), model, Tools(), structured=output_contract()).run(())

    assert result == Finished((), "result", structured=True)


@pytest.mark.asyncio
async def test_invalid_finish_is_tool_feedback_the_next_round_can_correct() -> None:
    bad = ToolCall("bad", "finish", {})
    good = ToolCall("good", "finish", {"result": "result"})
    model = ScriptedModel([round_("", bad), round_("", good)])
    conversation = Conversation()

    result = await AgentEngine(
        definition(), model, Tools(), conversation, structured=output_contract()
    ).run(())

    assert result.answer == "result"
    assert conversation.exchanges == [
        (
            Message("assistant", (bad,)),
            Message("user", (ToolResult("bad", "invalid: result is empty", is_error=True),)),
        )
    ]


@pytest.mark.asyncio
async def test_prose_on_a_structured_run_forces_a_finish_call() -> None:
    call = ToolCall("done", "finish", {"result": "result"})
    model = ScriptedModel([round_("draft"), round_("", call)])

    result = await AgentEngine(definition(), model, Tools(), structured=output_contract()).run(())

    assert result == Finished(
        (Message("assistant", "draft"), Message("user", "call finish")), "result", structured=True
    )
    assert model.requests[-1].tool_choice == "finish"
    assert model.requests[-1].tools == (output_contract().finish_tool,)


@pytest.mark.asyncio
async def test_arrival_interrupts_the_tool_boundary_before_the_next_round() -> None:
    call = ToolCall("one", "echo", {"value": "one"})
    conversation = Conversation(interrupted=True)
    tools = Tools()
    model = ScriptedModel([round_("", call), round_("done")])

    await AgentEngine(definition(), model, tools, conversation).run(())

    assert tools.interruptions == 1


@pytest.mark.asyncio
async def test_parallel_safe_calls_run_concurrently_and_keep_model_order() -> None:
    first = ToolCall("one", "echo", {"value": "one"})
    second = ToolCall("two", "echo", {"value": "two"})
    both_started = asyncio.Event()
    prepared: set[str] = set()
    started = 0

    @dataclass
    class ParallelTools(Tools):
        async def prepare(self, calls: tuple[ToolCall, ...]) -> None:
            prepared.update(call.id for call in calls)

        async def execute(self, call: ToolCall) -> ToolResult:
            nonlocal started
            assert prepared == {"one", "two"}
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return ToolResult(call.id, str(call.input["value"]))

    model = ScriptedModel([round_("", first, second), round_("done")])

    result = await AgentEngine(definition(), model, ParallelTools()).run(())

    assert result.messages[-1] == Message(
        "user", (ToolResult("one", "one"), ToolResult("two", "two"))
    )


@pytest.mark.asyncio
async def test_segments_follow_the_boundarys_flag_for_the_call_it_resolves() -> None:
    """One offered definition can dispatch calls whose declarations differ, so the boundary answers
    per call: two calls it calls safe share a segment, and the one it refuses is a barrier."""
    safe_one = ToolCall("one", "dispatch", {"value": "one", "safe": True})
    safe_two = ToolCall("two", "dispatch", {"value": "two", "safe": True})
    barrier = ToolCall("three", "dispatch", {"value": "three", "safe": False})
    segments: list[tuple[str, ...]] = []

    @dataclass
    class BoundaryTools(Tools):
        definitions_: tuple[ToolDefinition, ...] = (
            ToolDefinition("dispatch", "Dispatch a bound call.", {"type": "object"}),
        )

        def parallel_safe(self, call: ToolCall) -> bool:
            return bool(call.input["safe"])

        async def prepare(self, calls: tuple[ToolCall, ...]) -> None:
            segments.append(tuple(call.id for call in calls))

    model = ScriptedModel([round_("", safe_one, safe_two, barrier), round_("done")])

    result = await AgentEngine(definition(), model, BoundaryTools()).run(())

    assert segments == [("one", "two"), ("three",)]
    assert result.messages[-1] == Message(
        "user",
        (ToolResult("one", "one"), ToolResult("two", "two"), ToolResult("three", "three")),
    )


def test_agent_definition_rejects_invalid_execution_limits() -> None:
    with pytest.raises(ValueError, match="max_rounds must be positive"):
        definition(max_rounds=0)
    with pytest.raises(ValueError, match="max_parallel_calls must be positive"):
        AgentDefinition(
            system_prompt="prompt",
            max_rounds=1,
            max_parallel_calls=0,
            force_final_prompt="finish",
        )


def test_tool_definition_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="tool name is empty"):
        ToolDefinition(" ", "Empty.", {})


@pytest.mark.asyncio
async def test_engine_rejects_duplicate_tool_names() -> None:
    tools = Tools(
        definitions_=(
            ToolDefinition("echo", "First.", {}),
            ToolDefinition("echo", "Second.", {}),
        )
    )

    with pytest.raises(ValueError, match="duplicate tools: echo"):
        await AgentEngine(definition(), ScriptedModel([]), tools).run(())


@pytest.mark.asyncio
async def test_structured_finish_tool_cannot_shadow_an_agent_tool() -> None:
    finish = output_contract().finish_tool
    tools = Tools(definitions_=(finish,))

    with pytest.raises(ValueError, match="finish tool collides with agent tool: finish"):
        await AgentEngine(definition(), ScriptedModel([]), tools, structured=output_contract()).run(
            ()
        )
