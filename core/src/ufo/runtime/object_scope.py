"""The private task-local agent target for audited object handlers."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.runtime.agent_scope import agent_current


class ObjectAgent(BaseModel):
    """One exact agent namespace selected by object dispatch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str


class ObjectActionRequestTarget(BaseModel):
    """The validated wire target an object action requests before member-private resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str | None
    agent: str | None
    expected_generation: UUID | None


class ObjectActionTarget(BaseModel):
    """What one dispatched object action acts on, resolved by the engine before the handler runs:
    the bound kind, the instance name a collection action leaves None, the agent target an
    `agent_targetable` action resolved through the cross-agent gate, the live generation the
    instance read observed, and the generation the wire call supplied. Dispatch never refuses on
    the two differing. An interrupted dispatch carries this resolved target into its fresh DBOS
    step so an action that changed its own lookup key can reach its idempotent completion. A bound
    handler reads it from `ToolContext.target`; its input model never carries these fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str | None
    agent: ObjectAgent | None
    generation: UUID | None
    expected_generation: UUID | None


_target: ContextVar[ObjectAgent | None] = ContextVar("object_agent_target", default=None)


@contextmanager
def object_agent(target: ObjectAgent | None) -> Iterator[None]:
    if target is None:
        yield
        return
    token = _target.set(target)
    try:
        yield
    finally:
        _target.reset(token)


def object_agent_id() -> UUID:
    target = _target.get()
    return agent_current().agent_id if target is None else target.id
