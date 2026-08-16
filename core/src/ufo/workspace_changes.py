"""What a conversation changed in its workspace, as git sees it.

A change belongs to the workspace, not to a message stream. Replaying one conversation's tool
results counts only the files that conversation's own `write` and `edit` touched — never what its
subagents wrote into the sandbox they share with it, never what a shell command or a script did,
never a deletion or a rename, and never the file's whole distance from `HEAD` rather than each edit
that got it there. So the sandbox asks git, and the answer is recorded here when a turn commits: a
projection that outlives the sandbox it was read from, and that any writer's work lands in.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.db import workspace_tx
from ufo.o11y import log
from ufo.sandbox.session import SandboxSession
from ufo.schema import tables
from ufo.tools.file_changes import FILE_CHANGE_PATH_MAX_CHARS

WORKSPACE_CHANGE_PATCH_MAX_CHARS = 25_000
WORKSPACE_CHANGES_MAX = 100


class WorkspaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=FILE_CHANGE_PATH_MAX_CHARS)
    patch: str = Field(max_length=WORKSPACE_CHANGE_PATCH_MAX_CHARS)
    truncated: bool


class WorkspaceChanges(BaseModel):
    """One scan of every checkout in a workspace — the stored projection and the portal's payload,
    which are the same record and so the same type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["changes"] = "changes"
    changes: tuple[WorkspaceChange, ...] = Field(max_length=WORKSPACE_CHANGES_MAX)
    truncated: bool


NOTHING_CHANGED = WorkspaceChanges(changes=(), truncated=False)


@dataclass(frozen=True)
class WorkspaceChangeRecorder:
    """The turn-end refresh: read the sandbox's checkouts, store what git reported.

    It runs once the turn's terminal state is durable and published, so nothing here is inside a
    client's wait, and it answers for the *sandbox's* conversation rather than the turn's — a
    subagent shares its parent's workspace, so a child's work refreshes the projection the parent's
    portal reads. A scan that fails leaves the last one standing: this is a projection of the
    workspace, and a stale answer to `what changed` beats no answer and beats failing a turn that
    already ended."""

    sandbox: SandboxSession
    workspace_id: UUID
    conversation_id: UUID

    async def record(self) -> None:
        try:
            await self._store(await self._scan())
        except Exception as error:
            log(
                "workspace.changes.scan_failed",
                conversation_id=str(self.conversation_id),
                error_class=type(error).__name__,
                error=str(error),
            )

    async def _scan(self) -> WorkspaceChanges:
        result = await self.sandbox.run_sbxfs("changes", {})
        try:
            return WorkspaceChanges.model_validate(result)
        except ValidationError as error:
            raise RuntimeError("sbxfs changes returned a malformed scan") from error

    async def _store(self, scanned: WorkspaceChanges) -> None:
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            statement = insert(tables.conversation_change).values(
                workspace_id=self.workspace_id,
                conversation_id=self.conversation_id,
                scan=scanned.model_dump(mode="json"),
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["workspace_id", "conversation_id"],
                    set_={"scan": statement.excluded.scan},
                )
            )


async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges:
    """The last scan recorded for the conversation that owns the sandbox — a subagent's own id
    resolves to its parent's, since one workspace has one answer. Nothing recorded yet reads as
    nothing changed: a conversation whose turns never touched a checkout has no scan and no
    changes, and the two are the same fact to a member."""
    async with workspace_tx() as connection:
        owner = (
            await connection.execute(
                sa.select(
                    sa.func.coalesce(
                        tables.conversation.c.sandbox_conversation_id, tables.conversation.c.id
                    )
                ).where(tables.conversation.c.id == conversation_id)
            )
        ).scalar_one_or_none()
        if owner is None:
            return NOTHING_CHANGED
        scan = (
            await connection.execute(
                sa.select(tables.conversation_change.c.scan).where(
                    tables.conversation_change.c.conversation_id == owner
                )
            )
        ).scalar_one_or_none()
    return NOTHING_CHANGED if scan is None else WorkspaceChanges.model_validate(scan)
