"""Reaching a conversation's `/workspace`, on a turn and off it.

`/workspace` is the carrier's own storage and the only copy of a conversation's files, so everything
that touches them goes through the carrier: a turn's tools, a surface landing an inbound attachment
before the turn runs, a job appending to a change log, an operator's file browser. This module opens
the conversation's sandbox and is the one writer of the durable `<backend>:<id>` handle its row
carries, so a later process resumes the same sandbox instead of stranding it.

Off a turn there is no run token to carry, so the sandbox opens under a name that is not one: it
bears no signature this deployment made, the proxy refuses every CONNECT from it before rules are
even resolved, and a file op that needs no egress is given none. And a read never opens a sandbox —
a conversation whose row holds no handle has no workspace to browse, and answering a read by
creating one would make a GET a side effect."""

import asyncio
import os
import shlex
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.o11y import warn
from ufo.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_HANDLE_SEP,
    SANDBOX_UID,
    WORKSPACE_DIR,
    Carrier,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    sandbox_handle_id,
    workspace_path,
)
from ufo.schema import tables
from ufo.workspace import ws_current

SANDBOX_IMAGE_REF = "ufo-sandbox:latest"
UNSIGNED_RUN_TOKEN = "off-turn"
WORKSPACE_WRITE_MAX_BYTES = 100 * 1024 * 1024
OPEN_CLAIM_ATTEMPTS = 3


