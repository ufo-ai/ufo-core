"""Typed subagents: a registry of profiles and the spawn that runs one as a child turn.

A profile names a prompt, a tool subset, and an input/output schema. `spawn` validates the payload
against the input schema, admits a child turn linked to its parent (`parent_turn_id`) on its own
conversation, and enqueues it on the turn queue — a distinct partition, so the parent may await it
without the queue serializing them into a deadlock. Foreground awaits the child's terminal and
returns its schema-validated output; background returns the child turn id at once."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from selfhost.db import workspace_tx
from selfhost.ext.manifest import SubagentProfile
from selfhost.loop.prompts.render import (
    CITATION_BLOCK,
    CITATION_SLOT,
    PROMPT_VAR_RE,
    SKILL_INDEX_SLOT,
    render_skill_index,
)
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    Turn,
    turn_id_for,
)
from selfhost.skills.runtime import CORE_SKILLS
from selfhost.tools.context import SpawnResult, SubagentStatus

SUBAGENT_SURFACE = "subagent"
SUBAGENT_POLL_SECONDS = 0.1

SUBAGENT_OUTPUT_DISCIPLINE = (
    (Path(__file__).parent / "prompts" / "subagent_shell.md")
    .read_text()
    .strip()
    .replace(CITATION_SLOT, CITATION_BLOCK)
)
SUBAGENT_SKILL_INDEX = render_skill_index(
    tuple((skill.name, skill.description) for skill in CORE_SKILLS)
)


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
    """The child's system prompt: the profile's own instructions with its `{{skill_index}}` slot
    filled from the loadable-skill index, then the shared output discipline (citation and formatting
    rules, wrapped around every profile so a subagent inherits the same citation contract the main
    agent renders), then the output contract — so the child's final answer is a single JSON object
    the parent can validate against the schema. A slot the profile leaves unfilled fails loud rather
    than reaching the model as a literal brace."""
    body = profile.prompt.replace(SKILL_INDEX_SLOT, SUBAGENT_SKILL_INDEX)
    wrapped = f"{body}\n\n{SUBAGENT_OUTPUT_DISCIPLINE}"
    if unresolved := frozenset(PROMPT_VAR_RE.findall(wrapped)):
        raise ValueError(f"subagent prompt has unresolved slots: {', '.join(sorted(unresolved))}")
    schema = json.dumps(profile.output_model.model_json_schema(), sort_keys=True)
    contract = f"Respond with a single JSON object matching this schema and nothing else:\n{schema}"
    return f"{wrapped}\n\n{contract}"


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

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
        """Await each background child's terminal and report its status and final text — the parent
        ends its own turn and calls this when it has no other independent work, completing the
        background-spawn loop. Each id is polled through the same terminal read a foreground spawn
        awaits, so a child that has already finished returns at once."""
        statuses = []
        for turn_id in turn_ids:
            terminal = await self._await_terminal(turn_id)
            statuses.append(
                SubagentStatus(turn_id=turn_id, status=terminal.status, text=terminal.text)
            )
        return tuple(statuses)

    async def cancel(self, turn_id: UUID) -> SubagentStatus:
        """Cancel a running child by cancelling its durable workflow, then report the turn's current
        status. A child that has already finished is a no-op — the cancel is idempotent and the
        committed terminal stands. Refuses a turn id that is not a child of this parent."""
        await self._require_child(turn_id)
        await self.client.cancel_workflow_async(str(turn_id))
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one()
        text = "" if row.terminal is None else TerminalFrame.model_validate(row.terminal).text
        return SubagentStatus(turn_id=turn_id, status=row.status, text=text)

    async def _require_child(self, turn_id: UUID) -> None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.parent_turn_id).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None or row.parent_turn_id != self.parent.id:
            raise ValueError(f"{turn_id} is not a subagent this turn spawned")

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
