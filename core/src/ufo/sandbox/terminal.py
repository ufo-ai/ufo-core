"""The rendezvous with a member's connected terminal.

A sandbox the deploy owns is reached by dialing it. A sandbox that IS the member's terminal can only
be reached by asking: an op travels down the surface's held stream as a directive, and its answer
arrives as the client's next request. The turn awaits here; the surface's routes feed both halves.

Unlike the live-frame hub this is never lossy — the hub drops frames on a full subscriber because a
dropped `txt` costs a redraw, while a dropped op result costs a wedged turn. And unlike a stream, an
op's state is keyed by conversation rather than by connection: the client's stream dies at every
hold, so reconnect-mid-op is the normal case, and state scoped to one connection would strand the
waiting turn on the connection that ended. Ops are serial — the engine awaits one tool call at a
time — so a conversation's whole state is one slot.

The two halves run on different event loops — DBOS runs a turn's workflow on its own loop thread
while the surface holds the member's connection on serve's — so, exactly as the hub does, a lock
guards the shared state and every wakeup hops onto the waiter's own loop."""

import asyncio
import base64
import json
import shlex
import threading
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from ufo.media.document_renderer import (
    DOCUMENT_INPUT_MAX_BYTES,
    DOCUMENT_PAGES_MAX,
    DocumentRenderer,
)
from ufo.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    EGRESS_CA_CERT_ENV,
    NO_PROXY_HOSTS,
    PROXY_PASSWORD,
    RUNTIME_DIRNAME,
    UFO_HOME_ENV,
    WORKSPACE_DIR,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
    host_argv,
)

CLIENT_BACKEND = "client"
_PATH_PARAMS = frozenset({"path", "workspace", "staged_path"})
"""The `ufo fs` op params that name a workspace path and so map onto the bound directory; a
`pattern`, a `glob`, or an edit's text is a value, not a path, and is never rewritten."""
OP_EXEC = "exec"
OP_WRITE = "write"
OP_READ = "read"
OP_FILE = "fileop"
OP_SKILLS = "skills"
OP_NOT_FOUND_PREFIX = "ENOENT"
EXEC_TIMEOUT_CODE = 124
"""What an exec the op's own deadline stopped exits with, whatever the client's signal made of the
command — the code every other carrier reports for its own deadline, so a caller reading the code
sees one shape across all of them."""
OP_DEADLINE_SLACK_SECONDS = 30.0
"""Added to an op's own timeout: the margin for the round trips around the op, not for the op."""
WALK_SKIP_NAMES = (
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
)
_SKIPPED = " -o ".join(f"-name '{name}'" for name in WALK_SKIP_NAMES)
_UNSKIPPED_ROOT = r'! -path "$UFO_WALK_ROOT"'
_REPOSITORY_COMMANDS = r"""[ -n "$1" ] || exit 0
r=${1%/.git}
printf "R\000%s\000" "$r"
if [ -z "$(/usr/bin/git -C "$r" status --porcelain=v1 --no-renames -uall 2>/dev/null \
| /usr/bin/head -c1)" ]; then
printf "D\000\000"
exit 0
fi
/usr/bin/git -C "$r" -c core.quotePath=false status --porcelain=v1 -z --no-renames -uall
printf "D\000%s\000" "$(/usr/bin/git -C "$r" -c core.quotePath=false diff HEAD --no-renames -U3 \
2>/dev/null)"
"""
WALK_ENUMERATION = {
    "grep": rf"""case "$UFO_WALK_ROOT" in
'$UFO_HOME/'*) UFO_WALK_ROOT="$UFO_HOME/${{UFO_WALK_ROOT#\$UFO_HOME/}}" ;;
esac
/usr/bin/find "$UFO_WALK_ROOT" \( {_UNSKIPPED_ROOT} -a \( {_SKIPPED} \) \) -prune \
-o \( -type d -o -type f \) -print0 > "$UFO_OP_WORKDIR/grep-enum"
""",
    "glob": r"""case "$UFO_WALK_ROOT" in
'$UFO_HOME/'*) UFO_WALK_ROOT="$UFO_HOME/${UFO_WALK_ROOT#\$UFO_HOME/}" ;;
esac
measure() {
if /usr/bin/stat --version > /dev/null 2>&1; then
/usr/bin/xargs -0 -r /usr/bin/stat -c '%s %.9Y'
else
/usr/bin/xargs -0 /usr/bin/stat -f '%z %.9Fm'
fi
}
walked="$UFO_OP_WORKDIR/glob-walk"
/usr/bin/find "$UFO_WALK_ROOT" -type f -print0 > "$walked"
{ /bin/cat "$walked"; printf '\000'; measure < "$walked"; } > "$UFO_OP_WORKDIR/glob-enum"
""",
}
"""The one command each tree walk needs run first, reading `$UFO_WALK_ROOT` and leaving its
listing in the client's op workdir, where the walk's `enum` param names it. The walks read the
listing rather than the tree because the client must not re-decide what a walk visits — `find`
meets entries in the `readdir` order `os.walk` does, which is what keeps a truncated result
identical to the one `ufo fs` builds for itself. `grep` prunes `WALK_SKIP_NAMES` because the walk
it mirrors does; `glob` mirrors `Path.glob`, which prunes nothing, so pruning here would hide files
the container's own glob returns. Only glob measures with `stat` — over the listing it just wrote,
never a second walk, because the client pairs the two sections by position — and its flags split by
OS: `stat --version` succeeds on GNU/uutils (Linux), which take `-c '%s %.9Y'`, and fails on BSD
(macOS), which takes `-f '%z %.9Fm'` — both print `<size> <sec>.<9-digit-nanos>`, so the client
parses one shape and the double it reconstructs is the one `os.stat` reports on either. `find`,
`git`, `cat`, `head -c`, and `xargs` behave alike across both."""

