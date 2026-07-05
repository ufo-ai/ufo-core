"""The workspace-scoped user-skill loop end to end: a skill authored in the workspace is validated,
persisted, and merged back into that workspace's loadable set on later turns — resolvable by name,
never visible to another workspace, never able to shadow a core skill.

The store tests drive `UserSkillStore` against a real database and a real filesystem blob store; the
tool test drives `save_custom_skill` through a real `SandboxSession` on the local carrier (host
subprocesses, no container), so the sandbox read is real, not a fake."""

import json
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.sandbox.local import LocalCarrier
from selfhost.sandbox.session import (
    MountSpec,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.skills.runtime import CORE_SKILL_NAMES, CORE_SKILL_REGISTRY
from selfhost.skills.store import SkillCollidesWithCoreSkill, UserSkillStore
from selfhost.tools.builtins import SaveCustomSkillInput, save_custom_skill_handler
from selfhost.tools.context import SpawnResult, ToolContext


def _skill_md(name: str, description: str, body: str = "Follow the steps.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n".encode()


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


async def test_save_persists_and_load_all_round_trips(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    files = {"SKILL.md": _skill_md("greet", "greets people"), "references/tone.md": b"warm"}

    saved = await store.save(workspace_id, "greet", files, frozenset(CORE_SKILL_NAMES))
    assert saved.name == "greet"
    assert saved.description == "greets people"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.user_skill.c.name, tables.user_skill.c.digest).where(
                    tables.user_skill.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert row.name == "greet"
    assert row.digest.startswith("sha256:")

    loaded = await store.load_all(workspace_id)
    assert [skill.name for skill in loaded] == ["greet"]
    assert loaded[0].instructions == "Follow the steps."
    assert ("references/tone.md", b"warm") in loaded[0].files


async def test_merged_registry_resolves_a_saved_user_skill(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    await store.save(
        workspace_id, "greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset()
    )

    merged = CORE_SKILL_REGISTRY.merged_with(await store.load_all(workspace_id))
    assert [skill.name for skill in merged.tree("greet")] == ["greet"]
    assert ("greet", "greets people") in merged.index()
    assert set(CORE_SKILL_NAMES) <= set(merged.by_name)


async def test_a_saved_skill_is_scoped_to_its_workspace(db: None, tmp_path) -> None:
    author, other = await _workspace(), await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    await store.save(
        author, "greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset()
    )

    assert [skill.name for skill in await store.load_all(author)] == ["greet"]
    assert await store.load_all(other) == ()
    assert "greet" not in CORE_SKILL_REGISTRY.merged_with(await store.load_all(other)).by_name


async def test_save_refuses_a_name_that_shadows_a_core_skill(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    files = {"SKILL.md": _skill_md("sandbox", "a hijack attempt")}

    with pytest.raises(SkillCollidesWithCoreSkill):
        await store.save(workspace_id, "sandbox", files, frozenset(CORE_SKILL_NAMES))
    assert await store.load_all(workspace_id) == ()


async def test_resaving_an_owned_skill_replaces_it_in_place(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    await store.save(workspace_id, "greet", {"SKILL.md": _skill_md("greet", "v1")}, frozenset())

    await store.save(
        workspace_id, "greet", {"SKILL.md": _skill_md("greet", "v2")}, frozenset({"greet"})
    )

    loaded = await store.load_all(workspace_id)
    assert [skill.name for skill in loaded] == ["greet"]
    assert loaded[0].description == "v2"


async def test_save_custom_skill_tool_round_trips_through_the_sandbox(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="selfhost-sandbox:latest",
            mount=MountSpec(kind="filesystem", host_path=str(tmp_path / "workspace")),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file("greet/SKILL.md", _skill_md("greet", "greets people"))
    await session.write_file("greet/references/tone.md", b"warm")
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="save it",
    )
    ctx = ToolContext(
        sandbox=session,
        blob=blob,
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="secret",
    )

    result = await save_custom_skill_handler(ctx, SaveCustomSkillInput(path="greet"))
    payload = json.loads(result.content[0].text)
    assert payload["skill"] == "greet"
    assert payload["files"] == 2

    merged = CORE_SKILL_REGISTRY.merged_with(await UserSkillStore(blob=blob).load_all(workspace_id))
    assert [skill.name for skill in merged.tree("greet")] == ["greet"]
    assert ("references/tone.md", b"warm") in merged.named("greet").files
