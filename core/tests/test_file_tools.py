"""End-to-end proof of the sbxfs-backed file tools against a real container: the built sandbox
image, the real `sbxfs` CLI on PATH, real ripgrep/poppler, and — for `share_file` — a real host
bind mount the carrier streams out of. These are Docker-gated like test_sandbox_session; nothing
here asserts a fake."""

import base64
import hashlib
import json
import shutil
import struct
import subprocess
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from selfhost.artifact_token import verify_artifact_token
from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.sandbox import session as session_module
from selfhost.sandbox.carrier import DockerCarrier
from selfhost.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_UID,
    MountSpec,
    SandboxHandle,
    SandboxSession,
)
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import ImageContent, SpawnResult, TextContent, ToolContext
from selfhost.tools.registry import ToolRegistry

SANDBOX_TEST_IMAGE = "selfhost-sandbox:test"
OVER_INMEMORY_BYTES = 25 * 1024 * 1024
ARTIFACT_SECRET = "file-tools-secret"
REGISTRY = ToolRegistry(BUILTIN_TOOLS)

MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R \
/Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 46 >>
stream
BT /F1 24 Tf 40 100 Td (Hello sbxfs PDF) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
trailer
<< /Root 1 0 R /Size 6 >>
%%EOF
"""


def _png_1x1() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    return signature + ihdr + idat + chunk(b"IEND", b"")


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


@pytest.fixture(scope="module")
def sandbox_image() -> str:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    image_dir = Path(session_module.__file__).parent / "image"
    built = subprocess.run(
        ["docker", "build", "-t", SANDBOX_TEST_IMAGE, str(image_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.skip(f"cannot build the sandbox image: {built.stderr.strip()}")
    return SANDBOX_TEST_IMAGE


@pytest.fixture
def file_ctx(sandbox_image: str, tmp_path: Path) -> Iterator[tuple[ToolContext, Path]]:
    """A tool context over a live container whose /workspace is a host bind mount, set up exactly as
    prod: the mount is chowned to the sandbox uid (as `_workspace_mount` does when serve runs as
    root) and the container then runs as the image's default non-root `sandbox` user. So the tools
    write as the real sandbox user against a real carrier and a real bind mount the export streams
    out of — no `--user` override, no stand-in. The chown runs in a throwaway `--user 0` container,
    so the test needs no host root; input files are created through the sandbox (as the agent would)
    so they too are sandbox-owned."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chowned = subprocess.run(
        ["docker", "run", "--rm", "--user", "0:0", "-v", f"{workspace}:/workspace", sandbox_image,
         "chown", "-R", f"{SANDBOX_UID}:{SANDBOX_GID}", "/workspace"],
        capture_output=True,
        text=True,
        check=False,
    )
    if chowned.returncode != 0:
        pytest.skip(f"docker cannot chown the workspace mount: {chowned.stderr.strip()}")
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-v", f"{workspace}:/workspace", sandbox_image,
         "sleep", "infinity"],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"docker cannot run the sandbox image: {started.stderr.strip()}")
    container = started.stdout.strip()
    handle = SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        mount=MountSpec(kind="filesystem", host_path=str(workspace)),
    )
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
    )
    ctx = ToolContext(
        sandbox=SandboxSession(carrier=DockerCarrier(), handle=handle),
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=_StubMemory(),
        member_id=None,
        artifact_token_secret=ARTIFACT_SECRET,
    )
    try:
        yield ctx, workspace
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