CHANGES_ENUMERATION = rf"""r='{_REPOSITORY_COMMANDS}'
resolve() {{
case "$1" in .) d="$UFO_WALK_ROOT";; *) d="$UFO_WALK_ROOT/$1";; esac
found=
while :; do
if [ -e "$d/.git" ]; then found="$d"; fi
if [ "$d" = "$UFO_WALK_ROOT" ]; then break; fi
d="${{d%/*}}"
case "$d" in "$UFO_WALK_ROOT"|"$UFO_WALK_ROOT"/*) ;; *) break;; esac
done
if [ -n "$found" ]; then printf '%s/.git\000' "$found"; fi
}}
for target in "$@"; do resolve "$target"; done | /usr/bin/sort -z -u \
| /usr/bin/xargs -0 -n1 /bin/sh -c "$r" sh > "$UFO_OP_WORKDIR/changes-enum"
"""
"""The `changes` scan's enumeration: argv carries the workspace-relative directories the
conversation's file tools touched (`.` names the root itself) and each resolves *upward* to its
outermost enclosing checkout — never a walk down the tree, whose cost on a bound directory is the
member's whole disk rather than the agent's work. Ascending to the root keeps the `changes` op's own
`_repositories` rule (a repository nested inside another is what the checkout above already
reports), a target that lost its directory still resolves through the ancestors that remain, and
one under no checkout emits nothing. Roots dedupe through `sort -z -u` — GNU, BSD, and uutils
alike — since many targets share one checkout, and each root then answers
`_REPOSITORY_COMMANDS`: one cheap status probe, a diff only when it reports something."""
ARRIVAL_GRACE_SECONDS = 30.0
"""How long an op or an open waits for the terminal to reconnect. The client's stream ends at every
hold and reconnects on a ~1s poll, so work landing in that gap is the normal case — a different
quantity from the deadline slack above, coinciding at 30s by choice, not by identity."""
READ_REPLY_CHUNK_BYTES = 1024 * 1024


class TerminalGone(RuntimeError):
    """No terminal is bound to this conversation, or the bound one stopped answering."""


class TerminalAbsent(TerminalGone):
    """No terminal reconnected before the arrival grace ended."""


class TerminalOpFailed(RuntimeError):
    """The terminal answered that the op itself failed — a missing file, a refused rename. Carried
    apart from the reply body because a read's body is the file, so a failure cannot ride it."""


@dataclass(frozen=True, slots=True)
class TerminalOp:
    """One request to a connected terminal. `kind` picks the client's primitive arm; `name` picks
    the file op within it, which the client implements natively; `params` is that op's JSON; `arg`
    is a copy primitive's target path. The server names work and carries values — never code. The
    id is what the answer comes back under."""

    op_id: str
    kind: str
    timeout_s: int
    name: str = ""
    arg: str = ""
    params: str = ""


_Waiter = tuple["asyncio.Future[object]", asyncio.AbstractEventLoop]


