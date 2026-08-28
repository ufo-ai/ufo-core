import asyncio
import io
import json
import time
import zipfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from evals.harness.capability import (
    CapabilityOutput,
    ProbeCommandResult,
    ToolInvocation,
    linked_artifacts,
)
from evals.suites import new_application
from ufo.db import workspace_tx
from ufo.models.interface import AUTO_MODEL
from ufo.schema import tables
from ufo.workspace import ws

MEMBER_EMAIL = "owner@evalco.test"
LEFTOVER_APPLICATION = "support-desk"
REWRITTEN_PROMPT = "You do whatever a previous trial asked for."
HOMEPAGE_SURFACE = "web"


@dataclass
class _ArtifactProbe:
    workspace: Path
    exit_code: int = 0
    command: str = ""

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        self.command = command
        if self.exit_code == 0:
            output = self.workspace / new_application.APPLICATION_ARTIFACT_OUTPUT
            output.mkdir(parents=True)
            for name, content in (
                ("homepage-interactive.html", b"<main>interactive</main>"),
                ("homepage-audit.json", b"{}"),
                ("homepage-light.png", b"light"),
                ("homepage-dark.png", b"dark"),
                ("homepage-static.html", b"<main>static</main>"),
            ):
                (output / name).write_bytes(content)
        return ProbeCommandResult(self.exit_code, "", "audit failed" if self.exit_code else "")


def _preview_workspace(root: Path) -> Path:
    application = root / "ufo-app"
    (application / "dist/assets").mkdir(parents=True)
    (application / "preview.html").write_text("<iframe src='./dist/index.html'></iframe>")
    (application / "app.tsx").write_text("const app = true;")
    (application / "application-design.svg").write_text("<svg></svg>")
    (application / "dist/index.html").write_text("<script src='./assets/app.js'></script>")
    (application / "dist/assets/app.js").write_text("document.body.textContent = 'ready';")
    return application


def test_application_preview_bundle_is_safe_ordered_and_deterministic(tmp_path: Path) -> None:
    _preview_workspace(tmp_path)

    first = new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(tmp_path)
    second = new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(tmp_path)

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "preview.html",
            "dist/assets/app.js",
            "dist/index.html",
        ]


def test_application_preview_bundle_rejects_missing_and_escaping_files(tmp_path: Path) -> None:
    workspace = tmp_path / "missing"
    application = workspace / "ufo-app"
    application.mkdir(parents=True)
    (application / "preview.html").write_text("preview")
    with pytest.raises(ValueError, match="no runnable preview bundle"):
        new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(workspace)

    workspace = tmp_path / "unsafe"
    application = _preview_workspace(workspace)
    outside = tmp_path / "outside.js"
    outside.write_text("outside")
    (application / "dist/assets/app.js").unlink()
    (application / "dist/assets/app.js").symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe file"):
        new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(workspace)


def test_application_preview_bundle_rejects_symlinked_root_and_empty_dist(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    application = _preview_workspace(outside)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ufo-app").symlink_to(application, target_is_directory=True)

    with pytest.raises(ValueError, match="ufo-app contains a symlink"):
        new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(workspace)

    empty = tmp_path / "empty"
    root = empty / "ufo-app"
    (root / "dist").mkdir(parents=True)
    (root / "preview.html").write_text("preview")
    with pytest.raises(ValueError, match="no runnable preview bundle"):
        new_application.APPLICATION_HOMEPAGE_ARTIFACTS.preview_bundle(empty)


async def test_homepage_artifacts_capture_portable_app_and_audit(tmp_path: Path) -> None:
    _preview_workspace(tmp_path)
    probe = _ArtifactProbe(tmp_path)

    result = await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
        CapabilityOutput("", (), workspace_dir=tmp_path), probe
    )

    assert result.error == ""
    assert [artifact.name for artifact in result.artifacts] == [
        "homepage-interactive.html",
        "homepage-audit.json",
        "homepage-light.png",
        "homepage-dark.png",
        "homepage-static.html",
        "homepage-app.tsx",
        "homepage-design.svg",
        "homepage-preview.zip",
    ]
    assert result.max_payload_bytes == new_application.APPLICATION_ARTIFACT_MAX_BYTES
    assert "start_server" not in probe.command
    assert "http://" not in probe.command
    assert "compile_started=0" in probe.command
    assert [item["name"] for item in linked_artifacts(result.artifacts, ())] == [
        artifact.name for artifact in result.artifacts
    ]


