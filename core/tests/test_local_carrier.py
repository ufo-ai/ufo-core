"""The core default carrier against real subprocesses in a temp-dir workspace.

The local carrier has no fake to stand in for — it runs the host's own shell, so these drive it
end to end: create the workspace, exec a command that writes into it, rewrite the logical
`/workspace` path, carry the egress environment, reach a service on the sandbox's own loopback,
stream a file out in bounded chunks, and confine that read to the workspace. The last test proves
the payoff — the sbxfs-backed file tools run through the default carrier with only a Python
interpreter present, no container."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ufo.sandbox.local import (
    EXEC_TIMEOUT_CODE,
    LOCAL_CONTAINER_ID,
    READ_CHUNK_BYTES,
    LocalCarrier,
)
from ufo.sandbox.session import (
    SENTINEL_MODEL_KEY,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)

RUN_TOKEN = "run-token-abc"
PROXY_PORT = 9999
LOOPBACK_PROBE = (
    "import urllib.request;"
    "print(urllib.request.urlopen('http://127.0.0.1:{port}/json/version', timeout=2).read())"
)


def _spec(workspace: Path) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path=str(workspace),
        proxy=ProxyEndpoint(port=PROXY_PORT, ca_cert="CA-PEM-BYTES"),
        run_token=RUN_TOKEN,
    )


async def test_create_exec_and_read_in_a_temp_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    assert handle.container_id == LOCAL_CONTAINER_ID
    assert workspace.is_dir()

    result = await carrier.exec(handle, ("bash", "-lc", "echo hi > out.txt && printf done"), 30)
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert (workspace / "out.txt").read_text() == "hi\n"

    out = [chunk async for chunk in carrier.read(handle, "/workspace/out.txt")]
    assert b"".join(out) == b"hi\n"


async def test_read_streams_a_large_file_in_bounded_chunks(tmp_path: Path) -> None:
    """A workspace read streams through the carrier in bounded chunks, so an arbitrarily large
    produced file crosses without a whole-file buffer forming here."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    payload = bytes(range(256)) * (READ_CHUNK_BYTES // 128)
    (workspace / "big.bin").write_bytes(payload)

    chunks = [chunk async for chunk in carrier.read(handle, "/workspace/big.bin")]

    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= READ_CHUNK_BYTES
    assert b"".join(chunks) == payload


async def test_read_of_an_absent_file_raises(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in carrier.read(handle, "/workspace/absent.bin")]


async def test_exec_rewrites_the_logical_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    await carrier.exec(handle, ("bash", "-lc", "printf x > /workspace/w.txt"), 30)

    assert (workspace / "w.txt").read_text() == "x"


