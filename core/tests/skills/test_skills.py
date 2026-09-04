import json
import zipfile
from base64 import urlsafe_b64decode
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from ufo.harness.containment import ContainmentError
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.host.ext.loader import member_skill_listing, turn_member_skills
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import Manifest, MemberSkillsSpec
from ufo.runtime.queue import _without_workspace_skills
from ufo.runtime.skills.runtime import (
    CORE_SKILL_NAMES,
    CORE_SKILL_REGISTRY,
    CORE_SKILLS,
    SUGGESTION_LIMIT,
    LoadedRef,
    LoadedSkills,
    RuntimeSkill,
    SkillCard,
    SkillRegistry,
    SystemSkillBundle,
    discover_skills,
    install_skill,
    loaded_context,
    parse_skill,
    parse_skill_content,
)


def _write_nested_child(
    parent_dir: Path,
    name: str,
    description: str,
    body: str,
    depends: tuple[str, ...] = (),
    *,
    indexed: bool = False,
) -> Path:
    child_dir = parent_dir / name
    child_dir.mkdir()
    (child_dir / "SKILL.md").write_text(
        _skill_md(name, description, body, depends, indexed=indexed)
    )
    return child_dir


def _skill_md(
    name: str,
    description: str,
    body: str,
    depends: tuple[str, ...] = (),
    *,
    indexed: bool = False,
) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if depends or indexed:
        lines.append("metadata:")
    if depends:
        lines.append("  depends:")
        lines.extend(f"  - {dep}" for dep in depends)
    if indexed:
        lines.append("  indexed: true")
    lines.extend(("---", body))
    return "\n".join(lines) + "\n"


def _write_skill(
    root: Path, name: str, description: str, body: str, depends: tuple[str, ...] = ()
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_skill_md(name, description, body, depends))
    return skill_dir


def test_core_ships_exactly_the_fixed_skills() -> None:
    assert {skill.name for skill in CORE_SKILLS} == set(CORE_SKILL_NAMES)


def test_the_house_style_ships_the_logo_sheet_and_names_it() -> None:
    """The logo sheet is bytes the deploy carries, so every workspace holds it without anyone
    fetching a file: the skill mounts its own files, and a member who asks for the logo is handed
    that sheet rather than a mark the agent drew. The skill has to name it, or nothing reads it.

    The share is of a workspace copy, because `share_file` stages its path through the workspace
    guard and the skill tree is outside it: a section that shared the mounted path would fail every
    ask for the logo."""
    style = CORE_SKILL_REGISTRY.named("ufo-style")
    sheet = dict(style.files)["assets/ufo-logo-ratio.pdf"]
    assert sheet.startswith(b"%PDF")
    assert "assets/ufo-logo-ratio.pdf" in style.instructions
    assert "share_file" in style.instructions
    assert "/workspace/ufo-logo-ratio.pdf" in style.instructions


def test_skill_named_unknown_fails_loud_and_suggests_only_the_closest() -> None:
    """The message is bounded by what is close, never by how many skills exist: a member-authored
    set runs to hundreds, and every name in the error is context spent on one typo — the model
    already carries the whole index in its system prompt."""
    registry = SkillRegistry(
        {
            name: RuntimeSkill(name=name, description="d", instructions="i")
            for name in (f"ghost-{index}" for index in range(200))
        }
    )
    with pytest.raises(ValueError, match="unknown skill 'ghost'") as raised:
        registry.named("ghost")
    suggested = str(raised.value).partition("closest: ")[2].rstrip(")").split(", ")
    assert len(suggested) == SUGGESTION_LIMIT
    assert set(suggested) <= set(registry.by_name)
    with pytest.raises(ValueError, match=r"unknown skill 'ghost'$"):
        CORE_SKILL_REGISTRY.named("ghost")


def test_parse_reads_frontmatter_body_and_bundled_files(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "probe", "a probe skill", "Do the thing.")
    (skill_dir / "helper.py").write_bytes(b"print('hi')")
    skill = parse_skill(skill_dir)
    assert skill.name == "probe"
    assert skill.description == "a probe skill"
    assert skill.instructions == "Do the thing."
    assert ("helper.py", b"print('hi')") in skill.files
    assert skill.raw_skill_md.startswith("---\n")