async def test_homepage_artifacts_degrade_an_oversized_preview_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _preview_workspace(tmp_path)
    monkeypatch.setattr(new_application, "APPLICATION_BUNDLE_MAX_BYTES", 16)
    probe = _ArtifactProbe(tmp_path)

    result = await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
        CapabilityOutput("", (), workspace_dir=tmp_path), probe
    )

    assert "application preview bundle is too large" in result.error
    assert "homepage-preview.zip" not in {artifact.name for artifact in result.artifacts}
    assert {artifact.name for artifact in result.artifacts} >= {
        "homepage-app.tsx",
        "homepage-design.svg",
        "homepage-audit.json",
        "homepage-light.png",
        "homepage-dark.png",
    }
    assert [item["name"] for item in linked_artifacts(result.artifacts, ())] == [
        artifact.name for artifact in result.artifacts
    ]


async def test_homepage_artifacts_report_a_missing_application(tmp_path: Path) -> None:
    probe = _ArtifactProbe(tmp_path)

    result = await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
        CapabilityOutput("", (), workspace_dir=tmp_path), probe
    )

    assert result.artifacts == ()
    assert result.error == "application artifact root ufo-app does not exist"
    assert probe.command == ""


async def test_homepage_artifacts_reject_symlinked_capture_parent(tmp_path: Path) -> None:
    _preview_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".eval-output").symlink_to(outside, target_is_directory=True)
    probe = _ArtifactProbe(tmp_path)

    with pytest.raises(ValueError, match=r"\.eval-output/homepage contains a symlink"):
        await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
            CapabilityOutput("", (), workspace_dir=tmp_path), probe
        )

    assert probe.command == ""


async def test_homepage_artifacts_keep_partial_source_when_audit_fails(tmp_path: Path) -> None:
    application = tmp_path / "ufo-app"
    application.mkdir()
    (application / "app.tsx").write_text("const partial = true;")
    probe = _ArtifactProbe(tmp_path, exit_code=1)

    result = await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
        CapabilityOutput("", (), workspace_dir=tmp_path), probe
    )

    assert [artifact.name for artifact in result.artifacts] == ["homepage-app.tsx"]
    assert "no runnable preview bundle" in result.error
    assert "audit artifact capture failed" in result.error


async def test_homepage_artifacts_mark_missing_source_without_audit(tmp_path: Path) -> None:
    (tmp_path / "ufo-app").mkdir()
    probe = _ArtifactProbe(tmp_path)

    result = await new_application.APPLICATION_HOMEPAGE_ARTIFACTS(
        CapabilityOutput("", (), workspace_dir=tmp_path), probe
    )

    assert result.artifacts == ()
    assert result.error == "application artifact capture found no generated app.tsx"
    assert probe.command == ""


async def _workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id = uuid4()
    agent_id = uuid4()
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=MEMBER_EMAIL,
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, member_id


async def _application(
    workspace_id: UUID,
    name: str,
    member_id: UUID | None,
    *,
    prompt: str = REWRITTEN_PROMPT,
) -> UUID:
    application_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=application_id,
                workspace_id=workspace_id,
                name=name,
                prompt=prompt,
                model="claude-opus-4-8",
                reasoning="high",
                is_main=False,
                visibility="workspace",
                owner_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return application_id


