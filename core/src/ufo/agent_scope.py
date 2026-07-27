"""The ambient identity for agent-owned capabilities."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from ufo.workspace import ws_current


class AgentUnbound(RuntimeError):
    """An agent-scoped capability ran outside an agent boundary."""


@dataclass(frozen=True)
class AgentScope:
    """The agent bound inside the current workspace."""

    workspace_id: UUID
    agent_id: UUID


_current_agent: ContextVar[AgentScope | None] = ContextVar("current_agent", default=None)


@contextmanager
def agent(agent_id: UUID) -> Iterator[AgentScope]:
    """Bind one agent inside the ambient workspace without permitting an in-place switch."""
    scope = AgentScope(workspace_id=ws_current().workspace_id, agent_id=agent_id)
    current = _current_agent.get()
    if current is not None and current != scope:
        raise RuntimeError("cannot switch agent inside a bound agent scope")
    token = _current_agent.set(scope)
    try:
        yield scope
    finally:
        _current_agent.reset(token)


def agent_current() -> AgentScope:
    """Return the bound agent, failing loud outside or across its workspace boundary."""
    scope = _current_agent.get()
    if scope is None:
        raise AgentUnbound("no agent bound; wrap the call in `with agent(agent_id):`")
    if ws_current().workspace_id != scope.workspace_id:
        raise RuntimeError("bound workspace does not match the bound agent")
    return scope
