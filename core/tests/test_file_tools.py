"""End-to-end proof of the `ufo fs`-backed file tools against a real container: the built sandbox
image, the real `ufo` client on PATH, real ripgrep/poppler, and — for `share_file` — both stores it
can land an artifact in, the filesystem one it streams out of the container into and the S3 one the
container uploads to itself. These are Docker-gated like test_sandbox_session; nothing here asserts
a fake."""

import asyncio
import base64
import errno
import hashlib
import json
import re
import struct
import tarfile
import zlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from ufo_ext_docker import DockerCarrier
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import ModelEvent, ModelRequest, ToolResultBlock, ToolUseBlock
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    sandbox_runtime_root,
)
from ufo.host.ext.loader import HookChain
from ufo.host.tools import builtins
from ufo.host.tools.builtins import BUILTIN_TOOLS
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.compaction import Compaction
from ufo.runtime.engine import (
    MAX_TOOL_RESULT_CHARS,
    OFFLOAD_NOTICE,
    TOOL_RESULT_PREVIEW_CHARS,
    TurnEngine,
)
from ufo.runtime.hub import InProcessHub
from ufo.runtime.media.artifact_url import ARTIFACT_KEY_PREFIX, ArtifactClaims, verify_artifact_url
from ufo.runtime.prompts.render import rendered_prompt
from ufo.runtime.tools.context import (
    ImageContent,
    SpawnResult,
    TextContent,
    ToolContext,
    ToolResult,
)
from ufo.runtime.tools.registry import ToolDef, ToolRegistry
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import ActivitySummarizer
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage

TOOL_NARRATION = "working through their files"


class _ActivityModel:
    model = "gpt-5.6-luna"

    async def complete(self, _request: ModelRequest) -> str:
        return "Working on the request."


pytestmark = pytest.mark.docker

OVER_INMEMORY_BYTES = 25 * 1024 * 1024
ARTIFACT_SECRET = "file-tools-secret"


async def test_docker_mount_preparation_makes_workspace_and_runtime_writable(
    sandbox_container: tuple[str, Path],
) -> None:
    container, workspace = sandbox_container
    conversation_id = uuid4()
    carrier = DockerCarrier()
    handle = SandboxHandle(
        conversation_id=conversation_id,
        container_id=container,
        workspace_host_path=str(workspace),
        runtime_root=sandbox_runtime_root(conversation_id),
    )
    seeded = await carrier.exec_skill(
        handle,
        (
            "install",
            "-d",
            "-o",
            "0",
            "-g",
            "0",
            "-m",
            "0755",
            "/home/user/.ufo/skills",
        ),
        30,
    )
    assert seeded.exit_code == 0
    rooted = await carrier.exec_skill(handle, ("chown", "-R", "0:0", WORKSPACE_DIR), 30)
    assert rooted.exit_code == 0
    refused = await carrier.exec(handle, ("mkdir", f"{WORKSPACE_DIR}/.eval-output"), 30)
    assert refused.exit_code != 0
    await carrier._prepare_mounts(container, conversation_id)
    writable = await carrier.exec(handle, ("mkdir", f"{WORKSPACE_DIR}/.eval-output"), 30)
    assert writable.exit_code == 0

    direct = await carrier.exec(handle, ("ufo", "run", "--", "true"), 30)
    assert direct.exit_code == 0
    assert "Permission denied" not in direct.stderr
    session = await carrier.exec(
        handle,
        (
            "stat",
            "-c",
            "%u:%g %a",
            "/home/user/.ufo",
            "/home/user/.ufo/session",
            sandbox_runtime_root(conversation_id),
            "/home/user/.ufo/skills",
        ),
        30,
    )
    assert session.stdout.splitlines() == [
        "0:0 755",
        "1000:1000 600",
        "1000:1000 700",
        "0:0 755",
    ]
    session_id = await carrier.exec(handle, ("cat", "/home/user/.ufo/session"), 30)
    await carrier._prepare_mounts(container, conversation_id)
    assert await carrier.exec(handle, ("cat", "/home/user/.ufo/session"), 30) == session_id
    rename = await carrier.exec(
        handle,
        ("mv", "/home/user/.ufo/skills", "/home/user/.ufo/skills-renamed"),
        30,
    )
    assert rename.exit_code != 0
    replacement = await carrier.exec(
        handle,
        (
            "sh",
            "-c",
            "ln -s /tmp /tmp/replacement-skills && "
            "mv -Tf /tmp/replacement-skills /home/user/.ufo/skills",
        ),
        30,
    )
    assert replacement.exit_code != 0
    skills = await carrier.exec(
        handle,
        ("sh", "-c", "test -d /home/user/.ufo/skills && test ! -L /home/user/.ufo/skills"),
        30,
    )
    assert skills.exit_code == 0
    through_session = await SandboxSession(carrier=carrier, handle=handle).bash("true")
    assert through_session.exit_code == 0
    assert "Permission denied" not in through_session.stderr


def _download_claims(url: str) -> ArtifactClaims:
    split = urlsplit(url)
    artifact_id, filename = split.path.removeprefix(f"/{ARTIFACT_KEY_PREFIX}").split("/")
    query = parse_qs(split.query)
    return verify_artifact_url(
        ARTIFACT_SECRET,
        artifact_id,
        unquote(filename),
        query["exp"][0],
        query["sig"][0],
        "",
        query["ws"][0],
        datetime.now(UTC),
    )