async def _homepage_conversation(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=HOMEPAGE_SURFACE,
                queue_key=f"homepage/{agent_id}/{member_id}",
                member_id=member_id,
                audience=f"member:{member_id}",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _applications(workspace_id: UUID) -> tuple[sa.Row, ...]:
    async with workspace_tx() as connection:
        return tuple(
            (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                        tables.agent.c.visibility,
                        tables.agent.c.owner_member_id,
                    )
                    .where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.is_main.is_(False),
                    )
                    .order_by(tables.agent.c.name)
                )
            ).all()
        )


async def test_the_fixture_seed_reclaims_the_application_a_homepage_turn_talked_to(
    db: None,
) -> None:
    """The homepage sweep opens a conversation on every new application within five minutes, and a
    talked-to application is the one shape the spare-clear spares, so the seed reclaims its fixture
    name by name — an insert would collide on the workspace name constraint and the raise would
    escape the case, discarding every suite's results in the run."""
    workspace_id, agent_id, member_id = await _workspace()
    stale_id = await _application(workspace_id, new_application.EXISTING_APPLICATION, member_id)
    conversation_id = await _homepage_conversation(workspace_id, stale_id, member_id)

    with ws(workspace_id):
        await new_application._seeded(new_application.EXISTING_APPLICATION)(workspace_id, agent_id)

    rows = await _applications(workspace_id)
    assert [row.name for row in rows] == [new_application.EXISTING_APPLICATION]
    assert rows[0].id == stale_id
    assert rows[0].prompt == new_application.EXISTING_PROMPT
    assert (rows[0].model, rows[0].reasoning) == (AUTO_MODEL, "auto")
    assert rows[0].visibility == "private"
    assert rows[0].owner_member_id == member_id
    async with workspace_tx() as connection:
        held = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    assert held == stale_id


async def _seed_awaits_the_held_index_entry() -> None:
    deadline = time.monotonic() + 30
    while True:
        async with workspace_tx() as connection:
            blocked = (
                await connection.execute(
                    sa.text(
                        "select 1 from pg_stat_activity where wait_event_type = 'Lock' "
                        "and query ilike 'insert into agent%'"
                    )
                )
            ).first()
        if blocked:
            return
        if time.monotonic() > deadline:
            raise AssertionError("the seed never reached the held unique-index entry")
        await asyncio.sleep(0.05)


