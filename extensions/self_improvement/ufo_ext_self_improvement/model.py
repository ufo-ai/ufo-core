"""The one metered model leg used by proposing, replay, and grading."""

from dataclasses import dataclass
from typing import Protocol

from ufo.sdk.context import ModelAccess
from ufo.sdk.models import Message, ModelRequest, ToolSchema

MAX_OUTPUT_TOKENS = 2048


class ModelLeg(Protocol):
    async def complete(self, system: str, messages: tuple[Message, ...]) -> str: ...


class ReplayLeg(Protocol):
    async def turn(
        self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]
    ) -> Message: ...


@dataclass(frozen=True)
class ModelAccessLeg:
    """The extension's narrow adapter onto the SDK's workspace-keyed, metered model seam."""

    model: ModelAccess

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        return await self.model.complete(
            ModelRequest(
                model=self.model.model,
                system=system,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                reasoning="off",
            )
        )

    async def turn(
        self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]
    ) -> Message:
        return await self.model.turn(
            ModelRequest(
                model=self.model.model,
                system=system,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                tools=tools,
                reasoning="off",
            )
        )
