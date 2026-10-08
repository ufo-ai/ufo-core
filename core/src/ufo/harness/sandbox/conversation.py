"""Reaching a conversation's `/workspace`, on a turn and off it.

`/workspace` is the carrier's own storage and the only copy of a conversation's files, so everything
that touches them goes through the carrier: a turn's tools, a surface landing an inbound attachment
before the turn runs, a job appending to a change log, an operator's file browser. This module opens
the conversation's sandbox and is the one writer of the durable `<backend>:<id>` handle its row
carries, so a later process resumes the same sandbox instead of stranding it.

Egress is the opener's to grant: an open that lands on a carrier enforcing it — the member's
terminal, or a carrier whose sandbox runs off the cluster — asks the caller's opener for a proxy
session and carries its env and CA; an in-cluster carrier runs unenforced and asks nothing. An open
off a turn passes no opener, so a file op that needs no egress is given none. And a read never opens
a sandbox — a conversation whose row holds no handle has no workspace to browse, and answering a
read by creating one would make a GET a side effect."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ufo.runtime.access.proxy_sessions import SessionCreated
    from ufo.runtime.ext.manifest import CarrierSpec

from ufo.db import workspace_tx
from ufo.harness.document_renderer import DocumentRenderer
from ufo.harness.o11y import warn
from ufo.harness.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_HANDLE_SEP,
    SANDBOX_UID,
    WORKSPACE_DIR,
    Carrier,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    sandbox_handle_backend,
    sandbox_handle_id,
    workspace_path,
)
from ufo.harness.sandbox.terminal import (
    CLIENT_BACKEND,
    TerminalCarrier,
    Terminals,
    TerminalTransport,
)
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

SANDBOX_IMAGE_REF = "ufo-sandbox:latest"
WORKSPACE_WRITE_MAX_BYTES = 100 * 1024 * 1024
OPEN_CLAIM_ATTEMPTS = 3
WORKSPACE_ROOT_SETTING = "sandbox.workspace_root"
WORKSPACE_LISTING_EXCLUDE_NAMES = (".git",)

type SessionOpener = Callable[[], Awaitable[SessionCreated | None]]
"""Opens the proxy session an enforced open egresses under, or answers None where no proxy service
is configured. One open may ask it once per claim attempt, a terminal open before its terminal is
confirmed, so asking again answers the session it opened."""


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
    workspace_root: Path
    terminals: TerminalTransport = field(default_factory=Terminals)
    document_renderer: DocumentRenderer | None = None
    """The rendezvous a conversation's terminal is reached through — the in-process transport on a
    single node, a cross-pod transport (Redis) on a shared fleet. Whichever the deploy selects can
    serve a terminal, so a `client:` binding is always admissible: the transport, not the process,
    owns whether the held connection and the turn's workflow reach one terminal."""
    resume_carriers: Mapping[str, tuple[Carrier, "CarrierSpec"]] = field(default_factory=dict)
    """Backends kept live only for the stored handles bearing their scheme (`[sandbox]
    resume_backends`): a conversation whose workspace another provider still holds keeps opening
    there, while a fresh conversation always opens on the deploy's own carrier."""
    system_skill_archive: bytes = b""

    def _route(self, stored: str | None) -> tuple[Carrier, str, bool]:
        if stored is not None:
            resumed = self.resume_carriers.get(sandbox_handle_backend(stored))
            if resumed is not None:
                carrier, spec = resumed
                return carrier, spec.name, spec.off_cluster
        return self.carrier, self.backend, self.off_cluster

    async def open(
        self,
        conversation_id: UUID,
        turn_id: UUID | None,
        env: Mapping[str, str],
        proxied: SessionOpener | None = None,
    ) -> SandboxSession:
        """The conversation's sandbox, created or resumed, with its handle persisted.

        `env` is what every command of the open runs under. `proxied` opens the proxy session the
        sandbox egresses under, and is asked only when the open lands on a carrier that enforces
        egress — the member's terminal or an off-cluster carrier — whose spec then carries the
        session's env over `env` and its CA; an in-cluster carrier runs unenforced and never asks.

        `turn_id` is the turn this session serves, and None where no turn owns the open. It scopes
        the commands a carrier leaves running to the turn that launched them: one container serves
        every turn of the conversation, plus every subagent turn that inherited it, so the turn is
        the only thing that tells one caller's in-flight command from another's.

        The session carries the carrier that serves this conversation — the deploy's own, or the
        member's connected terminal — so which backend a conversation runs on is answered in one
        place rather than read off the process by every caller that needs to reach a workspace. A
        conversation binds on first open and the row records it: a fresh conversation whose member
        has a terminal connected binds to that terminal's directory, and a bound one refuses to run
        anywhere else — a terminal at a different directory, or none, is named to the member rather
        than answered from a workspace they cannot see.

        Nothing serializes concurrent opens of one brand-new conversation — an inbound attachment
        landing off-turn can race the turn's own open — so persistence is a compare-and-swap over
        the handle this open read, and the loser of a double-create adopts the winner: it reopens
        against the persisted id and returns the winner's sandbox, so both callers end on the one
        sandbox the row names and nothing is ever written into a sandbox no row references. The
        loser's extra sandbox is unreferenced and empty: docker arbitrates the name at the daemon so
        none exists there, and an e2b one idles into a paused, unbilled husk."""
        stored, size = await self._binding(conversation_id)
        for _ in range(OPEN_CLAIM_ATTEMPTS):
            backend, carrier, handle = await self._opened(
                conversation_id, turn_id, stored, env, size, proxied
            )
            persisted = f"{backend}{SANDBOX_HANDLE_SEP}{handle.container_id}"
            if persisted == stored:
                return SandboxSession(
                    carrier=carrier,
                    handle=handle,
                    system_skill_archive=self.system_skill_archive,
                )
            winner = await self._claim(conversation_id, stored, persisted)
            if winner == persisted:
                return SandboxSession(
                    carrier=carrier,
                    handle=handle,
                    system_skill_archive=self.system_skill_archive,
                )
            stored = winner
        raise RuntimeError(
            f"conversation {conversation_id}'s sandbox handle kept moving across "
            f"{OPEN_CLAIM_ATTEMPTS} open attempts"
        )

    async def existing(self, conversation_id: UUID) -> SandboxSession | None:
        """The conversation's sandbox when one is reachable, else None — the read path. A read
        never provisions: a conversation that never grew a sandbox, one whose stored handle another
        backend wrote, and one whose sandbox the carrier reclaimed or its provider lost all answer
        None rather than resurrecting anything, and the row is never written. A terminal-bound
        conversation is reachable exactly while its terminal is connected at the bound directory."""
        stored = await self._stored(conversation_id)
        if stored is None:
            return None
        bound_path = sandbox_handle_id(CLIENT_BACKEND, stored)
        if bound_path is not None:
            carrier = TerminalCarrier(
                terminals=self.terminals, document_renderer=self.document_renderer
            )
            handle = await carrier.attach(
                SandboxSpec(
                    conversation_id=conversation_id,
                    image_ref=self.image_ref,
                    workspace_host_path=bound_path,
                    resume_id=bound_path,
                )
            )
            return (
                None
                if handle is None
                else SandboxSession(
                    carrier=carrier,
                    handle=handle,
                    system_skill_archive=self.system_skill_archive,
                )
            )
        routed, backend, off_cluster = self._route(stored)
        resume_id = sandbox_handle_id(backend, stored)
        if resume_id is None:
            return None
        if off_cluster:
            host_path = (self.workspace_root / str(conversation_id)).resolve()
        else:
            existing = await asyncio.to_thread(self._existing_dir, conversation_id)
            if existing is None:
                return None
            host_path = existing
        handle = await routed.attach(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=self.image_ref,
                workspace_host_path=str(host_path),
                resume_id=resume_id,
            )
        )
        return (
            None
            if handle is None
            else SandboxSession(
                carrier=routed,
                handle=handle,
                system_skill_archive=self.system_skill_archive,
            )
        )

    async def bound(self, conversation_id: UUID) -> bool:
        """Whether the row records a sandbox for this conversation. The cheapest of the three
        questions a caller can ask about one: `open` provisions, `existing` asks the provider
        whether its container is still there, and this reads the handle alone — what a portal poll
        needs to say a conversation has a sandbox at all."""
        return await self._stored(conversation_id) is not None

    async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool:
        """Bind an unbound conversation to the terminal at `cwd`, reporting whether this call made
        the claim. Admission calls it while the member's connection is live, so a turn whose first
        open lands after that connection's hold still selects the terminal — the row, not the
        transient binding, is what `_opened` trusts. A conversation already bound anywhere keeps
        its binding: the compare-and-swap only fills an empty handle."""
        claimed = f"{CLIENT_BACKEND}{SANDBOX_HANDLE_SEP}{cwd}"
        stored = await self._stored(conversation_id)
        if stored is not None:
            return False
        return await self._claim(conversation_id, None, claimed) == claimed

    async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str:
        """Land `content` at `rel` inside the conversation's workspace and return the `/workspace`
        path the agent will read it at. Bounded, because the copy-in crosses whole: a producer with
        no limit of its own would otherwise size this process's memory."""
        if len(content) > WORKSPACE_WRITE_MAX_BYTES:
            raise ValueError(
                f"{rel} is {len(content)} bytes, over the {WORKSPACE_WRITE_MAX_BYTES}-byte limit "
                "for a workspace write"
            )
        session = await self.open(conversation_id, None, {})
        await session.write_file(rel, content)
        return workspace_path(rel)

    async def write_runtime(
        self, conversation_id: UUID, category: str, rel: str, content: bytes
    ) -> str:
        """Land bounded internal output below one named runtime category."""
        if len(content) > WORKSPACE_WRITE_MAX_BYTES:
            raise ValueError(
                f"{rel} is {len(content)} bytes, over the {WORKSPACE_WRITE_MAX_BYTES}-byte limit "
                "for a runtime write"
            )
        session = await self.open(conversation_id, None, {})
        relative = f"{category}/{rel}"
        await session.write_runtime_file(relative, content)
        return await session.runtime_display_path(relative)

    async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None:
        """Keep only the newest `keep` files directly under `rel_prefix`, deleting the rest —
        the bound on an off-turn writer that appends unattended. Runs in the container, so the
        newest are the newest the agent sees."""
        session = await self.existing(conversation_id)
        if session is None:
            return
        result = await session.python(PRUNE_PROG, workspace_path(rel_prefix), str(keep))
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot prune {rel_prefix}")

    async def prune_runtime(
        self, conversation_id: UUID, category: str, rel_prefix: str, keep: int
    ) -> None:
        """Keep only the newest files below one internal runtime directory."""
        session = await self.existing(conversation_id)
        if session is None:
            return
        target = await session.runtime_path(f"{category}/{rel_prefix}")
        result = await session.python(PRUNE_PROG, target, str(keep))
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot prune {rel_prefix}")

    async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]:
        """Member-visible files in the conversation's workspace, path-sorted. The in-container
        `ufo fs glob` walk excludes Git metadata before its result cap; filtering in a surface would
        let metadata consume the cap and hide files. Empty when the conversation has no sandbox yet.
        Paths come back absolute in the walker's own view — `/workspace/…` from a containerised
        carrier, the host directory from the local carrier's argv rewrite — so each is made relative
        against whichever of those two roots it carries."""
        session = await self.existing(conversation_id)
        if session is None:
            return ()
        listed = await session.run_ufo_fs(
            "glob",
            {
                "pattern": "**/*",
                "path": WORKSPACE_DIR,
                "exclude_names": list(WORKSPACE_LISTING_EXCLUDE_NAMES),
            },
        )
        files = listed["files"]
        if not isinstance(files, list):
            raise RuntimeError("ufo fs glob did not return a file list")
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
                        path=self._workspace_rel(session.handle, str(entry["path"])),
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
        session = await self.existing(conversation_id)
        if session is None:
            return None
        if not await session.file_exists(rel):
            return None
        return session.read_file(rel)

    async def _opened(
        self,
        conversation_id: UUID,
        turn_id: UUID | None,
        stored: str | None,
        env: Mapping[str, str],
        size: str,
        proxied: SessionOpener | None,
    ) -> tuple[str, Carrier, SandboxHandle]:
        """The terminal carrier's `create` waits out a reconnect and raises `TerminalGone` when no
        terminal is connected at the bound directory."""
        bound_path = None if stored is None else sandbox_handle_id(CLIENT_BACKEND, stored)
        if bound_path is None and stored is None:
            bound = self.terminals.workspace(conversation_id)
            bound_path = None if bound is None else bound.cwd
        if bound_path is not None:
            carrier = TerminalCarrier(
                terminals=self.terminals, document_renderer=self.document_renderer
            )
            session_env, proxy_ca = await self._proxied(env, proxied)
            handle = await carrier.create(
                SandboxSpec(
                    conversation_id=conversation_id,
                    image_ref=self.image_ref,
                    workspace_host_path=bound_path,
                    env=session_env,
                    proxy_ca=proxy_ca,
                    turn_id=turn_id,
                )
            )
            return CLIENT_BACKEND, carrier, handle
        routed, backend, off_cluster = self._route(stored)
        if off_cluster:
            host_path = (self.workspace_root / str(conversation_id)).resolve()
            session_env, proxy_ca = await self._proxied(env, proxied)
        else:
            host_path = await asyncio.to_thread(self._provisioned_dir, conversation_id)
            if os.geteuid() == 0:
                await asyncio.to_thread(os.chown, host_path, SANDBOX_UID, SANDBOX_GID)
            session_env, proxy_ca = env, ""
        handle = await routed.create(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=self.image_ref,
                workspace_host_path=str(host_path),
                resume_id=None if stored is None else sandbox_handle_id(backend, stored),
                env=session_env,
                proxy_ca=proxy_ca,
                size=size,
                turn_id=turn_id,
            )
        )
        return backend, routed, handle

    @staticmethod
    async def _proxied(
        env: Mapping[str, str], proxied: SessionOpener | None
    ) -> tuple[Mapping[str, str], str]:
        session = None if proxied is None else await proxied()
        if session is None:
            return env, ""
        return {**env, **session.env}, session.ca_pem

    def _provisioned_dir(self, conversation_id: UUID) -> Path:
        """A root linking to the conversations' volume is an ordinary compose or k8s layout, so it
        is resolved once."""
        with suppress(FileExistsError):
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        root = self.workspace_root.resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"{WORKSPACE_ROOT_SETTING} names {self.workspace_root}, which is not a directory"
            )
        path = root / str(conversation_id)
        path.mkdir(exist_ok=True)
        return path

    def _existing_dir(self, conversation_id: UUID) -> Path | None:
        """The same directory on the read path, or None when nothing has made it — a read never
        provisions."""
        path = self.workspace_root.resolve() / str(conversation_id)
        return path if path.is_dir() else None

    async def _stored(self, conversation_id: UUID) -> str | None:
        handle, _ = await self._binding(conversation_id)
        return handle

    async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.sandbox_handle, tables.agent.c.sandbox_size)
                    .select_from(
                        tables.conversation.join(
                            tables.agent, tables.conversation.c.agent_id == tables.agent.c.id
                        )
                    )
                    .where(
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.workspace_id == ws_current().workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise ValueError(f"conversation {conversation_id} is not in this workspace")
        return row.sandbox_handle, row.sandbox_size

    async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str:
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
import sys
from pathlib import Path

base = Path(sys.argv[1])
keep = int(sys.argv[2])
if base.is_dir():
    names = sorted(entry.name for entry in base.iterdir() if entry.is_file())
    for name in names[: max(len(names) - keep, 0)]:
        (base / name).unlink(missing_ok=True)
"""
