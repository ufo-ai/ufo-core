"""The live workspace driver: what the operator runner hands the harness so a case runs as a real
turn against a running `selfhost serve`. It fills the two steps the scoped ExtensionContext cannot —
opening a fresh conversation per case and awaiting an admitted turn's terminal transcript — by
reaching the workspace's own rows and blob store, and it builds the ExtensionContext bound to the
shared admission invoker (the same producer every surface and job admits through). Enqueuing needs a
running serve to drain the turn queue; the driver polls the turn row until it settles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient

from selfhost.blob import BlobNotFound, BlobStore
from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ExtensionContext, Trajectory, context_for
from selfhost.governance import prompt_digest
from selfhost.schema import tables
from selfhost.surfaces.admission import Admission, AdmissionInvoker
from selfhost.transcript import TranscriptDecodeError, decode, transcript_key

EVAL_SURFACE = "eval"
POLL_INTERVAL_SECONDS = 1.0
MAX_POLLS = 300
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})


async def resolve_workspace_and_agent(agent_name: str) -> tuple[UUID, UUID, str]:
    """The single workspace and the named agent's id + prompt — the target every eval case runs."""
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.prompt).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == agent_name,
                )
            )
        ).one()
    return workspace_id, agent.id, agent.prompt


def eval_context(config: Config, workspace_id: UUID, blob: BlobStore) -> ExtensionContext:
    dbos = DBOSClient(system_database_url=config.database.system_url)
    return context_for(
        workspace_id,
        "eval_harness",
        frozenset(),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        blob=blob,
        invoker=AdmissionInvoker(admission=Admission(dbos=dbos), workspace_id=workspace_id),
    )


@dataclass(frozen=True)
class WorkspaceDriver:
    workspace_id: UUID
    agent_id: UUID
    agent_prompt: str
    blob: BlobStore
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    max_polls: int = MAX_POLLS

    async def open(self, case_name: str) -> UUID:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}:{case_name}:{conversation_id}",
                    member_id=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return conversation_id

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        for _ in range(self.max_polls):
            async with workspace_tx() as connection:
                status = (
                    await connection.execute(
                        sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one_or_none()
            if status in TERMINAL_STATUSES:
                return await self._trajectory(conversation_id)
            await asyncio.sleep(self.poll_interval_seconds)
        return None

    async def _trajectory(self, conversation_id: UUID) -> Trajectory | None:
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None
        try:
            conversation = decode(body)
        except TranscriptDecodeError:
            return None
        return Trajectory(
            conversation_id=conversation_id,
            agent_id=self.agent_id,
            agent_prompt=self.agent_prompt,
            agent_prompt_digest=prompt_digest(self.agent_prompt),
            messages=conversation.messages,
        )
