"""core's half of a shared document's preview: mint the unmeasured PUT for the preview key, send
the file to the preview service in one request carrying that `put_url`, and record the size the
service reports. The render and the store are the service's own, proven end-to-end in the preview
crate's suite; here the transport returns the service's contractual metadata reply."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ufo.blob import S3BlobStore, WorkspaceBlobStore
from ufo.media.artifact_url import ARTIFACT_KEY_PREFIX
from ufo.sandbox.session import ExecResult
from ufo.schema.records import Agent, Turn
from ufo.tools import builtins
from ufo.tools.context import SpawnResult, ToolContext
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws


@dataclass
class _ReplyingSandbox:
    """A sandbox stand-in returning one canned command result — the preview service's reply — so
    the test drives `_shared_preview` without the service and egress proxy the live path needs."""

    result: ExecResult
    command: str = ""

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        self.command = command
        return self.result


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


def _ctx(blob: WorkspaceBlobStore, sandbox: _ReplyingSandbox) -> ToolContext:
    workspace_id = uuid4()
    return ToolContext(
        sandbox=sandbox,  # type: ignore[arg-type]
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="preview-test-secret",
    )


async def test_shared_preview_records_the_size_the_service_reports(
    s3_store: S3BlobStore,
) -> None:
    sandbox = _ReplyingSandbox(
        ExecResult(stdout=json.dumps({"size_bytes": 5120, "page_count": 1}), stderr="", exit_code=0)
    )
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)
    with ws(ctx.turn.workspace_id):
        preview = await builtins._shared_preview(ctx, "/workspace/report.pdf", "report.pdf")
    assert preview is not None
    assert preview.media_type == "image/png"
    assert preview.size_bytes == 5120
    assert preview.blob_key.startswith(ARTIFACT_KEY_PREFIX)
    assert preview.blob_key.endswith("/report.png")
    assert "/render" in sandbox.command
    assert "put_url" in sandbox.command
    assert '"kind": "pdf"' in sandbox.command
    assert "file=@/workspace/report.pdf" in sandbox.command


async def test_shared_preview_nones_on_a_service_failure(
    s3_store: S3BlobStore,
) -> None:
    sandbox = _ReplyingSandbox(ExecResult(stdout="", stderr="curl: (22) 502", exit_code=22))
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)
    with ws(ctx.turn.workspace_id):
        assert await builtins._shared_preview(ctx, "/workspace/report.pdf", "report.pdf") is None


async def test_shared_preview_nones_on_an_unparseable_reply(
    s3_store: S3BlobStore,
) -> None:
    sandbox = _ReplyingSandbox(ExecResult(stdout="not json", stderr="", exit_code=0))
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)
    with ws(ctx.turn.workspace_id):
        assert await builtins._shared_preview(ctx, "/workspace/report.pdf", "report.pdf") is None


async def test_shared_preview_skips_an_ineligible_suffix(
    s3_store: S3BlobStore,
) -> None:
    sandbox = _ReplyingSandbox(ExecResult(stdout="", stderr="", exit_code=0))
    ctx = _ctx(WorkspaceBlobStore(backend=s3_store), sandbox)
    with ws(ctx.turn.workspace_id):
        assert await builtins._shared_preview(ctx, "/workspace/notes.txt", "notes.txt") is None
    assert sandbox.command == ""