def test_system_skill_bundle_is_content_addressed_and_deterministic(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "probe", "a probe skill", "Do the thing.")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_bytes(b"print('hi')")
    skill = parse_skill(skill_dir)

    first = SystemSkillBundle.from_skills((skill,))
    second = SystemSkillBundle.from_skills((skill,))

    assert first == second
    manifest = json.loads(first.manifest)
    assert manifest["digest"] == first.digest
    assert manifest["skills"] == {
        "probe": {
            "digest": skill.content_digest(),
            "files": ["SKILL.md", "scripts/run.py"],
        }
    }
    with zipfile.ZipFile(BytesIO(first.archive)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "probe/SKILL.md",
            "probe/scripts/run.py",
        }
        assert archive.read("probe/scripts/run.py") == b"print('hi')"


def test_parse_rejects_a_name_that_does_not_match_its_directory(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "declared", "x", "y")
    (skill_dir / "SKILL.md").write_text(_skill_md("mismatch", "x", "y"))
    with pytest.raises(ValueError, match="must match its directory"):
        parse_skill(skill_dir)


def test_parse_rejects_a_file_without_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "bare"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# no frontmatter here\n")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill(skill_dir)


def test_closure_leads_with_the_asked_for_skill_then_its_dependencies(tmp_path: Path) -> None:
    base = parse_skill(_write_skill(tmp_path, "base", "base skill", "base body"))
    leaf = parse_skill(_write_skill(tmp_path, "leaf", "leaf skill", "leaf body", depends=("base",)))
    registry = SkillRegistry({"base": base, "leaf": leaf})
    assert [ref.card.name for ref in registry.closure("leaf")] == ["leaf", "base"]


async def test_loaded_context_closes_with_one_tree_for_the_whole_closure(tmp_path: Path) -> None:
    """Every workflow first, then a single tree of everything mounted — not a tree per skill. Only
    `SKILL.md` content is injected; a bundled file appears as a path and nothing more."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "base body")
    (base_dir / "notes.md").write_text("BUNDLED CONTENT")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "leaf body", depends=("base",))
    (leaf_dir / "scripts").mkdir()
    (leaf_dir / "scripts" / "run.py").write_text("print(1)")
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(await registry.materialize(registry.closure("leaf")))

    assert text.count("Loaded files:") == 1
    assert "BUNDLED CONTENT" not in text
    assert text.index("leaf body") < text.index("base body") < text.index("Loaded files:")
    assert text.endswith(
        "Loaded files:\n"
        "$UFO_HOME/skills/\n"
        "  base/\n"
        "    SKILL.md\n"
        "    notes.md\n"
        "  leaf/\n"
        "    SKILL.md\n"
        "    scripts/\n"
        "      run.py"
    )


async def test_a_skill_already_in_context_costs_a_note_instead_of_its_workflow(
    tmp_path: Path,
) -> None:
    """A repeat load: every file still mounts and the tree still names the whole closure, but the
    workflow the model is already reading is not sent a second time."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "BASE BODY")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "LEAF BODY", depends=("base",))
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(await registry.materialize(registry.closure("leaf")), {"leaf", "base"})

    assert "BODY" not in text
    assert "# Skill:" not in text
    assert text.startswith("Already in context above, not repeated: leaf, base\n\nLoaded files:")
    assert "$UFO_HOME/skills/\n  base/\n    SKILL.md\n  leaf/\n    SKILL.md" in text


