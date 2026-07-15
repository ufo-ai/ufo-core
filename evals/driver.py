"""The live workspace driver: what the operator runner hands the harness so a case runs as a real
turn against a running `ufoctl serve`. It fills the two steps the scoped ExtensionContext cannot —
opening a fresh conversation per case and awaiting an admitted turn's terminal transcript — by
reaching the workspace's own rows and blob store, and it builds the ExtensionContext bound to the
shared admission invoker (the same producer every surface and job admits through). Enqueuing needs a
running serve to drain the turn queue; the driver awaits a queued or running durable workflow, then
reads the terminal row and its exact-sequence transcript."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, WorkflowHandleAsync
from dbos import error as dbos_error

from ufo.blob import BlobNotFound, BlobStore
from ufo.db import workspace_tx
from ufo.ext.context import Trajectory
from ufo.governance import prompt_digest
from ufo.schema import tables
from ufo.transcript import TranscriptDecodeError, decode, transcript_key
from ufo.workspace import ws

EVAL_SURFACE = "eval"
POLL_INTERVAL_SECONDS = 1.0
WORKFLOW_WAIT_SECONDS = 300.0
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})
WORKFLOW_STATUSES = frozenset({"queued", "running"})
FAILED_WORKFLOW_STATUSES = frozenset({"ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED", "CANCELLED"})


async def resolve_workspace_and_agent(
    agent_name: str, workspace_id: UUID | None = None
) -> tuple[UUID, UUID, str, str]:
    """Resolve the named target agent in an explicit workspace or the dedicated workspace."""
    if workspace_id is None:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.prompt, tables.agent.c.model).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == agent_name,
                    )
                )
            ).one()
    return workspace_id, agent.id, agent.prompt, agent.model


@dataclass(frozen=True)
class WorkspaceDriver:
    workspace_id: UUID
    agent_id: UUID
    agent_prompt: str
    blob: BlobStore
    dbos: DBOSClient
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    workflow_wait_seconds: float = WORKFLOW_WAIT_SECONDS

    async def open(self, case_name: str, member_key: str | None = None) -> UUID:
        """Open one isolated eval conversation. A member-bound case names its member by the exact
        workspace `member.email`; an absent email fails rather than degrading to shared-only
        recall."""
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            member_id = None
            if member_key is not None:
                member_id = (
                    await connection.execute(
                        sa.select(tables.member.c.id).where(
                            tables.member.c.workspace_id == self.workspace_id,
                            tables.member.c.email == member_key,
                        )
                    )
                ).scalar_one_or_none()
                if member_id is None:
                    raise ValueError(
                        f"eval member_key {member_key!r} is not a member email in this workspace"
                    )
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}:{case_name}:{conversation_id}",
                    member_id=member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return conversation_id

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            return await self._trajectory(conversation_id, row.seq)
        if row.status not in WORKFLOW_STATUSES:
            return None
        try:
            async with asyncio.timeout(self.workflow_wait_seconds):
                handle: WorkflowHandleAsync[object]
                while True:
                    try:
                        handle = await self.dbos.retrieve_workflow_async(str(turn_id))
                        break
                    except dbos_error.DBOSNonExistentWorkflowError:
                        async with workspace_tx() as connection:
                            row = (
                                await connection.execute(
                                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                                        tables.turn.c.id == turn_id
                                    )
                                )
                            ).one_or_none()
                        if row is None:
                            return None
                        if row.status in TERMINAL_STATUSES:
                            return await self._trajectory(conversation_id, row.seq)
                        if row.status not in WORKFLOW_STATUSES:
                            return None
                        if row.status == "running":
                            raise
                        await asyncio.sleep(self.poll_interval_seconds)
                try:
                    await handle.get_result(polling_interval_sec=self.poll_interval_seconds)
                except Exception:
                    workflow = await handle.get_status()
                    if workflow.status not in FAILED_WORKFLOW_STATUSES:
                        raise
        except TimeoutError:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None or row.status not in TERMINAL_STATUSES:
            return None
        return await self._trajectory(conversation_id, row.seq)

    async def _trajectory(self, conversation_id: UUID, turn_seq: int) -> Trajectory | None:
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None
        try:
            conversation = decode(body)
        except TranscriptDecodeError:
            return None
        if conversation.seq != turn_seq:
            return None
        return Trajectory(
            conversation_id=conversation_id,
            agent_id=self.agent_id,
            agent_prompt=self.agent_prompt,
            agent_prompt_digest=prompt_digest(self.agent_prompt),
            messages=conversation.messages,
        )
