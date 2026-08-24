"""What a conversation changed in its workspace, as git sees it.

A change belongs to the workspace, not to a message stream: git reports a deletion, a rename, and
what a shell command did inside a touched checkout — none of which replaying tool results carries.
Which checkouts to ask is the recorder's `targets`: the paths the turn's `write` and `edit` calls
named and the workspace root itself when the turn ran `bash`, mined from each round's tool calls
as they dispatch — the memoized round outputs a recovered turn replays, and a record no mid-turn
compaction of the message window rewrites — plus every directory the last recorded scan reported,
so a checkout stays watched until git says it is clean. A deploy carrier walks its own disk and
answers for every checkout regardless; a terminal-bound workspace is the member's real directory,
arbitrarily large, and is asked only where the targets point.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.db import workspace_tx
from ufo.models.interface import ToolUseBlock
from ufo.o11y import log
from ufo.sandbox.session import WORKSPACE_DIR, Sandbox, workspace_path
from ufo.schema import tables
from ufo.tools.file_changes import FILE_CHANGE_PATH_MAX_CHARS

WORKSPACE_CHANGE_PATCH_MAX_CHARS = 25_000
WORKSPACE_CHANGES_MAX = 100
WORKSPACE_CHANGE_TARGET_DIRS_MAX = 256


def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]:
    """The workspace-relative paths these tool calls may have changed, deduped in call order: the
    files `write` and `edit` named, and the workspace root itself for a `bash` call, whose command
    can change the outermost checkout without naming any path. A path that does not parse under
    the workspace root named nothing and is skipped."""
    found: dict[str, None] = {}
    for call in calls:
        match call:
            case ToolUseBlock(name="write" | "edit", input={"file_path": str() as path}):
                try:
                    scoped = workspace_path(path)
                except ValueError:
                    continue
                found[str(PurePosixPath(scoped).relative_to(WORKSPACE_DIR))] = None
            case ToolUseBlock(name="bash"):
                found["."] = None
            case _:
                pass
    return tuple(found)


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
    """The turn-end refresh: ask git about the checkouts the conversation's file tools touched,
    store what it reported.

    It runs once the turn's terminal state is durable and published, so nothing here is inside a
    client's wait, and it answers for the *sandbox's* conversation rather than the turn's — a
    subagent shares its parent's workspace, so a child's work refreshes the projection the parent's
    portal reads. The scan is asked over the targets' directories plus every directory the last
    scan reported, so a checkout stays watched until git reports it clean. Two turns sharing one
    sandbox can end together, so the store merges under the row's own lock: entries in checkouts
    this scan never asked about are a concurrent recorder's work and survive; in the single-writer
    case every stored entry's directory was asked and the merge is the plain replace. A scan that
    fails leaves the last one standing: this is a projection of the workspace, and a stale answer
    to `what changed` beats no answer and beats failing a turn that already ended."""

    sandbox: Sandbox
    workspace_id: UUID
    conversation_id: UUID
    targets: tuple[str, ...]

    async def record(self) -> None:
        if not self.sandbox.created and not self.targets:
            return
        try:
            recorded = await recorded_workspace_changes(self.conversation_id)
            asked = self._directories(recorded)
            await self._store(await self._scan(asked), frozenset(asked))
        except Exception as error:
            log(
                "workspace.changes.scan_failed",
                conversation_id=str(self.conversation_id),
                error_class=type(error).__name__,
                error=str(error),
            )

    def _directories(self, recorded: WorkspaceChanges) -> list[str]:
        watched = {
            str(PurePosixPath(path).parent)
            for path in (*self.targets, *(change.path for change in recorded.changes))
        }
        directories = sorted(watched)
        if len(directories) > WORKSPACE_CHANGE_TARGET_DIRS_MAX:
            log(
                "workspace.changes.targets_capped",
                conversation_id=str(self.conversation_id),
                dropped=len(directories) - WORKSPACE_CHANGE_TARGET_DIRS_MAX,
            )
            directories = directories[:WORKSPACE_CHANGE_TARGET_DIRS_MAX]
        return directories

    async def _scan(self, directories: list[str]) -> WorkspaceChanges:
        result = await self.sandbox.run_ufo_fs("changes", {"paths": directories})
        try:
            return WorkspaceChanges.model_validate(result)
        except ValidationError as error:
            raise RuntimeError("ufo fs changes returned a malformed scan") from error

    async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None:
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation_change)
                .values(
                    workspace_id=self.workspace_id,
                    conversation_id=self.conversation_id,
                    scan=scanned.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "conversation_id"])
            )
            row = tables.conversation_change
            owned = sa.and_(
                row.c.workspace_id == self.workspace_id,
                row.c.conversation_id == self.conversation_id,
            )
            current = (
                await connection.execute(sa.select(row.c.scan).where(owned).with_for_update())
            ).scalar_one()
            merged = self._merged(scanned, WorkspaceChanges.model_validate(current), asked)
            await connection.execute(
                sa.update(row).where(owned).values(scan=merged.model_dump(mode="json"))
            )

    def _merged(
        self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]
    ) -> WorkspaceChanges:
        fresh = {change.path for change in scanned.changes}
        kept = tuple(
            change
            for change in stored.changes
            if str(PurePosixPath(change.path).parent) not in asked and change.path not in fresh
        )
        changes = (*scanned.changes, *kept)
        return WorkspaceChanges(
            changes=changes[:WORKSPACE_CHANGES_MAX],
            truncated=scanned.truncated
            or (stored.truncated and bool(kept))
            or len(changes) > WORKSPACE_CHANGES_MAX,
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
