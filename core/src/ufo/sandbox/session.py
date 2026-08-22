"""The sandbox seam: the carrier interface, the per-turn session tools hold, and value objects.

Everything downstream (tools, engine) depends only on this module; the Docker carrier and the
egress proxy implement against it. A deploy swaps the carrier (E2B, remote) without touching a
tool. The invariant the session exists to hold: a tool reaches only the conversation's
`/workspace`, never the transcript or compaction records, which live in the blob store the sandbox
holds no credential for."""

import base64
import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable
from uuid import UUID

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.auth.token_signing import SignedTokenError, sign_token, verify_token

WORKSPACE_DIR = "/workspace"
WORKSPACE_WRITE_MODE = 0o644
SANDBOX_MODULE_BOOTSTRAP = (
    "import os, shutil, sys\n"
    "_sbxfs = shutil.which('sbxfs')\n"
    "if _sbxfs is None:\n"
    "    raise SystemExit('sbxfs is not on PATH: this sandbox predates the containment guard')\n"
    "sys.path.insert(0, os.path.dirname(_sbxfs))\n"
)
"""Put the directory holding the baked guard on `sys.path`, located through `sbxfs` because which
directory that is differs by carrier. An image built before the guard was baked has neither, and
what it does have is a workspace the agent writes — so the miss is named here and the program exits,
rather than `dirname(None)` raising a TypeError that reads like a bug in the program itself."""
SANDBOX_PYTHON_FLAG = "-I"
"""Isolated mode, which is what makes the bootstrap above a hardening step rather than an ingress of
its own: `python3 -c` otherwise puts the process cwd at `sys.path[0]`, and a carrier runs commands
with cwd inside the workspace the agent writes to, so `import shutil` — then `import containment`
itself — would resolve against a module the agent planted there, before the guard has checked
anything. `-I` drops cwd and the `PYTHON*` variables from module resolution, leaving the stdlib and
the directory the bootstrap names."""
COPY_IN_PROG = """
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        target.replace_bytes(sys.stdin.buffer.read(), target.mode(0o644))
except ContainmentError as error:
    raise SystemExit(str(error))
"""
"""The copy-in a carrier whose `/workspace` lives inside a container runs instead of a shell
redirect: `> "$1"` truncates through a planted link and `mkdir -p` follows a symlinked ancestor,
while this builds each directory as the descent reaches it and renames a staged inode onto the
target. The mode repeats `WORKSPACE_WRITE_MODE` because a `-c` program inside the sandbox cannot
import it."""
TOOL_OUTPUT_DIRNAME = ".tool-output"
TOOL_OUTPUT_DIR = f"{WORKSPACE_DIR}/{TOOL_OUTPUT_DIRNAME}"
DEFAULT_EXEC_TIMEOUT_SECONDS = 120
SENTINEL_MODEL_KEY = "UFO_SENTINEL_MODEL_KEY"
SANDBOX_UID = 1000
SANDBOX_GID = 1000
PROXY_ENV_NAMES = frozenset(("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))
NO_PROXY_HOSTS = "localhost,127.0.0.1,::1"
"""The destinations every carrier's egress env exempts from the proxy, in both spellings the
ecosystem reads. A sandbox reaching its own loopback is not egress: the proxy admits only globally
routable addresses, so a proxied loopback request can only 403, and a service the turn started
inside the container — Chrome's DevTools port, a dev-server preview — would be unreachable from
inside it. Exempting loopback grants no reach a raw socket does not already have."""


PROBE_TOKEN_KIND = "ufo-probe"


def _basic_username(header: str) -> str:
    """The username inside a `Proxy-Authorization: Basic` header, where every token class rides: a
    sandbox client is handed a proxy URL and nothing else, so userinfo is the only channel."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise ValueError("proxy authorization is not basic auth")
    return base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class RunToken:
    """The turn and member authority attributed to one sandbox process tree."""

    workspace_id: UUID
    turn_id: UUID
    acting_member_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RunTokenCodec:
    """Sign the per-turn proxy username and recover only tokens minted by this deployment."""

    secret: bytes

    @classmethod
    def from_env(cls) -> "RunTokenCodec":
        value = os.environ.get(UFO_TOKEN_SECRET_ENV)
        if not value:
            raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} must be set to sign sandbox run tokens")
        return cls(secret=value.encode())

    def encode(self, run: RunToken) -> str:
        member = "-" if run.acting_member_id is None else str(run.acting_member_id)
        payload = f"ufo-run/{run.workspace_id}/{run.turn_id}/{member}".encode()
        return sign_token(self.secret, payload)

    def from_proxy_auth(self, header: str) -> RunToken:
        username = _basic_username(header)
        try:
            kind, workspace, turn, member = verify_token(username, self.secret).decode().split("/")
            if kind != "ufo-run":
                raise ValueError("invalid run token domain")
            return RunToken(
                workspace_id=UUID(workspace),
                turn_id=UUID(turn),
                acting_member_id=None if member == "-" else UUID(member),
            )
        except (UnicodeDecodeError, SignedTokenError, ValueError) as error:
            raise ValueError("invalid signed run token") from error


@dataclass(frozen=True, slots=True)
class ProbeToken:
    """The conversation and member authority attributed to one off-turn sandbox exec, until it
    expires.

    A turn's egress is authorized by the turn: the proxy admits a CONNECT while the DB still reports
    that turn running. A probe runs off every turn, so there is no row whose status answers whether
    it is still live — the token carries its own deadline, minted per exec for that exec's timeout,
    and the proxy compares it fresh per CONNECT. `probe_id` names the one exec.

    `acting_member_id` is the member the probe acts as: whoever armed the watch this exec serves, so
    a command that reached their own connected account in the arming turn keeps reaching it on every
    probe after it. Unset means nobody, and then only connections shared with the whole workspace
    are forwarded — the same authority a turn's own sandbox open carries before a tool call
    re-authorizes it for its speaker."""

    workspace_id: UUID
    conversation_id: UUID
    probe_id: UUID
    expires_at: int
    acting_member_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ProbeTokenCodec:
    """Sign the per-probe proxy username and recover only probes minted by this deployment. It holds
    the same deploy secret `RunTokenCodec` does, and each class names its own domain inside the
    signed payload — so a run token presented as a probe (or the reverse) is refused as firmly as a
    forgery, and neither codec can be made to read the other's token as its own."""

    secret: bytes

    def encode(self, probe: ProbeToken) -> str:
        member = "-" if probe.acting_member_id is None else str(probe.acting_member_id)
        payload = (
            f"{PROBE_TOKEN_KIND}/{probe.workspace_id}/{probe.conversation_id}"
            f"/{probe.probe_id}/{member}/{probe.expires_at}"
        ).encode()
        return sign_token(self.secret, payload)

    def from_proxy_auth(self, header: str) -> ProbeToken:
        username = _basic_username(header)
        try:
            kind, workspace, conversation, probe, member, expires = (
                verify_token(username, self.secret).decode().split("/")
            )
            if kind != PROBE_TOKEN_KIND:
                raise ValueError("invalid probe token domain")
            return ProbeToken(
                workspace_id=UUID(workspace),
                conversation_id=UUID(conversation),
                probe_id=UUID(probe),
                expires_at=int(expires),
                acting_member_id=None if member == "-" else UUID(member),
            )
        except (UnicodeDecodeError, SignedTokenError, ValueError) as error:
            raise ValueError("invalid signed probe token") from error


EGRESS_CA_CERT_ENV = "UFO_EGRESS_CA_CERT"
EGRESS_CA_KEY_ENV = "UFO_EGRESS_CA_KEY"
EGRESS_CONTROL_TOKEN_ENV = "UFO_EGRESS_CONTROL_TOKEN"


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where the egress proxy listens, backend-neutral: the port plus the CA the sandbox trusts so
    the proxy can terminate TLS and swap sentinels onto the wire. Each carrier decides how its
    sandbox addresses the host the proxy runs on — that reachability detail is the carrier's, not
    the proxy's. `public_url` is the externally-reachable base an off-cluster sandbox (e2b) dials
    the proxy at; unset for an in-pod carrier (docker/local) whose sandbox reaches the proxy over a
    host-local address it forms from `port` alone."""

    port: int
    ca_cert: str
    public_url: str | None = None


SANDBOX_SIZES: tuple[str, ...] = ("small", "medium", "large")
"""The sandbox sizes a sizing carrier provisions, in ascending order. A carrier that offers them
declares them on its `CarrierSpec.sizes`; one that provisions a single shape (docker, local, the
terminal) declares none and ignores `SandboxSpec.size`."""


@dataclass(frozen=True)
class SandboxSpec:
    """`workspace_host_path` is the host directory an in-cluster carrier serves `/workspace` from —
    the Docker carrier's bind-mount source, the local carrier's cwd. An off-cluster carrier (e2b)
    cannot see the host filesystem and serves `/workspace` from its own sandbox disk, so it ignores
    the field.

    `resume_id` is the sandbox id a prior process persisted on the conversation row: when this
    process holds no live sandbox for the conversation, the carrier resumes that id rather than
    opening a fresh sandbox, so a serve restart reattaches instead of stranding it. None means
    create fresh (no stored handle, or one another backend wrote). A carrier that resumes by
    conversation identity (docker's container name, the local host directory) ignores it.

    `size` is the owning agent's sandbox size, read off its row by the open that builds this spec.
    It shapes only a fresh sandbox on a carrier that declares sizes — a resumed sandbox keeps the
    size it was created at, and a single-shape carrier ignores it. None means the caller states no
    size (an attach, a terminal bind).

    `turn_id` is the turn this open serves, which one container answers many of: a subagent inherits
    the sandbox of the turn that spawned it, so several turns run commands in one container at once.
    A `CommandStopping` carrier keys the commands it leaves running on it, so a stop reaches the
    turn's own groups and no sibling's. None means no turn owns the open (a read, an off-turn write,
    a probe) and nothing will ever stop its commands; a carrier whose commands die with the call
    that launched them ignores it."""

    conversation_id: UUID
    image_ref: str
    workspace_host_path: str
    proxy: ProxyEndpoint
    run_token: str
    resume_id: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    size: str | None = None
    turn_id: UUID | None = None


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a created-or-attached container; the carrier reads it, not tools. It
    carries `workspace_host_path` so a host-path carrier can rewrite a logical `/workspace` path to
    where it actually serves it, and the base `run_token` plus `egress_env` a scoped session
    rewrites for each exec. A container shared across turns never pins either one's authority.
    Whatever a public per-port host requires on the wire is not here: `dial` reads it off the live
    container, so a handle rebuilt from the durable row alone (the ingress) reaches a port exactly
    as the process that created it does. `turn_id` names the turn this reference was opened for —
    `SandboxSpec.turn_id`, carried across re-authorization — and is what scopes a stop to that
    turn's own commands where several turns share the container."""

    conversation_id: UUID
    container_id: str
    workspace_host_path: str | None = None
    run_token: str | None = None
    egress_env: Mapping[str, str] = field(default_factory=dict)
    turn_id: UUID | None = None


SANDBOX_HANDLE_SEP = ":"


def sandbox_handle_id(backend: str, value: str) -> str | None:
    """The sandbox id inside a stored `<backend>:<id>` handle when it belongs to `backend`, else
    None — a handle another backend wrote is not this carrier's to resume. The backend prefix is
    load-bearing: a deploy that switched carriers must not resume another backend's id."""
    prefix = f"{backend}{SANDBOX_HANDLE_SEP}"
    return value[len(prefix) :] if value.startswith(prefix) else None


@dataclass(frozen=True)
class ExecResult:
    """One command's outcome. `timed_out_after_s` is the carrier's own deadline firing, in the
    seconds it allowed; None means the command chose its own exit however it ended. The exit code
    cannot carry this: a command that runs `timeout` exits 124 exactly as a carrier-stopped one
    does, so a caller reading the code alone cannot tell whose deadline ended the work."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out_after_s: int | None = None


@dataclass(frozen=True)
class DialTarget:
    """An externally dialable authority for one in-sandbox port, plus whatever the carrier
    requires on the wire to reach it (e2b's traffic-access header). `tls` says whether the
    authority terminates TLS, so a consumer picks https/wss vs http/ws instead of guessing."""

    host: str
    tls: bool
    headers: Mapping[str, str] = field(default_factory=dict)


class SandboxUnreachable(RuntimeError):
    """The dial contract's error: a carrier's sandbox is gone or has no external route."""


class Carrier(Protocol):
    """Create-or-attach a per-conversation container and reach its `/workspace`: run commands in it,
    write bytes in, stream bytes out. `/workspace` is the carrier's own storage and the only copy of
    a conversation's files, so nothing here reclaims a container — whether one can be dropped
    without taking the workspace with it is knowledge only a carrier holds, and the carrier that can
    reclaims its own."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The sandbox `spec.resume_id` names when it is reachable, else None — never a fresh one.
        The read seam: a browse of a conversation's files must not answer by provisioning, so a
        container the carrier reclaimed or a sandbox its provider lost reads as absent rather than
        resurrected. The returned handle carries no egress env — a read runs no command that leaves
        the box."""
        ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult: ...

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Write `content` to the absolute workspace `path`, creating parent directories — the
        copy-in that pairs with `read`'s copy-out. Each carrier supplies its own (e2b uploads
        through its filesystem API, docker streams over a real stdin), because bytes must never ride
        `exec`'s argv: a carrier whose command API takes a shell string has to inline them, which
        the provider rejects once they are large — exactly when a caller offloads a large result.

        The path is a filename an agent, a model, or an inbound surface chose, so the write runs
        through the containment guard — `COPY_IN_PROG` where the bytes land inside a container —
        rather than a shell redirect, and a refusal is an OSError. A non-regular target is replaced,
        not refused: the rename cannot write through a link, and a link the agent left at an inbox
        name must not deny every later delivery to that name."""
        ...

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """Stream the workspace file at `path` out of the container in bounded chunks, never
        buffering it whole in the host process — the copy-out that pairs with `write`. Each carrier
        supplies its own (e2b streams from its filesystem API, docker over a real stdout, the local
        carrier off the host directory). A missing path raises FileNotFoundError on every carrier;
        the local carrier confines the path through the containment guard first, so a target that
        is a symlink, a directory, or outside the workspace is refused as a ContainmentError before
        any open, and the docker carrier raises each filesystem refusal as the OSError its errno
        names (resolving cat's reason through strerror) while e2b surfaces its SDK's exception; a
        read that dies for a non-filesystem reason raises the carrier's own error naming what is
        known."""
        ...

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """Everything a caller outside the sandbox needs to reach one in-sandbox `port` — the
        generic inbound seam for a service the turn started inside the container (a browser's CDP
        endpoint, a site's dev-server preview). The returned `DialTarget` carries the authority to
        dial (`host`, `host:port` where the carrier publishes no per-port name), whether that
        authority terminates TLS (`tls` picks the caller's scheme — https/wss or http/ws — so no
        caller guesses), and any header the wire requires (e2b's traffic-access token). A carrier
        with no external route, or whose sandbox is gone or unroutable, raises
        `SandboxUnreachable` — never the provider SDK's own error."""
        ...

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """Run one file op — the windowed read, write, edit, glob, grep and change listing the file
        tools are built on — against the sandbox's workspace, and answer its parsed JSON object.
        The work runs inside the sandbox and comes back bounded, so the host never pulls a whole
        file across the boundary to loop over it. How the op reaches the files is the carrier's:
        one whose sandbox bakes the `sbxfs` CLI answers with `sbxfs_file_op`. A handled failure
        raises `ValueError` — a recoverable tool error to the model — and anything else raises
        `RuntimeError`."""
        ...


@runtime_checkable
class CommandStopping(Protocol):
    """A carrier whose commands outlive the call that launched them, and which can stop them on
    demand. It is separate from `Carrier` because it answers a question only some backends have: one
    that runs a command off-box, where the launch and the wait are two round trips, holds work a
    cancelled `exec` leaves behind, while one whose command dies with the call it was made in has
    nothing to stop.

    Which cancel a carrier is unwinding is not knowable inside `exec` — a member's stop and an
    executor preemption both arrive there as a bare `asyncio.CancelledError`, and DBOS names the
    difference only once the step's body has unwound — so a carrier that implements this leaves the
    command running and the engine issues the stop on a deliberate cancel alone.

    The stop reaches only what `handle.turn_id` launched. One container serves every turn of a
    conversation and every subagent turn that inherited it, so a stop scoped to the container would
    kill the in-flight commands of turns nobody cancelled."""

    async def stop_commands(self, handle: SandboxHandle) -> None: ...


async def sbxfs_file_op(
    carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]
) -> dict[str, object]:
    """`Carrier.file_op` for a sandbox that bakes the `sbxfs` CLI: run the op as one command and
    read its single JSON object off stdout. Shared by every such carrier, so none of them restates
    the argv, the parse, or which failures the model may recover from."""
    result = await carrier.exec(
        handle,
        ("sbxfs", op, json.dumps(params, separators=(",", ":"))),
        timeout_s=DEFAULT_EXEC_TIMEOUT_SECONDS,
    )
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(result.stderr.strip() or f"sbxfs {op} produced no output")
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(result.stderr.strip() or stdout) from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"sbxfs {op} did not return a JSON object")
    failure = parsed.get("error")
    if isinstance(failure, str):
        raise ValueError(failure)
    return parsed


def workspace_path(path: str) -> str:
    """Resolve a tool-supplied path under WORKSPACE_DIR and reject any escape from the subtree —
    the container's mount already scopes the filesystem, this scopes the arguments tools pass."""
    candidate = PurePosixPath(path if path.startswith("/") else f"{WORKSPACE_DIR}/{path}")
    resolved = PurePosixPath(*_resolve_parts(candidate.parts))
    root = PurePosixPath(WORKSPACE_DIR)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path {path!r} escapes {WORKSPACE_DIR}")
    return str(resolved)


def _resolve_parts(parts: tuple[str, ...]) -> list[str]:
    stack: list[str] = []
    for part in parts:
        if part == "..":
            if len(stack) <= 1:
                raise ValueError("path escapes the workspace root")
            stack.pop()
        elif part not in ("", "."):
            stack.append(part)
    return stack


@dataclass(frozen=True)
class SandboxSession:
    """The per-turn handle a tool holds: bash runs in the container through the carrier; file reads
    and writes go through the carrier too, so the same scoping and proxy rules apply whether a byte
    arrives via a shell command or a file op."""

    carrier: Carrier
    handle: SandboxHandle

    def authorize(
        self,
        run_token: str,
        cleared_env: frozenset[str],
        env: Mapping[str, str],
    ) -> "SandboxSession":
        current = self.handle.run_token
        if current is None:
            raise RuntimeError("sandbox handle carries no run token")
        authorized = {
            key: (value.replace(current, run_token) if key in PROXY_ENV_NAMES else value)
            for key, value in self.handle.egress_env.items()
            if key not in cleared_env
        }
        if any(current not in self.handle.egress_env.get(name, "") for name in PROXY_ENV_NAMES):
            raise RuntimeError("sandbox proxy environment does not carry its run token")
        return SandboxSession(
            carrier=self.carrier,
            handle=SandboxHandle(
                conversation_id=self.handle.conversation_id,
                container_id=self.handle.container_id,
                workspace_host_path=self.handle.workspace_host_path,
                run_token=run_token,
                egress_env={**authorized, **env},
                turn_id=self.handle.turn_id,
            ),
        )

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        return await self.carrier.exec(
            self.handle,
            ("bash", "-lc", command),
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_EXEC_TIMEOUT_SECONDS,
        )

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        """Run a POSIX script with `args` as its positional parameters — each one its own argv
        element, so a host-path carrier's `/workspace` rewrite reaches it and no quoting ever
        interpolates it into the script."""
        return await self.carrier.exec(
            self.handle,
            ("sh", "-c", script, "sh", *args),
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_EXEC_TIMEOUT_SECONDS,
        )

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        """Run an in-sandbox python program with the containment guard importable, so a program that
        builds a path from an argument runs the checks `sbxfs` runs rather than its own — the one
        place every such program reaches the guard from.

        The guard is baked beside `sbxfs` rather than installed as a package, so the bootstrap
        locates the scripts through `sbxfs` itself: which directory holds them differs by carrier.
        The interpreter runs isolated (`SANDBOX_PYTHON_FLAG`), which is what keeps that bootstrap
        from resolving against the very workspace it is about to guard. Run as argv, never through a
        login shell, whose profile resets PATH and drops the local carrier's own bin directory."""
        return await self.carrier.exec(
            self.handle,
            ("python3", SANDBOX_PYTHON_FLAG, "-c", f"{SANDBOX_MODULE_BOOTSTRAP}{program}", *args),
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_EXEC_TIMEOUT_SECONDS,
        )

    async def stop_commands(self) -> None:
        """Stop what this turn left running in the container, for a cancel already known to be a
        member's. A carrier whose commands cannot outlive the `exec` that launched them declares no
        stop and needs none — there is nothing left for this to reach."""
        if isinstance(self.carrier, CommandStopping):
            await self.carrier.stop_commands(self.handle)

    async def write_file(self, path: str, content: bytes) -> None:
        await self.carrier.write(self.handle, workspace_path(path), content)

    async def ensure_tool_output_dir(self) -> bool:
        """Guarantee the engine's private `.tool-output` offload dir exists, reclaiming a
        non-directory squatting the name — a bare `mkdir -p` fails `File exists` when a file or
        broken symlink already occupies it, so a member write to that name would otherwise poison
        every later offload. The target is fixed to `TOOL_OUTPUT_DIR`, never a caller-supplied path,
        so this destructive reclaim can only ever touch the engine's own namespace, never member
        data. Returns whether a squatter was reclaimed."""
        result = await self.carrier.exec(
            self.handle,
            (
                "sh",
                "-c",
                'if [ -d "$1" ]; then exit 0; fi; '
                'if [ -e "$1" ] || [ -L "$1" ]; then rm -f "$1" && printf r; fi; '
                'mkdir -p "$1"',
                "sh",
                TOOL_OUTPUT_DIR,
            ),
            timeout_s=30,
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"cannot ensure {TOOL_OUTPUT_DIR}")
        return result.stdout == "r"

    async def file_exists(self, path: str) -> bool:
        target = workspace_path(path)
        result = await self.carrier.exec(
            self.handle, ("sh", "-c", 'test -f "$1"', "sh", target), timeout_s=30
        )
        return result.exit_code == 0

    async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        """Run one in-sandbox file op through the carrier and return its parsed JSON. A `path` arg
        is workspace-scoped here so every op inherits the same subtree guard, and the `workspace`
        root each op confines itself to is set here rather than passed in: which subtree a file op
        may touch is not a caller's choice. Everything below that — how the op runs inside the
        sandbox, and which failure the model may recover from — is `Carrier.file_op`."""
        params = dict(args)
        raw_path = params.get("path")
        if isinstance(raw_path, str):
            params["path"] = workspace_path(raw_path)
        params["workspace"] = WORKSPACE_DIR
        return await self.carrier.file_op(self.handle, op, params)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        """The workspace file's bytes in bounded chunks — how a produced file leaves the container
        without the host process ever holding it whole."""
        return self.carrier.read(self.handle, workspace_path(path))

    async def dial(self, port: int) -> DialTarget:
        """The externally dialable target for an in-sandbox `port`, from the carrier's own
        reachability map — address, TLS, and any header the wire requires (e2b's traffic token)."""
        return await self.carrier.dial(self.handle, port)