async def test_a_dependency_already_in_context_still_injects_the_asked_for_workflow(
    tmp_path: Path,
) -> None:
    """Suppression is per skill inside one closure: loading a skill whose dependency is already in
    context pays for the one workflow that is new and names the other."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "BASE BODY")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "LEAF BODY", depends=("base",))
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(await registry.materialize(registry.closure("leaf")), {"base"})

    assert "# Skill: leaf\n\nLEAF BODY" in text
    assert "BASE BODY" not in text
    assert "Already in context above, not repeated: base" in text
    assert text.index("LEAF BODY") < text.index("not repeated: base") < text.index("Loaded files:")


def test_loaded_skills_drain_yields_the_asked_for_names_and_empties_the_tracker() -> None:
    leaf = RuntimeSkill(name="leaf", description="d", instructions="LEAF", depends=("base",))
    base = RuntimeSkill(name="base", description="d", instructions="BASE")
    tracker = LoadedSkills()
    tracker.reseed(
        ((LoadedRef(card=leaf.card()), LoadedRef(card=base.card(), dependency_of="leaf")),)
    )

    assert tracker.drain() == ("leaf",)
    assert tracker.in_context == set()
    assert tracker.asked_for == set()
    assert tracker.drain() == ()


def test_closure_follows_a_dependency_chain_to_its_end(tmp_path: Path) -> None:
    """`depends` is fully recursive: a chain resolves to its last link, and a cycle anywhere in it
    terminates instead of looping."""
    for name, dep in (("a", "b"), ("b", "c"), ("c", "d")):
        _write_skill(tmp_path, name, f"{name} skill", f"{name} body", depends=(dep,))
    _write_skill(tmp_path, "d", "d skill", "d body")
    chain = SkillRegistry({name: parse_skill(tmp_path / name) for name in "abcd"})
    assert [ref.card.name for ref in chain.closure("a")] == ["a", "b", "c", "d"]

    looped = SkillRegistry(
        {
            "x": RuntimeSkill(name="x", description="x", instructions="x", depends=("y",)),
            "y": RuntimeSkill(name="y", description="y", instructions="y", depends=("x",)),
        }
    )
    assert [ref.card.name for ref in looped.closure("x")] == ["x", "y"]


def test_nesting_alone_pulls_no_parent_and_the_index_hides_the_child(tmp_path: Path) -> None:
    """Nesting names a child and nests its mount path; it never pulls the parent. A child that
    needs the parent declares `depends` — the one mechanism that pulls."""
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "p")
    _write_nested_child(parent_dir, "app", "a child skill", "c")
    registry = SkillRegistry(discover_skills(parent_dir))
    assert [ref.card.name for ref in registry.closure("site/app")] == ["site/app"]
    assert registry.index() == (("site", "a parent skill"),)


def test_indexed_metadata_must_be_boolean(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "probe", "a probe skill", "p")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: probe\ndescription: a probe skill\nmetadata:\n  indexed: sometimes\n---\np\n"
    )

    with pytest.raises(ValueError, match=r"metadata\.indexed must be a boolean"):
        parse_skill(skill_dir)


def test_a_nested_child_that_declares_its_parent_pulls_it(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "p")
    _write_nested_child(parent_dir, "app", "a child skill", "c", depends=("site",))
    registry = SkillRegistry(discover_skills(parent_dir))
    assert [ref.card.name for ref in registry.closure("site/app")] == ["site/app", "site"]


class _RecordingSandbox:
    """Records every mounted path exactly as the carrier would receive it. The real carrier writes
    with `write_bytes` after `mkdir(exist_ok=True)`, so a repeat mount overwrites its own paths and
    touches nothing else — this stands in for that, never asserting on a fake for behaviour."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files if files is not None else {}

    async def load_skills(self, payload: dict[str, object]) -> dict[str, str]:
        roots: dict[str, str] = {}
        user = payload["user"]
        assert isinstance(user, dict)
        for name, wire in user.items():
            assert isinstance(name, str) and isinstance(wire, dict)
            files = wire["files"]
            assert isinstance(files, dict)
            root = f"$UFO_HOME/skills/{name}"
            for path, content in files.items():
                assert isinstance(path, str) and isinstance(content, str)
                self.files[f"{root}/{path}"] = urlsafe_b64decode(content)
            roots[name] = root
        return roots


def _bundle(root: Path, name: str, body: str, depends: tuple[str, ...] = ()) -> Path:
    """A skill with assets at top level and two directories deep, so a mount assertion covers
    nesting rather than only the `SKILL.md` beside it."""
    skill_dir = _write_skill(root, name, f"{name} skill", body, depends)
    (skill_dir / "notes.md").write_text(f"{name} notes")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text(f"# {name}")
    (skill_dir / "scripts" / "templates").mkdir()
    (skill_dir / "scripts" / "templates" / "seed.xml").write_text(f"<{name}/>")
    return skill_dir