REGISTRY = ToolRegistry(BUILTIN_TOOLS)
CONTAINER_OP_TIMEOUT_S = 180


MINIMAL_PDF_STREAM = "BT /F1 24 Tf 40 100 Td (Hello ufo fs PDF) Tj ET"
MINIMAL_PDF = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R \
/Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length {len(MINIMAL_PDF_STREAM.encode())} >>
stream
{MINIMAL_PDF_STREAM}
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
trailer
<< /Root 1 0 R /Size 6 >>
%%EOF
""".encode()


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


@pytest.fixture
def file_ctx(
    sandbox_container: tuple[str, Path], tmp_path: Path
) -> Iterator[tuple[ToolContext, Path]]:
    """A tool context over a live container whose /workspace is a host bind mount, set up exactly as
    prod. So the tools write as the real sandbox user against a real carrier and a real bind mount
    the file reads stream out of — no `--user` override, no stand-in. Input files are created
    through the sandbox (as the agent would) so they too are sandbox-owned."""
    container, workspace = sandbox_container
    handle = SandboxHandle(
        conversation_id=uuid4(),
        container_id=container,
        workspace_host_path=str(workspace),
    )
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=SandboxSession(carrier=DockerCarrier(), handle=handle),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs")),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret=ARTIFACT_SECRET,
    )
    yield ctx, workspace


async def test_cancelled_docker_create_removes_its_real_container_and_network(
    sandbox_image: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installing = asyncio.Event()

    async def wait_during_install(carrier: DockerCarrier, container_id: str, ca_cert: str) -> None:
        installing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DockerCarrier, "_install_ca", wait_during_install)
    conversation = uuid4()
    task = asyncio.create_task(
        DockerCarrier().create(
            SandboxSpec(
                conversation_id=conversation,
                image_ref=sandbox_image,
                workspace_host_path=str(tmp_path),
                proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
                run_token="integration-run",
            )
        )
    )
    await asyncio.wait_for(installing.wait(), timeout=30)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    container = await asyncio.create_subprocess_exec(
        "docker",
        "container",
        "inspect",
        f"ufo-sbx-{conversation}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    network = await asyncio.create_subprocess_exec(
        "docker",
        "network",
        "inspect",
        f"ufo-sandbox-{conversation.hex}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    container_code = await asyncio.wait_for(container.wait(), timeout=CONTAINER_OP_TIMEOUT_S)
    network_code = await asyncio.wait_for(network.wait(), timeout=CONTAINER_OP_TIMEOUT_S)
    assert container_code != 0
    assert network_code != 0


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
                agent_id=turn.agent_id,
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
    with ws(ctx.turn.workspace_id):
        return await tool.handler(ctx, tool.input_model.model_validate({**args}))


async def _share(ctx: ToolContext, **spec: object) -> dict:
    result = await _run("share_file", ctx, files=[spec])
    return json.loads(result.content[0].text)[0]


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


async def test_read_rejects_an_image_file_whose_bytes_are_not_an_image(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The observed failure: a sandbox pipeline writes PDF bytes into a `.png`, `read` ships them
    as image content, and the provider 400s the whole model request. The mismatch must surface
    here as a recoverable tool error the model can act on."""
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("fake.png", MINIMAL_PDF)
    with pytest.raises(ValueError, match="bytes are not png/jpeg/gif/webp"):
        await _run("read", ctx, file_path="fake.png")