async def test_exec_carries_the_egress_environment(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(
        handle, ("bash", "-lc", 'printf "%s|%s" "$HTTPS_PROXY" "$ANTHROPIC_API_KEY"'), 30
    )

    proxy, sentinel = result.stdout.split("|")
    assert proxy == f"http://{RUN_TOKEN}:@127.0.0.1:{PROXY_PORT}"
    assert sentinel == SENTINEL_MODEL_KEY


async def test_exec_reaches_a_service_on_the_sandbox_loopback(tmp_path: Path) -> None:
    """A proxy-aware client in the sandbox reaches a service the turn started on the sandbox's own
    loopback. The proxy admits only globally routable destinations, so a loopback request routed to
    it can only be refused — without the exemption an in-sandbox service (Chrome's DevTools port,
    a dev-server preview) is unreachable from inside the box that runs it."""
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nlive!")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await carrier.exec(handle, ("python3", "-c", LOOPBACK_PROBE.format(port=port)), 30)

    assert result.exit_code == 0, result.stderr
    assert "live!" in result.stdout


async def test_write_creates_parents_for_a_payload_too_large_for_a_command_line(
    tmp_path: Path,
) -> None:
    carrier = LocalCarrier()
    workspace = tmp_path / "workspace"
    handle = await carrier.create(_spec(workspace))
    content = b"x" * (2 * 1024 * 1024)

    await carrier.write(handle, "/workspace/nested/deeper/big.txt", content)

    assert (workspace / "nested" / "deeper" / "big.txt").read_bytes() == content


async def test_exec_maps_a_timeout_to_the_timeout_code(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 5"), 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE


async def test_read_confines_to_the_workspace(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(ValueError):
        [chunk async for chunk in carrier.read(handle, "/etc/passwd")]


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


async def test_ensure_tool_output_dir_creates_the_directory_when_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (workspace / ".tool-output").is_dir()


async def test_ensure_tool_output_dir_reclaims_a_file_squatting_the_name(tmp_path: Path) -> None:
    """The poison-pill case: a member write left a regular file where the engine's offload
    directory must be, so a bare `mkdir -p` would fail `File exists` on every later offload. The
    ensure reclaims it to a directory and reports the reclaim."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file(".tool-output", b"squatter")
    assert (workspace / ".tool-output").is_file()

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is True
    assert (workspace / ".tool-output").is_dir()
    await session.write_file(".tool-output/call.txt", b"offloaded")
    assert (workspace / ".tool-output" / "call.txt").read_text() == "offloaded"


async def test_ensure_tool_output_dir_leaves_an_existing_directory_and_its_contents(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file(".tool-output/kept.txt", b"keep me")

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (workspace / ".tool-output" / "kept.txt").read_text() == "keep me"


async def test_exec_env_rides_the_handle_not_the_conversation(tmp_path: Path) -> None:
    """Two turns can hold one conversation's workspace at once (a subagent beside its parent): each
    exec runs under its own handle's run token and the spec's per-turn sentinel entries, so a later
    create never re-points an earlier turn's egress attribution."""
    carrier = LocalCarrier()
    conversation = uuid4()
    base = replace(_spec(tmp_path / "workspace"), conversation_id=conversation)
    first = await carrier.create(replace(base, run_token="turn-a"))
    second = await carrier.create(replace(base, run_token="turn-b", env={"GH_TOKEN": "sent-b"}))

    probe = ("bash", "-lc", 'printf "%s|%s" "$HTTPS_PROXY" "${GH_TOKEN:-none}"')
    result_a = await carrier.exec(first, probe, 30)
    result_b = await carrier.exec(second, probe, 30)

    assert result_a.stdout == f"http://turn-a:@127.0.0.1:{PROXY_PORT}|none"
    assert result_b.stdout == f"http://turn-b:@127.0.0.1:{PROXY_PORT}|sent-b"


async def test_background_descendant_keeps_its_authority_across_later_execs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    base = SandboxSession(carrier=carrier, handle=await carrier.create(_spec(workspace)))
    first = base.authorize(
        "member-a",
        frozenset(("GH_TOKEN",)),
        {"GH_TOKEN": "sent-a"},
    )
    second = base.authorize(
        "member-b",
        frozenset(("GH_TOKEN",)),
        {"GH_TOKEN": "sent-b"},
    )

    launched = await first.bash(
        "nohup sh -c 'while [ ! -f release ]; do :; done; "
        'printf "%s|%s" "$HTTPS_PROXY" "$GH_TOKEN" > first.txt\' '
        ">/dev/null 2>&1 &"
    )
    assert launched.exit_code == 0
    written = await second.bash(
        'printf "%s|%s" "$HTTPS_PROXY" "$GH_TOKEN" > second.txt; '
        "touch release; while [ ! -f first.txt ]; do :; done"
    )
    assert written.exit_code == 0

    assert (workspace / "first.txt").read_text() == (
        f"http://member-a:@127.0.0.1:{PROXY_PORT}|sent-a"
    )
    assert (workspace / "second.txt").read_text() == (
        f"http://member-b:@127.0.0.1:{PROXY_PORT}|sent-b"
    )
