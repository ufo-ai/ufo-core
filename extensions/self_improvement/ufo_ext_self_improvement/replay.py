"""Counterfactual, tool-aware replay of one archived task against one prompt arm.

A replay re-runs only the model legs of an archived conversation under a swapped system prompt,
feeding each requested tool call back from the SAME archived result rather than executing anything —
so it has zero side effects and isolates the effect of the prompt body on the final answer. The
prompt body is the only feature a candidate changes, and it shapes the answer, not (usually) the
tool path; a replay that requests a tool with no archived result has DIVERGED off the archived path
and is graded on what it produced so far. The replay tool catalog is derived from the archived
trajectory's own tool calls — the scoped extension has no reach into the live agent's tool registry,
and the archived conversation shows the model the exact call shapes to reproduce."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from ufo.sdk.models import Message, ToolResultBlock, ToolUseBlock
from ufo_ext_self_improvement.model import ReplayLeg, ToolSchema

REPLAY_ROUND_LIMIT = 6


@dataclass(frozen=True)
class ReplayResult:
    """One arm's counterfactual outcome: the regenerated final answer, whether the model diverged
    off the archived tool path, and how many model legs it took. `diverged` is surfaced, not hidden:
    a diverged replay is a weaker signal the grader still scores on the partial answer."""

    final_text: str
    diverged: bool
    rounds: int


def _canonical_input(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """The archived conversation with its original final answer stripped — the context the model
    regenerates from. Drop trailing assistant messages that carry no tool_use (the final answer the
    original prompt produced); keep every user turn and every tool round, whose results are the
    context the counterfactual reuses."""
    head = list(messages)
    while head and head[-1].role == "assistant":
        content = head[-1].content
        if not isinstance(content, str) and any(
            isinstance(block, ToolUseBlock) for block in content
        ):
            break
        head.pop()
    return tuple(head)


def archived_tool_results(
    messages: tuple[Message, ...],
) -> dict[tuple[str, str], ToolResultBlock]:
    """Index the archived tool results by the (name, canonical-input) of the tool_use that produced
    them, so a replayed call is answered with the SAME result the original run got — never by
    executing the tool. A tool_use block pairs with the tool_result carrying its id."""
    result_by_id: dict[str, ToolResultBlock] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                result_by_id[block.tool_use_id] = block
    results: dict[tuple[str, str], ToolResultBlock] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                result = result_by_id.get(block.id)
                if result is not None:
                    results[(block.name, _canonical_input(block.input))] = result
    return results


def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]:
    """A permissive tool schema per distinct tool the archived run called — enough for the model to
    reproduce the call; the archived conversation shows it the exact argument shapes."""
    names: list[str] = []
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.name not in names:
                names.append(block.name)
    return tuple(
        {
            "name": name,
            "description": "Archived tool replayed from the trajectory.",
            "input_schema": {"type": "object", "additionalProperties": True},
        }
        for name in names
    )


def _feed_archived(
    tool_uses: tuple[ToolUseBlock, ...],
    results: Mapping[tuple[str, str], ToolResultBlock],
) -> Message | None:
    """The single tool-result user message answering this round's calls from the archive, or None
    when any call has no archived result (the replay has diverged off the archived tool path)."""
    blocks: list[ToolResultBlock] = []
    for call in tool_uses:
        archived = results.get((call.name, _canonical_input(call.input)))
        if archived is None:
            return None
        blocks.append(
            ToolResultBlock(
                tool_use_id=call.id, content=archived.content, is_error=archived.is_error
            )
        )
    return Message(role="user", content=tuple(blocks))


@dataclass(frozen=True)
class ReplayEvaluation:
    """Tool-aware model-replay of one archived task against one prompt arm. `replay` is the flow:
    strip the original final answer, then run model legs over the archived context, feeding each
    requested call back from the archived results (never executing) until the model emits a final
    answer, diverges off the archived tool path, or hits the round limit."""

    model: ReplayLeg
    round_limit: int = REPLAY_ROUND_LIMIT

    async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult:
        results = archived_tool_results(archived)
        tools = replay_tools(archived)
        messages = replay_head(archived)
        last_text = ""
        for turn in range(1, self.round_limit + 1):
            leg = await self.model.turn(system_prompt, messages, tools)
            if not leg.tool_uses:
                return ReplayResult(leg.text, diverged=False, rounds=turn)
            fed = _feed_archived(leg.tool_uses, results)
            if fed is None:
                return ReplayResult(last_text, diverged=True, rounds=turn)
            messages = (*messages, Message(role="assistant", content=leg.content), fed)
            last_text = leg.text or last_text
        return ReplayResult(last_text, diverged=True, rounds=self.round_limit)
