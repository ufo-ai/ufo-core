"""End-to-end proof of the sbxfs-backed file tools against a real container: the built sandbox
image, the real `sbxfs` CLI on PATH, real ripgrep/poppler, and — for `share_file` — a real host
bind mount the carrier streams out of. These are Docker-gated like test_sandbox_session; nothing
here asserts a fake."""

import asyncio
import base64
import hashlib
import json
import shutil
import struct
import subprocess
import zlib
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from ufo_ext_docker import DockerCarrier, _docker

from ufo.artifact_token import verify_artifact_token
from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.loader import HookChain
from ufo.hub import InProcessHub
from ufo.loop.compaction import Compaction
from ufo.loop.engine import (
    MAX_TOOL_RESULT_CHARS,
    OFFLOAD_NOTICE,
    TOOL_OUTPUT_DIR,
    TOOL_RESULT_PREVIEW_CHARS,
    TurnEngine,
)
from ufo.loop.prompts.render import rendered_prompt
from ufo.loop.queue import _workspace_mount
from ufo.loop.transcript import Transcript
from ufo.models.interface import ModelEvent, ModelRequest, ToolUseBlock
from ufo.sandbox.fs_creds import (
    AwsStsClient,
    SandboxFsCredentialMinter,
    workspace_key_prefix,
)
from ufo.sandbox.fs_mount import SANDBOX_FS_RELAY_SECRET_PATH, SANDBOX_FS_TOKEN_PATH
from ufo.sandbox.proxy.rules import Rule
from ufo.sandbox.proxy.server import EgressProxy, generate_ca
from ufo.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_UID,
    MountSpec,
    ProxyEndpoint,
    RunToken,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import (
    ImageContent,
    SpawnResult,
    TextContent,
    ToolContext,
    ToolResult,
)
from ufo.tools.registry import ToolDef, ToolRegistry

pytestmark = pytest.mark.docker

SANDBOX_TEST_IMAGE = "ufo-sandbox:test"
OVER_INMEMORY_BYTES = 25 * 1024 * 1024
ARTIFACT_SECRET = "file-tools-secret"
REGISTRY = ToolRegistry(BUILTIN_TOOLS)
IMAGE_BUILD_TIMEOUT_S = 1200
CONTAINER_OP_TIMEOUT_S = 180


def _docker_or_skip(
    argv: list[str], *, timeout: int, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command with a hard wall. A stalled image pull/build or a wedged daemon is
    external, network-bound work; bounding it skips this docker-gated test with a clear reason
    instead of hanging the whole suite forever (a client's wait always ends)."""
    try:
        return subprocess.run(
            argv, input=stdin_text, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"docker '{argv[1]}' exceeded {timeout}s (stalled pull/build or wedged daemon)")


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
    from sandbox.build_template import ROOT, pod_dockerfile

    built = _docker_or_skip(
        ["docker", "build", "-t", SANDBOX_TEST_IMAGE, "-f", "-", str(ROOT)],
        timeout=IMAGE_BUILD_TIMEOUT_S,
        stdin_text=pod_dockerfile(),
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
    chowned = _docker_or_skip(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
            "chown",
            "-R",
            f"{SANDBOX_UID}:{SANDBOX_GID}",
            "/workspace",
        ],
        timeout=CONTAINER_OP_TIMEOUT_S,
    )
    if chowned.returncode != 0:
        pytest.skip(f"docker cannot chown the workspace mount: {chowned.stderr.strip()}")
    started = _docker_or_skip(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-v",
            f"{workspace}:/workspace",
            sandbox_image,
            "sleep",
            "infinity",
        ],
        timeout=CONTAINER_OP_TIMEOUT_S,
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
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=SandboxSession(carrier=DockerCarrier(), handle=handle),
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret=ARTIFACT_SECRET,
    )
    try:
        yield ctx, workspace
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            capture_output=True,
            check=False,
            timeout=CONTAINER_OP_TIMEOUT_S,
        )


