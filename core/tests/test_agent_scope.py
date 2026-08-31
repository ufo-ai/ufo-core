from uuid import uuid4

import pytest

from ufo.runtime.agent_scope import AgentUnbound, agent, agent_current
from ufo.runtime.workspace import WorkspaceUnbound, ws


def test_agent_requires_workspace() -> None:
    with pytest.raises(WorkspaceUnbound):
        with agent(uuid4()):
            pass


def test_agent_current_requires_scope() -> None:
    with pytest.raises(AgentUnbound):
        agent_current()


def test_agent_binds_and_resets() -> None:
    workspace_id = uuid4()
    agent_id = uuid4()

    with ws(workspace_id), agent(agent_id) as scope:
        assert scope.workspace_id == workspace_id
        assert scope.agent_id == agent_id
        assert agent_current() == scope

        with agent(agent_id):
            assert agent_current() == scope

    with pytest.raises(AgentUnbound):
        agent_current()


def test_agent_refuses_switch_inside_scope() -> None:
    with ws(uuid4()), agent(uuid4()), pytest.raises(RuntimeError, match="cannot switch agent"):
        with agent(uuid4()):
            pass


def test_agent_refuses_workspace_switch_inside_scope() -> None:
    with (
        ws(uuid4()),
        agent(uuid4()),
        ws(uuid4()),
        pytest.raises(RuntimeError, match="workspace does not match"),
    ):
        agent_current()