async def test_a_load_mounts_every_file_of_every_skill_it_pulls(tmp_path: Path) -> None:
    """A dependency is mounted whole, not just its `SKILL.md`: its assets are what the injected
    workflow sends the agent to read, so every one has to be on disk after the load."""
    _bundle(tmp_path, "base", "base body")
    _bundle(tmp_path, "leaf", "leaf body", depends=("base",))
    registry = SkillRegistry({name: parse_skill(tmp_path / name) for name in ("base", "leaf")})
    sandbox = _RecordingSandbox()

    for entry in await registry.materialize(registry.closure("leaf")):
        await install_skill(sandbox, entry.skill)

    assert set(sandbox.files) == {
        f"$UFO_HOME/skills/{name}/{path}"
        for name in ("base", "leaf")
        for path in ("SKILL.md", "notes.md", "scripts/run.py", "scripts/templates/seed.xml")
    }
    assert sandbox.files["$UFO_HOME/skills/base/scripts/templates/seed.xml"] == b"<base/>"


async def test_a_later_load_leaves_everything_else_in_the_workspace_alone(tmp_path: Path) -> None:
    """A mount writes only the paths the skill declares. Work the agent produced — anywhere in the
    workspace, including inside a mounted skill's own directory — survives a later load."""
    _bundle(tmp_path, "base", "base body")
    skill = parse_skill(tmp_path / "base")
    produced = {
        "/workspace/report.docx": b"the deliverable",
        "$UFO_HOME/skills/base/scratch.md": b"agent scratch",
    }
    sandbox = _RecordingSandbox(dict(produced))

    await install_skill(sandbox, skill)
    await install_skill(sandbox, skill)

    assert produced.items() <= sandbox.files.items()
    assert sandbox.files["$UFO_HOME/skills/base/SKILL.md"] == skill.raw_skill_md.encode()


def test_a_dependency_cycle_resolves_each_skill_once_however_it_is_shaped() -> None:
    """`depends` is member-authored, so a cycle is reachable state, not a bug to assert against.
    Every shape of it terminates and yields each skill once: a self-dependency, a mutual pair, a
    longer ring, and a ring entered through an acyclic chain."""

    def registry(**depends: tuple[str, ...]) -> SkillRegistry:
        return SkillRegistry(
            {
                name: RuntimeSkill(name=name, description=name, instructions=name, depends=deps)
                for name, deps in depends.items()
            }
        )

    def loaded(reg: SkillRegistry, name: str) -> list[str]:
        return [ref.card.name for ref in reg.closure(name)]

    assert loaded(registry(s=("s",)), "s") == ["s"]
    assert loaded(registry(x=("y",), y=("x",)), "x") == ["x", "y"]
    assert loaded(registry(a=("b",), b=("c",), c=("a",)), "a") == ["a", "b", "c"]
    entered = registry(head=("ring",), ring=("spoke",), spoke=("ring",))
    assert loaded(entered, "head") == ["head", "ring", "spoke"]

    mutual = registry(x=("y",), y=("x",))
    both = mutual.closure("x", "y")
    assert [ref.card.name for ref in both] == ["x", "y"]
    assert [entry.dependency_of for entry in both] == [None, None]


def test_a_named_skill_is_never_labelled_a_dependency_of_another_named_skill() -> None:
    """Several names load together (a subagent's `preload_skills`). A name the caller asked for is
    direct even when another name depends on it, in either order — otherwise `base` would render
    as `# Skill: base (dependency of leaf)` despite having been requested outright."""
    registry = SkillRegistry(
        {
            "base": RuntimeSkill(name="base", description="b", instructions="b"),
            "leaf": RuntimeSkill(name="leaf", description="l", instructions="l", depends=("base",)),
        }
    )

    for names in (("leaf", "base"), ("base", "leaf")):
        loaded = registry.closure(*names)
        assert {ref.card.name for ref in loaded} == {"leaf", "base"}
        assert [entry.dependency_of for entry in loaded] == [None, None]

    pulled = registry.closure("leaf")
    assert [(ref.card.name, ref.dependency_of) for ref in pulled] == [
        ("leaf", None),
        ("base", "leaf"),
    ]


