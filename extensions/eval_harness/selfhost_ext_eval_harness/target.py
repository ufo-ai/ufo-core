"""The in-process target: drive one case as a real turn through the agent, then reconstruct the
grader-visible output from the durable transcript.

`invoke` is the whole seam the scoped ExtensionContext exposes — it admits a turn and returns its
id; it neither opens a conversation nor reports when the turn is terminal. So the target is handed
two injected collaborators for what the scoped context cannot do: `conversations` opens a fresh
conversation per case, and `outcome` awaits the admitted turn's terminal transcript. A privileged
core eval-driver unit (a sibling of the surface seam) would supply both directly and carry a
structured trajectory, retiring these injections and the transcript-shape reconstruction below."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from selfhost.sdk.context import ExtensionContext, Trajectory
from selfhost.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock
from selfhost_ext_eval_harness.capability import CapabilityCase, CapabilityOutput, ToolInvocation


@dataclass(frozen=True)
class TargetResult:
    """A target's reconstruction of one run: the grader-visible output, whether the turn terminated
    cleanly, and the failure reason when it did not (the grader is skipped on an unclean run)."""

    output: CapabilityOutput
    clean: bool
    failure_reason: str = ""


class CapabilityTarget(Protocol):
    async def run(self, case: CapabilityCase) -> TargetResult: ...


class EvalConversations(Protocol):
    async def open(self, case_name: str) -> UUID: ...


class TurnOutcome(Protocol):
    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...


@dataclass(frozen=True)
class InProcessTarget:
    ctx: ExtensionContext
    agent_id: UUID
    conversations: EvalConversations
    outcome: TurnOutcome

    async def run(self, case: CapabilityCase) -> TargetResult:
        conversation_id = await self.conversations.open(case.name)
        try:
            turn_id = await self.ctx.invoke(conversation_id, self.agent_id, case.message, case.name)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            return TargetResult(
                CapabilityOutput("", (), (message,)), False, f"invoke raised: {message}"
            )
        trajectory = await self.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            return TargetResult(
                CapabilityOutput("", (), ()), False, "turn produced no terminal transcript"
            )
        return TargetResult(capability_output(trajectory.messages), clean=True)


def capability_output(messages: tuple[Message, ...]) -> CapabilityOutput:
    """Rebuild the grader-visible output from the transcript: the final answer (the last assistant
    text), the ordered tool calls (each tool_use joined to its tool_result by id), and the error
    text of any call that failed."""
    result_by_id: dict[str, ToolResultBlock] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                result_by_id[block.tool_use_id] = block
    calls: list[ToolInvocation] = []
    errors: list[str] = []
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                result = result_by_id.get(block.id)
                calls.append(ToolInvocation(block.name, dict(block.input), _result_text(result)))
                if result is not None and result.is_error:
                    errors.append(_result_text(result))
    return CapabilityOutput(_final_answer(messages), tuple(calls), tuple(errors))


def _final_answer(messages: tuple[Message, ...]) -> str:
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if isinstance(message.content, str):
            return message.content
        text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
        if text:
            return text
    return ""


def _result_text(result: ToolResultBlock | None) -> str:
    if result is None:
        return ""
    if isinstance(result.content, str):
        return result.content
    return "".join(block.text for block in result.content if isinstance(block, TextBlock))
