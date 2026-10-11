"""Which of a directory's bytes become the site, decided the way a deploy decides it: the real
listing step over a real sandbox — the shipped program, a real interpreter, a real tree on
disk."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from ufo_ext_sites.tools import SOURCE_SKIP_NAMES, _source_listing

from ufo.blob import FilesystemBlobStore
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import WORKSPACE_DIR, SandboxSession, SandboxSpec
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

PAGE = "<!doctype html><title>hello</title>"
STYLE = "body{background:#000;color:#fff}"
PRIVATE_REMOTE = "[remote]\n\turl = git@github.com:owner/private.git\n"


async def _no_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("the listing must not spawn")


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
            created_at=datetime(2026, 8, 23, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


async def test_the_listing_is_what_a_visitor_can_ask_for(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "site"
    (project / "assets").mkdir(parents=True)
    (project / "index.html").write_text(PAGE)
    (project / "assets" / "app.css").write_text(STYLE)
    (project / ".git" / "objects").mkdir(parents=True)
    (project / ".git" / "config").write_text(PRIVATE_REMOTE)
    (project / "node_modules" / "left-pad").mkdir(parents=True)
    (project / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "build.cpython-312.pyc").write_bytes(b"\x00cached")
    (project / ".DS_Store").write_bytes(b"\x00finder")
    ctx = await _context(workspace, tmp_path)

    listed = await _source_listing(ctx, f"{WORKSPACE_DIR}/site")

    assert sorted(listed) == ["assets/app.css", "index.html"]


async def test_a_directory_holding_only_skipped_names_is_refused_as_empty(tmp_path: Path) -> None:
    """The refusal a member can act on: a directory the walk passes over entirely holds no page,
    and saying so beats registering a site whose manifest names nothing."""
    workspace = tmp_path / "workspace"
    project = workspace / "site"
    (project / ".git").mkdir(parents=True)
    (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    ctx = await _context(workspace, tmp_path)

    with pytest.raises(RuntimeError, match="holds no files to host"):
        await _source_listing(ctx, f"{WORKSPACE_DIR}/site")


@pytest.mark.parametrize("name", SOURCE_SKIP_NAMES)
def test_a_skipped_name_is_one_path_component(name: str) -> None:
    """The program matches a name against one component of the walk, so a name carrying a separator
    would never match anything."""
    assert name and "/" not in name
