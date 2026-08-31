from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from ufo.harness.replies import MarkedReply, marked_replies
from ufo.harness.tools import dispatch_segments


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Image:
    media_type: str
    data: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, object]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str | tuple[Text | Image, ...]
    is_error: bool = False
    activity: bool = False
    activity_text: str = ""


@dataclass(frozen=True)
class Reasoning:
    kind: str
    payload: dict[str, object]


type Content = Text | Image | ToolCall | ToolResult | Reasoning


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str | tuple[Content, ...]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name is empty")


@dataclass(frozen=True)
class AgentDefinition:
    system_prompt: str
    max_rounds: int
    max_parallel_calls: int
    force_final_prompt: str
    empty_response_feedback: str = "Previous model response was empty. Answer now."

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if self.max_parallel_calls < 1:
            raise ValueError("max_parallel_calls must be positive")


class RoundMode(Enum):
    NORMAL = "normal"
    NO_TOOLS = "no_tools"
    FORCE_FINISH = "force_finish"


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    tool_choice: str | None
    first_round: bool
    mode: RoundMode


@dataclass(frozen=True)
class ModelRound:
    messages: tuple[Message, ...]
    text: str
    calls: tuple[ToolCall, ...]
    reasoning: tuple[Reasoning, ...] = ()


@dataclass(frozen=True)
class PreparedRound:
    messages: tuple[Message, ...]
    interrupted_final_act: bool = False


@dataclass(frozen=True)
class Finished:
    """One run's result. `structured` marks an answer the output contract produced, so a consumer
    holding acts an earlier round left open reads the run as having answered rather than asked.
    `exhausted` marks an answer the round budget forced."""

    messages: tuple[Message, ...]
    answer: str
    exhausted: bool = False
    structured: bool = False


class RecoverableModelError(Exception):
    """A failed model round the engine may continue after adding the supplied feedback."""

    def __init__(self, feedback: str) -> None:
        super().__init__(feedback)
        self.feedback = feedback


class AgentModel(Protocol):
    """Run one model round from the engine's complete request."""

    async def stream(self, request: ModelRequest, round_index: int) -> ModelRound: ...


class AgentTools(Protocol):
    """Describe, prepare, and execute the tools available to one agent run. `parallel_safe` answers
    for the declaration the boundary resolves a call to, so a call a wire dispatcher makes on
    another declaration's behalf schedules by that declaration's flag, never the dispatcher's."""

    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    def parallel_safe(self, call: ToolCall) -> bool: ...

    async def prepare(self, calls: tuple[ToolCall, ...]) -> None: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...

    async def after_round(
        self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]
    ) -> None: ...

    def interrupted(self) -> None: ...


class AgentConversation(Protocol):
    """Prepare and checkpoint the transcript around model and tool effects."""

    async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound: ...

    async def checkpoint(self, messages: tuple[Message, ...]) -> None: ...

    async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]: ...


class AgentEvents(Protocol):
    """Observe member-visible replies and run exhaustion without changing execution."""

    async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None: ...

    async def closing(self, replies: tuple[MarkedReply, ...]) -> None: ...

    def exhausted(self) -> None: ...


@dataclass(frozen=True)
class StructuredOutput:
    finish_tool: ToolDefinition
    finish_prompt: str
    force_finish_prompt: str
    coexisting_error: str
    blocked: Callable[[], bool]
    validate: Callable[[ToolCall], str]
    invalid_error: Callable[[Exception], str]
    accept_prose: Callable[[str], str | None]


@dataclass(frozen=True)
class MemoryConversation:
    """An in-memory transcript boundary for local runs and tests."""

    async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound:
        return PreparedRound(messages)

    async def checkpoint(self, messages: tuple[Message, ...]) -> None:
        return None

    async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]:
        return messages


@dataclass(frozen=True)
class NullEvents:
    """An observer for local runs that do not stream member-facing events."""

    async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None:
        return None

    async def closing(self, replies: tuple[MarkedReply, ...]) -> None:
        return None

    def exhausted(self) -> None:
        return None


