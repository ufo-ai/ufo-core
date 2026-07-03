"""Typed subagents: a registry of profiles and the spawn that runs one as a child turn.

A profile names a prompt, a tool subset, and an input/output schema. `spawn` validates the payload
against the input schema, admits a child turn linked to its parent (`parent_turn_id`) on its own
conversation, and enqueues it on the turn queue — a distinct partition, so the parent may await it
without the queue serializing them into a deadlock. Foreground awaits the child's terminal and
returns its schema-validated output; background returns the child turn id at once."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from pydantic import BaseModel

from selfhost.db import workspace_tx
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    Turn,
    turn_id_for,
)
from selfhost.tools.context import SpawnResult

SUBAGENT_SURFACE = "subagent"
SUBAGENT_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class SubagentProfile:
    name: str
    prompt: str
    tool_names: tuple[str, ...]
    input_model: type[BaseModel]
    output_model: type[BaseModel]


@dataclass(frozen=True)
class SubagentRegistry:
    """The frozen set of profiles a spawn dispatches against — extensions contribute profiles the
    same way they contribute tools. Rejects a duplicate name at construction, fails loud on an
    unknown lookup."""

    profiles: tuple[SubagentProfile, ...]

    def __post_init__(self) -> None:
        names = [profile.name for profile in self.profiles]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate subagent profiles: {', '.join(duplicates)}")

    def get(self, name: str) -> SubagentProfile:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise KeyError(f"unknown subagent profile: {name}")


def subagent_system_prompt(profile: SubagentProfile) -> str:
    """The child's system prompt: the profile's own instructions plus the output contract, so the
    child's final answer is a single JSON object the parent can validate against the schema."""
    schema = json.dumps(profile.output_model.model_json_schema(), sort_keys=True)
    return (
        f"{profile.prompt}\n\n"
        f"Respond with a single JSON object matching this schema and nothing else:\n{schema}"
    )


@dataclass(frozen=True)
class Subagents:
    """The spawn workflow, bound to the turn that spawns: resolve the profile, admit and enqueue a
    child turn, then (foreground) await and validate its output."""

    client: DBOSClient
    registry: SubagentRegistry
    parent: Turn

    async def spawn(
        self, profile: str, payload: dict[str, Any], background: bool = False
    ) -> SpawnResult:
        resolved = self.registry.get(profile)
        typed_input = resolved.input_model.model_validate(payload)
        conversation_id = uuid4()
        turn_id = turn_id_for(self.parent.workspace_id, conversation_id, 1)
        await self._admit(conversation_id, turn_id, profile, typed_input.model_dump_json())
        await self._enqueue(turn_id, conversation_id)
        if background:
            return SpawnResult(turn_id=turn_id, output=None)
        terminal = await self._await_terminal(turn_id)
        if terminal.status != "done":
            raise RuntimeError(f"subagent {profile!r} turn ended {terminal.status}")
        output = resolved.output_model.model_validate_json(terminal.text)
        return SpawnResult(turn_id=turn_id, output=output)

    async def _admit(
        self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str
    ) -> None:
        async with workspace_tx() as connection:
            member_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.id == self.parent.conversation_id
                    )
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.parent.workspace_id,
                    surface=SUBAGENT_SURFACE,
                    queue_key=str(turn_id),
                    member_id=member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=self.parent.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=self.parent.agent_id,
                    seq=1,
                    status="queued",
                    inbound=inbound,
                    terminal=None,
                    parent_turn_id=self.parent.id,
                    subagent_profile=profile,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "queue_partition_key": str(conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        await self.client.enqueue_async(options, str(turn_id))

    async def _await_terminal(self, turn_id: UUID) -> TerminalFrame:
        while True:
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).one()
            if row.terminal is not None:
                return TerminalFrame.model_validate(row.terminal)
            await asyncio.sleep(SUBAGENT_POLL_SECONDS)
