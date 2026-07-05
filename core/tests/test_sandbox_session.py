import base64
from uuid import uuid4

import pytest

from selfhost.blob import BlobStore
from selfhost.sandbox.session import (
    ExecResult,
    RunToken,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)


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
    """Records the exec timeout so the session's default-timeout wiring can be asserted."""

    def __init__(self) -> None:
        self.timeouts: list[int] = []

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise NotImplementedError

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        raise NotImplementedError

    async def destroy(self, handle: SandboxHandle) -> None:
        raise NotImplementedError


async def test_bash_uses_the_sessions_exec_timeout_unless_a_call_names_its_own() -> None:
    """An agent `bash` call names no timeout, so it takes the session's configured ceiling (from
    `[sandbox] exec_timeout_seconds`); an internal caller that passes one still overrides it."""
    carrier = _RecordingCarrier()
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
        exec_timeout=240,
    )
    await session.bash("echo hi")
    await session.bash("echo hi", timeout_s=5)
    assert carrier.timeouts == [240, 5]
