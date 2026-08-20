"""Inert dependencies for tests that mount surfaces without exercising the skills view or the
ambient reply decision — a surface context requires both, so a test that reads neither passes
something real and empty."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from ufo.ambient_reply import AmbientDecision, AmbientReplyClassifier
from ufo.ext.surface import Stopped
from ufo.models.interface import ModelRequest
from ufo.skills.runtime import RuntimeSkill, SkillRegistry

EMPTY_SKILL_REGISTRY = SkillRegistry({})


async def no_member_skills() -> tuple[RuntimeSkill, ...]:
    return ()


@dataclass(frozen=True)
class FixedDecisionModel:
    """The provider leg of the ambient reply decision, fixed to one answer and recording the payload
    it was asked about — so a test drives ingest either way and reads back the thread the surface
    built, never the prompt around it. A test whose surface should reach no model at all leaves
    `decision` empty, and a call raises. A `gate` holds the answer until the test sets it, so a test
    can assert what the surface did while the decision was still outstanding."""

    decision: AmbientDecision | None = None
    asked: list[str] = field(default_factory=list)
    model: str = "fixed-decision"
    gate: asyncio.Event | None = None

    async def complete(self, request: ModelRequest) -> str:
        if self.decision is None:
            raise AssertionError("this surface reaches no model")
        self.asked.append(str(request.messages[-1].content))
        if self.gate is not None:
            await self.gate.wait()
        return self.decision


UNREACHED_AMBIENT_REPLY = AmbientReplyClassifier(model=FixedDecisionModel())


@dataclass(frozen=True)
class UnreachedStopper:
    """A surface context requires a stopper; a test whose surface stops no turn gets one that
    fails loud on use."""

    async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped:
        raise AssertionError("this surface stops no turn")


UNREACHED_STOPPER = UnreachedStopper()
