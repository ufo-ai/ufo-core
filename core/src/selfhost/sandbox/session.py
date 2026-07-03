"""The sandbox seam: the carrier interface, the per-turn session tools hold, and value objects.

Everything downstream (tools, engine) depends only on this module; the Docker carrier and the
egress proxy implement against it. A deploy swaps the carrier (E2B, remote) without touching a
tool. The invariant the session exists to hold: a tool reaches only the conversation's
`workspace/` subtree, never the transcript or compaction records above it."""

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

WORKSPACE_DIR = "/workspace"
DEFAULT_EXEC_TIMEOUT_SECONDS = 120
SENTINEL_MODEL_KEY = "SELFHOST_SENTINEL_MODEL_KEY"


@dataclass(frozen=True, slots=True)
class RunToken:
    """Attributes a sandbox egress request to the turn that made it. Minted per turn, carried as the
    proxy basic-auth username in the container's HTTP(S)_PROXY URL (`http://<token>:@host:port`),
    and recovered by the egress proxy from the `Proxy-Authorization` header so a metered request
    keys its ledger row to (workspace, turn). The encoding is base64url of `workspace_id/turn_id`,
    whose alphabet is URL-safe, so the token drops straight into the URL's userinfo unescaped."""

    workspace_id: UUID
    turn_id: UUID

    def encode(self) -> str:
        raw = f"{self.workspace_id}/{self.turn_id}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def from_proxy_auth(cls, header: str) -> "RunToken":
        """Recover the turn from a `Proxy-Authorization: Basic …` header — the basic-auth username
        is the run token. Raises on a missing, non-basic, or malformed header: an unattributed
        metered request is a wiring fault, surfaced loud, never a silently dropped ledger row."""
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            raise ValueError("proxy authorization is not basic auth")
        username = base64.b64decode(encoded).decode("utf-8", "replace").split(":", 1)[0]
        padded = username + "=" * (-len(username) % 4)
        workspace, _, turn = base64.urlsafe_b64decode(padded).decode("utf-8").partition("/")
        if not workspace or not turn:
            raise ValueError(f"invalid run token {username!r}")
        return cls(workspace_id=UUID(workspace), turn_id=UUID(turn))


@dataclass(frozen=True)
class MountSpec:
    """How the conversation's `workspace/` subtree reaches the container: the carrier bind-mounts
    the host directory at `host_path` into `/workspace`, so the container sees only `workspace/`,
    never the transcript above it."""

    kind: str
    host_path: str | None = None


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where the egress proxy listens, backend-neutral: the port plus the CA the sandbox trusts so
    the proxy can terminate TLS and swap sentinels onto the wire. Each carrier decides how its
    sandbox addresses the host the proxy runs on — that reachability detail is the carrier's, not
    the proxy's."""

    port: int
    ca_cert: str


@dataclass(frozen=True)
class SandboxSpec:
    conversation_id: UUID
    image_ref: str
    mount: MountSpec
    proxy: ProxyEndpoint
    run_token: str


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to a created-or-attached container; the carrier reads it, not tools."""

    conversation_id: UUID
    container_id: str


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
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...


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

    async def bash(
        self, command: str, timeout_s: int = DEFAULT_EXEC_TIMEOUT_SECONDS
    ) -> ExecResult:
        return await self.carrier.exec(
            self.handle, ("bash", "-lc", command), stdin=b"", timeout_s=timeout_s
        )

    async def read_file(self, path: str) -> bytes:
        result = await self.carrier.exec(
            self.handle, ("cat", "--", workspace_path(path)), stdin=b"", timeout_s=30
        )
        if result.exit_code != 0:
            raise FileNotFoundError(result.stderr.strip() or path)
        return result.stdout.encode()

    async def write_file(self, path: str, content: bytes) -> None:
        target = workspace_path(path)
        result = await self.carrier.exec(
            self.handle,
            ("sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", target),
            stdin=content,
            timeout_s=30,
        )
        if result.exit_code != 0:
            raise OSError(result.stderr.strip() or f"write failed: {path}")


SandboxFactory = Callable[[UUID], Awaitable[SandboxSession]]
