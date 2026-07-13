"""The workspace-scoped user-skill loop end to end: a skill authored in the workspace is validated,
persisted in the extension's own `user_skill` table, and merged back into that workspace's loadable
set on later turns — resolvable by name, never visible to another workspace, never able to shadow a
core skill.

The store tests drive `UserSkillStore` over a real `ExtensionContext` against a real database; the
tool test drives `save_custom_skill` through a real `SandboxSession` on the local carrier (host
subprocesses, no container), so the sandbox read is real, not a fake. The seam tests drive the real
`turn_runtime_skills` collector over the extension's real `runtime_skills` provider — the producer
and consumer proven together."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_skill_create.manifest import (
    SaveCustomSkillInput,
    manifest,
    save_custom_skill_handler,
)
from ufo_ext_skill_create.store import (
    InvalidSkillName,
    SkillCollidesWithCoreSkill,
    TooManyUserSkills,
    UserSkillStore,
    user_skill,
)

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import turn_runtime_skills
from ufo.loop.profiles import GENERAL_PURPOSE_PROFILE
from ufo.loop.prompts.render import render_system_prompt
from ufo.loop.subagents import subagent_system_prompt
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    MountSpec,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.skills.runtime import CORE_SKILL_NAMES, CORE_SKILL_REGISTRY, RuntimeSkill
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws


def _skill_md(name: str, description: str, body: str = "Follow the steps.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n".encode()


def _credential_store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _ext() -> ExtensionContext:
    return context_for("skill_create", frozenset())


def _store() -> UserSkillStore:
    return UserSkillStore(_ext())


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


async def test_save_persists_and_load_all_round_trips(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    files = {"SKILL.md": _skill_md("greet", "greets people"), "references/tone.md": b"warm"}

    saved = await store.save(workspace_id, "greet", files, frozenset(CORE_SKILL_NAMES))
    assert saved.name == "greet"
    assert saved.description == "greets people"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(user_skill.c.name, user_skill.c.digest).where(
                    user_skill.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert row.name == "greet"
    assert row.digest.startswith("sha256:")

    loaded = await store.load_all(workspace_id)
    assert [skill.name for skill in loaded] == ["greet"]
    assert loaded[0].instructions == "Follow the steps."
    assert ("references/tone.md", b"warm") in loaded[0].files


async def test_merged_registry_resolves_a_saved_user_skill(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.save(
        workspace_id, "greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset()
    )

    merged = CORE_SKILL_REGISTRY.merged_with(await store.load_all(workspace_id))
    assert [skill.name for skill in merged.tree("greet")] == ["greet"]
    assert ("greet", "greets people") in merged.index()
    assert set(CORE_SKILL_NAMES) <= set(merged.by_name)


async def test_a_saved_skill_is_scoped_to_its_workspace(db: None) -> None:
    author, other = await _workspace(), await _workspace()
    await _store().save(
        author, "greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset()
    )

    assert [skill.name for skill in await _store().load_all(author)] == ["greet"]
    assert await _store().load_all(other) == ()
    merged_other = CORE_SKILL_REGISTRY.merged_with(await _store().load_all(other))
    assert "greet" not in merged_other.by_name


async def test_save_refuses_a_name_that_shadows_a_core_skill(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    files = {"SKILL.md": _skill_md("sandbox", "a hijack attempt")}

    with pytest.raises(SkillCollidesWithCoreSkill):
        await store.save(workspace_id, "sandbox", files, frozenset(CORE_SKILL_NAMES))
    assert await store.load_all(workspace_id) == ()


@pytest.mark.parametrize(
    "bad_name", ["../../etc", "..", ".", "a/b", "sandbox/child", "Sandbox", "my skill", "", "-x"]
)
async def test_save_refuses_an_unsafe_skill_name(db: None, bad_name: str) -> None:
    workspace_id = await _workspace()
    store = _store()
    with pytest.raises(InvalidSkillName):
        await store.save(
            workspace_id, bad_name, {"SKILL.md": _skill_md(bad_name, "d")}, frozenset()
        )
    assert await store.load_all(workspace_id) == ()


async def test_save_accepts_a_valid_slug_name(db: None) -> None:
    workspace_id = await _workspace()
    saved = await _store().save(
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


async def test_load_all_skips_a_corrupt_skill_and_keeps_the_rest(db: None) -> None:
    """A saved skill whose stored bundle no longer parses is dropped with a log, not raised — one
    bad skill must never wedge the workspace's turns, and the member's other skills still load."""
    workspace_id = await _workspace()
    store = _store()
    await store.save(workspace_id, "alpha", {"SKILL.md": _skill_md("alpha", "A")}, frozenset())
    await store.save(workspace_id, "beta", {"SKILL.md": _skill_md("beta", "B")}, frozenset())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(user_skill)
            .values(content="{ not valid json")
            .where(
                user_skill.c.workspace_id == workspace_id,
                user_skill.c.name == "alpha",
            )
        )

    assert [skill.name for skill in await store.load_all(workspace_id)] == ["beta"]


async def test_save_refuses_over_the_skill_cap_but_allows_a_resave(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo_ext_skill_create.store.MAX_USER_SKILLS_PER_WORKSPACE", 2)
    workspace_id = await _workspace()
    store = _store()
    await store.save(workspace_id, "one", {"SKILL.md": _skill_md("one", "1")}, frozenset())
    await store.save(workspace_id, "two", {"SKILL.md": _skill_md("two", "2")}, frozenset())

    with pytest.raises(TooManyUserSkills):
        await store.save(workspace_id, "three", {"SKILL.md": _skill_md("three", "3")}, frozenset())
    await store.save(workspace_id, "one", {"SKILL.md": _skill_md("one", "1b")}, frozenset({"one"}))

    assert sorted(skill.name for skill in await store.load_all(workspace_id)) == ["one", "two"]


async def test_resaving_an_owned_skill_replaces_it_in_place(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.save(workspace_id, "greet", {"SKILL.md": _skill_md("greet", "v1")}, frozenset())

    await store.save(
        workspace_id, "greet", {"SKILL.md": _skill_md("greet", "v2")}, frozenset({"greet"})
    )

    loaded = await store.load_all(workspace_id)
    assert [skill.name for skill in loaded] == ["greet"]
    assert loaded[0].description == "v2"


async def test_turn_runtime_skills_feeds_a_saved_skill_into_the_merge(db: None) -> None:
    """The seam end to end: the extension's `runtime_skills` provider is the producer, the real
    `turn_runtime_skills` collector the consumer — a saved skill flows through into the merged
    registry, and a second workspace's turn sees none of it."""
    author, other = await _workspace(), await _workspace()
    await _store().save(
        author, "greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset()
    )

    with ws(author):
        provided = await turn_runtime_skills((manifest(),), _credential_store())
    assert [skill.name for skill in provided] == ["greet"]
    merged = CORE_SKILL_REGISTRY.merged_with(provided)
    assert ("greet", "greets people") in merged.index()
    main_prompt = render_system_prompt(
        "You are the assistant.", (), skills=merged.index(), model="claude-opus-4-8"
    ).content
    subagent_prompt = subagent_system_prompt(GENERAL_PURPOSE_PROFILE, skills=merged.index())
    assert "- greet: greets people" in main_prompt
    assert "- greet: greets people" in subagent_prompt

    with ws(other):
        assert await turn_runtime_skills((manifest(),), _credential_store()) == ()