async def _seed_turn_rows(turn: Turn) -> None:
    """share_file records a `shared_artifact` row (FK → turn, workspace); seed the file_ctx turn's
    FK chain so the insert holds against the real db."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=turn.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=turn.agent_id,
                workspace_id=turn.workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=turn.conversation_id,
                workspace_id=turn.workspace_id,
                surface="cli",
                queue_key="file-tools",
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn.id,
                workspace_id=turn.workspace_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                seq=turn.seq or 1,
                status=turn.status,
                inbound=turn.inbound,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _run(tool_name: str, ctx: ToolContext, **args: object):
    tool = REGISTRY.get(tool_name)
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def test_read_numbers_lines_and_appends_truncation_footer(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("notes.txt", b"a\nb\nc\nd\ne\n")
    result = await _run("read", ctx, file_path="notes.txt", offset=1, limit=2)
    assert result.content[0].text == "1\ta\n2\tb\n\n[lines 1-2 of 5]; 3 more - read with offset=3"
    assert "notes.txt" in ctx.read_paths


async def test_read_is_one_based_and_windows_from_offset(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("notes.txt", b"a\nb\nc\nd\ne\n")
    result = await _run("read", ctx, file_path="notes.txt", offset=2, limit=2)
    assert result.content[0].text == "2\tb\n3\tc\n\n[lines 2-3 of 5]; 2 more - read with offset=4"


async def test_read_image_returns_an_image_content_block(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    png = _png_1x1()
    await ctx.sandbox.write_file("pixel.png", png)
    result = await _run("read", ctx, file_path="pixel.png")
    block = result.content[0]
    assert isinstance(block, ImageContent)
    assert block.media_type == "image/png"
    assert base64.b64decode(block.data) == png


async def test_read_pdf_returns_text_then_page_image_blocks(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("doc.pdf", MINIMAL_PDF)
    result = await _run("read", ctx, file_path="doc.pdf")
    text_block = result.content[0]
    assert isinstance(text_block, TextContent)
    assert "Hello sbxfs PDF" in text_block.text
    assert "of 1]" in text_block.text
    pages = [block for block in result.content if isinstance(block, ImageContent)]
    assert pages and pages[0].media_type == "image/png"
    assert base64.b64decode(pages[0].data).startswith(b"\x89PNG")


async def test_read_refuses_a_binary_file(file_ctx: tuple[ToolContext, Path]) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("data.bin", bytes(range(256)))
    with pytest.raises(ValueError, match="binary file"):
        await _run("read", ctx, file_path="data.bin")


async def test_write_creates_and_reports_size_and_lines(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, workspace = file_ctx
    result = await _run("write", ctx, file_path="new.txt", content="hello\nworld\n")
    payload = json.loads(result.content[0].text)
    assert payload == {"path": "new.txt", "created": True, "size_bytes": 12, "lines": 2}
    assert (workspace / "new.txt").read_text() == "hello\nworld\n"


async def test_write_guard_refuses_overwriting_an_unread_file(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, workspace = file_ctx
    await ctx.sandbox.write_file("exist.txt", b"original")
    with pytest.raises(ValueError, match="must be read before it is written"):
        await _run("write", ctx, file_path="exist.txt", content="clobber")
    assert (workspace / "exist.txt").read_text() == "original"


async def test_write_overwrites_after_read(file_ctx: tuple[ToolContext, Path]) -> None:
    ctx, workspace = file_ctx
    await ctx.sandbox.write_file("exist.txt", b"original\n")
    await _run("read", ctx, file_path="exist.txt")
    result = await _run("write", ctx, file_path="exist.txt", content="replaced\n")
    assert json.loads(result.content[0].text)["created"] is False
    assert (workspace / "exist.txt").read_text() == "replaced\n"


async def test_edit_applies_multiple_edits_sequentially_with_snippet(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, workspace = file_ctx
    await ctx.sandbox.write_file("code.py", b"alpha = 1\nbeta = 2\n")
    await _run("read", ctx, file_path="code.py")
    result = await _run(
        "edit",
        ctx,
        file_path="code.py",
        edits=[
            {"old_string": "alpha = 1", "new_string": "alpha = 9"},
            {"old_string": "beta = 2", "new_string": "beta = 8"},
        ],
    )
    payload = json.loads(result.content[0].text)
    assert payload["replacements"] == 2
    assert "alpha = 9" in payload["snippet"]
    assert (workspace / "code.py").read_text() == "alpha = 9\nbeta = 8\n"


async def test_edit_replace_all(file_ctx: tuple[ToolContext, Path]) -> None:
    ctx, workspace = file_ctx
    await ctx.sandbox.write_file("dup.py", b"x\nx\nx\n")
    await _run("read", ctx, file_path="dup.py")
    result = await _run(
        "edit",
        ctx,
        file_path="dup.py",
        edits=[{"old_string": "x", "new_string": "y", "replace_all": True}],
    )
    assert json.loads(result.content[0].text)["replacements"] == 3
    assert (workspace / "dup.py").read_text() == "y\ny\ny\n"


async def test_edit_non_unique_without_replace_all_raises(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("dup.py", b"x\nx\n")
    await _run("read", ctx, file_path="dup.py")
    with pytest.raises(ValueError, match="found 2 times"):
        await _run(
            "edit", ctx, file_path="dup.py", edits=[{"old_string": "x", "new_string": "y"}]
        )


async def test_share_file_streams_a_file_over_the_read_cap_byte_exact(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    payload = b"\x00\x01\x02\x03\x04\x05\x06\x07" * (OVER_INMEMORY_BYTES // 8 + 200000)
    assert len(payload) > OVER_INMEMORY_BYTES
    await ctx.sandbox.write_file("big.bin", payload)
    result = await _run("share_file", ctx, file_path="big.bin")
    shared = json.loads(result.content[0].text)
    assert shared["size_bytes"] == len(payload)
    assert shared["digest"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert shared["is_text"] is False
    assert shared["url"].startswith("/web/artifacts/download?token=")
    token = shared["url"].split("token=", 1)[1]
    claims = verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC))
    assert await ctx.blob.get(claims.blob_key) == payload


async def test_share_file_text_preflight_and_download_url(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    body = b"the produced report\n"
    await ctx.sandbox.write_file("report.txt", body)
    result = await _run("share_file", ctx, file_path="report.txt")
    shared = json.loads(result.content[0].text)
    assert shared["is_text"] is True
    assert shared["digest"] == "sha256:" + hashlib.sha256(body).hexdigest()
    token = shared["url"].split("token=", 1)[1]
    claims = verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC))
    parts = claims.blob_key.split("/")
    assert parts[0] == "artifacts" and len(parts) == 3 and parts[-1] == "report.txt"
    assert await ctx.blob.get(claims.blob_key) == body


async def test_share_file_confines_a_traversal_name(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.write_file("report.txt", b"data")
    result = await _run("share_file", ctx, file_path="report.txt", name="../../conversations/x")
    token = json.loads(result.content[0].text)["url"].split("token=", 1)[1]
    claims = verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC))
    parts = claims.blob_key.split("/")
    assert parts[0] == "artifacts" and ".." not in parts and parts[-1] == "x"
    assert claims.filename == "x"
