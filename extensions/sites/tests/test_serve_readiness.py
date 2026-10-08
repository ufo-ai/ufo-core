"""What a start that never listens tells the caller, decided over a real sandbox: a real detached
task, the real readiness probe, a real log on disk."""

import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from ufo_ext_sites import tools
from ufo_ext_sites.tools import PUBLISH_LOG, ServeFailed, _serve, _stop_server

from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import SandboxSession, SandboxSpec
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

PROBE_DEADLINE_SECONDS = 3
SILENT_SERVER = """import socket
import sys
import time

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen(8)
time.sleep(300)
"""


async def _no_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("a start must not spawn")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _context(workspace: Path, tmp_path: Path) -> ToolContext:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="unused",
            workspace_host_path=str(workspace),
        )
    )
    return ToolContext(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 9, 3, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


async def test_a_command_that_binds_the_exported_port_is_served(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "index.html").write_text("<!doctype html><title>hi</title>")
    ctx = await _context(workspace, tmp_path)
    port = _free_port()
    log = await ctx.sandbox.runtime_path(PUBLISH_LOG.format(port=port))
    try:
        served = await _serve(
            ctx,
            'python3 -m http.server "$PORT" --bind 127.0.0.1',
            str(workspace),
            port,
            log,
        )
        assert served["port"] == port
        assert served["url"] == f"http://localhost:{port}"
    finally:
        await _stop_server(ctx, port)


async def test_a_command_that_binds_another_port_names_the_port_it_had_to_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "READINESS_TIMEOUT_SECONDS", PROBE_DEADLINE_SECONDS)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "silent_server.py").write_text(SILENT_SERVER)
    ctx = await _context(workspace, tmp_path)
    port = _free_port()
    elsewhere = _free_port()
    log = await ctx.sandbox.runtime_path(PUBLISH_LOG.format(port=port))
    try:
        with pytest.raises(ServeFailed) as raised:
            await _serve(
                ctx,
                f"python3 silent_server.py {elsewhere}",
                str(workspace),
                port,
                log,
            )
        failure = raised.value.failure
        assert str(port) in failure.summary
        assert "$PORT" in failure.summary
        assert log in failure.summary
        assert failure.command is not None
        assert failure.command.exit_code != 0
        assert failure.applied[0].kind == "port"
        assert failure.applied[0].identity == str(port)
    finally:
        await _stop_server(ctx, elsewhere)


async def test_what_the_command_printed_before_dying_is_what_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "READINESS_TIMEOUT_SECONDS", PROBE_DEADLINE_SECONDS)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = await _context(workspace, tmp_path)
    port = _free_port()
    log = await ctx.sandbox.runtime_path(PUBLISH_LOG.format(port=port))
    with pytest.raises(ServeFailed) as raised:
        await _serve(
            ctx,
            "echo 'ModuleNotFoundError: no module named flask' >&2; exit 1",
            str(workspace),
            port,
            log,
        )
    failure = raised.value.failure
    assert "ModuleNotFoundError: no module named flask" in failure.summary
    assert "$PORT" not in failure.summary
    assert failure.command is not None
    assert failure.command.exit_code != 0
