"""The live workspace driver: what the operator runner hands the harness so a case runs as a real
turn against a running `ufoctl serve`. It fills the two steps the scoped ExtensionContext cannot —
opening a fresh conversation per case and awaiting an admitted turn's terminal transcript — by
reaching the workspace's own rows and blob store, and it builds the ExtensionContext bound to the
shared admission invoker (the same producer every surface and job admits through). Enqueuing needs a
running serve to drain the turn queue; the driver polls the turn row until it settles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient

from ufo.blob import BlobNotFound, BlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, Trajectory, context_for
from ufo.ext.loader import load_manifests
from ufo.governance import prompt_digest
from ufo.models.registry import model_registry
from ufo.schema import tables
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.transcript import TranscriptDecodeError, decode, transcript_key
from ufo.workspace import ws

EVAL_SURFACE = "eval"
POLL_INTERVAL_SECONDS = 1.0
MAX_POLLS = 300
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})


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


def eval_context(config: Config, workspace_id: UUID, blob: BlobStore) -> ExtensionContext:
    dbos = DBOSClient(system_database_url=config.database.system_url)
    return context_for(
        "evals",
        frozenset(),
        blob=blob,
        invoker=AdmissionInvoker(
            admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
            workspace_id=workspace_id,
        ),
        model_resolver=model_registry(config, load_manifests(config.pack.name)),
    )


@dataclass(frozen=True)
class WorkspaceDriver:
    workspace_id: UUID
    agent_id: UUID
    agent_prompt: str
    blob: BlobStore
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    max_polls: int = MAX_POLLS

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
