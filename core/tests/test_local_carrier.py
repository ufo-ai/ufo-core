"""The core default carrier against real subprocesses in a temp-dir workspace.

The local carrier has no fake to stand in for — it runs the host's own shell, so these drive it
end to end: create the workspace, exec a command that writes into it, rewrite the logical
`/workspace` path, carry the egress environment, stream a file out, and confine export to the
workspace. The last test proves the payoff — the sbxfs-backed file tools run through the default
carrier with only a Python interpreter present, no container."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.local import EXEC_TIMEOUT_CODE, LOCAL_CONTAINER_ID, LocalCarrier
from selfhost.sandbox.session import (
    SENTINEL_MODEL_KEY,
    MountSpec,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)

RUN_TOKEN = "run-token-abc"
PROXY_PORT = 9999


def _spec(workspace: Path) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=uuid4(),
        image_ref="selfhost-sandbox:latest",
        mount=MountSpec(kind="filesystem", host_path=str(workspace)),
        proxy=ProxyEndpoint(port=PROXY_PORT, ca_cert="CA-PEM-BYTES"),
        run_token=RUN_TOKEN,
    )


async def test_create_exec_export_and_destroy_in_a_temp_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    assert handle.container_id == LOCAL_CONTAINER_ID
    assert workspace.is_dir()

    result = await carrier.exec(
        handle, ("bash", "-lc", "echo hi > out.txt && printf done"), b"", 30
    )
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert (workspace / "out.txt").read_text() == "hi\n"

    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    await carrier.export(handle, "/workspace/out.txt", blob, "exports/out.txt")
    assert await blob.get("exports/out.txt") == b"hi\n"

    await carrier.destroy(handle)


async def test_exec_rewrites_the_logical_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    await carrier.exec(handle, ("bash", "-lc", "printf x > /workspace/w.txt"), b"", 30)

    assert (workspace / "w.txt").read_text() == "x"


async def test_exec_carries_the_egress_environment(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(
        handle, ("bash", "-lc", 'printf "%s|%s" "$HTTPS_PROXY" "$ANTHROPIC_API_KEY"'), b"", 30
    )

    proxy, sentinel = result.stdout.split("|")
    assert proxy == f"http://{RUN_TOKEN}:@127.0.0.1:{PROXY_PORT}"
    assert sentinel == SENTINEL_MODEL_KEY


async def test_exec_pipes_stdin(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(handle, ("bash", "-lc", "cat"), b"piped-in", 30)

    assert result.stdout == "piped-in"


async def test_exec_maps_a_timeout_to_the_timeout_code(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 5"), b"", 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE


async def test_export_confines_to_the_workspace(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))
    blob = FilesystemBlobStore(root=tmp_path / "blobs")

    with pytest.raises(ValueError):
        await carrier.export(handle, "/etc/passwd", blob, "leak")


async def test_file_tools_run_through_sbxfs_locally(tmp_path: Path) -> None:
    """The default carrier ships sbxfs on the command PATH, so the file tools work with only a
    Python interpreter present: a write lands in the workspace and the sbxfs read reflects it."""
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))
    session = SandboxSession(carrier=carrier, handle=handle)

    await session.write_file("notes.txt", b"alpha\nbeta\n")
    assert await session.file_exists("notes.txt")

    read = await session.run_sbxfs("read", {"path": "notes.txt"})
    assert "alpha" in json.dumps(read)
