"""Durable turn-step projection for trusted surface reads."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dbos import DBOSClient

from ufo.ext.surface import TurnStep
from ufo.loop.engine import DispatchResult, StreamResult


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
                )
            )
        return tuple(projected)

    @staticmethod
    def _timestamp(epoch_ms: int | None) -> datetime | None:
        return None if epoch_ms is None else datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