class WorkspaceFile(BaseModel):
    """One file in a conversation's `/workspace`, as the file browser lists it — the path
    relative to the workspace root, never an absolute container path."""

    model_config = ConfigDict(frozen=True)
    path: str
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True)
class ConversationSandbox:
    """Create-or-attach the conversation's sandbox, and reach its workspace off a turn.

    `workspace_root` holds one directory per conversation for an in-cluster carrier to serve
    `/workspace` from; an `off_cluster` carrier serves its own sandbox disk instead, so no host
    directory is made for it. The directory is created under serve's own uid, and when serve runs
    as root (the bundle default) it holds CAP_CHOWN and hands the directory to the sandbox user,
    without which the container's file tools could not write. Off root — dev, where the container
    is not uid-enforced — the chown is skipped."""

    carrier: Carrier
    backend: str
    off_cluster: bool
    image_ref: str
    proxy: ProxyEndpoint
    workspace_root: Path

    async def open(
        self, conversation_id: UUID, run_token: str, env: Mapping[str, str]
    ) -> SandboxHandle:
        """The conversation's sandbox, created or resumed, with its handle persisted.

        Nothing serializes concurrent opens of one brand-new conversation — an inbound attachment
        landing off-turn can race the turn's own open — so persistence is a compare-and-swap over
        the handle this open read, and the loser of a double-create adopts the winner: it reopens
        against the persisted id and returns the winner's sandbox, so both callers end on the one
        sandbox the row names and nothing is ever written into a sandbox no row references. The
        loser's extra sandbox is unreferenced and empty: docker arbitrates the name at the daemon so
        none exists there, and an e2b one idles into a paused, unbilled husk."""
        stored = await self._stored(conversation_id)
        for _ in range(OPEN_CLAIM_ATTEMPTS):
            handle = await self._opened(conversation_id, stored, run_token, env)
            persisted = f"{self.backend}{SANDBOX_HANDLE_SEP}{handle.container_id}"
            if persisted == stored:
                return handle
            winner = await self._claim(conversation_id, stored, persisted)
            if winner == persisted:
                return handle
            stored = winner
        raise RuntimeError(
            f"conversation {conversation_id}'s sandbox handle kept moving across "
            f"{OPEN_CLAIM_ATTEMPTS} open attempts"
        )

    async def existing(self, conversation_id: UUID) -> SandboxHandle | None:
        """The conversation's sandbox when one is reachable, else None — the read path. A read
        never provisions: a conversation that never grew a sandbox, one whose stored handle another
        backend wrote, and one whose sandbox the carrier reclaimed or its provider lost all answer
        None rather than resurrecting anything, and the row is never written."""
        stored = await self._stored(conversation_id)
        if stored is None:
            return None
        resume_id = sandbox_handle_id(self.backend, stored)
        if resume_id is None:
            return None
        return await self.carrier.attach(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=self.image_ref,
                workspace_host_path=str((self.workspace_root / str(conversation_id)).resolve()),
                proxy=self.proxy,
                run_token=UNSIGNED_RUN_TOKEN,
                resume_id=resume_id,
            )
        )

    async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str:
        """Land `content` at `rel` inside the conversation's workspace and return the `/workspace`
        path the agent will read it at. Bounded, because the copy-in crosses whole: a producer with
        no limit of its own would otherwise size this process's memory."""
        if len(content) > WORKSPACE_WRITE_MAX_BYTES:
            raise ValueError(
                f"{rel} is {len(content)} bytes, over the {WORKSPACE_WRITE_MAX_BYTES}-byte limit "
                "for a workspace write"
            )
        target = workspace_path(rel)
        handle = await self.open(conversation_id, UNSIGNED_RUN_TOKEN, {})
        await self.carrier.write(handle, target, content)
        return target

    async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None:
        """Keep only the newest `keep` files directly under `rel_prefix`, deleting the rest —
        the bound on an off-turn writer that appends unattended. Runs in the container, so the
        newest are the newest the agent sees."""
        handle = await self.existing(conversation_id)
        if handle is None:
            return
        session = SandboxSession(carrier=self.carrier, handle=handle)
        result = await session.bash(
            f"python3 -c {shlex.quote(PRUNE_PROG)} {shlex.quote(workspace_path(rel_prefix))} {keep}"
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot prune {rel_prefix}")

    async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]:
        """Every file in the conversation's workspace, path-sorted — the in-container `sbxfs glob`
        walk over `**/*`, so the browser lists exactly what the sandbox holds, bounded by glob's own
        result cap. Empty when the conversation has no sandbox yet. Paths come back absolute in the
        walker's own view — `/workspace/…` from a containerised carrier, the host directory from the
        local carrier's argv rewrite — so each is made relative against whichever of those two roots
        it carries."""
        handle = await self.existing(conversation_id)
        if handle is None:
            return ()
        session = SandboxSession(carrier=self.carrier, handle=handle)
        listed = await session.run_sbxfs("glob", {"pattern": "**/*", "path": WORKSPACE_DIR})
        files = listed["files"]
        if not isinstance(files, list):
            raise RuntimeError("sbxfs glob did not return a file list")
        if listed.get("truncated"):
            warn(
                "workspace.listing_truncated",
                conversation_id=str(conversation_id),
                listed=len(files),
            )
        return tuple(
            sorted(
                (
                    WorkspaceFile(
                        path=self._workspace_rel(handle, str(entry["path"])),
                        size_bytes=int(entry["size"]),
                        modified_at=datetime.fromtimestamp(float(entry["modified"]), tz=UTC),
                    )
                    for entry in files
                ),
                key=lambda entry: entry.path,
            )
        )

    def _workspace_rel(self, handle: SandboxHandle, path: str) -> str:
        for root in (WORKSPACE_DIR, handle.workspace_host_path):
            if root and path.startswith(f"{root}/"):
                return path.removeprefix(f"{root}/")
        raise RuntimeError(f"workspace walk returned a path outside the workspace: {path!r}")

    async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None:
        """One workspace file's bytes in bounded chunks, or None when the conversation has no
        sandbox or the path holds no file."""
        handle = await self.existing(conversation_id)
        if handle is None:
            return None
        session = SandboxSession(carrier=self.carrier, handle=handle)
        if not await session.file_exists(rel):
            return None
        return session.read_file(rel)

    async def _opened(
        self,
        conversation_id: UUID,
        stored: str | None,
        run_token: str,
        env: Mapping[str, str],
    ) -> SandboxHandle:
        host_path = self.workspace_root / str(conversation_id)
        if not self.off_cluster:
            await asyncio.to_thread(host_path.mkdir, parents=True, exist_ok=True)
            if os.geteuid() == 0:
                await asyncio.to_thread(os.chown, host_path, SANDBOX_UID, SANDBOX_GID)
        return await self.carrier.create(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=self.image_ref,
                workspace_host_path=str(host_path.resolve()),
                proxy=self.proxy,
                run_token=run_token,
                resume_id=None if stored is None else sandbox_handle_id(self.backend, stored),
                env=env,
            )
        )

    async def _stored(self, conversation_id: UUID) -> str | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.sandbox_handle).where(
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.workspace_id == ws_current().workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise ValueError(f"conversation {conversation_id} is not in this workspace")
        handle: str | None = row.sandbox_handle
        return handle

    async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str:
        """Persist `handle` only over the value this open read, and return whatever the row holds
        after — `handle` when the swap won, the concurrent winner's when it lost. The condition is
        the whole race arbiter: two opens that both read the same prior value cannot both win."""
        async with workspace_tx() as connection:
            swapped = await connection.execute(
                sa.update(tables.conversation)
                .values(sandbox_handle=handle)
                .where(
                    tables.conversation.c.id == conversation_id,
                    tables.conversation.c.workspace_id == ws_current().workspace_id,
                    tables.conversation.c.sandbox_handle.is_(None)
                    if stored is None
                    else tables.conversation.c.sandbox_handle == stored,
                )
            )
            if swapped.rowcount:
                return handle
        winner = await self._stored(conversation_id)
        if winner is None:
            raise RuntimeError(f"conversation {conversation_id} lost its sandbox handle mid-open")
        return winner


PRUNE_PROG = """
import os, sys
directory, keep = sys.argv[1], int(sys.argv[2])
try:
    names = sorted(
        entry for entry in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, entry))
    )
except FileNotFoundError:
    sys.exit(0)
for name in names[: max(len(names) - keep, 0)]:
    os.unlink(os.path.join(directory, name))
"""