@dataclass
class _Slot:
    """One conversation's live terminal state: where it stands, the op in flight, and what that op
    sends down. `body` holds a copy-in's bytes — they never ride the directive line — served by
    the op's own read projection. `reply` is the
    in-flight sender's waiter and `watcher` the stream's, each remembered with its own loop so the
    other side's thread can wake it. `queue` holds senders waiting their turn — a background
    subagent's op behind the parent's, an off-turn attachment write behind a running turn — so a
    terminal that runs one op at a time serializes them rather than failing the second. Mutated only
    under the rendezvous lock."""

    cwd: str
    member_id: UUID | None
    runtime_id: str
    connections: int = 0
    busy: bool = False
    op: TerminalOp | None = None
    delivered: bool = False
    resolved: bool = False
    body: bytes | None = None
    reply: _Waiter | None = None
    watcher: _Waiter | None = None
    queue: deque[_Waiter] = field(default_factory=deque)


@dataclass(frozen=True, slots=True)
class TerminalWorkspace:
    """What the selection rule reads about a bound terminal: whose it is and where it stands.
    `member_id` gates who may answer an op or fetch its staged bytes."""

    cwd: str
    member_id: UUID | None
    runtime_id: str


class TerminalTransport(Protocol):
    """The rendezvous the surface and the terminal carrier reach a member's connected terminal
    through, so which topology serves the terminal is one selected object, not a branch at every
    call. Core ships the in-process `Terminals`; the `redis_hub` extension ships a Redis-Streams
    transport for the shared fleet, where the held connection and the turn's workflow can land on
    different pods.

    The two-loop split is intrinsic: `send` runs on the turn's DBOS workflow loop while `next_op`,
    `connect`, `disconnect`, `resolve`, and `staged` run on serve's, so every implementation wakes a
    waiter on the waiter's own loop. `connect`/`disconnect`/`resolve` return without awaiting
    because they are called from surface routes that must not block on the transport; a
    cross-process backend does its own I/O off the running loop and the sender's `send` deadline is
    the wait that always ends.

    `member_id` on `resolve`/`staged` gates who may answer an op or read its staged bytes; `None`
    leaves it ungated (the direct-drive tests), and the surface always passes the connected
    member."""

    def connect(
        self,
        conversation_id: UUID,
        cwd: str,
        member_id: UUID | None,
        runtime_id: str | None = None,
    ) -> None: ...

    def disconnect(self, conversation_id: UUID) -> None: ...

    def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None: ...

    async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None: ...

    async def send(
        self,
        conversation_id: UUID,
        kind: str,
        timeout_s: int,
        name: str = "",
        arg: str = "",
        params: str = "",
        body: bytes | None = None,
    ) -> bytes: ...

    async def next_op(
        self, conversation_id: UUID, exclude_op_id: str | None = None
    ) -> TerminalOp: ...

    async def staged(
        self, conversation_id: UUID, op_id: str, member_id: UUID | None = None
    ) -> bytes | None: ...

    def resolve(
        self,
        conversation_id: UUID,
        op_id: str,
        reply: bytes,
        failed: str | None = None,
        member_id: UUID | None = None,
    ) -> bool: ...

    def in_flight(self, conversation_id: UUID) -> TerminalOp | None: ...


def _wake(waiter: _Waiter, answer: object) -> None:
    """Resolve a future from whichever thread holds the lock, on the future's own loop — the
    done-check must run there, since a future is not thread-safe even to read."""
    future, loop = waiter

    def _set() -> None:
        if not future.done():
            future.set_result(answer)

    loop.call_soon_threadsafe(_set)