async def test_read_labels_a_mislabeled_image_by_its_bytes(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("photo.png", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    result = await _run("read", ctx, file_path="photo.png")
    block = result.content[0]
    assert isinstance(block, ImageContent)
    assert block.media_type == "image/jpeg"


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
    assert json.loads(result.content[0].text) == {
        "path": "new.txt",
        "created": True,
        "size_bytes": 12,
        "lines": 2,
    }
    assert (workspace / "new.txt").read_text() == "hello\nworld\n"


async def test_changes_reports_what_the_shell_did_to_a_checkout(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The whole reason git is the producer: nothing here goes through a file tool. The container's
    own shell edits a tracked file and drops a note beside the checkout — the shape a subagent
    sharing this sandbox, or a script the agent ran, leaves behind — and the change comes back
    anyway, while the note does not."""
    ctx, _ = file_ctx
    prepared = await ctx.sandbox.bash(
        "set -e\n"
        "git init -q /workspace/checkout\n"
        "cd /workspace/checkout\n"
        "printf 'x = 1\\n' > mod.py\n"
        "git add -A\n"
        "git -c user.email=t@t -c user.name=t commit -qm base\n"
        "printf 'x = 2\\n' > mod.py\n"
        "printf 'what I found\\n' > /workspace/findings.md\n"
    )
    assert prepared.exit_code == 0, prepared.stderr

    assert await ctx.sandbox.run_ufo_fs("changes", {}) == {
        "changes": [
            {
                "path": "checkout/mod.py",
                "patch": "--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
                "truncated": False,
            }
        ],
        "truncated": False,
    }


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


async def test_glob_matches_files_in_the_workspace(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("src/one.py", b"a = 1\n")
    await ctx.sandbox.write_file("src/two.py", b"b = 2\n")
    await ctx.sandbox.write_file("notes.txt", b"hi\n")
    result = await _run("glob", ctx, pattern="**/*.py")
    text = result.content[0].text
    assert "one.py" in text and "two.py" in text
    assert "notes.txt" not in text


async def test_grep_finds_a_pattern_across_the_workspace(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    await ctx.sandbox.write_file("app.py", b"def handler():\n    return TARGET\n")
    await ctx.sandbox.write_file("other.py", b"x = 1\n")
    result = await _run("grep", ctx, pattern="TARGET")
    text = result.content[0].text
    assert "app.py" in text
    assert "other.py" not in text


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
        await _run("edit", ctx, file_path="dup.py", edits=[{"old_string": "x", "new_string": "y"}])


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_streams_a_file_over_the_read_cap_byte_exact(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    payload = b"\x00\x01\x02\x03\x04\x05\x06\x07" * (OVER_INMEMORY_BYTES // 8 + 200000)
    assert len(payload) > OVER_INMEMORY_BYTES
    await ctx.sandbox.write_file("big.bin", payload)
    shared = await _share(ctx, file_path="big.bin")
    assert shared["size_bytes"] == len(payload)
    assert shared["digest"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert shared["is_text"] is False
    assert shared["url"].startswith(f"/{ARTIFACT_KEY_PREFIX}")
    claims = _download_claims(shared["url"])
    with ws(ctx.turn.workspace_id):
        assert await ctx.blob.get(claims.blob_key) == payload


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_text_preflight_and_download_url(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    body = b"the produced report\n"
    await ctx.sandbox.write_file("report.txt", body)
    shared = await _share(ctx, file_path="report.txt")
    assert shared["is_text"] is True
    assert shared["digest"] == "sha256:" + hashlib.sha256(body).hexdigest()
    claims = _download_claims(shared["url"])
    parts = claims.blob_key.split("/")
    assert parts[0] == "artifacts" and len(parts) == 3 and parts[-1] == "report.txt"
    with ws(ctx.turn.workspace_id):
        assert await ctx.blob.get(claims.blob_key) == body


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_packs_a_directory_into_a_tarball(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    """A directory path shares as a `.tar.gz` of itself: the archive is packed in the container,
    named after the directory (a caller name without an extension gains `.tar.gz`), and unpacks
    back to the directory's own tree."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.bash(
        "mkdir -p /workspace/reports/nested"
        " && printf 'summary' > /workspace/reports/summary.txt"
        " && printf 'detail' > /workspace/reports/nested/detail.txt"
    )
    shared = await _share(ctx, file_path="reports")
    assert shared["name"] == "reports.tar.gz"
    assert shared["is_text"] is False
    claims = _download_claims(shared["url"])
    with ws(ctx.turn.workspace_id):
        archive = await ctx.blob.get(claims.blob_key)
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        members = {member.name: member for member in tar.getmembers()}
        summary = tar.extractfile(members["reports/summary.txt"])
        assert summary is not None and summary.read() == b"summary"
        detail = tar.extractfile(members["reports/nested/detail.txt"])
        assert detail is not None and detail.read() == b"detail"

    named = await _share(ctx, file_path="reports", name="q3_bundle")
    assert named["name"] == "q3_bundle.tar.gz"

    root = await _share(ctx, file_path="/workspace")
    assert root["name"] == "workspace.tar.gz"
    with ws(ctx.turn.workspace_id):
        whole = await ctx.blob.get(_download_claims(root["url"]).blob_key)
    with tarfile.open(fileobj=BytesIO(whole), mode="r:gz") as tar:
        names = tar.getnames()
        assert "workspace/reports/summary.txt" in names
        assert not any("tool-output" in name for name in names)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_packs_a_workspace_a_carrier_serves_under_another_name(
    file_ctx: tuple[ToolContext, Path],
    tmp_path: Path,
    db: None,
) -> None:
    """The local and terminal carriers serve `/workspace` from a host directory named after the
    conversation, so `tar` stores every member under that name instead of `workspace` — the whole
    workspace still shares without the engine's offload dir in the archive."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    conversation = uuid4()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=conversation,
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / str(conversation)),
            proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
            run_token="pack-run",
        )
    )
    local = replace(ctx, sandbox=SandboxSession(carrier=carrier, handle=handle))
    await local.sandbox.bash(
        "mkdir -p /workspace/reports && printf 'summary' > /workspace/reports/summary.txt"
    )
    await local.sandbox.ensure_tool_output_dir()
    await local.sandbox.write_runtime_file("tool-output/scratch.txt", b"engine scratch")

    root = await _share(local, file_path="/workspace")

    assert root["name"] == "workspace.tar.gz"
    with ws(ctx.turn.workspace_id):
        whole = await ctx.blob.get(_download_claims(root["url"]).blob_key)
    with tarfile.open(fileobj=BytesIO(whole), mode="r:gz") as tar:
        names = tar.getnames()
        assert f"{conversation}/reports/summary.txt" in names
        assert not any("tool-output" in name for name in names)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_delivers_a_list_in_share_order(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    """One call, several files: each lands its own row, URL, and per-file name/subject, and the
    rows' `created_at` stamps carry the list order — `(created_at, blob_key)` is what every surface
    sorts shared files by, so the order must never fall to the random blob key."""
    ctx, _ = file_ctx
    published = 0

    async def publish_artifacts() -> None:
        nonlocal published
        published += 1

    ctx = replace(ctx, publish_artifacts=publish_artifacts)
    await _seed_turn_rows(ctx.turn)
    bodies = {name: f"{name} body\n".encode() for name in ("one.txt", "two.txt", "three.txt")}
    for name, body in bodies.items():
        await ctx.sandbox.write_file(name, body)
    result = await _run(
        "share_file",
        ctx,
        files=[
            {"file_path": "one.txt", "subject": "first"},
            {"file_path": "two.txt"},
            {"file_path": "three.txt", "name": "renamed_three"},
        ],
    )
    shared = json.loads(result.content[0].text)
    assert [entry["name"] for entry in shared] == ["one.txt", "two.txt", "renamed_three.txt"]
    for entry, body in zip(shared, bodies.values(), strict=True):
        assert entry["digest"] == "sha256:" + hashlib.sha256(body).hexdigest()
        claims = _download_claims(entry["url"])
        with ws(ctx.turn.workspace_id):
            assert await ctx.blob.get(claims.blob_key) == body
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.shared_artifact.c.filename,
                    tables.shared_artifact.c.subject,
                    tables.shared_artifact.c.created_at,
                )
                .where(tables.shared_artifact.c.turn_id == ctx.turn.id)
                .order_by(tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key)
            )
        ).all()
    assert [(row.filename, row.subject) for row in rows] == [
        ("one.txt", "first"),
        ("two.txt", None),
        ("renamed_three.txt", None),
    ]
    assert len({row.created_at for row in rows}) == 3
    assert published == 1


async def _shared_row(blob_key: str) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.shared_artifact.c.preview_blob_key,
                    tables.shared_artifact.c.preview_media_type,
                    tables.shared_artifact.c.preview_size_bytes,
                ).where(tables.shared_artifact.c.blob_key == blob_key)
            )
        ).one()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_records_no_preview_without_an_s3_store(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    """The service stores through a presigned PUT, which only an S3 store can mint. On a filesystem
    store an eligible document shares with no preview — the file lands, the picture is simply
    absent."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.write_file("report.pdf", b"%PDF-1.7 minimal\n")
    claims = _download_claims((await _share(ctx, file_path="report.pdf"))["url"])
    row = await _shared_row(claims.blob_key)
    assert row.preview_blob_key is None
    assert row.preview_media_type is None
    assert row.preview_size_bytes is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_leaves_a_plain_file_without_a_rendered_page(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    """Only a document earns a render. A text file has nothing to rasterize, and an image is
    already its own preview — minted off its own bytes, never a second blob."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.write_file("report.txt", b"the produced report\n")
    claims = _download_claims((await _share(ctx, file_path="report.txt"))["url"])
    row = await _shared_row(claims.blob_key)
    assert row.preview_blob_key is None
    assert row.preview_media_type is None
    assert row.preview_size_bytes is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_uploads_from_inside_the_sandbox_on_the_s3_backend(
    file_ctx: tuple[ToolContext, Path],
    s3_store: S3BlobStore,
    db: None,
) -> None:
    """The property the presigned PUT exists for: the bytes go sandbox → S3 and never cross this
    process. Proven by handing the tool a store signed against an endpoint only the container can
    resolve, so serve itself cannot reach the bucket at all — if the object lands, the sandbox put
    it there. Read back through the host-reachable store over the same bucket, byte-exact, at the
    size the tool reported."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    assert s3_store.endpoint_url is not None
    sandbox_store = S3BlobStore(
        bucket=s3_store.bucket,
        endpoint_url=f"http://host.docker.internal:{urlsplit(s3_store.endpoint_url).port}",
        region=s3_store.region,
    )
    payload = bytes(range(256)) * 8192
    await ctx.sandbox.write_file("figures.bin", payload)

    shared = await _share(
        replace(ctx, blob=WorkspaceBlobStore(backend=sandbox_store)), file_path="figures.bin"
    )

    assert shared["size_bytes"] == len(payload)
    claims = _download_claims(shared["url"])
    with ws(ctx.turn.workspace_id):
        assert await WorkspaceBlobStore(backend=s3_store).get(claims.blob_key) == payload


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_refuses_a_file_over_the_artifact_cap(
    file_ctx: tuple[ToolContext, Path],
    s3_store: S3BlobStore,
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single presigned PUT cannot carry more than S3 accepts in one request, so the preflight
    size is the bound — checked before anything is minted, so an over-cap share records no artifact
    rather than half-uploading one."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    monkeypatch.setattr(builtins, "ARTIFACT_PUT_MAX_BYTES", 8)
    await ctx.sandbox.write_file("oversize.bin", b"nine byte")

    with pytest.raises(ValueError, match="capped at 8 bytes"):
        await _share(
            replace(ctx, blob=WorkspaceBlobStore(backend=s3_store)), file_path="oversize.bin"
        )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.shared_artifact.c.blob_key).where(
                    tables.shared_artifact.c.workspace_id == ctx.turn.workspace_id
                )
            )
        ).all()
    assert rows == []


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_confines_a_traversal_name(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.write_file("report.txt", b"data")
    shared = await _share(ctx, file_path="report.txt", name="../../conversations/x")
    claims = _download_claims(shared["url"])
    parts = claims.blob_key.split("/")
    assert parts[0] == "artifacts" and ".." not in parts and parts[-1] == "x.txt"
    assert claims.filename == "x.txt"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_file_appends_the_source_extension_to_a_display_name(
    file_ctx: tuple[ToolContext, Path],
    db: None,
) -> None:
    """Agents habitually pass display-style names ('Q1_Risk_Report'); the recipient must still
    get an openable file, and suffix-based consumers (office apps, graders) must still see the
    format — so a name with no recognizable extension gains the source file's."""
    ctx, _ = file_ctx
    await _seed_turn_rows(ctx.turn)
    await ctx.sandbox.write_file("risk.xlsx", b"PK\x03\x04fake")
    shared = await _share(ctx, file_path="risk.xlsx", name="Q1_Risk_Report")
    assert _download_claims(shared["url"]).filename == "Q1_Risk_Report.xlsx"

    named = await _share(ctx, file_path="risk.xlsx", name="already_named.xlsx")
    assert _download_claims(named["url"]).filename == "already_named.xlsx"


class _NoArgs(BaseModel):
    pass


class _QuietModel:
    """A model the engine never calls in these dispatch-only tests; it satisfies TurnEngine's
    required fields without standing in for real completion."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield Usage(input_tokens=0, output_tokens=0)


def _fixed_result_tool(name: str, content: str) -> ToolDef:
    async def handler(ctx: ToolContext, args: BaseModel) -> ToolResult:
        return ToolResult(content=(TextContent(text=content),))

    return ToolDef(name=name, description="d", input_model=_NoArgs, handler=handler)


def _dispatch_engine(ctx: ToolContext, tools: ToolRegistry) -> TurnEngine:
    return TurnEngine(
        turn=ctx.turn,
        agent=ctx.agent,
        byok=False,
        system_prompt=rendered_prompt("p"),
        serving=serving_model(_QuietModel()),
        activity_summarizer=ActivitySummarizer(_ActivityModel()),
        transcript=Transcript(blob=ctx.blob, conversation_id=ctx.turn.conversation_id),
        compaction=Compaction(
            serving=serving_model(_QuietModel()),
            blob=ctx.blob,
            conversation_id=ctx.turn.conversation_id,
        ),
        hub=InProcessHub(),
        sandbox=ctx.sandbox,
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=tools,
        tool_ext={},
        hooks=HookChain(),
        blob=ctx.blob,
        spawn=ctx.spawn,
        audience=ctx.audience,
        artifact_token_secret=ctx.artifact_token_secret,
        grants=None,
    )


async def _dispatch(
    engine: TurnEngine, context: ToolContext, call: ToolUseBlock
) -> ToolResultBlock:
    with ws(context.turn.workspace_id):
        bound = await engine._bind_or_error(context, engine._resolve_call(call), {})
        return await engine._dispatch(bound)


GITHUB_REPO_URL_KEYS = (
    "archive_url",
    "assignees_url",
    "blobs_url",
    "branches_url",
    "collaborators_url",
    "comments_url",
    "commits_url",
    "compare_url",
    "contents_url",
    "contributors_url",
    "deployments_url",
    "downloads_url",
    "events_url",
    "forks_url",
    "git_commits_url",
    "git_refs_url",
    "git_tags_url",
    "hooks_url",
    "issue_comment_url",
    "issue_events_url",
    "issues_url",
    "keys_url",
    "labels_url",
    "languages_url",
    "merges_url",
    "milestones_url",
    "notifications_url",
    "pulls_url",
    "releases_url",
    "stargazers_url",
    "statuses_url",
    "subscribers_url",
    "subscription_url",
    "tags_url",
    "teams_url",
    "trees_url",
    "url",
)
GITHUB_OWNER_URL_KEYS = (
    "avatar_url",
    "events_url",
    "followers_url",
    "following_url",
    "gists_url",
    "html_url",
    "organizations_url",
    "received_events_url",
    "repos_url",
    "starred_url",
    "subscriptions_url",
    "url",
)
GITHUB_SEARCH_HITS = 30
GITHUB_SEARCH_CHARS_PER_HIT = 4_000


def _github_code_search_payload(hits: int) -> str:
    """The shape a connector's GITHUB_SEARCH_CODE call returns: a minified envelope over one record
    per hit, each repeating that hit's whole `repository` object (45 keys, itself carrying a 19-key
    `owner`) — so ~5 KB a hit, and GitHub's default page of 30 is ~150 KB of provider JSON of which
    the member's question needs two fields."""
    items = []
    for index in range(hits):
        login = f"owner-{index:03d}"
        slug = f"{login}/atlas-service-{index:03d}"
        sha = f"{index:03d}{'a1b2c3d4e5' * 4}"[:40]
        repo_id = 1207685915 + index
        owner = {
            "login": login,
            "id": repo_id,
            "node_id": f"MDQ6VXNlcjEyMDc2ODU5MTV{index:03d}",
            "gravatar_id": "",
            "type": "User",
            "site_admin": False,
            "user_view_type": "public",
        } | {
            key: f"https://api.github.com/users/{login}/{key}{{/other_user}}"
            for key in GITHUB_OWNER_URL_KEYS
        }
        repository = {
            "id": repo_id,
            "node_id": f"R_kgDOR8vXy{index:03d}",
            "name": f"atlas-service-{index:03d}",
            "full_name": slug,
            "private": False,
            "owner": owner,
            "description": f"Streaming transpiler service {index:03d} for the atlas fleet.",
            "fork": False,
        } | {
            key: f"https://api.github.com/repos/{slug}/{key}{{/sha}}"
            for key in GITHUB_REPO_URL_KEYS
        }
        items.append(
            {
                "name": f"stream_{index:03d}.rs",
                "path": f"src/transport/stream_{index:03d}.rs",
                "sha": sha,
                "url": f"https://api.github.com/repositories/{repo_id}/contents/{index:03d}?ref={sha}",
                "git_url": f"https://api.github.com/repositories/{repo_id}/git/blobs/{sha}",
                "html_url": f"https://github.com/{slug}/blob/{sha}/src/stream_{index:03d}.rs",
                "repository": repository,
                "score": 1.0,
            }
        )
    return json.dumps(
        {"total_count": 15800, "incomplete_results": False, "items": items},
        separators=(",", ":"),
    )


async def test_a_connector_sized_result_offloads_to_a_file_the_sandbox_can_filter(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The measured gap in #282: one page of provider JSON is ~150 KB, so it must not ride inline
    and be re-ingested every later round. It offloads, the model's context keeps only the preview
    plus the path, and the payload survives whole in the workspace — provably filterable, since the
    real `jq` in the sandbox image answers the member's question from the file with the two fields
    they asked for instead of the 45-key repository object repeated 30 times."""
    ctx, workspace = file_ctx
    payload = _github_code_search_payload(GITHUB_SEARCH_HITS)
    assert len(payload) > MAX_TOOL_RESULT_CHARS
    assert len(payload) // GITHUB_SEARCH_HITS > GITHUB_SEARCH_CHARS_PER_HIT
    engine = _dispatch_engine(ctx, ToolRegistry((_fixed_result_tool("search_code", payload),)))

    block = await _dispatch(engine, ctx, ToolUseBlock(id="call1", name="search_code", input={}))
    path = await ctx.sandbox.runtime_display_path("tool-output/call1.txt")
    stored = await ctx.sandbox.runtime_path("tool-output/call1.txt")
    assert not block.is_error
    assert isinstance(block.content, str)
    assert path in block.content
    assert len(block.content) <= TOOL_RESULT_PREVIEW_CHARS + len(
        OFFLOAD_NOTICE.format(total=len(payload), path=path)
    )
    assert b"".join([chunk async for chunk in ctx.sandbox.read_file(stored)]).decode() == payload
    assert not (workspace / ".tool-output").exists()

    filtered = await _run(
        "bash", ctx, command=f"jq -r '.items[] | \"\\(.path)\\t\\(.html_url)\"' {path}"
    )
    lines = filtered.content[0].text.strip().splitlines()
    assert len(lines) == GITHUB_SEARCH_HITS
    assert lines[0].startswith("src/transport/stream_000.rs\thttps://github.com/owner-000/")
    assert len(filtered.content[0].text) < TOOL_RESULT_PREVIEW_CHARS

    windowed = await _run("read", ctx, file_path=path, offset=1, limit=1)
    assert '"total_count":15800' in windowed.content[0].text


async def test_the_offload_preview_carries_one_whole_record_of_the_payload(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The preview is what the model reasons over to decide whether — and how — to open the file.
    One whole record is the unit that makes it actionable: a preview cut mid-record hides the rest
    of that record's keys, so the model cannot tell what fields exist to filter on. The first record
    of a real page closes past 4,096 chars, which is why the preview is larger than that."""
    ctx, _ = file_ctx
    payload = _github_code_search_payload(GITHUB_SEARCH_HITS)
    first_record = json.dumps(json.loads(payload)["items"][0], separators=(",", ":"))
    envelope = payload.index("[") + 1
    assert envelope + len(first_record) > 4_096
    engine = _dispatch_engine(ctx, ToolRegistry((_fixed_result_tool("search_code", payload),)))

    block = await _dispatch(engine, ctx, ToolUseBlock(id="call2", name="search_code", input={}))
    assert isinstance(block.content, str)
    assert first_record in block.content
    assert '"total_count":15800' in block.content


async def test_the_offload_fires_only_past_the_cap(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The cap governs every tool, so where exactly it fires is the contract: a result at the cap
    rides inline whole and writes no file, and one char more offloads. Nothing weaker holds — a
    tool's own limit caps a field, and the JSON its handler wraps that field in grows by an escaping
    cost the limit does not describe, so a producer's declared maximum is not a promise of staying
    inline."""
    ctx, workspace = file_ctx
    at_cap = "page text line\n" * (MAX_TOOL_RESULT_CHARS // 15) + "x" * (MAX_TOOL_RESULT_CHARS % 15)
    assert len(at_cap) == MAX_TOOL_RESULT_CHARS
    engine = _dispatch_engine(
        ctx,
        ToolRegistry(
            (
                _fixed_result_tool("at_cap", at_cap),
                _fixed_result_tool("over_cap", at_cap + "y"),
            )
        ),
    )

    inline = await _dispatch(engine, ctx, ToolUseBlock(id="call3", name="at_cap", input={}))
    assert inline.content == at_cap
    assert not await ctx.sandbox.runtime_file_exists("tool-output/call3.txt")

    offloaded = await _dispatch(engine, ctx, ToolUseBlock(id="call5", name="over_cap", input={}))
    assert isinstance(offloaded.content, str)
    path = await ctx.sandbox.runtime_display_path("tool-output/call5.txt")
    stored = await ctx.sandbox.runtime_path("tool-output/call5.txt")
    assert path in offloaded.content
    assert (
        b"".join([chunk async for chunk in ctx.sandbox.read_file(stored)]).decode() == at_cap + "y"
    )
    assert not (workspace / ".tool-output").exists()


async def test_a_read_over_the_cap_offloads_without_losing_the_file_it_read(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """A file read is a newly-hot path whose result the model wanted verbatim, so the offload must
    cost it nothing recoverable: the whole page survives in the workspace, and the notice's narrowed
    access — a windowed read of the original path — returns the same lines inline."""
    ctx, workspace = file_ctx
    lines = [f"{index:05d} " + "payload" * 12 for index in range(1_400)]
    await ctx.sandbox.write_file("wide.log", ("\n".join(lines) + "\n").encode())
    engine = _dispatch_engine(ctx, ToolRegistry(BUILTIN_TOOLS))

    block = await _dispatch(
        engine,
        ctx,
        ToolUseBlock(
            id="call4",
            name="read",
            input={"file_path": "wide.log"},
        ),
    )
    assert isinstance(block.content, str)
    path = await ctx.sandbox.runtime_display_path("tool-output/call4.txt")
    stored = await ctx.sandbox.runtime_path("tool-output/call4.txt")
    assert path in block.content
    offloaded = b"".join([chunk async for chunk in ctx.sandbox.read_file(stored)]).decode()
    assert len(offloaded) > MAX_TOOL_RESULT_CHARS
    assert lines[-1] in offloaded
    assert not (workspace / ".tool-output").exists()

    windowed = await _run("read", ctx, file_path="wide.log", offset=1_400, limit=1)
    assert lines[-1] in windowed.content[0].text
    assert len(windowed.content[0].text) < TOOL_RESULT_PREVIEW_CHARS


async def test_carrier_read_streams_container_bytes_byte_exact(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """The copy-out against a real container: bytes written through the carrier come back through
    `read` byte-exact in bounded chunks, and an absent path raises rather than reading empty."""
    ctx, _ = file_ctx
    payload = bytes(range(256)) * 4096
    await ctx.sandbox.write_file("big.bin", payload)
    carrier = ctx.sandbox.carrier
    handle = ctx.sandbox.handle

    chunks = [chunk async for chunk in carrier.read(handle, "/workspace/big.bin")]

    assert len(chunks) > 1
    assert b"".join(chunks) == payload
    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in carrier.read(handle, "/workspace/absent.bin")]


async def _docker_cli(*argv: str) -> str:
    """One checked docker CLI call for container-lifecycle tests: asserts success with stderr in
    the failure, so a no-op stop or a failed create can never produce a vacuous pass."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    assert process.returncode == 0, err.decode(errors="replace")
    return out.decode().strip()


async def _docker_cleanup(*argv: str) -> None:
    """Best-effort teardown for the one target a failed body may never have created (the network
    the carrier's revive builds) — never for `rm -f`, whose failure is the leak these teardowns
    exist to prevent and which already tolerates an absent container."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def _sibling_container(ctx: ToolContext, workspace: Path) -> str:
    """A persistent (non `--rm`) container over the fixture's workspace mount — the state the
    carrier's own containers hold, which the fixture's autoremoving container cannot reach."""
    image = await _docker_cli(
        "inspect", "--format", "{{.Config.Image}}", ctx.sandbox.handle.container_id
    )
    return await _docker_cli("run", "-d", "-v", f"{workspace}:/workspace", image)


async def test_carrier_read_killed_mid_stream_names_the_kill_not_the_file(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """A container stopped under an in-flight read surfaces as what it is: `cat` killed mid-stream
    exits non-zero with empty stderr, and the drain raises RuntimeError carrying the exit and the
    container's own reported state — never FileNotFoundError, whose name would send the debugging
    at a file-lifecycle bug instead of the kill. The container persists past its stop, so the
    report's fields are deterministic."""
    ctx, workspace = file_ctx
    payload = bytes(range(256)) * 262_144
    await ctx.sandbox.write_file("big.bin", payload)
    carrier = ctx.sandbox.carrier
    container = await _sibling_container(ctx, workspace)
    handle = SandboxHandle(conversation_id=uuid4(), container_id=container)
    try:
        stream = carrier.read(handle, "/workspace/big.bin")
        expected = (
            r"read of /workspace/big\.bin died: cat exited \d+ with no stderr — "
            r"its container reports: exited exit=137 oom-killed=false"
        )
        with pytest.raises(RuntimeError, match=expected):
            got_first = False
            async for _ in stream:
                if not got_first:
                    got_first = True
                    await _docker_cli("stop", "-t", "0", container)
        assert got_first
    finally:
        await _docker_cli("rm", "-f", container)


async def test_death_report_of_a_gone_container_reports_the_failed_inspect(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """A container gone by inspect time yields docker inspect's own exit and stderr — never a
    lifecycle claim the inspect could not establish. Proven against the real daemon directly:
    reaching this branch through `read` needs autoremoval to win a race against the drain (a
    removal under a live exec answers on stderr and takes the stderr path instead), so the step is
    driven with the gone-container state it classifies."""
    ctx, _ = file_ctx
    carrier = ctx.sandbox.carrier
    gone = SandboxHandle(conversation_id=uuid4(), container_id=f"ufo-gone-{uuid4().hex}")

    report = await carrier._death_report(gone)

    assert re.search(r"^ with no stderr — docker inspect exited \d+: ", report)
    assert "no such object" in report.lower()
    assert "container reports" not in report


async def test_carrier_read_classifies_by_reason_not_by_filename(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """Classification reads cat's reason segment, never the caller's path: a DIRECTORY literally
    named with the missing-file phrase raises IsADirectoryError (a whole-stderr substring test
    reports it missing), a root-owned mode-000 file refuses the non-root exec user with
    PermissionError (on the container's own filesystem — a Docker Desktop bind mount does not
    enforce host ownership modes), a path through a plain file raises NotADirectoryError, a
    symlink cycle raises the base OSError carrying ELOOP, and only a genuinely absent path raises
    FileNotFoundError — the class and errno an open() of the path gives, read off cat's reason
    rather than the caller's string."""
    ctx, _ = file_ctx
    carrier = ctx.sandbox.carrier
    handle = ctx.sandbox.handle
    tricky = "No such file or directory"
    await _docker_cli("exec", handle.container_id, "mkdir", f"/workspace/{tricky}")
    await _docker_cli(
        "exec",
        "-u",
        "0",
        handle.container_id,
        "install",
        "-m",
        "000",
        "/dev/null",
        "/tmp/sealed",
    )
    await ctx.sandbox.write_file("plain.txt", b"plain")

    with pytest.raises(IsADirectoryError):
        [chunk async for chunk in carrier.read(handle, f"/workspace/{tricky}")]
    with pytest.raises(PermissionError):
        [chunk async for chunk in carrier.read(handle, "/tmp/sealed")]
    with pytest.raises(NotADirectoryError):
        [chunk async for chunk in carrier.read(handle, "/workspace/plain.txt/inside")]
    await _docker_cli(
        "exec", handle.container_id, "ln", "-s", "/workspace/loopb", "/workspace/loopa"
    )
    await _docker_cli(
        "exec", handle.container_id, "ln", "-s", "/workspace/loopa", "/workspace/loopb"
    )
    with pytest.raises(OSError) as looped:
        [chunk async for chunk in carrier.read(handle, "/workspace/loopa")]
    assert type(looped.value) is OSError
    assert looped.value.errno == errno.ELOOP
    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in carrier.read(handle, "/workspace/absent No such file.txt")]


async def test_carrier_read_never_fails_a_successful_read(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """A successful read never reports a death. The regression this gates on misclassifies a
    clean read per-read and independently — signalling a finished child reaps its real exit
    status, so `read` sees asyncio's 255 placeholder (`_read_started`'s docstring holds the
    mechanism) — at about 3% per read for this payload. The count sizes the gate: at that rate,
    350 clean reads let the regression slip through green fewer than once in ten thousand runs."""
    ctx, _ = file_ctx
    payload = bytes(range(256)) * 512
    await ctx.sandbox.write_file("steady.bin", payload)
    carrier = ctx.sandbox.carrier
    handle = ctx.sandbox.handle

    for _ in range(350):
        chunks = [chunk async for chunk in carrier.read(handle, "/workspace/steady.bin")]
        assert b"".join(chunks) == payload


async def test_carrier_read_abandoned_mid_stream_leaves_no_exec_behind(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """Closing the generator mid-stream is the abandoned path — a member cancelling a
    workspace-file download aborts the surface's iteration — and the exec must die with its
    generator: an orphaned `cat` blocks on the full pipe and lives for the container's lifetime,
    and these containers are per-conversation and long-lived. Abandonment must land strictly
    before EOF (payload beyond one chunk, a single `__anext__`) and after the consumer has
    awaited since the last chunk — every real consumer awaits per chunk, which pauses the stdout
    transport over its high-water mark, and a paused pipe never disconnects, so a reap that waits
    on pipe EOF wedges forever. The bounded `aclose` turns that wedge into a named failure."""
    ctx, _ = file_ctx
    carrier = ctx.sandbox.carrier
    handle = ctx.sandbox.handle
    await _docker_cli(
        "exec",
        handle.container_id,
        "sh",
        "-c",
        "dd if=/dev/zero of=/workspace/abandoned.bin bs=1M count=64 status=none",
    )
    stream = carrier.read(handle, "/workspace/abandoned.bin")
    assert await stream.__anext__()
    await asyncio.sleep(0.05)
    await asyncio.wait_for(stream.aclose(), timeout=10)
    procs = ""
    for _ in range(50):
        procs = await _docker_cli("exec", handle.container_id, "ps", "-eo", "args")
        if "abandoned.bin" not in procs:
            break
        await asyncio.sleep(0.1)
    assert "abandoned.bin" not in procs


async def test_carrier_read_revives_a_stopped_container_and_streams(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    """A container stopped before the first byte revives and re-streams from the start — the
    read's third failure consumer, which classifies docker's own is-not-running stderr before any
    byte lands. The fixture's container is `--rm` (a stop removes it), so the stopped-but-present
    state the carrier's own persistent containers reach comes from a dedicated container over the
    same workspace mount."""
    ctx, workspace = file_ctx
    payload = bytes(range(256)) * 64
    await ctx.sandbox.write_file("revive.bin", payload)
    carrier = ctx.sandbox.carrier
    container = await _sibling_container(ctx, workspace)
    handle = SandboxHandle(conversation_id=uuid4(), container_id=container)
    try:
        await _docker_cli("stop", "-t", "0", container)

        chunks = [chunk async for chunk in carrier.read(handle, "/workspace/revive.bin")]

        assert b"".join(chunks) == payload
    finally:
        await _docker_cli("rm", "-f", container)
        await _docker_cleanup("network", "rm", f"ufo-sandbox-{handle.conversation_id.hex}")
