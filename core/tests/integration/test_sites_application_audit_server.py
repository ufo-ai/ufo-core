from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from ufo_ext_docker import DockerCarrier
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.application_audit import APPLICATION_AUDIT_SERVER
from ufo_ext_sites.tools import _serve, _stop_server

from evals.sandbox_image import SandboxImagePlan
from sandbox.build_template import build_definition_digest
from ufo.sandbox.session import SandboxHandle, SandboxSession
from ufo.tools.context import ToolContext

pytestmark = pytest.mark.docker

DESIGN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
FETCH = """import sys
import urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
    sys.stdout.buffer.write(str(response.status).encode() + b"\\n" + response.read())
"""
REFUSED = """import sys
import urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=1)
except OSError:
    raise SystemExit(7)
raise SystemExit(0)
"""
TASK_STOPPED = """pid=$(cat "$1.pid") || exit 2
if kill -0 "$pid" 2>/dev/null; then exit 3; fi
test -s "$1.exit"
"""
TASK_PID = 'cat "$1.pid"'


def _task_base(key: str, port: int, log_path: str) -> str:
    identity = f"{key}:{port}:{log_path}"
    return (
        f"/workspace/runtime/tool-output/server-tasks/{sha256(identity.encode()).hexdigest()[:16]}"
    )


@dataclass(frozen=True)
class _Context:
    sandbox: SandboxSession
    idempotency_key: str


def test_application_eval_image_runs_the_detached_task_protocol(
    sandbox_image: str, tmp_path: Path
) -> None:
    SandboxImagePlan(
        tmp_path / "unused.Dockerfile",
        sandbox_image,
        build_definition_digest(None),
    ).prepare()


async def test_detached_application_audit_server_survives_its_start_exec(
    sandbox_container: tuple[str, Path], unused_tcp_port: int
) -> None:
    container, workspace = sandbox_container
    carrier = DockerCarrier()
    handle = SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        workspace_host_path=str(workspace),
        runtime_root="/workspace/runtime",
    )
    sandbox = SandboxSession(carrier=carrier, handle=handle)
    ctx = cast(ToolContext, _Context(sandbox=sandbox, idempotency_key="sites-audit-server"))
    await carrier.exec(handle, ("mkdir", "-p", "/workspace/runtime", "/workspace/site"), 30)
    await carrier.write(handle, "/workspace/server.py", APPLICATION_AUDIT_SERVER)
    await carrier.write(handle, "/workspace/accepted-design.svg", DESIGN)
    command = (
        f"python3 /workspace/server.py /workspace/site {unused_tcp_port} "
        "/workspace/accepted-design.svg"
    )
    url = f"http://localhost:{unused_tcp_port}/accepted-design.svg"

    try:
        served = await _serve(
            ctx, command, "/workspace/site", unused_tcp_port, "/workspace/runtime/audit.log"
        )
        fetched = await carrier.exec(handle, ("python3", "-c", FETCH, url), 30)
    finally:
        await _stop_server(ctx, unused_tcp_port)

    refused = await carrier.exec(handle, ("python3", "-c", REFUSED, url), 30)
    assert served["url"] == f"http://localhost:{unused_tcp_port}"
    assert fetched.exit_code == 0, fetched.stderr
    assert fetched.stdout.encode() == b"200\n" + DESIGN
    assert refused.exit_code == 7


async def test_a_second_start_on_one_identity_serves_again(
    sandbox_container: tuple[str, Path], unused_tcp_port: int
) -> None:
    """A turn may audit its application three times, and every audit starts the server on the same
    port and log, so the second start resolves the first start's task journal. `ufo run --task`
    reattaches to a journal that holds a pid instead of launching, so this is what proves the second
    start leaves a server answering rather than a finished run adopted."""
    container, workspace = sandbox_container
    carrier = DockerCarrier()
    handle = SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        workspace_host_path=str(workspace),
        runtime_root="/workspace/runtime",
    )
    sandbox = SandboxSession(carrier=carrier, handle=handle)
    key = "sites-audit-server-restart"
    ctx = cast(ToolContext, _Context(sandbox=sandbox, idempotency_key=key))
    log_path = "/workspace/runtime/restart.log"
    task_base = _task_base(key, unused_tcp_port, log_path)
    await carrier.exec(handle, ("mkdir", "-p", "/workspace/runtime", "/workspace/site"), 30)
    await carrier.write(handle, "/workspace/server.py", APPLICATION_AUDIT_SERVER)
    await carrier.write(handle, "/workspace/accepted-design.svg", DESIGN)
    command = (
        f"python3 /workspace/server.py /workspace/site {unused_tcp_port} "
        "/workspace/accepted-design.svg"
    )
    url = f"http://localhost:{unused_tcp_port}/accepted-design.svg"

    try:
        await _serve(ctx, command, "/workspace/site", unused_tcp_port, log_path)
        first_pid = await carrier.exec(handle, ("sh", "-c", TASK_PID, "sh", task_base), 30)
        served = await _serve(ctx, command, "/workspace/site", unused_tcp_port, log_path)
        second_pid = await carrier.exec(handle, ("sh", "-c", TASK_PID, "sh", task_base), 30)
        fetched = await carrier.exec(handle, ("python3", "-c", FETCH, url), 30)
    finally:
        await _stop_server(ctx, unused_tcp_port)

    assert served["url"] == f"http://localhost:{unused_tcp_port}"
    assert first_pid.exit_code == 0, first_pid.stderr
    assert second_pid.exit_code == 0, second_pid.stderr
    assert second_pid.stdout.strip() != first_pid.stdout.strip()
    assert fetched.exit_code == 0, fetched.stderr
    assert fetched.stdout.encode() == b"200\n" + DESIGN


async def test_failed_readiness_stops_the_detached_task(
    sandbox_container: tuple[str, Path], unused_tcp_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    container, workspace = sandbox_container
    carrier = DockerCarrier()
    handle = SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        workspace_host_path=str(workspace),
        runtime_root="/workspace/runtime",
    )
    sandbox = SandboxSession(carrier=carrier, handle=handle)
    key = "sites-audit-never-ready"
    ctx = cast(ToolContext, _Context(sandbox=sandbox, idempotency_key=key))
    log_path = "/workspace/runtime/never-ready.log"
    task_base = _task_base(key, unused_tcp_port, log_path)
    await carrier.exec(handle, ("mkdir", "-p", "/workspace/runtime", "/workspace/site"), 30)
    monkeypatch.setattr(sites_tools, "READINESS_TIMEOUT_SECONDS", 1)

    try:
        with pytest.raises(RuntimeError):
            await _serve(
                ctx,
                "while :; do sleep 1; done",
                "/workspace/site",
                unused_tcp_port,
                log_path,
            )
        stopped = await carrier.exec(handle, ("sh", "-c", TASK_STOPPED, "sh", task_base), 30)
        assert stopped.exit_code == 0, stopped.stderr
    finally:
        await carrier.exec(
            handle,
            ("sh", "-c", 'kill "$(cat "$1.pid")" 2>/dev/null || true', "sh", task_base),
            30,
        )
