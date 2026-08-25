"""Durably dispatch a sandbox JSON tool call through the turn loop."""

import asyncio
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.db import workspace_tx
from ufo.ext.surface import TurnTailer
from ufo.hub import Parked, Terminal
from ufo.loop.subagents import SubagentRegistry
from ufo.models.interface import ToolSchema
from ufo.o11y import current_traceparent, log
from ufo.sandbox.session import RunToken
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    INTENT_ADMISSION,
    RUNNING,
    SUBAGENT_SURFACE,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    turn_id_for,
)
from ufo.tools.bridge import (
    BridgeToolName,
    ToolBridgeFailure,
    ToolBridgeIntent,
    ToolBridgeListedTool,
    ToolBridgeRequest,
    ToolBridgeResponse,
    ToolBridgeSuccess,
    ToolBridgeToolList,
)
from ufo.tools.registry import ToolDef


@dataclass(frozen=True)
class ToolBridge:
    """Describe or durably dispatch one bridge tool under a live sandbox run's authority."""

    dbos: DBOSClient
    tailer: TurnTailer
    tools: tuple[ToolDef, ...]
    subagents: SubagentRegistry
    subagent_grants: dict[str, frozenset[str]]

    async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse:
        parent = await self._parent(run)
        if parent is None:
            return ToolBridgeFailure(error="the parent turn is not running")
        if request.action == "list":
            return ToolBridgeSuccess(
                result=ToolBridgeToolList(
                    tools=[
                        ToolBridgeListedTool(name=tool.name, description=tool.description)
                        for tool in sorted(self.tools, key=lambda item: item.name)
                        if self._allowed(parent, tool)
                    ]
                ).model_dump(mode="json")
            )
        if request.tool_name is None:
            raise ValueError(f"{request.action} request has no tool name")
        tool = next((tool for tool in self.tools if tool.name == request.tool_name), None)
        if tool is None:
            return ToolBridgeFailure(error=f"tool {request.tool_name!r} is not bridge-callable")
        if not self._allowed(parent, tool):
            return ToolBridgeFailure(error=f"tool {tool.name!r} is not available to this agent")
        if request.action == "get_schema":
            return ToolBridgeSuccess(
                result=ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_model.model_json_schema(),
                ).model_dump(mode="json")
            )
        admitted = await self._admit(run, parent, request)
        if admitted is None:
            return ToolBridgeFailure(error="the parent turn is not running")
        turn_id, conversation_id = admitted
        await self._enqueue(run.workspace_id, turn_id, conversation_id)
        return await self._terminal(turn_id)

    async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.agent_id,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.subagent_name,
                        tables.agent.c.tools,
                        tables.conversation.c.id.label("conversation_id"),
                        tables.conversation.c.sandbox_conversation_id,
                        tables.conversation.c.member_id,
                        tables.conversation.c.audience,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.agent, tables.agent.c.id == tables.turn.c.agent_id
                        ).join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(
                        tables.turn.c.workspace_id == run.workspace_id,
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.status == RUNNING,
                    )
                )
            ).one_or_none()

    def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool:
        if parent.subagent_profile is None:
            return parent.tools is None or tool.name in parent.tools
        profile = self.subagents.get(parent.subagent_profile)
        allowed = set(profile.tool_names)
        if not profile.isolated_tools:
            allowed.update(self.subagent_grants.get(profile.name, frozenset()))
        return tool.name in allowed or (tool.subagent_default and not profile.isolated_tools)

    async def _admit(
        self,
        run: RunToken,
        parent: sa.Row[tuple[object, ...]],
        request: ToolBridgeRequest,
    ) -> tuple[UUID, UUID] | None:
        conversation_id = uuid5(
            NAMESPACE_URL, f"{run.workspace_id}/{run.turn_id}/tool-bridge/{request.request_id}"
        )
        turn_id = turn_id_for(run.workspace_id, conversation_id, 1)
        intent = ToolBridgeIntent(
            request_id=request.request_id,
            tool=TypeAdapter(BridgeToolName).validate_python(request.tool_name),
            input=request.arguments,
        ).model_dump_json()
        async with workspace_tx() as connection:
            live = (
                await connection.execute(
                    sa.select(tables.turn.c.id)
                    .where(
                        tables.turn.c.workspace_id == run.workspace_id,
                        tables.turn.c.id == run.turn_id,
                        tables.turn.c.status == RUNNING,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if live is None:
                return None
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation)
                .values(
                    id=conversation_id,
                    workspace_id=run.workspace_id,
                    agent_id=parent.agent_id,
                    surface=SUBAGENT_SURFACE,
                    sandbox_conversation_id=(
                        parent.sandbox_conversation_id or parent.conversation_id
                    ),
                    queue_key=str(turn_id),
                    member_id=parent.member_id,
                    audience=parent.audience,
                    title=request.tool_name,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            await connection.execute(
                insert(tables.turn)
                .values(
                    id=turn_id,
                    workspace_id=run.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=parent.agent_id,
                    seq=1,
                    status="queued",
                    inbound=intent,
                    admission_source=INTENT_ADMISSION,
                    speaker_member_id=None,
                    on_behalf_of_member_id=run.acting_member_id,
                    terminal=None,
                    parent_turn_id=run.turn_id,
                    subagent_profile=parent.subagent_profile,
                    subagent_name=parent.subagent_name,
                    traceparent=current_traceparent(),
                    idempotency_key=f"tool-bridge:{run.turn_id}:{request.request_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            existing = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.inbound,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.on_behalf_of_member_id,
                    )
                    .where(tables.turn.c.id == turn_id)
                    .with_for_update()
                )
            ).one()
            if (
                existing.inbound != intent
                or existing.parent_turn_id != run.turn_id
                or existing.on_behalf_of_member_id != run.acting_member_id
            ):
                raise ValueError("tool bridge request id was reused for another call")
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
            )
        return turn_id, conversation_id

    async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "queue_partition_key": str(conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        try:
            await self.dbos.enqueue_async(options, str(workspace_id), str(turn_id))
        except asyncio.CancelledError:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
                )
            raise
        except Exception as error:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                    .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
                )
            log(
                "tool_bridge.enqueue_deferred",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )

    async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse:
        async with self.tailer.tail(turn_id) as frames:
            async for _, frame in frames:
                match frame:
                    case Terminal(frame=terminal):
                        return self._response(terminal)
                    case Parked(message=message):
                        return ToolBridgeFailure(error=message)
        raise RuntimeError("tool bridge tail ended without a terminal")

    def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse:
        if terminal.status != "done":
            detail = ": ".join(
                part
                for part in (terminal.error_class, terminal.error_message or terminal.text)
                if part
            )
            return ToolBridgeFailure(error=detail or f"tool turn ended {terminal.status}")
        try:
            parsed = json.loads(terminal.text)
        except json.JSONDecodeError:
            parsed = terminal.text
        return ToolBridgeSuccess(result=TypeAdapter(JsonValue).validate_python(parsed))
