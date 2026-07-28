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