@dataclass(frozen=True)
class AgentEngine:
    """Run one agent from an immutable definition and explicit effect boundaries."""

    definition: AgentDefinition
    model: AgentModel
    tools: AgentTools
    conversation: AgentConversation = MemoryConversation()
    events: AgentEvents = NullEvents()
    structured: StructuredOutput | None = None

    async def run(self, messages: tuple[Message, ...]) -> Finished:
        nudged = False
        for round_index in range(self.definition.max_rounds):
            prepared = await self.conversation.prepare(messages, round_index)
            messages = prepared.messages
            if prepared.interrupted_final_act:
                self.tools.interrupted()
            try:
                streamed = await self._stream(messages, round_index, RoundMode.NORMAL)
            except RecoverableModelError as error:
                messages = (*messages, Message(role="user", content=error.feedback))
                continue
            spoken, text = marked_replies(streamed.text)
            streamed = ModelRound(streamed.messages, text, streamed.calls, streamed.reasoning)
            if streamed.calls:
                outcome = await self._tool_exchange(streamed, spoken, round_index)
                if isinstance(outcome, Finished):
                    return outcome
                messages = outcome
                await self.conversation.checkpoint(messages)
                continue
            if streamed.text.strip():
                return await self._close(streamed, spoken, round_index)
            if nudged:
                raise RuntimeError("model returned an empty response twice")
            nudged = True
            messages = (
                *streamed.messages,
                Message(role="user", content=self.definition.empty_response_feedback),
            )
        return await self._exhaust(messages)

    async def _stream(
        self, messages: tuple[Message, ...], round_index: int, mode: RoundMode
    ) -> ModelRound:
        definitions: tuple[ToolDefinition, ...]
        if mode is RoundMode.FORCE_FINISH:
            if self.structured is None:
                raise RuntimeError("finish forced without a structured output")
            definitions = (self.structured.finish_tool,)
            tool_choice = self.structured.finish_tool.name
        elif mode is RoundMode.NO_TOOLS:
            definitions = ()
            tool_choice = None
        else:
            definitions = self.tools.definitions()
            names = [definition.name for definition in definitions]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                raise ValueError(f"duplicate tools: {', '.join(duplicates)}")
            if self.structured is not None:
                if self.structured.finish_tool.name in names:
                    raise ValueError(
                        f"finish tool collides with agent tool: {self.structured.finish_tool.name}"
                    )
                definitions = (*definitions, self.structured.finish_tool)
            tool_choice = None
        streamed = await self.model.stream(
            ModelRequest(
                system_prompt=self.definition.system_prompt,
                messages=messages,
                tools=definitions,
                tool_choice=tool_choice,
                first_round=round_index == 0 and mode is RoundMode.NORMAL,
                mode=mode,
            ),
            round_index,
        )
        return streamed

    async def _tool_exchange(
        self,
        streamed: ModelRound,
        spoken: tuple[MarkedReply, ...],
        round_index: int,
    ) -> Finished | tuple[Message, ...]:
        await self.events.speak(spoken, round_index + 1)
        finish_calls = (
            ()
            if self.structured is None
            else tuple(
                call for call in streamed.calls if call.name == self.structured.finish_tool.name
            )
        )
        finish_error: str | None = None
        if finish_calls:
            assert self.structured is not None
            if len(streamed.calls) > 1:
                finish_error = self.structured.coexisting_error
            else:
                try:
                    return Finished(
                        streamed.messages,
                        self.structured.validate(finish_calls[0]),
                        structured=True,
                    )
                except Exception as error:
                    finish_error = self.structured.invalid_error(error)
        finish_ids = {call.id for call in finish_calls}
        results: tuple[ToolResult, ...] = ()
        try:
            for segment in dispatch_segments(
                streamed.calls,
                parallel_safe=self.tools.parallel_safe,
                limit=self.definition.max_parallel_calls,
            ):
                if finish_error is not None and len(segment) == 1 and segment[0].id in finish_ids:
                    results = (
                        *results,
                        ToolResult(segment[0].id, finish_error, is_error=True),
                    )
                    continue
                await self.tools.prepare(segment)
                dispatched = await asyncio.gather(
                    *(self.tools.execute(call) for call in segment), return_exceptions=True
                )
                results = (
                    *results,
                    *(item for item in dispatched if not isinstance(item, BaseException)),
                )
                failure = next(
                    (item for item in dispatched if isinstance(item, BaseException)), None
                )
                if failure is not None:
                    raise failure
        finally:
            await self.tools.after_round(streamed.calls, results)
        content: tuple[Content, ...] = (
            *streamed.reasoning,
            *((Text(streamed.text),) if streamed.text else ()),
            *streamed.calls,
        )
        return (
            *streamed.messages,
            Message(role="assistant", content=content),
            Message(role="user", content=results),
        )

    async def _close(
        self,
        streamed: ModelRound,
        spoken: tuple[MarkedReply, ...],
        round_index: int,
    ) -> Finished:
        if self.structured is not None and not self.structured.blocked():
            prose = self.structured.accept_prose(streamed.text)
            if prose is not None:
                return Finished(streamed.messages, prose, structured=True)
            messages = (
                *streamed.messages,
                Message(role="assistant", content=streamed.text),
                Message(role="user", content=self.structured.finish_prompt),
            )
            return await self._force_finish(messages, round_index)
        await self.events.closing(spoken)
        return Finished(streamed.messages, streamed.text)

    async def _force_finish(self, messages: tuple[Message, ...], round_index: int) -> Finished:
        assert self.structured is not None
        streamed = await self._stream(messages, round_index, RoundMode.FORCE_FINISH)
        if len(streamed.calls) != 1 or streamed.calls[0].name != self.structured.finish_tool.name:
            raise RuntimeError("forced finish round did not return a lone finish call")
        try:
            answer = self.structured.validate(streamed.calls[0])
        except Exception as error:
            raise RuntimeError(f"forced finish failed the output schema: {error}") from error
        return Finished(streamed.messages, answer, structured=True)

    async def _exhaust(self, messages: tuple[Message, ...]) -> Finished:
        self.events.exhausted()
        messages = await self.conversation.prepare_exhaust(messages)
        if self.structured is not None:
            messages = (
                *messages,
                Message(role="user", content=self.structured.force_finish_prompt),
            )
            finished = await self._force_finish(messages, self.definition.max_rounds)
            return Finished(finished.messages, finished.answer, exhausted=True, structured=True)
        messages = (
            *messages,
            Message(role="user", content=self.definition.force_final_prompt),
        )
        streamed = await self._stream(messages, self.definition.max_rounds, RoundMode.NO_TOOLS)
        spoken, text = marked_replies(streamed.text)
        await self.events.closing(spoken)
        return Finished(streamed.messages, text, exhausted=True)