async def test_s3_mount_runs_s3fs_as_nobody_without_exposing_relay_secret(
    sandbox_image: str, s3_store: S3BlobStore
) -> None:
    conversation_id = uuid4()
    assert s3_store.endpoint_url is not None
    assert s3_store.region is not None

    async def resolve(_: RunToken | None) -> tuple[Rule, ...]:
        return ()

    async def authorize(_: RunToken) -> bool:
        return True

    parsed_s3_url = urlsplit(s3_store.endpoint_url)
    assert parsed_s3_url.port is not None
    sandbox_s3_url = f"http://host.docker.internal:{parsed_s3_url.port}"
    minter = SandboxFsCredentialMinter(
        sts=AwsStsClient(endpoint_url=s3_store.endpoint_url, region=s3_store.region),
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=s3_store.bucket,
        s3_url=sandbox_s3_url,
        region=s3_store.region,
        path_style=True,
        token_secret=b"integration-secret",
    )
    ca_cert, ca_key = await generate_ca()
    run = RunToken(workspace_id=uuid4(), turn_id=uuid4())
    proxy = EgressProxy(
        resolve=resolve,
        authorize=authorize,
        ca_cert=ca_cert,
        ca_key=ca_key,
        workspace_credentials=minter.refresh,
    )
    endpoint = await proxy.start()
    carrier = DockerCarrier()
    handle: SandboxHandle | None = None
    try:
        handle = await carrier.create(
            SandboxSpec(
                conversation_id=conversation_id,
                image_ref=sandbox_image,
                mount=await _workspace_mount(
                    s3_store, minter, conversation_id, run, fresh_sandbox=True
                ),
                proxy=endpoint,
                run_token="integration-run",
            )
        )

        process = await carrier.exec(handle, ("ps", "-o", "user=", "-C", "s3fs"), b"", 30)
        assert process.exit_code == 0
        assert process.stdout.strip() == "nobody"

        code, secret, _ = await _docker(
            "exec",
            "-i",
            "-u",
            "root",
            handle.container_id,
            "cat",
            SANDBOX_FS_RELAY_SECRET_PATH,
        )
        assert code == 0
        process_args = await carrier.exec(
            handle,
            (
                "sh",
                "-c",
                "for pid in $(pgrep -x s3fs) "
                "$(pgrep -f '[s]bxcred'); "
                "do tr '\\0' ' ' < /proc/$pid/cmdline; printf '\\n'; done",
            ),
            b"",
            30,
        )
        assert process_args.exit_code == 0
        assert "s3fs" in process_args.stdout
        assert "sbxcred" in process_args.stdout
        assert secret.decode().strip() not in process_args.stdout

        written = await carrier.exec(
            handle,
            ("sh", "-c", "printf mounted > /workspace/proof.txt && cat /workspace/proof.txt"),
            b"",
            30,
        )
        assert written.exit_code == 0
        assert written.stdout == "mounted"
        assert (
            await s3_store.get(f"{workspace_key_prefix(conversation_id)}/proof.txt") == b"mounted"
        )

        token_read = await carrier.exec(handle, ("cat", SANDBOX_FS_TOKEN_PATH), b"", 30)
        assert token_read.exit_code != 0
    finally:
        if handle is not None:
            await carrier.destroy(handle)
        await proxy.stop()


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
                mount=MountSpec(kind="filesystem", host_path=str(tmp_path)),
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


