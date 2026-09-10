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
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ufo.runtime.ext.manifest import CarrierSpec

from ufo.db import workspace_tx
from ufo.harness.containment import PathNotFound, configured_root, contained_dir
from ufo.harness.document_renderer import DocumentRenderer
from ufo.harness.o11y import warn
from ufo.harness.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_HANDLE_SEP,
    SANDBOX_UID,
    WORKSPACE_DIR,
    Carrier,
    ProxyEndpoint,
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
UNSIGNED_RUN_TOKEN = "off-turn"
WORKSPACE_WRITE_MAX_BYTES = 100 * 1024 * 1024
OPEN_CLAIM_ATTEMPTS = 3
WORKSPACE_ROOT_SETTING = "sandbox.workspace_root"
WORKSPACE_LISTING_EXCLUDE_NAMES = (".git",)


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
        """The carrier a stored handle's backend scheme selects: a resume backend's for the handles
        bearing its scheme — the conversation's workspace lives on the provider that wrote the
        handle, so the open follows it there — and the deploy's own for its handles, for a fresh
        conversation, and for a scheme no configured backend answers to (that workspace is
        unreachable, so the open falls forward and overwrites the handle)."""
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
        run_token: str,
        env: Mapping[str, str],
    ) -> SandboxSession:
        """The conversation's sandbox, created or resumed, with its handle persisted.

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
                conversation_id, turn_id, stored, run_token, env, size
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
                    proxy=self.proxy,
                    run_token=UNSIGNED_RUN_TOKEN,
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
                proxy=self.proxy,
                run_token=UNSIGNED_RUN_TOKEN,
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
        session = await self.open(conversation_id, None, UNSIGNED_RUN_TOKEN, {})
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
        session = await self.open(conversation_id, None, UNSIGNED_RUN_TOKEN, {})
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
        result = await session.python(
            PRUNE_PROG, workspace_path(rel_prefix), WORKSPACE_DIR, str(keep)
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot prune {rel_prefix}")

    async def prune_runtime(
        self, conversation_id: UUID, category: str, rel_prefix: str, keep: int
    ) -> None:
        """Keep only the newest files below one internal runtime directory."""
        session = await self.existing(conversation_id)
        if session is None:
            return
        relative = f"{category}/{rel_prefix}"
        target = await session.runtime_path(relative)
        root = await session.runtime_path(category)
        result = await session.python(PRUNE_PROG, target, root, str(keep))
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
        run_token: str,
        env: Mapping[str, str],
        size: str,
    ) -> tuple[str, Carrier, SandboxHandle]:
        """The carrier this open runs on and the handle it opened — the one place the choice is
        made. A stored `client:` handle stays a terminal conversation forever, connected or not; a
        fresh conversation binds to a connected terminal when one holds it; everything else is the
        deploy's carrier, and a stored handle from the deploy's own backend keeps it even while a
        terminal is connected — that conversation's workspace already lives elsewhere.

        The transport, not the process, owns whether a `client:` handle can be served: the terminal
        carrier's `create` waits out the connection's reconnect and raises `TerminalGone` when the
        terminal is not connected at the bound directory, so a stored handle whose terminal is
        absent is named to the member rather than opened as a directory under `workspace_root`."""
        bound_path = None if stored is None else sandbox_handle_id(CLIENT_BACKEND, stored)
        if bound_path is None and stored is None:
            bound = self.terminals.workspace(conversation_id)
            bound_path = None if bound is None else bound.cwd
        if bound_path is not None:
            carrier = TerminalCarrier(
                terminals=self.terminals, document_renderer=self.document_renderer
            )
            handle = await carrier.create(
                SandboxSpec(
                    conversation_id=conversation_id,
                    image_ref=self.image_ref,
                    workspace_host_path=bound_path,
                    proxy=self.proxy,
                    run_token=run_token,
                    env=env,
                    turn_id=turn_id,
                )
            )
            return CLIENT_BACKEND, carrier, handle
        routed, backend, off_cluster = self._route(stored)
        if off_cluster:
            host_path = (self.workspace_root / str(conversation_id)).resolve()
        else:
            host_path = await asyncio.to_thread(self._provisioned_dir, conversation_id)
            if os.geteuid() == 0:
                await asyncio.to_thread(os.chown, host_path, SANDBOX_UID, SANDBOX_GID)
        handle = await routed.create(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=self.image_ref,
                workspace_host_path=str(host_path),
                proxy=self.proxy,
                run_token=run_token,
                resume_id=None if stored is None else sandbox_handle_id(backend, stored),
                env=env,
                size=size,
                turn_id=turn_id,
            )
        )
        return backend, routed, handle

    def _provisioned_dir(self, conversation_id: UUID) -> Path:
        """The conversation's own directory under `workspace_root`, made if absent and then proved:
        every component under the root is re-opened `O_NOFOLLOW`. A containment check cannot stand
        in for that — with a symlinked directory planted under the root the `mkdir` follows the
        link, the chown hands the link's target to the sandbox user, and every path the carrier
        builds still resolves inside the root the check consulted.

        The root itself is deploy config, so the one link an operator may have put there — the root
        pointing at the volume the conversations live on — is followed once and canonicalized before
        the descent, rather than refused into an outage of every turn and every browse. Anything
        else already holding the name — a file, a broken link — is that check's to refuse with the
        setting named, not `mkdir`'s to report as a bare errno."""
        with suppress(FileExistsError):
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        root = configured_root(self.workspace_root, WORKSPACE_ROOT_SETTING)
        return contained_dir(root / str(conversation_id), root, create=True)

    def _existing_dir(self, conversation_id: UUID) -> Path | None:
        """The same directory on the read path, or None when nothing has made it — a read never
        provisions. Absent is a conversation with no workspace; a link pointing out of the root is a
        workspace pointed somewhere it may not go, and raises. A link to another directory *under*
        the root resolves to it, which is the guard's policy on an ancestor everywhere: the
        canonical directory is what the descent then pins. Nothing an agent reaches writes here —
        a sandbox is mounted at its own conversation directory, never at the root above it."""
        try:
            root = configured_root(self.workspace_root, WORKSPACE_ROOT_SETTING)
            return contained_dir(root / str(conversation_id), root)
        except PathNotFound:
            return None

    async def _stored(self, conversation_id: UUID) -> str | None:
        handle, _ = await self._binding(conversation_id)
        return handle

    async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]:
        """The conversation's stored sandbox handle and its owning agent's sandbox size — one row
        read for every open, so whichever caller creates first (a turn, an off-turn attachment
        write) creates at the size the agent names."""
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
import sys
from containment import (
    ContainmentError,
    PathNotFound,
    contained_dir,
    contained_file,
    contained_root,
    is_contained_regular,
)


def prune(directory, root, keep):
    try:
        workspace = contained_root(root)
        base = contained_dir(directory, workspace)
    except PathNotFound:
        return
    names = sorted(
        entry.name for entry in base.iterdir() if is_contained_regular(entry, workspace)
    )
    for name in names[: max(len(names) - keep, 0)]:
        with contained_file(base / name, workspace) as target:
            target.unlink()


try:
    prune(sys.argv[1], sys.argv[2], int(sys.argv[3]))
except ContainmentError as error:
    raise SystemExit(str(error))
"""