def test_an_unknown_dependency_fails_loud_naming_the_missing_skill() -> None:
    registry = SkillRegistry(
        {"leaf": RuntimeSkill(name="leaf", description="d", instructions="i", depends=("ghost",))}
    )
    with pytest.raises(ValueError, match="unknown skill 'ghost'"):
        registry.closure("leaf")


def test_parse_skill_content_rejects_a_missing_skill_md() -> None:
    with pytest.raises(ValueError, match="has no"):
        parse_skill_content("probe", {"notes.txt": b"loose"})


def test_parse_skill_content_rejects_a_name_that_does_not_match_its_directory() -> None:
    files = {"SKILL.md": _skill_md("declared", "x", "y").encode()}
    with pytest.raises(ValueError, match="must match its directory"):
        parse_skill_content("mismatch", files)


def test_merged_with_appends_generated_skills_beside_the_core_floor() -> None:
    generated = RuntimeSkill(
        name="model-catalog", description="a generated skill", instructions="g"
    )
    merged = CORE_SKILL_REGISTRY.merged_with((generated,))
    assert merged.named("model-catalog") is generated
    assert set(CORE_SKILL_NAMES) <= set(merged.by_name)
    assert ("model-catalog", "a generated skill") in merged.index()


def test_merged_with_never_lets_a_generated_skill_shadow_a_core_skill() -> None:
    impostor = RuntimeSkill(name="sandbox", description="hijacked", instructions="evil")
    merged = CORE_SKILL_REGISTRY.merged_with((impostor,))
    assert merged.named("sandbox") is CORE_SKILL_REGISTRY.named("sandbox")
    assert merged.named("sandbox").description != "hijacked"


