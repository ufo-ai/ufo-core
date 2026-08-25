import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import EnqueueOptions
from pydantic import ValidationError
from pytest import raises

from ufo.db import workspace_tx
from ufo.hub import InProcessHub, Terminal
from ufo.loop.subagents import SubagentRegistry
from ufo.loop.tool_bridge import ToolBridge
from ufo.sandbox.session import RunToken
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.surfaces.hub_tail import HubTailer
from ufo.tools.bridge import (
    ToolBridgeFailure,
    ToolBridgeIntent,
    ToolBridgeRequest,
    ToolBridgeSuccess,
    ToolBridgeToolList,
    bridge_tools,
)
from ufo.workspace import ws


@dataclass
class _DBOS:
    enqueued: asyncio.Event = field(default_factory=asyncio.Event)
    options: EnqueueOptions | None = None
    args: tuple[str, str] | None = None

    async def enqueue_async(self, options: EnqueueOptions, *args: str) -> None:
        self.options = options
        self.args = (args[0], args[1])
        self.enqueued.set()


async def _seed(tools: tuple[str, ...] | None = None) -> tuple[RunToken, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id, sandbox_id, turn_id = (
        uuid4() for _ in range(6)
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                tools=tools,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                sandbox_conversation_id=sandbox_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="use the tool",
                admission_source="member",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return RunToken(workspace_id, turn_id, member_id), conversation_id, sandbox_id


def _bridge(dbos: _DBOS, hub: InProcessHub) -> ToolBridge:
    return ToolBridge(
        dbos=dbos,
        tailer=HubTailer(hub),
        tools=bridge_tools(()),
        subagents=SubagentRegistry(()),
        subagent_grants={},
    )


def test_bridge_request_action_controls_the_tool_name() -> None:
    request_id = uuid4()
    assert ToolBridgeRequest(request_id=request_id, action="list").tool_name is None
    with raises(ValidationError, match="take no tool name"):
        ToolBridgeRequest(request_id=request_id, action="list", tool_name="object_list")
    with raises(ValidationError, match="require a tool name"):
        ToolBridgeRequest(request_id=request_id, action="execute")


async def test_list_returns_only_live_allowed_tools_with_descriptions(db: None) -> None:
    run, _, _ = await _seed(("object_list",))
    bridge = _bridge(_DBOS(), InProcessHub())
    with ws(run.workspace_id):
        response = await bridge.request(
            run,
            ToolBridgeRequest(request_id=uuid4(), action="list"),
        )
    assert isinstance(response, ToolBridgeSuccess)
    listed = ToolBridgeToolList.model_validate(response.result)
    assert [tool.name for tool in listed.tools] == ["object_list"]
    assert listed.tools[0].description


async def test_describe_returns_the_bridge_input_schema_for_a_live_allowed_turn(db: None) -> None:
    run, _, _ = await _seed()
    bridge = _bridge(_DBOS(), InProcessHub())
    with ws(run.workspace_id):
        response = await bridge.request(
            run,
            ToolBridgeRequest(request_id=uuid4(), action="get_schema", tool_name="object_get"),
        )
    assert isinstance(response, ToolBridgeSuccess)
    assert isinstance(response.result, dict)
    assert response.result["name"] == "object_get"
    assert "requested_by" not in response.result["input_schema"]["properties"]


async def test_describe_refuses_a_tool_outside_the_agents_allowlist(db: None) -> None:
    run, _, _ = await _seed(("object_list",))
    bridge = _bridge(_DBOS(), InProcessHub())
    with ws(run.workspace_id):
        response = await bridge.request(
            run,
            ToolBridgeRequest(request_id=uuid4(), action="get_schema", tool_name="object_get"),
        )
    assert response == ToolBridgeFailure(error="tool 'object_get' is not available to this agent")


async def test_execute_admits_a_durable_child_and_returns_its_json_terminal(db: None) -> None:
    run, parent_conversation_id, sandbox_id = await _seed()
    dbos = _DBOS()
    hub = InProcessHub()
    bridge = _bridge(dbos, hub)
    request_id = uuid4()
    request = ToolBridgeRequest(
        request_id=request_id,
        action="execute",
        tool_name="object_list",
        arguments={"kind": "agent"},
    )
    with ws(run.workspace_id):
        waiting = asyncio.create_task(bridge.request(run, request))
        await dbos.enqueued.wait()
        assert dbos.args is not None
        child_id = UUID(dbos.args[1])
        async with workspace_tx() as connection:
            child = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.inbound,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.on_behalf_of_member_id,
                        tables.turn.c.admission_source,
                        tables.conversation.c.sandbox_conversation_id,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == child_id)
                )
            ).one()
            terminal = TerminalFrame(status="done", text='{"objects":[]}')
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    status="done",
                    terminal=terminal.model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
                .where(tables.turn.c.id == child_id)
            )
        await hub.publish(child_id, Terminal(frame=terminal))
        response = await waiting
    intent = ToolBridgeIntent.model_validate_json(child.inbound)
    assert intent.request_id == request_id
    assert intent.tool == "object_list"
    assert intent.input == {"kind": "agent"}
    assert child.parent_turn_id == run.turn_id
    assert child.on_behalf_of_member_id == run.acting_member_id
    assert child.admission_source == "intent"
    assert child.sandbox_conversation_id == sandbox_id
    assert parent_conversation_id != UUID(dbos.options["queue_partition_key"])
    assert response == ToolBridgeSuccess(result={"objects": []})
