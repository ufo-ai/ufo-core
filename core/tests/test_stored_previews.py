"""core's half of a picture a tool renders in the sandbox: name the blob key the signed preview
route serves, take the bytes out of the container, and mint the link the row publishes.

The bytes go straight from the sandbox to the store, so the sandbox is the one stand-in here and
nothing is asserted about the render itself. What is asserted is the durable end: the key, the
measured size, the bytes the store now holds, and the grant the minted link really carries."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from PIL import Image

from ufo.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    mint_image_preview_url,
    verify_artifact_url,
)
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore
from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES, ImagePreviewGrant
from ufo.sandbox.session import ExecResult
from ufo.schema.records import Agent, Turn
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

SHOT_PATH = "/workspace/.tool-output/preview-8000.png"
PUBLIC_BASE_URL = "https://ufo.example.test"
SECRET = "stored-preview-secret"


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PAGE_PNG = _png()


@dataclass
class _ShotSandbox:
    """The sandbox a tool just rendered a picture in: `png` is the file at the shot's path, empty
    for a render that never landed, and `upload` is what the store's own PUT answers. It records the
    commands so a test can say the upload carried the presigned URL."""

    png: bytes = PAGE_PNG
    upload: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        self.commands.append(command)
        if not command.startswith("wc -c"):
            return self.upload
        if not self.png:
            return ExecResult(stdout="", stderr=f"{SHOT_PATH}: No such file", exit_code=1)
        return ExecResult(stdout=str(len(self.png)), stderr="", exit_code=0)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield self.png

        return bytes_of()


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


def _ctx(blob: WorkspaceBlobStore, sandbox: _ShotSandbox) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,  # type: ignore[arg-type]
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="running",
            inbound="deploy the site",
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        ),
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret=SECRET,
    )


async def test_a_rendered_picture_lands_in_the_artifact_namespace(tmp_path: Path) -> None:
    sandbox = _ShotSandbox()
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    ctx = _ctx(blob, sandbox)

    with ws(ctx.turn.workspace_id):
        stored = await ctx.store_preview(SHOT_PATH, "marketing")
        assert stored is not None
        assert await blob.get(stored.blob_key) == PAGE_PNG

    assert stored.blob_key.startswith(ARTIFACT_KEY_PREFIX)
    assert stored.blob_key.endswith("/marketing.png")
    assert stored.size_bytes == len(PAGE_PNG)


async def test_an_s3_store_takes_the_bytes_on_a_presigned_put(s3_store: S3BlobStore) -> None:
    sandbox = _ShotSandbox()
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)

    with ws(ctx.turn.workspace_id):
        stored = await ctx.store_preview(SHOT_PATH, "marketing")

    assert stored is not None
    upload = sandbox.commands[-1]
    assert f"-T {SHOT_PATH}" in upload
    assert stored.blob_key in upload
    assert "X-Amz-Signature" in upload


async def test_a_render_that_never_landed_stores_nothing(tmp_path: Path) -> None:
    """`wc -c` fails on a path holding no file, which is the whole readiness check: nothing is
    uploaded and the caller's row keeps no key."""
    sandbox = _ShotSandbox(png=b"")
    ctx = _ctx(WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)), sandbox)

    with ws(ctx.turn.workspace_id):
        assert await ctx.store_preview(SHOT_PATH, "marketing") is None

    assert sandbox.commands == [f"wc -c < {SHOT_PATH}"]


async def test_an_upload_the_store_refused_records_no_preview(s3_store: S3BlobStore) -> None:
    sandbox = _ShotSandbox(upload=ExecResult(stdout="", stderr="curl: (22) 403", exit_code=22))
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)

    with ws(ctx.turn.workspace_id):
        assert await ctx.store_preview(SHOT_PATH, "marketing") is None


def test_a_minted_preview_link_grants_the_picture_it_names() -> None:
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/marketing.png"

    url = mint_image_preview_url(
        SECRET, PUBLIC_BASE_URL, key, len(PAGE_PNG), workspace_id=workspace_id
    )

    assert url is not None
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    artifact_id, filename = parsed.path.removeprefix(f"/{ARTIFACT_KEY_PREFIX}").split("/")
    claims = verify_artifact_url(
        SECRET,
        artifact_id,
        filename,
        query["exp"][0],
        query["sig"][0],
        query["preview"][0],
        query["ws"][0],
        datetime.now(UTC),
    )
    assert claims.blob_key == key
    assert claims.workspace_id == workspace_id
    assert claims.preview == ImagePreviewGrant(media_type="image/png", size_bytes=len(PAGE_PNG))


def test_a_preview_link_is_withheld_where_the_route_could_not_serve_it() -> None:
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/marketing.png"
    text = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/notes.txt"

    assert mint_image_preview_url(SECRET, None, key, 10, workspace_id=workspace_id) is None
    assert mint_image_preview_url("", PUBLIC_BASE_URL, key, 10, workspace_id=workspace_id) is None
    assert (
        mint_image_preview_url(SECRET, PUBLIC_BASE_URL, text, 10, workspace_id=workspace_id) is None
    )
    assert (
        mint_image_preview_url(
            SECRET, PUBLIC_BASE_URL, key, IMAGE_PREVIEW_MAX_BYTES + 1, workspace_id=workspace_id
        )
        is None
    )