async def test_read_pptx_renders_slides_as_image_blocks(
    file_ctx: tuple[ToolContext, Path],
) -> None:
    ctx, _ = file_ctx
    built = await ctx.sandbox.bash(
        "python3 - <<'PY'\n"
        "from pptx import Presentation\n"
        "p = Presentation()\n"
        "slide = p.slides.add_slide(p.slide_layouts[5])\n"
        "slide.shapes.title.text = 'Hello sbxfs PPTX'\n"
        "p.save('/workspace/deck.pptx')\n"
        "PY\n"
    )
    assert built.exit_code == 0, built.stderr
    result = await _run("read", ctx, file_path="deck.pptx")
    text_block = result.content[0]
    assert isinstance(text_block, TextContent)
    assert "pptx slides 1-1 of 1]" in text_block.text
    slides = [block for block in result.content if isinstance(block, ImageContent)]
    assert slides and slides[0].media_type == "image/png"
    assert base64.b64decode(slides[0].data).startswith(b"\x89PNG")


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
    assert shared["url"].startswith("/artifacts/download?token=")
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
    assert parts[0] == "artifacts" and ".." not in parts and parts[-1] == "x.txt"
    assert claims.filename == "x.txt"


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
    result = await _run("share_file", ctx, file_path="risk.xlsx", name="Q1_Risk_Report")
    token = json.loads(result.content[0].text)["url"].split("token=", 1)[1]
    claims = verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC))
    assert claims.filename == "Q1_Risk_Report.xlsx"

    named = await _run("share_file", ctx, file_path="risk.xlsx", name="already_named.xlsx")
    token = json.loads(named.content[0].text)["url"].split("token=", 1)[1]
    assert (
        verify_artifact_token(token, ARTIFACT_SECRET, datetime.now(UTC)).filename
        == "already_named.xlsx"
    )


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
        system_prompt=rendered_prompt("p"),
        model=_QuietModel(),
        transcript=Transcript(blob=ctx.blob, conversation_id=ctx.turn.conversation_id),
        compaction=Compaction(
            client=_QuietModel(),
            model="claude-opus-4-8",
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
        audience_member_id=ctx.audience_member_id,
        artifact_token_secret=ctx.artifact_token_secret,
        grants=None,
    )


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

    block = await engine._dispatch(ctx, ToolUseBlock(id="call1", name="search_code", input={}))
    path = f"{TOOL_OUTPUT_DIR}/call1.txt"
    assert not block.is_error
    assert isinstance(block.content, str)
    assert path in block.content
    assert len(block.content) <= TOOL_RESULT_PREVIEW_CHARS + len(
        OFFLOAD_NOTICE.format(total=len(payload), path=path)
    )
    assert (workspace / ".tool-output" / "call1.txt").read_text() == payload

    filtered = await _run(
        "bash", ctx, command=f"jq -r '.items[] | \"\\(.path)\\t\\(.html_url)\"' {path}"
    )
    lines = filtered.content[0].text.strip().splitlines()
    assert len(lines) == GITHUB_SEARCH_HITS
    assert lines[0].startswith("src/transport/stream_000.rs\thttps://github.com/owner-000/")
    assert len(filtered.content[0].text) < TOOL_RESULT_PREVIEW_CHARS

    windowed = await _run("read", ctx, file_path=".tool-output/call1.txt", offset=1, limit=1)
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

    block = await engine._dispatch(ctx, ToolUseBlock(id="call2", name="search_code", input={}))
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

    inline = await engine._dispatch(ctx, ToolUseBlock(id="call3", name="at_cap", input={}))
    assert inline.content == at_cap
    assert not (workspace / ".tool-output" / "call3.txt").exists()

    offloaded = await engine._dispatch(ctx, ToolUseBlock(id="call5", name="over_cap", input={}))
    assert isinstance(offloaded.content, str)
    assert f"{TOOL_OUTPUT_DIR}/call5.txt" in offloaded.content
    assert (workspace / ".tool-output" / "call5.txt").read_text() == at_cap + "y"


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

    block = await engine._dispatch(
        ctx, ToolUseBlock(id="call4", name="read", input={"file_path": "wide.log"})
    )
    assert isinstance(block.content, str)
    assert f"{TOOL_OUTPUT_DIR}/call4.txt" in block.content
    offloaded = (workspace / ".tool-output" / "call4.txt").read_text()
    assert len(offloaded) > MAX_TOOL_RESULT_CHARS
    assert lines[-1] in offloaded

    windowed = await _run("read", ctx, file_path="wide.log", offset=1_400, limit=1)
    assert lines[-1] in windowed.content[0].text
    assert len(windowed.content[0].text) < TOOL_RESULT_PREVIEW_CHARS