@dataclass(frozen=True)
class Terminals:
    """Where the surface publishes the terminal its connection holds and the sandbox seam asks.

    Correct exactly while one process serves both the member's connection and their turn — the
    condition the in-process hub already carries, and the same two-loops shape: the sender runs on
    the DBOS workflow loop, the surface on serve's. On a fleet no terminal binds, and the deploy's
    own carrier serves the conversation, which is what every member had before this existed."""

    slots: dict[UUID, _Slot] = field(default_factory=dict)
    _arrivals: dict[UUID, list[_Waiter]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def connect(
        self,
        conversation_id: UUID,
        cwd: str,
        member_id: UUID | None,
        runtime_id: str | None = None,
    ) -> None:
        """One client connection arrived. The slot is per conversation and shared across
        connections, so an op sent while no stream was open is delivered by the next one; only the
        last connection leaving tears the slot down, and a fresh connection standing somewhere else
        replaces an idle slot rather than reviving a stale address."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            resolved_runtime_id = runtime_id or conversation_id.hex
            if slot is None or (
                slot.reply is None and (slot.cwd != cwd or slot.runtime_id != resolved_runtime_id)
            ):
                slot = _Slot(cwd=cwd, member_id=member_id, runtime_id=resolved_runtime_id)
                self.slots[conversation_id] = slot
            slot.connections += 1
            waiting = self._arrivals.pop(conversation_id, [])
        for waiter in waiting:
            _wake(waiter, None)

    def disconnect(self, conversation_id: UUID) -> None:
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None:
                return
            slot.connections -= 1
            if slot.connections <= 0 and slot.reply is None:
                del self.slots[conversation_id]

    def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None:
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None:
                return None
            return TerminalWorkspace(
                cwd=slot.cwd, member_id=slot.member_id, runtime_id=slot.runtime_id
            )

    async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None:
        """The bound terminal, waiting up to `grace_s` for one to connect. The client's stream ends
        at every hold and reconnects on a ~1s poll, so a turn's open landing in that gap is the
        normal case — an instant None there would fail a turn whose member is right here. Waiting
        on a member's reconnect is waiting on the network, not on our own code."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace_s
        while True:
            waiter: asyncio.Future[object] = loop.create_future()
            with self._lock:
                slot = self.slots.get(conversation_id)
                if slot is not None:
                    return TerminalWorkspace(
                        cwd=slot.cwd, member_id=slot.member_id, runtime_id=slot.runtime_id
                    )
                self._arrivals.setdefault(conversation_id, []).append((waiter, loop))
            remaining = deadline - loop.time()
            if remaining <= 0:
                self._drop_arrival(conversation_id, waiter)
                return None
            try:
                await asyncio.wait_for(waiter, remaining)
            except TimeoutError:
                self._drop_arrival(conversation_id, waiter)
                return None

    def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None:
        with self._lock:
            waiting = self._arrivals.get(conversation_id, [])
            self._arrivals[conversation_id] = [entry for entry in waiting if entry[0] != waiter]
            if not self._arrivals[conversation_id]:
                del self._arrivals[conversation_id]

    async def send(
        self,
        conversation_id: UUID,
        kind: str,
        timeout_s: int,
        name: str = "",
        arg: str = "",
        params: str = "",
        body: bytes | None = None,
    ) -> bytes:
        """Ask the conversation's terminal to run one op and answer its reply, raising
        `TerminalGone` when none is bound or the bound one stops answering. The deadline is the op's
        own timeout plus slack — the margin covers the wire, never the op. Every exit path clears
        the slot's op, so a timed-out write does not hold a file copy for the life of the session,
        and clears an idle slot whose last connection already left.

        The answer arrives as a value on the waiter rather than `set_exception`, deliberately: a
        sender cancelled between resolution and its own wakeup never retrieves the future, and an
        unretrieved exception is a GC-time log line blaming nobody.

        A terminal runs one op at a time, so a second sender queues behind the first rather than
        failing: the op ahead of it may run for its own full timeout, so the wait for a turn is
        bounded by that plus slack, not by this op's timeout alone."""
        if await self.arrived(conversation_id, ARRIVAL_GRACE_SECONDS) is None:
            raise TerminalAbsent("no terminal is connected to this conversation")
        loop = asyncio.get_running_loop()
        await self._take_turn(conversation_id, loop, timeout_s)
        waiter: asyncio.Future[object] = loop.create_future()
        op = TerminalOp(
            op_id=uuid4().hex,
            kind=kind,
            timeout_s=timeout_s,
            name=name,
            arg=arg,
            params=params,
        )
        try:
            with self._lock:
                slot = self.slots.get(conversation_id)
                if slot is None:
                    raise TerminalAbsent("the terminal disconnected before the op could run")
                slot.op = op
                slot.delivered = False
                slot.resolved = False
                slot.body = body
                slot.reply = (waiter, loop)
                watching = slot.watcher
                slot.watcher = None
                if watching is not None:
                    slot.delivered = True
            if watching is not None:
                _wake(watching, op)
            answer = await asyncio.wait_for(waiter, timeout_s + OP_DEADLINE_SLACK_SECONDS)
            if isinstance(answer, TerminalOpFailed):
                raise answer
            assert isinstance(answer, bytes)
            return answer
        except TimeoutError as error:
            raise TerminalGone(f"the terminal did not answer within {timeout_s:.0f}s") from error
        finally:
            woken: list[_Waiter] = []
            with self._lock:
                slot = self.slots.get(conversation_id)
                if slot is not None:
                    slot.op = None
                    slot.body = None
                    slot.reply = None
                    slot.delivered = False
                    slot.resolved = False
                    slot.busy = False
                    woken = list(slot.queue)
                    slot.queue.clear()
                    if not woken and slot.connections <= 0:
                        del self.slots[conversation_id]
            for entry in woken:
                _wake(entry, None)

    async def _take_turn(
        self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int
    ) -> None:
        """Block until this sender holds the conversation's one op turn — the terminal runs one at a
        time. The first caller claims `busy` and returns; a later one waits, and every release wakes
        all waiters to re-contend for the claim, which is atomic under the lock. Bounded by the op
        ahead running its own full timeout, so the wait allows for that plus slack; a dropped waiter
        is pulled from the queue so a timed-out sender leaves nothing behind."""
        deadline = loop.time() + timeout_s + OP_DEADLINE_SLACK_SECONDS
        while True:
            ticket: asyncio.Future[object] = loop.create_future()
            with self._lock:
                slot = self.slots.get(conversation_id)
                if slot is None:
                    raise TerminalAbsent("no terminal is connected to this conversation")
                if not slot.busy:
                    slot.busy = True
                    return
                slot.queue.append((ticket, loop))
            remaining = deadline - loop.time()
            timed_out = remaining <= 0
            if not timed_out:
                try:
                    await asyncio.wait_for(ticket, remaining)
                except TimeoutError:
                    timed_out = True
            if timed_out:
                with self._lock:
                    slot = self.slots.get(conversation_id)
                    if slot is not None:
                        slot.queue = deque(e for e in slot.queue if e[0] is not ticket)
                raise TerminalGone(f"the terminal did not free up within {timeout_s:.0f}s")

    async def next_op(self, conversation_id: UUID, exclude_op_id: str | None = None) -> TerminalOp:
        """The next op asked of this conversation's terminal, awaited by the held stream that will
        render it: the undelivered in-flight op when one is already waiting, else the next `send`.
        A newer stream replaces an older watcher — the old stream is the one that ended at its
        hold, and two live watchers would race one op. `exclude_op_id` is the op the same request
        just answered as a reply — never re-render it, so a reply POST that also resumes the tail
        cannot re-run the op it is the answer to."""
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[object] = loop.create_future()
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None:
                raise TerminalAbsent("no terminal is connected to this conversation")
            if slot.op is not None and not slot.delivered and slot.op.op_id != exclude_op_id:
                slot.delivered = True
                return slot.op
            slot.watcher = (waiter, loop)
        try:
            answer = await waiter
        finally:
            with self._lock:
                fresh = self.slots.get(conversation_id)
                if fresh is not None and fresh.watcher is not None and fresh.watcher[0] is waiter:
                    fresh.watcher = None
        assert isinstance(answer, TerminalOp)
        return answer

    async def staged(
        self, conversation_id: UUID, op_id: str, member_id: UUID | None = None
    ) -> bytes | None:
        """The bytes the in-flight op sends to the terminal, for the read projection serving them —
        only while that exact op is the one waiting, and only for the member the binding named.
        `async` to match the transport whose backend fetches the body from the blob store."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None or slot.op is None or slot.op.op_id != op_id:
                return None
            if member_id is not None and slot.member_id != member_id:
                return None
            return slot.body

    def in_flight(self, conversation_id: UUID) -> TerminalOp | None:
        """The op currently awaiting its reply, if any — how a test playing the terminal, or an
        operator read, sees what the turn is waiting on."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            return None if slot is None else slot.op

    def resolve(
        self,
        conversation_id: UUID,
        op_id: str,
        reply: bytes,
        failed: str | None = None,
        member_id: UUID | None = None,
    ) -> bool:
        """Answer the in-flight op, reporting whether it was the one waiting. `failed` is the
        terminal's own statement that the op could not run — surfaced to the sender as
        `TerminalOpFailed`, apart from the body, because a read's body is the file. A stale id
        belongs to an op that already timed out and is dropped rather than raised: the client that
        answered it has nothing left to do with it either way. `member_id` gates the answer to the
        member the binding named — another member on the same channel resolves nothing; `None`
        leaves it ungated (the direct-drive tests)."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            if (
                slot is None
                or slot.op is None
                or slot.op.op_id != op_id
                or slot.reply is None
                or slot.resolved
                or (member_id is not None and slot.member_id != member_id)
            ):
                return False
            slot.resolved = True
            waiting = slot.reply
        _wake(waiting, reply if failed is None else TerminalOpFailed(failed))
        return True


@dataclass(frozen=True)
class TerminalCarrier:
    """The carrier whose sandbox is the member's own terminal: `/workspace` is the directory they
    launched `ufo` in, commands run as their subprocesses, and every op is asked over the rendezvous
    rather than dialed. The client implements each op natively; the wire carries the op's name and
    params, never code.

    No isolation: the agent acts as the member, on their machine, guarded by nothing the member's
    own shell is not. The container carriers are where `containment` is load-bearing."""

    terminals: TerminalTransport
    document_renderer: DocumentRenderer | None = None

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """The bound terminal as a handle, waiting out the gap between the client's held streams —
        a turn's open landing there is routine, not an absence. A terminal standing in a different
        directory refuses by naming both, since a workspace that moves under a thread makes every
        earlier line of its transcript a lie.

        The proxy env carries the run token in its userinfo. `ufo run` replaces a public URL with a
        plaintext loopback for its child and forwards the bytes over verified TLS; with no public
        URL the env names the client's own loopback, where nothing answers, so a command that
        honours the env fails closed instead of leaking unmetered. A command that ignores it was
        never metered on this carrier anyway, and the re-authorization every exec runs keeps its
        invariant that the proxy env carries the turn's token."""
        bound = await self.terminals.arrived(spec.conversation_id, ARRIVAL_GRACE_SECONDS)
        if bound is None:
            raise TerminalAbsent(
                f"this conversation's workspace is the terminal at {spec.workspace_host_path}, "
                "which is not connected"
            )
        if bound.cwd != spec.workspace_host_path:
            raise TerminalGone(
                f"this conversation's workspace is {spec.workspace_host_path}; the connected "
                f"terminal is at {bound.cwd}"
            )
        if spec.proxy.public_url is not None:
            parts = urlsplit(spec.proxy.public_url)
            proxy_url = (
                f"{parts.scheme}://{spec.run_token}:{PROXY_PASSWORD}@{parts.netloc}{parts.path}"
            )
        else:
            proxy_url = f"http://{spec.run_token}:{PROXY_PASSWORD}@127.0.0.1:{spec.proxy.port}"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=spec.workspace_host_path,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            runtime_root=f"${UFO_HOME_ENV}/{RUNTIME_DIRNAME}/{bound.runtime_id}",
            egress_env={
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "NO_PROXY": NO_PROXY_HOSTS,
                "no_proxy": NO_PROXY_HOSTS,
                EGRESS_CA_CERT_ENV: spec.proxy.ca_cert,
                **spec.env,
            },
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """Reattach off a turn, so the file browser and off-turn writers reach the bound terminal.
        Reads the binding through `arrived` with no grace — a single cross-pod read — never the
        process-local `workspace`, so a read that lands on a pod which never held the connection
        still sees the terminal a peer holds rather than reporting an empty workspace."""
        bound = await self.terminals.arrived(spec.conversation_id, 0.0)
        if bound is None or bound.cwd != spec.resume_id:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=bound.cwd,
            workspace_host_path=bound.cwd,
            run_token=spec.run_token,
            runtime_root=f"${UFO_HOME_ENV}/{RUNTIME_DIRNAME}/{bound.runtime_id}",
        )

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """One command on the member's machine, in the bound directory, under their own user. The
        `/workspace` paths tools pass are rewritten here — path logic never reaches the client.
        `EGRESS_CA_CERT_ENV` rides the env whole; the client merges it with the machine's own trust
        store into one bundle and points each CA variable there, since only the client knows both a
        path on its own disk and the roots the member already trusts — the container carriers reach
        the same bundle by installing the CA into the system store, which a member's machine is
        never asked to accept. Model-generated shell commands invoke `ufo run`, whose child sees the
        plaintext forward-proxy protocol standard clients speak while its public hop uses TLS. A
        `gh` operation uses the client's embedded Go 1.27 build, whose verifier reads that bundle
        without changing the member's certificate store."""
        root = _root(handle)
        return await self._exec(handle, host_argv(argv, root), timeout_s)

    async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult:
        """Ask the running client to load skills under `$UFO_HOME/skills`."""
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_SKILLS,
                DEFAULT_EXEC_TIMEOUT_SECONDS,
                params=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            )
        except TerminalOpFailed as error:
            raise RuntimeError(str(error)) from error
        return ExecResult(stdout=reply.decode(), stderr="", exit_code=0)

    async def _exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run an argv already resolved to the member's real paths, without the `/workspace`
        rewrite `exec` applies — the one caller that composes concrete host paths itself (a tree
        walk's enumeration) must not have them rewritten a second time, which would mangle a bound
        directory that itself contains `/workspace`.

        The client reports its own deadline firing in the reply, and only it can: the op's deadline
        is enforced on the member's machine, where the stopped command dies of the group signal and
        so answers `128 + SIGKILL` — a code a command killed by anything else answers too. Reading
        it as a timeout would call a member's own `kill` a budget expiry, and reading nothing at all
        is what left a timed-out `bash` on this carrier reporting a bare exit code instead of the
        handles its command is still running behind. A client the deploy has not yet updated sends
        no such field, and its reply reads as the command's own exit exactly as before."""
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_EXEC,
                timeout_s,
                name=OP_EXEC,
                params=json.dumps(
                    {"argv": list(argv), "env": dict(handle.egress_env)}, separators=(",", ":")
                ),
            )
        except TerminalOpFailed as error:
            raise RuntimeError(str(error)) from error
        result = _reply_object(reply, OP_EXEC)
        timed_out = result.get("timed_out") is True
        code = EXEC_TIMEOUT_CODE if timed_out else result.get("exit_code", 1)
        return ExecResult(
            stdout=_reply_stream(result, "stdout").decode(errors="replace"),
            stderr=_reply_stream(result, "stderr").decode(errors="replace"),
            exit_code=code if isinstance(code, int) else 1,
            timed_out_after_s=timeout_s if timed_out else None,
        )

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """The copy-in: the bytes are staged on the rendezvous and served by the op's own read
        projection — never the directive line — and the client lands them by staged temp and
        rename, the same rename-into-place every carrier's write keeps."""
        try:
            await self.terminals.send(
                handle.conversation_id,
                OP_WRITE,
                DEFAULT_EXEC_TIMEOUT_SECONDS,
                arg=_client_path(handle, path),
                body=content,
            )
        except TerminalOpFailed as error:
            raise OSError(str(error)) from error

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """The copy-out: the reply's body is the file. Held whole on the rendezvous — the reply is
        one request — so the surface bounds it where it lands; a path holding no file answers
        `FileNotFoundError` on every carrier."""
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_READ,
                DEFAULT_EXEC_TIMEOUT_SECONDS,
                arg=_client_path(handle, path),
            )
        except TerminalOpFailed as error:
            if str(error).startswith(OP_NOT_FOUND_PREFIX):
                raise FileNotFoundError(str(error)) from error
            raise OSError(str(error)) from error
        for start in range(0, len(reply), READ_REPLY_CHUNK_BYTES):
            yield reply[start : start + READ_REPLY_CHUNK_BYTES]

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """One `ufo fs` op, run where the files are: the directive names the op and the client runs
        its native implementation, so only the params and the result cross. A tree walk is two
        ops: the enumeration `find` first, through the same exec primitive, then the op reading
        its listing — what a walk visits is decided by a command this process composed, never by
        the client. `changes` enumerates from its target directories instead of walking, and with
        no targets there is nothing to ask: no op travels and the scan is empty."""
        root = _root(handle)
        rewritten = {
            key: _under_root(root, value)
            if key in _PATH_PARAMS and isinstance(value, str)
            else value
            for key, value in params.items()
        }
        path = rewritten.get("path")
        suffix = PurePosixPath(path).suffix.lower() if isinstance(path, str) else ""
        kind = {
            ".pdf": "pdf",
            ".pptx": "pptx",
            ".docx": "docx",
            ".xlsx": "xlsx",
        }.get(suffix)
        if (
            op == "read"
            and isinstance(path, str)
            and kind is not None
            and self.document_renderer is not None
        ):
            workspace = rewritten.get("workspace", root)
            if not isinstance(workspace, str):
                raise ValueError("workspace must be a path")
            offset = rewritten.get("offset", 1)
            limit = rewritten.get("limit", DOCUMENT_PAGES_MAX)
            if not isinstance(offset, int) or isinstance(offset, bool):
                raise ValueError("offset must be a number")
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise ValueError("limit must be a number")
            try:
                content = await self.terminals.send(
                    handle.conversation_id,
                    OP_READ,
                    DEFAULT_EXEC_TIMEOUT_SECONDS,
                    arg=path,
                    params=json.dumps(
                        {
                            "max_bytes": DOCUMENT_INPUT_MAX_BYTES,
                            "workspace": workspace,
                        },
                        separators=(",", ":"),
                    ),
                )
            except TerminalOpFailed as error:
                raise ValueError(str(error)) from error
            if len(content) > DOCUMENT_INPUT_MAX_BYTES:
                raise ValueError(
                    f"{path} is over the {DOCUMENT_INPUT_MAX_BYTES}-byte document read cap"
                )
            return await self.document_renderer.render(path, kind, content, offset, limit)
        if op == "changes":
            targets = rewritten.pop("paths", None)
            if not isinstance(targets, list) or any(
                not isinstance(entry, str) for entry in targets
            ):
                raise ValueError("a changes scan names its target directories")
            if not targets:
                return {"changes": [], "truncated": False}
            rewritten["enum"] = "changes-enum"
            await self._enumerate(handle, op, root, CHANGES_ENUMERATION, tuple(targets))
        elif op in WALK_ENUMERATION:
            rewritten["enum"] = f"{op}-enum"
            walk_root = rewritten.get("path") or rewritten.get("workspace") or root
            await self._enumerate(handle, op, str(walk_root), WALK_ENUMERATION[op])
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_FILE,
                DEFAULT_EXEC_TIMEOUT_SECONDS,
                name=op,
                params=json.dumps(rewritten, separators=(",", ":")),
            )
        except TerminalOpFailed as error:
            raise RuntimeError(str(error)) from error
        result = _reply_object(reply, op)
        failure = result.get("error")
        if isinstance(failure, str):
            raise ValueError(failure)
        return result

    async def _enumerate(
        self,
        handle: SandboxHandle,
        op: str,
        walk_root: str,
        program: str,
        arguments: tuple[str, ...] = (),
    ) -> None:
        listing = await self._exec(
            handle,
            (
                "sh",
                "-c",
                f"UFO_WALK_ROOT={shlex.quote(walk_root)}\nexport UFO_WALK_ROOT\n{program}",
                *(("sh", *arguments) if arguments else ()),
            ),
            DEFAULT_EXEC_TIMEOUT_SECONDS,
        )
        if listing.exit_code not in (0, 1):
            raise ValueError(
                listing.stderr.strip() or f"the {op} walk could not list the workspace"
            )

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise SandboxUnreachable(
            "a terminal-bound sandbox exposes no external per-port host; reach an in-sandbox "
            "service through a remote carrier (e2b)"
        )


