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
import json
import shlex
import threading
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from ufo.sandbox.containment import ContainmentError, contained_file
from ufo.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    EGRESS_CA_CERT_ENV,
    NO_PROXY_HOSTS,
    WORKSPACE_DIR,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)

CLIENT_BACKEND = "client"
CLIENT_PROGRAM_DIR = Path(__file__).parent / "client"
_PATH_PARAMS = frozenset({"path", "workspace", "staged_path"})
"""The `sbxfs` op params that name a workspace path and so map onto the bound directory; a
`pattern`, a `glob`, or an edit's text is a value, not a path, and is never rewritten."""
OP_EXEC = "exec"
OP_WRITE = "write"
OP_READ = "read"
OP_FILE = "fileop"
OP_NOT_FOUND_PREFIX = "ENOENT"
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
/usr/bin/git -C "$r" -c core.quotePath=false status --porcelain=v1 -z --no-renames -uall
printf "D\000%s\000" "$(/usr/bin/git -C "$r" -c core.quotePath=false diff HEAD --no-renames -U3 \
2>/dev/null)"
"""
WALK_ENUMERATION = {
    "grep": rf"""/usr/bin/find "$UFO_WALK_ROOT" \( {_UNSKIPPED_ROOT} -a \( {_SKIPPED} \) \) -prune \
-o \( -type d -o -type f \) -print0 > "$UFO_OP_WORKDIR/grep-enum"
""",
    "glob": r"""/usr/bin/find "$UFO_WALK_ROOT" -type f -print0 > "$UFO_OP_WORKDIR/glob-enum"
printf '\000' >> "$UFO_OP_WORKDIR/glob-enum"
/usr/bin/find "$UFO_WALK_ROOT" -type f -print0 \
| /usr/bin/xargs -0 /usr/bin/stat -f '%z %.9Fm' >> "$UFO_OP_WORKDIR/glob-enum"
""",
    "changes": rf"""r='{_REPOSITORY_COMMANDS}'
/usr/bin/find "$UFO_WALK_ROOT" \( {_UNSKIPPED_ROOT} -a -name '.git' \) -prune -print0 \
-o \( {_UNSKIPPED_ROOT} -a \( {_SKIPPED} \) \) -prune \
| /usr/bin/xargs -0 -n1 /bin/sh -c "$r" sh > "$UFO_OP_WORKDIR/changes-enum"
""",
}
"""The one command each tree walk's program needs run first, reading `$UFO_WALK_ROOT` and leaving
its listing in the relay's scratch dir, where the program's `enum` param names it. The walks read
the listing rather than the tree because the runner has no subprocess and must not re-decide what a
walk visits — `find` meets entries in the `readdir` order `os.walk` does, which is what keeps a
truncated result identical to `sbxfs`'s."""
ARRIVAL_GRACE_SECONDS = 30.0
"""How long an op or an open waits for the terminal to reconnect. The client's stream ends at every
hold and reconnects on a ~1s poll, so work landing in that gap is the normal case — a different
quantity from the deadline slack above, coinciding at 30s by choice, not by identity."""
READ_REPLY_CHUNK_BYTES = 1024 * 1024


class TerminalGone(RuntimeError):
    """No terminal is bound to this conversation, or the bound one stopped answering."""


class TerminalOpFailed(RuntimeError):
    """The terminal answered that the op itself failed — a missing file, a refused rename. Carried
    apart from the reply body because a read's body is the file, so a failure cannot ride it."""


@dataclass(frozen=True, slots=True)
class TerminalOp:
    """One request to a connected terminal, shaped so the relay never parses: `kind` picks the
    fixed primitive arm, `timeout_s` and `arg` ride as their own directive fields, and `payload` is
    ready-to-run JS the arm pipes to the runner whole — the id is what its answer comes back under.
    """

    op_id: str
    kind: str
    timeout_s: int
    arg: str = ""
    payload: str = ""


_Waiter = tuple["asyncio.Future[object]", asyncio.AbstractEventLoop]


