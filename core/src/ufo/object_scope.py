"""The private task-local agent target for audited object handlers."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from ufo.agent_scope import agent_current


@dataclass(frozen=True)
class ObjectAgent:
    """One exact agent namespace selected by object dispatch."""

    id: UUID
    name: str


@dataclass(frozen=True)
class ObjectActionTarget:
    """What one dispatched object action acts on, resolved by the engine before the handler runs:
    the bound kind, the instance name a collection action leaves None, the agent target an
    `agent_targetable` action resolved through the cross-agent gate, the live generation the
    instance read observed, and the generation the wire call supplied. Dispatch never refuses on
    the two differing — a self-mutating action interrupted after its write and resumed by DBOS
    re-reads the row at the generation it minted, and its idempotent dedup must run first — so a
    handler that must fence compares them and fences its own write atomically. A bound handler
    reads it from `ToolContext.target`; its input model never carries these fields."""

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
