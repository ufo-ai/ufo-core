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
from selfhost.skills.runtime import CORE_SKILL_NAMES, CORE_SKILL_REGISTRY, RuntimeSkill
from selfhost.skills.store import (
    InvalidSkillName,
    SkillCollidesWithCoreSkill,
    TooManyUserSkills,
    UserSkillStore,
    _content_key,
)
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


@pytest.mark.parametrize(
    "bad_name", ["../../etc", "..", ".", "a/b", "sandbox/child", "Sandbox", "my skill", "", "-x"]
)
async def test_save_refuses_an_unsafe_skill_name(db: None, tmp_path, bad_name: str) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    with pytest.raises(InvalidSkillName):
        await store.save(
            workspace_id, bad_name, {"SKILL.md": _skill_md(bad_name, "d")}, frozenset()
        )
    assert await store.load_all(workspace_id) == ()


async def test_save_accepts_a_valid_slug_name(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    saved = await store.save(
        workspace_id, "weekly-report", {"SKILL.md": _skill_md("weekly-report", "d")}, frozenset()
    )
    assert saved.name == "weekly-report"


def test_mutually_dependent_user_skills_resolve_without_recursing() -> None:
    alpha = RuntimeSkill(name="alpha", description="A", instructions="a", depends=("beta",))
    beta = RuntimeSkill(name="beta", description="B", instructions="b", depends=("alpha",))
    merged = CORE_SKILL_REGISTRY.merged_with((alpha, beta))
    resolved = [skill.name for skill in merged.tree("alpha")]
    assert sorted(resolved) == ["alpha", "beta"]
    assert len(resolved) == 2


async def test_load_all_skips_a_corrupt_skill_and_keeps_the_rest(db: None, tmp_path) -> None:
    """A saved skill whose stored bundle no longer parses is dropped with a log, not raised — one
    bad skill must never wedge the workspace's turns, and the member's other skills still load."""
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    await store.save(workspace_id, "alpha", {"SKILL.md": _skill_md("alpha", "A")}, frozenset())
    await store.save(workspace_id, "beta", {"SKILL.md": _skill_md("beta", "B")}, frozenset())
    async with workspace_tx() as connection:
        digest = (
            await connection.execute(
                sa.select(tables.user_skill.c.digest).where(
                    tables.user_skill.c.workspace_id == workspace_id,
                    tables.user_skill.c.name == "alpha",
                )
            )
        ).scalar_one()
    await store.blob.put(_content_key(workspace_id, "alpha", digest), b"{ not valid json")

    assert [skill.name for skill in await store.load_all(workspace_id)] == ["beta"]


async def test_save_refuses_over_the_skill_cap_but_allows_a_resave(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("selfhost.skills.store.MAX_USER_SKILLS_PER_WORKSPACE", 2)
    workspace_id = await _workspace()
    store = UserSkillStore(blob=FilesystemBlobStore(root=tmp_path))
    await store.save(workspace_id, "one", {"SKILL.md": _skill_md("one", "1")}, frozenset())
    await store.save(workspace_id, "two", {"SKILL.md": _skill_md("two", "2")}, frozenset())

    with pytest.raises(TooManyUserSkills):
        await store.save(workspace_id, "three", {"SKILL.md": _skill_md("three", "3")}, frozenset())
    await store.save(workspace_id, "one", {"SKILL.md": _skill_md("one", "1b")}, frozenset({"one"}))

    assert sorted(skill.name for skill in await store.load_all(workspace_id)) == ["one", "two"]


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


async def test_save_custom_skill_tool_skips_a_non_regular_file(db: None, tmp_path) -> None:
    """A skill-dir entry that is not a regular file — here a symlink to the unbounded /dev/zero — is
    skipped by the in-sandbox reader, so it neither reads unbounded nor lands in the saved skill."""
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
    linked = await session.bash("ln -s /dev/zero /workspace/greet/evil")
    assert linked.exit_code == 0
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
    assert payload["files"] == 1
    loaded = await UserSkillStore(blob=blob).load_all(workspace_id)
    assert [path for path, _ in loaded[0].files] == []