def _reply_stream(result: dict[str, object], name: str) -> bytes:
    """One captured stream off an exec reply, base64 as the client sends it. A missing key or a
    malformed value raises — an empty stream is an empty string under a present key, so absence is
    a client speaking some other reply shape, and reading it as silence would hand the model empty
    output as a success."""
    encoded = result.get(f"{name}_b64")
    if encoded is None:
        raise RuntimeError(f"the terminal's exec reply carries no {name}_b64")
    return base64.b64decode(str(encoded), validate=True)


def _reply_object(reply: bytes, op: str) -> dict[str, object]:
    try:
        parsed = json.loads(reply.decode("utf-8", "replace"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"the terminal's {op} reply is not JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"the terminal's {op} reply is not a JSON object")
    return parsed


def _root(handle: SandboxHandle) -> str:
    if handle.workspace_host_path is None:
        raise RuntimeError("a terminal sandbox serves /workspace from the bound directory")
    return handle.workspace_host_path


def _client_path(handle: SandboxHandle, path: str) -> str:
    """A logical `/workspace/…` path mapped onto the bound directory by stripping the one leading
    `/workspace`, never a global replace: a member's own subdirectory named `workspace`
    (`/workspace/workspace/notes.md`) must land under the root once, not have every occurrence
    rewritten into a bogus nested tree. The container carriers strip the prefix the same way through
    `relative_to`; only the client had done an unanchored replace."""
    return _under_root(_root(handle), path)


def _under_root(root: str, path: str) -> str:
    candidate = PurePosixPath(path)
    if not candidate.is_relative_to(WORKSPACE_DIR):
        return path
    rel = candidate.relative_to(WORKSPACE_DIR)
    return root if rel == PurePosixPath(".") else str(PurePosixPath(root) / rel)
