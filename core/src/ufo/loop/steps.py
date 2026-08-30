"""Durable turn-step projection for trusted surface reads."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dbos import DBOSClient

from ufo.ext.surface import TurnStep
from ufo.loop.engine import DispatchResult, StreamResult
from ufo.models.interface import Message, TextBlock, ToolResultBlock

IMAGE_ATTACHMENT_NOTE = "\n[{count} image attachment(s) omitted]"


def _step_messages(output: object) -> tuple[Message, ...]:
    """The window a recorded step output rebuilds, for a diagnostic read of a turn's trajectory.
    A model round is the assistant message it produced — its reasoning, its text, its tool calls —
    or its salvaged partial output when the round errored mid-stream; a tool dispatch is the result
    the model saw, image bytes left as a note rather than rehydrated. Every other output — a
    compaction, a claimed-arrival batch, a round that errored with nothing salvaged — rebuilds no
    window and contributes none. The text is the model's own, reply markup and all, because a
    reader diagnosing a turn wants what was emitted, not what the member was shown."""
    match output:
        case StreamResult() as streamed:
            blocks = (
                *streamed.reasoning,
                *((TextBlock(text=streamed.text),) if streamed.text else ()),
                *streamed.tool_calls,
            )
            if blocks:
                return (Message(role="assistant", content=blocks),)
            if streamed.partial_output:
                return (Message(role="assistant", content=streamed.partial_output),)
            return ()
        case DispatchResult() as dispatched:
            content = dispatched.text
            if dispatched.image_refs:
                content += IMAGE_ATTACHMENT_NOTE.format(count=len(dispatched.image_refs))
            return (
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(
                            tool_use_id=dispatched.tool_use_id,
                            content=content,
                            is_error=dispatched.is_error,
                            activity=dispatched.activity,
                        ),
                    ),
                ),
            )
        case _:
            return ()


@dataclass(frozen=True)
class DurableTurnSteps:
    """Decode DBOS's typed step outputs into the surface boundary shape."""

    client: DBOSClient

    async def read(self, workflow_id: str) -> tuple[TurnStep, ...]:
        recorded = await self.client.list_workflow_steps_async(workflow_id)
        tool_names: dict[str, str] = {}
        for step in recorded:
            match step.get("output"):
                case StreamResult(tool_calls=calls):
                    tool_names.update((call.id, call.name) for call in calls)
        projected = []
        for number, step in enumerate(recorded, 1):
            match step["function_name"]:
                case str() as function_name:
                    pass
                case value:
                    raise TypeError(f"DBOS step function_name is {type(value).__name__}")
            kind: Literal["model", "tool", "workflow"]
            match step.get("output"):
                case StreamResult():
                    kind, name = "model", "model round"
                case DispatchResult(tool_use_id=call_id):
                    kind, name = "tool", tool_names.get(call_id, "tool call")
                case _:
                    kind, name = "workflow", function_name.rsplit(".", 1)[-1]
            started_ms = step.get("started_at_epoch_ms")
            completed_ms = step.get("completed_at_epoch_ms")
            projected.append(
                TurnStep(
                    number=number,
                    kind=kind,
                    name=name,
                    function_name=function_name,
                    started_at=self._timestamp(started_ms),
                    completed_at=self._timestamp(completed_ms),
                    duration_ms=(
                        None
                        if started_ms is None or completed_ms is None
                        else max(completed_ms - started_ms, 0)
                    ),
                    messages=_step_messages(step.get("output")),
                )
            )
        return tuple(projected)

    @staticmethod
    def _timestamp(epoch_ms: int | None) -> datetime | None:
        return None if epoch_ms is None else datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