async def test_turn_runtime_skills_without_a_credential_key_fails_loud() -> None:
    with pytest.raises(RuntimeError):
        await turn_runtime_skills((manifest(),), None)


async def test_save_custom_skill_tool_round_trips_through_the_sandbox(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            mount=MountSpec(kind="filesystem", host_path=str(tmp_path / "workspace")),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file("greet/SKILL.md", _skill_md("greet", "greets people"))
    await session.write_file("greet/references/tone.md", b"warm")
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="save it",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=session,
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret="secret",
        ext=_ext(),
    )

    result = await save_custom_skill_handler(ctx, SaveCustomSkillInput(path="greet"))
    payload = json.loads(result.content[0].text)
    assert payload["skill"] == "greet"
    assert payload["files"] == 2

    merged = CORE_SKILL_REGISTRY.merged_with(await _store().load_all(workspace_id))
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
            image_ref="ufo-sandbox:latest",
            mount=MountSpec(kind="filesystem", host_path=str(tmp_path / "workspace")),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file("greet/SKILL.md", _skill_md("greet", "greets people"))
    linked = await session.bash("ln -s /dev/zero /workspace/greet/evil")
    assert linked.exit_code == 0
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="save it",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    ctx = ToolContext(
        sandbox=session,
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret="secret",
        ext=_ext(),
    )

    result = await save_custom_skill_handler(ctx, SaveCustomSkillInput(path="greet"))
    payload = json.loads(result.content[0].text)
    assert payload["files"] == 1
    loaded = await _store().load_all(workspace_id)
    assert [path for path, _ in loaded[0].files] == []
