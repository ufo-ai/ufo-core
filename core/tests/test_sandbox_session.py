import base64
from pathlib import Path
from uuid import uuid4

import pytest

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    ExecResult,
    RunToken,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BashInput, bash_handler
from selfhost.tools.context import SpawnResult, ToolContext


def _basic(username: str) -> str:
    """The Proxy-Authorization header an HTTP client sends for `http://<username>:@host` — Basic
    base64 of `username:` (empty password), exactly what the container's proxy URL produces."""
    return "Basic " + base64.b64encode(f"{username}:".encode()).decode()


def test_run_token_round_trips_encode_then_proxy_auth() -> None:
    token = RunToken(workspace_id=uuid4(), turn_id=uuid4())
    assert RunToken.from_proxy_auth(_basic(token.encode())) == token


def test_encoded_token_is_url_safe_userinfo() -> None:
    encoded = RunToken(workspace_id=uuid4(), turn_id=uuid4()).encode()
    assert all(char.isalnum() or char in "-_" for char in encoded)


def test_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        RunToken.from_proxy_auth("Bearer " + RunToken(uuid4(), uuid4()).encode())


def test_from_proxy_auth_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="basic"):
        RunToken.from_proxy_auth("")


def test_from_proxy_auth_rejects_a_malformed_run_token() -> None:
    with pytest.raises(ValueError):
        RunToken.from_proxy_auth(_basic("not-a-run-token"))


class _RecordingCarrier:
    """Records the exec timeout so the bash tool's ms→s conversion and cap can be asserted."""

    def __init__(self) -> None:
        self.timeouts: list[int] = []

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise NotImplementedError

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def export(self, handle: SandboxHandle, path: str, blob: object, key: str) -> None:
        raise NotImplementedError

    async def destroy(self, handle: SandboxHandle) -> None:
        raise NotImplementedError


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn unused")


def _bash_ctx(carrier: _RecordingCarrier, tmp_path: Path) -> ToolContext:
    session = SandboxSession(
        carrier=carrier, handle=SandboxHandle(conversation_id=uuid4(), container_id="c")
    )
    return ToolContext(
        sandbox=session,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        member_id=None,
        artifact_token_secret="",
    )


async def test_bash_timeout_is_milliseconds_capped_and_converted(tmp_path: Path) -> None:
    """Source-faithful: bash `timeout` is milliseconds (max 600000). The handler caps then converts
    to the carrier's seconds; an omitted timeout falls back to the default budget."""
    carrier = _RecordingCarrier()
    ctx = _bash_ctx(carrier, tmp_path)
    await bash_handler(ctx, BashInput(command="echo hi", timeout=5000))
    await bash_handler(ctx, BashInput(command="echo hi", timeout=9_000_000))
    await bash_handler(ctx, BashInput(command="echo hi"))
    assert carrier.timeouts == [5, 600, DEFAULT_EXEC_TIMEOUT_SECONDS]