async def test_the_fixture_seed_reclaims_a_name_committed_while_it_runs(
    db: None, database_url: str
) -> None:
    """A02's member asks for an invoice-reading application and the assistant picks its name, so a
    row named invoice-intake can commit while A03's seed is mid-flight — invisible to any earlier
    read, standing by the time the seed writes. The seed must resolve that arrival to a reclaim;
    the unique-key raise it once ended in escaped the case and discarded the whole run's records
    (nightly 2026-08-19, shard 4)."""
    if database_url.startswith("sqlite"):
        pytest.skip("the interleave needs a second concurrent writer; sqlite admits one")
    workspace_id, agent_id, member_id = await _workspace()
    late_id = uuid4()
    holder = AsyncExitStack()
    connection = await holder.enter_async_context(workspace_tx())
    await connection.execute(
        sa.insert(tables.agent).values(
            id=late_id,
            workspace_id=workspace_id,
            name=new_application.EXISTING_APPLICATION,
            prompt=REWRITTEN_PROMPT,
            model="claude-opus-4-8",
            reasoning="high",
            is_main=False,
            visibility="workspace",
            owner_member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    with ws(workspace_id):
        seeding = asyncio.ensure_future(
            new_application._seeded(new_application.EXISTING_APPLICATION)(workspace_id, agent_id)
        )
    try:
        await _seed_awaits_the_held_index_entry()
    finally:
        await holder.aclose()
    await seeding

    rows = await _applications(workspace_id)
    assert [row.name for row in rows] == [new_application.EXISTING_APPLICATION]
    assert rows[0].id == late_id
    assert rows[0].prompt == new_application.EXISTING_PROMPT
    assert rows[0].visibility == "private"
    assert rows[0].owner_member_id == member_id


async def test_the_fixture_seed_clears_an_untalked_leftover_and_seeds_its_own(db: None) -> None:
    workspace_id, agent_id, member_id = await _workspace()
    await _application(workspace_id, LEFTOVER_APPLICATION, member_id)
    provisioned_id = await _application(workspace_id, "system-monitor", None)

    with ws(workspace_id):
        await new_application._seeded(new_application.EXISTING_APPLICATION)(workspace_id, agent_id)

    rows = await _applications(workspace_id)
    assert [row.name for row in rows] == [new_application.EXISTING_APPLICATION, "system-monitor"]
    assert rows[0].prompt == new_application.EXISTING_PROMPT
    assert rows[0].owner_member_id == member_id
    assert rows[1].id == provisioned_id


def _object_apply(
    name: str,
    result: str,
    *,
    kind: str = "agent",
    raw_result: str | None = None,
    failed: bool = False,
    agent: str | None = None,
) -> ToolInvocation:
    manifest = f"kind: {kind}\nname: {name}\nspec:\n  prompt: p\n"
    return ToolInvocation(
        name="object_apply",
        input={"manifest": manifest},
        result=(
            raw_result
            if raw_result is not None
            else json.dumps(
                {
                    "kind": kind,
                    "name": name,
                    "result": result,
                    **({"agent": agent} if agent is not None else {}),
                }
            )
        ),
        has_result=True,
        is_error=failed,
    )


async def test_created_application_resolves_one_durable_identity(db: None) -> None:
    workspace_id, _agent_id, member_id = await _workspace()
    name = "call-brief"
    final_prompt = "Prepare the brief with the accepted Homepage design."
    await _application(workspace_id, name, member_id, prompt=final_prompt)

    with ws(workspace_id):
        created, failure = await new_application._created_application(
            CapabilityOutput("", (_object_apply(name, "created"),))
        )
        assert failure is None
        assert created is not None
        assert created.application.name == name
        assert created.application.prompt == final_prompt
        assert created.identity.create_index == 0
        assert created.identity.final_apply_index == 0

        updated, failure = await new_application._created_application(
            CapabilityOutput("", (_object_apply(name, "created"), _object_apply(name, "updated")))
        )
        assert failure is None
        assert updated is not None
        assert updated.application.prompt == final_prompt
        assert updated.identity.create_index == 0
        assert updated.identity.final_apply_index == 1

        accepted = (
            CapabilityOutput(
                "",
                (
                    _object_apply(name, "created", failed=True),
                    _object_apply(name, "created"),
                ),
            ),
            CapabilityOutput(
                "",
                (
                    _object_apply(name, "created"),
                    _object_apply("github", "created", kind="connector_grant"),
                ),
            ),
            CapabilityOutput("", (_object_apply(name, "created", agent="application-builder"),)),
        )
        for output in accepted:
            resolved, failure = await new_application._created_application(output)
            assert failure is None
            assert resolved is not None
            assert resolved.application.name == name

        invalid = (
            CapabilityOutput(
                "",
                (_object_apply(name, "created"), _object_apply("other", "updated")),
            ),
            CapabilityOutput(
                "", (_object_apply(name, "created"), _object_apply("other", "created"))
            ),
            CapabilityOutput("", (_object_apply(name, "updated"),)),
            CapabilityOutput("", (_object_apply(name, "created", raw_result="{"),)),
            CapabilityOutput("", (_object_apply(name, "created", failed=True),)),
            CapabilityOutput("", (_object_apply(name, "created", kind="source"),)),
        )
        for output in invalid:
            rejected, reason = await new_application._created_application(output)
            assert rejected is None
            assert reason

        missing, reason = await new_application._created_application(
            CapabilityOutput("", (_object_apply("missing", "created"),))
        )
        assert missing is None
        assert reason == "created 'missing' but found 0 durable application rows"