@dataclass
class _Slot:
    """One conversation's live terminal state: where it stands, the op in flight, and what that op
    sends down. `body` holds a copy-in's bytes — they never ride the directive line, which the
    client reads into a shell variable — served by the op's own read projection. `reply` is the
    in-flight sender's waiter and `watcher` the stream's, each remembered with its own loop so the
    other side's thread can wake it. `queue` holds senders waiting their turn — a background
    subagent's op behind the parent's, an off-turn attachment write behind a running turn — so a
    terminal that runs one op at a time serializes them rather than failing the second. Mutated only
    under the rendezvous lock."""

    cwd: str
    member_id: UUID | None
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

    def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None:
        """One client connection arrived. The slot is per conversation and shared across
        connections, so an op sent while no stream was open is delivered by the next one; only the
        last connection leaving tears the slot down, and a fresh connection standing somewhere else
        replaces an idle slot rather than reviving a stale address."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None or (slot.reply is None and slot.cwd != cwd):
                slot = _Slot(cwd=cwd, member_id=member_id)
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
            return TerminalWorkspace(cwd=slot.cwd, member_id=slot.member_id)

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
                    return TerminalWorkspace(cwd=slot.cwd, member_id=slot.member_id)
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
        arg: str = "",
        payload: str = "",
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
            raise TerminalGone("no terminal is connected to this conversation")
        loop = asyncio.get_running_loop()
        await self._take_turn(conversation_id, loop, timeout_s)
        waiter: asyncio.Future[object] = loop.create_future()
        op = TerminalOp(op_id=uuid4().hex, kind=kind, timeout_s=timeout_s, arg=arg, payload=payload)
        try:
            with self._lock:
                slot = self.slots.get(conversation_id)
                if slot is None:
                    raise TerminalGone("the terminal disconnected before the op could run")
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
                    raise TerminalGone("no terminal is connected to this conversation")
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

    async def next_op(self, conversation_id: UUID) -> TerminalOp:
        """The next op asked of this conversation's terminal, awaited by the held stream that will
        render it: the undelivered in-flight op when one is already waiting, else the next `send`.
        A newer stream replaces an older watcher — the old stream is the one that ended at its
        hold, and two live watchers would race one op."""
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[object] = loop.create_future()
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None:
                raise TerminalGone("no terminal is connected to this conversation")
            if slot.op is not None and not slot.delivered:
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

    def staged(self, conversation_id: UUID, op_id: str) -> bytes | None:
        """The bytes the in-flight op sends to the terminal, for the read projection serving them —
        only while that exact op is the one waiting."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            if slot is None or slot.op is None or slot.op.op_id != op_id:
                return None
            return slot.body

    def in_flight(self, conversation_id: UUID) -> TerminalOp | None:
        """The op currently awaiting its reply, if any — how a test playing the terminal, or an
        operator read, sees what the turn is waiting on."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            return None if slot is None else slot.op

    def resolve(
        self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None = None
    ) -> bool:
        """Answer the in-flight op, reporting whether it was the one waiting. `failed` is the
        terminal's own statement that the op could not run — surfaced to the sender as
        `TerminalOpFailed`, apart from the body, because a read's body is the file. A stale id
        belongs to an op that already timed out and is dropped rather than raised: the client that
        answered it has nothing left to do with it either way."""
        with self._lock:
            slot = self.slots.get(conversation_id)
            if (
                slot is None
                or slot.op is None
                or slot.op.op_id != op_id
                or slot.reply is None
                or slot.resolved
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
    rather than dialed. The op logic itself is JavaScript this process ships in the payload, run
    under the client machine's stock `osascript` — the shell relay pipes and never parses.

    No isolation: the agent acts as the member, on their machine, guarded by nothing the member's
    own shell is not. The container carriers are where `containment` is load-bearing."""

    terminals: Terminals

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """The bound terminal as a handle, waiting out the gap between the client's held streams —
        a turn's open landing there is routine, not an absence. A terminal standing in a different
        directory refuses by naming both, since a workspace that moves under a thread makes every
        earlier line of its transcript a lie.

        The proxy env carries the run token in its userinfo, at the deploy's public proxy when one
        is named and at the client's own loopback when not — where nothing answers, so a command
        that honours the proxy env fails closed instead of leaking unmetered, one that ignores it
        was never metered on this carrier anyway, and the re-authorization every exec runs keeps
        its invariant that the proxy env carries the turn's token."""
        bound = await self.terminals.arrived(spec.conversation_id, ARRIVAL_GRACE_SECONDS)
        if bound is None:
            raise TerminalGone(
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
            proxy_url = f"{parts.scheme}://{spec.run_token}:@{parts.netloc}{parts.path}"
        else:
            proxy_url = f"http://{spec.run_token}:@127.0.0.1:{spec.proxy.port}"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=spec.workspace_host_path,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
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
        bound = self.terminals.workspace(spec.conversation_id)
        if bound is None or bound.cwd != spec.resume_id:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=bound.cwd,
            workspace_host_path=bound.cwd,
            run_token=spec.run_token,
        )

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """One command on the member's machine, in the bound directory, under their own user. The
        `/workspace` paths tools pass are rewritten here — path logic never reaches the shell — and
        the reply carries streams as hex, because the runner has no base64 and a directive field
        survives hex unescaped. `EGRESS_CA_CERT_ENV` rides the env whole; the relay's runner
        materializes it to a file and points each client's own CA variable at it, since only the
        client knows a path on its own disk."""
        root = _root(handle)
        return await self._exec(
            handle, tuple(arg.replace(WORKSPACE_DIR, root) for arg in argv), timeout_s
        )

    async def _exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run an argv already resolved to the member's real paths, without the `/workspace`
        rewrite `exec` applies — the one caller that composes concrete host paths itself (a tree
        walk's enumeration) must not have them rewritten a second time, which would mangle a bound
        directory that itself contains `/workspace`."""
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_EXEC,
                timeout_s,
                payload=client_payload(
                    OP_EXEC, {"argv": list(argv), "env": dict(handle.egress_env)}
                ),
            )
        except TerminalOpFailed as error:
            raise RuntimeError(str(error)) from error
        result = _reply_object(reply, OP_EXEC)
        code = result.get("exit_code", 1)
        return ExecResult(
            stdout=bytes.fromhex(str(result.get("stdout_hex", ""))).decode(errors="replace"),
            stderr=bytes.fromhex(str(result.get("stderr_hex", ""))).decode(errors="replace"),
            exit_code=code if isinstance(code, int) else 1,
        )

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """The copy-in: the bytes are staged on the rendezvous and served by the op's own read
        projection — never the directive line — and the relay lands them by staged temp and `mv`,
        the same rename-into-place every carrier's write keeps."""
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
        """The copy-out: the reply's body is the file, posted by the relay as `--data-binary`. Held
        whole on the rendezvous — the reply is one request — so the surface bounds it where it
        lands; a path holding no file answers `FileNotFoundError` on every carrier."""
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
        """One `sbxfs` op, run where the files are: the op's own JS program rides the payload and
        runs under the client's runner, so only the result crosses. The program is data this
        process ships — the op contract has one home here, and the client has no version. A tree
        walk is two ops: the enumeration `find` first, through the same exec primitive, then the
        program reading its listing — the runner has no subprocess, so what a walk visits is
        decided by a command this process composed, never by the program."""
        root = _root(handle)
        rewritten = {
            key: _under_root(root, value)
            if key in _PATH_PARAMS and isinstance(value, str)
            else value
            for key, value in params.items()
        }
        enumeration = WALK_ENUMERATION.get(op)
        if enumeration is not None:
            rewritten["enum"] = f"{op}-enum"
        payload = client_payload(op, rewritten)
        if enumeration is not None:
            walk_root = rewritten.get("path") if op == "grep" else None
            walk_root = walk_root or rewritten.get("workspace") or root
            listing = await self._exec(
                handle,
                (
                    "sh",
                    "-c",
                    f"UFO_WALK_ROOT={shlex.quote(str(walk_root))}\n"
                    f"export UFO_WALK_ROOT\n{enumeration}",
                ),
                DEFAULT_EXEC_TIMEOUT_SECONDS,
            )
            if listing.exit_code not in (0, 1):
                raise ValueError(
                    listing.stderr.strip() or f"the {op} walk could not list the workspace"
                )
        try:
            reply = await self.terminals.send(
                handle.conversation_id,
                OP_FILE,
                DEFAULT_EXEC_TIMEOUT_SECONDS,
                payload=payload,
            )
        except TerminalOpFailed as error:
            raise RuntimeError(str(error)) from error
        result = _reply_object(reply, op)
        failure = result.get("error")
        if isinstance(failure, str):
            raise ValueError(failure)
        return result

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise SandboxUnreachable(
            "a terminal-bound sandbox exposes no external per-port host; reach an in-sandbox "
            "service through a remote carrier (e2b)"
        )


def client_payload(op: str, params: dict[str, object]) -> str:
    """One op as the ready-to-run program its directive carries: the shared prelude (the Foundation
    shims and the `MODE`/`WORKDIR`/`ARGS` globals), the op's own JS, and the `run` entry
    `osascript -l JavaScript` calls with the relay's argv. Composed in one place so the carrier and
    the differential tests ship byte-identical programs."""
    prelude = _client_program("prelude")
    program = _client_program(op)
    trailer = (
        "function run(argv) { __start__(argv); "
        f"return main({json.dumps(params, separators=(',', ':'))}); }}"
    )
    return f"{prelude}\n{program}\n{trailer}"


def _client_program(op: str) -> str:
    """One op's JS, read through the containment guard — `op` names a file, and a name is an
    ingress wherever it lands."""
    try:
        with contained_file(CLIENT_PROGRAM_DIR / f"{op}.js", CLIENT_PROGRAM_DIR) as source:
            found = source.lstat()
            if found is None:
                raise RuntimeError(f"sbxfs {op} has no client program")
            return source.read_text(found.st_size + 1)
    except ContainmentError as error:
        raise RuntimeError(f"sbxfs {op} has no client program") from error


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