async def test_mount_writes_the_verbatim_skill_md_and_assets_under_the_workspace() -> None:
    sandbox = _RecordingSandbox()
    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=(("data/notes.txt", b"kept"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )
    await install_skill(sandbox, skill)
    assert sandbox.files["$UFO_HOME/skills/probe/SKILL.md"] == b"---\nname: probe\n---\nbody\n"
    assert sandbox.files["$UFO_HOME/skills/probe/data/notes.txt"] == b"kept"


@pytest.mark.parametrize("key", ["../../evil.md", "data/../../../evil.md", "/workspace/evil.md"])
async def test_mount_refuses_a_file_key_that_climbs_out_of_the_skill(key: str) -> None:
    """A file key is contained at `$UFO_HOME/skills/<name>/`."""
    sandbox = _RecordingSandbox()
    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=((key, b"planted"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )

    with pytest.raises(ContainmentError):
        await install_skill(sandbox, skill)

    assert not any(path.endswith("evil.md") for path in sandbox.files)


async def test_install_replaces_a_planted_user_skill_tree_without_following_links(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    session = SandboxSession(
        carrier=carrier,
        handle=handle,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    ufo_home = Path(handle.egress_env["UFO_HOME"])
    (ufo_home / "skills" / "probe").mkdir(parents=True)
    (ufo_home / "skills" / "probe" / "data").symlink_to(outside)
    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=(("data/notes.txt", b"kept"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )

    await install_skill(session, skill)

    assert list(outside.iterdir()) == []
    assert (ufo_home / "skills" / "probe" / "data" / "notes.txt").read_bytes() == b"kept"


async def test_install_replaces_a_top_level_user_skill_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    session = SandboxSession(carrier=carrier, handle=handle)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "kept.txt"
    sentinel.write_text("kept")
    ufo_home = Path(handle.egress_env["UFO_HOME"])
    root = ufo_home / "skills"
    root.mkdir(parents=True, exist_ok=True)
    name = "top-level-link-probe"
    (root / name).symlink_to(outside, target_is_directory=True)
    skill = RuntimeSkill(
        name=name,
        description="d",
        instructions="i",
        raw_skill_md=f"---\nname: {name}\n---\nbody\n",
    )

    await install_skill(session, skill)

    assert sentinel.read_text() == "kept"
    assert not (root / name).is_symlink()
    assert (root / name / "SKILL.md").read_text() == skill.raw_skill_md


def _member_registry(
    *cards: SkillCard, rows: dict[str, RuntimeSkill] | None = None
) -> tuple[SkillRegistry, list[str]]:
    stored = rows if rows is not None else {}
    calls: list[str] = []

    async def materialize(name: str) -> RuntimeSkill | None:
        calls.append(name)
        return stored.get(name)

    return CORE_SKILL_REGISTRY.with_member(cards, materialize), calls


def test_closure_resolves_member_cards_without_touching_a_stored_body() -> None:
    registry, calls = _member_registry(
        SkillCard(name="greet", description="say hi", depends=("farewell",)),
        SkillCard(name="farewell", description="say bye"),
    )

    refs = registry.closure("greet")

    assert [(ref.card.name, ref.dependency_of) for ref in refs] == [
        ("greet", None),
        ("farewell", "greet"),
    ]
    assert calls == []


def test_member_closure_pulls_a_deploy_dependency_and_survives_a_cycle() -> None:
    registry, _calls = _member_registry(
        SkillCard(name="ship", description="d", depends=("sandbox", "review")),
        SkillCard(name="review", description="d", depends=("ship",)),
    )

    refs = registry.closure("ship")

    assert [(ref.card.name, ref.dependency_of) for ref in refs] == [
        ("ship", None),
        ("sandbox", "ship"),
        ("review", "ship"),
    ]


async def test_materialize_reads_member_rows_and_deploy_skills_in_closure_order() -> None:
    greet = RuntimeSkill(name="greet", description="say hi", instructions="HI")
    registry, calls = _member_registry(
        SkillCard(name="greet", description="say hi", depends=("sandbox",)),
        rows={"greet": greet},
    )

    loaded = await registry.materialize(registry.closure("greet"))

    assert [(entry.skill.name, entry.dependency_of) for entry in loaded] == [
        ("greet", None),
        ("sandbox", "greet"),
    ]
    assert loaded[0].skill is greet
    assert loaded[1].skill is CORE_SKILL_REGISTRY.named("sandbox")
    assert calls == ["greet"]


async def test_materialize_of_a_vanished_member_row_fails_loud_naming_the_skill() -> None:
    registry, _calls = _member_registry(SkillCard(name="gone", description="d"))
    with pytest.raises(ValueError, match="skill 'gone' is no longer available"):
        await registry.materialize(registry.closure("gone"))


def test_with_member_never_lets_a_member_card_shadow_a_deploy_skill() -> None:
    registry, _calls = _member_registry(
        SkillCard(name="sandbox", description="hijacked"),
        SkillCard(name="greet", description="say hi"),
    )

    assert set(registry.member_cards) == {"greet"}
    assert registry.named("sandbox").description != "hijacked"


def test_merged_with_evicts_a_member_card_a_generated_skill_now_shadows() -> None:
    registry, _calls = _member_registry(SkillCard(name="setup", description="member text"))
    generated = RuntimeSkill(name="setup", description="deploy setup", instructions="g")

    merged = registry.merged_with((generated,))

    assert merged.named("setup") is generated
    assert "setup" not in merged.member_cards
    assert merged.materializer is registry.materializer


def test_index_excludes_member_cards_and_includes_generated_skills() -> None:
    generated = RuntimeSkill(name="model-catalog", description="the models", instructions="g")
    base, _calls = _member_registry(SkillCard(name="greet", description="say hi"))
    registry = base.merged_with((generated,))

    index = dict(registry.index())

    assert "greet" not in index
    assert index["model-catalog"] == "the models"
    assert set(CORE_SKILL_NAMES) <= set(index)


def test_known_names_and_all_cards_span_both_tiers() -> None:
    registry, _calls = _member_registry(SkillCard(name="greet", description="say hi"))

    assert registry.known_names() == frozenset(CORE_SKILL_REGISTRY.by_name) | {"greet"}
    cards = registry.all_cards()
    assert cards[-1] == SkillCard(name="greet", description="say hi")
    assert {card.name for card in cards} == registry.known_names()


def test_an_unknown_member_name_suggests_the_closest_without_enumerating() -> None:
    registry, _calls = _member_registry(
        SkillCard(name="deploy-frontend", description="d"),
        SkillCard(name="unrelated", description="d"),
    )
    with pytest.raises(ValueError, match="unknown skill 'deploy-frontnd'") as caught:
        registry.closure("deploy-frontnd")
    assert "deploy-frontend" in str(caught.value)
    assert "unrelated" not in str(caught.value)


def _provider_manifest(
    name: str, *skills: RuntimeSkill, cards: tuple[SkillCard, ...] | None = None
) -> Manifest:
    rows = {skill.name: skill for skill in skills}

    async def provider_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]:
        return cards if cards is not None else tuple(skill.card() for skill in skills)

    async def materialize(ctx: ExtensionContext, requested: str) -> RuntimeSkill | None:
        return rows.get(requested)

    async def materialize_all(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]:
        return tuple(rows.values())

    return Manifest(
        name=name,
        version="0",
        member_skills=MemberSkillsSpec(
            cards=provider_cards, materialize=materialize, materialize_all=materialize_all
        ),
    )


_STORE = CredentialStore(fernet=Fernet(Fernet.generate_key()))
GREET = RuntimeSkill(name="greet", description="say hi", instructions="HI")
FAREWELL = RuntimeSkill(name="farewell", description="say bye", instructions="BYE")


async def test_turn_member_skills_collects_cards_and_routes_materialization() -> None:
    cards, materialize = await turn_member_skills(
        (_provider_manifest("one", GREET),), _STORE, agent_name="assistant"
    )
    assert cards == (GREET.card(),)
    assert await materialize("greet") is GREET
    assert await materialize("unknown") is None


async def test_turn_member_skills_without_a_credential_key_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="no credential key"):
        await turn_member_skills((_provider_manifest("one", GREET),), None, agent_name="assistant")


async def test_a_cross_provider_name_collision_keeps_the_first_and_drops_the_rest() -> None:
    impostor = RuntimeSkill(name="greet", description="other greet", instructions="OTHER")
    manifests = (
        _provider_manifest("first", GREET, FAREWELL),
        _provider_manifest("second", impostor),
    )

    cards, materialize = await turn_member_skills(manifests, _STORE, agent_name="assistant")

    assert cards == (GREET.card(), FAREWELL.card())
    assert await materialize("greet") is GREET


async def test_frontmatter_targeting_narrows_the_member_tier_to_named_agents() -> None:
    """A card whose `agents` names agents is left out of every other agent's tier whole: it does
    not route and its name does not load; the untargeted card reaches every agent."""
    targeted = RuntimeSkill(
        name="triage", description="sort tickets", instructions="SORT", agents=("helpdesk",)
    )
    manifests = (_provider_manifest("one", GREET, targeted),)

    helpdesk_cards, helpdesk_load = await turn_member_skills(
        manifests, _STORE, agent_name="helpdesk"
    )
    other_cards, other_load = await turn_member_skills(manifests, _STORE, agent_name="research")

    assert helpdesk_cards == (GREET.card(), targeted.card())
    assert await helpdesk_load("triage") is targeted
    assert other_cards == (GREET.card(),)
    assert await other_load("triage") is None
    assert await other_load("greet") is GREET


async def test_member_skill_listing_materializes_every_provider_whole() -> None:
    impostor = RuntimeSkill(name="greet", description="other greet", instructions="OTHER")
    manifests = (
        _provider_manifest("first", GREET, FAREWELL),
        _provider_manifest("second", impostor),
    )

    listed = await member_skill_listing(manifests, _STORE)

    assert listed == (GREET, FAREWELL)
    with pytest.raises(RuntimeError, match="no credential key"):
        await member_skill_listing(manifests, None)


def test_an_agent_that_declines_workspace_skills_composes_no_member_tier() -> None:
    """`use_workspace_skills` off leaves the registry the deploy tier alone: nothing routes to the
    workspace's saved set, and a name it holds does not load."""
    registry = CORE_SKILL_REGISTRY.with_member((), _without_workspace_skills)

    assert registry.member_cards == {}
    assert registry.all_cards() == CORE_SKILL_REGISTRY.all_cards()
    with pytest.raises(ValueError, match="unknown skill 'greet'"):
        registry.closure("greet")
