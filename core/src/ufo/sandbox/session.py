"""The sandbox seam: the carrier interface, the per-turn session tools hold, and value objects.

Everything downstream (tools, engine) depends only on this module; the Docker carrier and the
egress proxy implement against it. A deploy swaps the carrier (E2B, remote) without touching a
tool. The invariant the session exists to hold: a tool reaches only the conversation's
`workspace/` subtree, never the transcript or compaction records above it."""

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from ufo.bearer import UFO_TOKEN_SECRET_ENV
from ufo.blob import BlobStore
from ufo.token_signing import SignedTokenError, sign_token, verify_token

WORKSPACE_DIR = "/workspace"
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
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            raise ValueError("proxy authorization is not basic auth")
        try:
            username = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)[0]
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


@dataclass(frozen=True)
class MountSpec:
    """How the conversation's `workspace/` subtree reaches the container, one field set per `kind`:

    - `filesystem`: the carrier bind-mounts the host directory at `host_path` into `/workspace`.
    - `s3`: the carrier mounts the `bucket`/`key_prefix` S3 prefix over s3fs at `/workspace`,
      redeeming the mount-user-only `credential_token` through the sandbox proxy and reaching S3 at
      `s3_url` (`region`, `path_style` shape the s3fs request).

    Either way the container sees only the conversation's `workspace/`, never the transcript above
    it — the bind mount is a sibling directory, the S3 credential is scoped to `workspace/*`."""

    kind: str
    host_path: str | None = None
    bucket: str | None = None
    key_prefix: str | None = None
    credential_token: str | None = None
    s3_url: str | None = None
    region: str | None = None
    path_style: bool = False


EGRESS_CA_CERT_ENV = "UFO_EGRESS_CA_CERT"
EGRESS_CA_KEY_ENV = "UFO_EGRESS_CA_KEY"


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


@dataclass(frozen=True)
class SandboxSpec:
    """`resume_id` is the sandbox id a prior process persisted on the conversation row: when this
    process holds no live sandbox for the conversation, the carrier resumes that id rather than
    opening a fresh sandbox, so a serve restart reattaches instead of stranding it. None means
    create fresh (no stored handle, or one another backend wrote). A carrier that resumes by
    conversation identity (docker's container name, the local host directory) ignores it."""

    conversation_id: UUID
    image_ref: str
    mount: MountSpec
    proxy: ProxyEndpoint
    run_token: str
    resume_id: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a created-or-attached container; the carrier reads it, not tools. It
    carries the workspace `mount` so the carrier can stream a produced file straight out of the
    mount on `export` without a whole-file read across the boundary, the `traffic_token` a
    carrier that gates its public per-port host behind one (e2b) sets at create so a caller dialing
    `host` carries it as a connection header, and the base `run_token` plus `egress_env` a scoped
    session rewrites for each exec. A container shared across turns never pins either one's
    authority."""

    conversation_id: UUID
    container_id: str
    mount: MountSpec | None = None
    traffic_token: str | None = None
    run_token: str | None = None
    egress_env: Mapping[str, str] = field(default_factory=dict)


SANDBOX_HANDLE_SEP = ":"


def format_sandbox_handle(backend: str, container_id: str) -> str:
    """The durable `<backend>:<id>` a conversation row carries so a later process resumes the same
    sandbox from the id and the reaper reclaims one a prior process created. The backend prefix is
    load-bearing: a deploy that switched carriers must not resume or reap another backend's id."""
    return f"{backend}{SANDBOX_HANDLE_SEP}{container_id}"


def sandbox_handle_id(backend: str, value: str) -> str | None:
    """The sandbox id inside a stored handle when it belongs to `backend`, else None — a handle
    another backend wrote is not this carrier's to resume or reap."""
    prefix = f"{backend}{SANDBOX_HANDLE_SEP}"
    return value[len(prefix) :] if value.startswith(prefix) else None


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int


class Carrier(Protocol):
    """Create-or-attach a per-conversation container, run commands in it, reclaim it. The container
    is disposable cache over the durable workspace — destroy and recreate between turns costs
    latency, never state."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult: ...

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Write `content` to the absolute workspace `path`, creating parent directories — the
        copy-in that pairs with `export`'s copy-out. Each carrier supplies its own (e2b uploads
        through its filesystem API, docker streams over a real stdin), because bytes must never ride
        `exec`'s argv: a carrier whose command API takes a shell string has to inline them, which
        the provider rejects once they are large — exactly when a caller offloads a large result."""
        ...

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        """Stream a workspace file straight into `blob` under `key`, never buffering it whole in the
        host process — the large-attachment path that must not hit the bounded in-memory read. Each
        carrier supplies its own copy-out (the Docker carrier reads its bind mount; a remote carrier
        streams from its own API)."""
        ...

    async def destroy(self, handle: SandboxHandle) -> None: ...

    async def host(self, handle: SandboxHandle, port: int) -> str:
        """The externally-reachable `host` (optionally `host:port`) a caller outside the sandbox
        dials to reach any in-sandbox `port` — the generic inbound seam for a service the turn
        started inside the container (a browser's CDP endpoint, a site's dev-server preview). Each
        carrier maps its own reachability (e2b's public per-port host); a carrier with no external
        route raises."""
        ...


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
                mount=self.handle.mount,
                traffic_token=self.handle.traffic_token,
                run_token=run_token,
                egress_env={**authorized, **env},
            ),
        )

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        return await self.carrier.exec(
            self.handle,
            ("bash", "-lc", command),
            timeout_s=timeout_s if timeout_s is not None else DEFAULT_EXEC_TIMEOUT_SECONDS,
        )

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
        """Run one in-sandbox file op through the `sbxfs` CLI and return its parsed JSON. The work
        (windowing, ripgrep, poppler render) runs inside the container and comes back as one bounded
        JSON object, so the host never pulls a whole file across the boundary to loop over it. A
        `path` arg is workspace-scoped here so every op inherits the same subtree guard. A handled
        `{"error": …}` surfaces as a ValueError — a recoverable tool error to the model."""
        params = dict(args)
        raw_path = params.get("path")
        if isinstance(raw_path, str):
            params["path"] = workspace_path(raw_path)
        result = await self.carrier.exec(
            self.handle,
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

    async def export_file(self, path: str, blob: BlobStore, key: str) -> None:
        await self.carrier.export(self.handle, workspace_path(path), blob, key)

    async def host(self, port: int) -> str:
        """The externally-reachable host for an in-sandbox `port`, from the carrier's own
        reachability map — how a caller in the serve process dials any service this turn started
        inside the container (a browser's CDP endpoint, a site's dev-server preview)."""
        return await self.carrier.host(self.handle, port)

    @property
    def traffic_token(self) -> str | None:
        """The carrier's per-sandbox traffic token when it gates the public per-port host behind
        one (e2b), else None — carried as a connection header by a caller dialing `host`."""
        return self.handle.traffic_token
