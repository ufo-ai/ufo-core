import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    ExecResult,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema.records import Agent, Turn
from ufo.tools.builtins import BashInput, bash_handler
from ufo.tools.context import SpawnResult, ToolContext

RUN_TOKENS = RunTokenCodec(b"run-token-test-secret")


def _basic(username: str) -> str:
    """The Proxy-Authorization header an HTTP client sends for `http://<username>:@host` — Basic
    base64 of `username:` (empty password), exactly what the container's proxy URL produces."""
    return "Basic " + base64.b64encode(f"{username}:".encode()).decode()


def test_run_token_round_trips_encode_then_proxy_auth() -> None:
    token = RunToken(workspace_id=uuid4(), turn_id=uuid4(), acting_member_id=uuid4())
    assert RUN_TOKENS.from_proxy_auth(_basic(RUN_TOKENS.encode(token))) == token


def test_encoded_token_is_url_safe_userinfo() -> None:
    encoded = RUN_TOKENS.encode(RunToken(workspace_id=uuid4(), turn_id=uuid4()))
    assert all(char.isalnum() or char in "-_." for char in encoded)


def test_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("Bearer " + RUN_TOKENS.encode(RunToken(uuid4(), uuid4())))


def test_from_proxy_auth_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("")


def test_from_proxy_auth_rejects_a_malformed_run_token() -> None:
    with pytest.raises(ValueError):
        RUN_TOKENS.from_proxy_auth(_basic("not-a-run-token"))


def test_run_token_rejects_a_valid_shape_signed_by_another_deployment() -> None:
    run = RunToken(uuid4(), uuid4())
    forged = RunTokenCodec(b"other-deployment").encode(run)
    with pytest.raises(ValueError, match="signed"):
        RUN_TOKENS.from_proxy_auth(_basic(forged))


def test_authorized_session_scopes_proxy_and_cli_environment_without_mutating_base() -> None:
    conversation_id = uuid4()
    common = RUN_TOKENS.encode(RunToken(uuid4(), uuid4()))
    member = RUN_TOKENS.encode(RunToken(uuid4(), uuid4(), uuid4()))
    proxy = f"http://{common}:@proxy:9000"
    base = SandboxSession(
        carrier=_RecordingCarrier(),
        handle=SandboxHandle(
            conversation_id=conversation_id,
            container_id="c",
            run_token=common,
            egress_env={
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
                "ALICE_KEY": "alice",
                "UNRELATED": common,
            },
        ),
    )

    authorized = base.authorize(
        member,
        frozenset(("ALICE_KEY", "BOB_KEY")),
        {"BOB_KEY": "bob"},
    )

    assert authorized.handle.run_token == member
    assert all(
        member in authorized.handle.egress_env[name]
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    )
    assert authorized.handle.egress_env["UNRELATED"] == common
    assert "ALICE_KEY" not in authorized.handle.egress_env
    assert authorized.handle.egress_env["BOB_KEY"] == "bob"
    assert base.handle.run_token == common
    assert base.handle.egress_env["HTTP_PROXY"] == proxy


class _RecordingCarrier:
    """Records the exec timeout so the bash tool's ms→s conversion and cap can be asserted."""

    def __init__(self) -> None:
        self.timeouts: list[int] = []

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise NotImplementedError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
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
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


async def test_bash_timeout_is_milliseconds_capped_and_converted(tmp_path: Path) -> None:
    """Source-faithful: bash `timeout` is milliseconds (max 600000). The handler caps then converts
    to the carrier's seconds; an omitted timeout falls back to the default budget."""
    carrier = _RecordingCarrier()
    ctx = _bash_ctx(carrier, tmp_path)
    await bash_handler(
        ctx, BashInput(command="echo hi", timeout=5000, user_description="checking the box")
    )
    await bash_handler(
        ctx, BashInput(command="echo hi", timeout=9_000_000, user_description="checking the box")
    )
    await bash_handler(ctx, BashInput(command="echo hi", user_description="checking the box"))
    assert carrier.timeouts == [5, 600, DEFAULT_EXEC_TIMEOUT_SECONDS]
