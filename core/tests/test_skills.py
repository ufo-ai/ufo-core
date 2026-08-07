from pathlib import Path
from uuid import uuid4

import pytest

from ufo.sandbox.containment import ContainmentError
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.skills.runtime import (
    CORE_SKILL_NAMES,
    CORE_SKILL_REGISTRY,
    CORE_SKILLS,
    LoadedSkill,
    LoadedSkills,
    RuntimeSkill,
    SkillRegistry,
    discover_skills,
    loaded_context,
    mount_skill,
    parse_skill,
    parse_skill_content,
)


def _write_nested_child(
    parent_dir: Path, name: str, description: str, body: str, depends: tuple[str, ...] = ()
) -> Path:
    child_dir = parent_dir / name
    child_dir.mkdir()
    (child_dir / "SKILL.md").write_text(_skill_md(name, description, body, depends))
    return child_dir


def _skill_md(name: str, description: str, body: str, depends: tuple[str, ...] = ()) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if depends:
        lines.append("metadata:")
        lines.append("  depends:")
        lines.extend(f"  - {dep}" for dep in depends)
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


def test_skill_named_unknown_fails_loud_and_lists_the_available() -> None:
    with pytest.raises(ValueError, match="unknown skill 'ghost'"):
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
    assert [entry.skill.name for entry in registry.closure("leaf")] == ["leaf", "base"]


def test_prompt_body_is_a_header_naming_the_skill_and_its_workflow(tmp_path: Path) -> None:
    """A header names the skill; the description and `depends` do not follow it into the load. They
    are routing metadata the skill index carries, not instructions the agent acts on."""
    skill_dir = _write_skill(
        tmp_path, "leaf", "a routing description", "# Leaf\n\nleaf body", depends=("base",)
    )

    body = LoadedSkill(skill=parse_skill(skill_dir)).prompt_body()

    assert body == "# Skill: leaf\n\n# Leaf\n\nleaf body"
    assert "a routing description" not in body
    assert "base" not in body


def test_a_pulled_skills_header_names_the_skill_that_pulled_it(tmp_path: Path) -> None:
    """The agent can tell the workflow it asked for from the ones that rode along: a dependency's
    header names its puller, and down a chain each hop names the link above it."""
    for name, dep in (("a", "b"), ("b", "c")):
        _write_skill(tmp_path, name, f"{name} skill", f"{name} body", depends=(dep,))
    _write_skill(tmp_path, "c", "c skill", "c body")
    registry = SkillRegistry({name: parse_skill(tmp_path / name) for name in "abc"})

    headers = [entry.prompt_body().splitlines()[0] for entry in registry.closure("a")]

    assert headers == [
        "# Skill: a",
        "# Skill: b (dependency of a)",
        "# Skill: c (dependency of b)",
    ]


def test_loaded_context_closes_with_one_tree_for_the_whole_closure(tmp_path: Path) -> None:
    """Every workflow first, then a single tree of everything mounted — not a tree per skill. Only
    `SKILL.md` content is injected; a bundled file appears as a path and nothing more."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "base body")
    (base_dir / "notes.md").write_text("BUNDLED CONTENT")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "leaf body", depends=("base",))
    (leaf_dir / "scripts").mkdir()
    (leaf_dir / "scripts" / "run.py").write_text("print(1)")
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(registry.closure("leaf"))

    assert text.count("Mounted files:") == 1
    assert "BUNDLED CONTENT" not in text
    assert text.index("leaf body") < text.index("base body") < text.index("Mounted files:")
    assert text.endswith(
        "Mounted files:\n"
        "/workspace/.skills/\n"
        "  base/\n"
        "    SKILL.md\n"
        "    notes.md\n"
        "  leaf/\n"
        "    SKILL.md\n"
        "    scripts/\n"
        "      run.py"
    )


def test_a_skill_already_in_context_costs_a_note_instead_of_its_workflow(tmp_path: Path) -> None:
    """A repeat load: every file still mounts and the tree still names the whole closure, but the
    workflow the model is already reading is not sent a second time."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "BASE BODY")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "LEAF BODY", depends=("base",))
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(registry.closure("leaf"), {"leaf", "base"})

    assert "BODY" not in text
    assert "# Skill:" not in text
    assert text.startswith("Already in context above, not repeated: leaf, base\n\nMounted files:")
    assert "/workspace/.skills/\n  base/\n    SKILL.md\n  leaf/\n    SKILL.md" in text


def test_a_dependency_already_in_context_still_injects_the_asked_for_workflow(
    tmp_path: Path,
) -> None:
    """Suppression is per skill inside one closure: loading a skill whose dependency is already in
    context pays for the one workflow that is new and names the other."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "BASE BODY")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "LEAF BODY", depends=("base",))
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})

    text = loaded_context(registry.closure("leaf"), {"base"})

    assert "# Skill: leaf\n\nLEAF BODY" in text
    assert "BASE BODY" not in text
    assert "Already in context above, not repeated: base" in text
    assert text.index("LEAF BODY") < text.index("not repeated: base") < text.index("Mounted files:")


def test_loaded_skills_reseeds_from_the_closure_a_load_injected(tmp_path: Path) -> None:
    """The tracker takes a load as the closure it resolved to: every entry names a skill whose
    workflow is in context, and only an entry no other skill pulled is one the agent asked for."""
    base_dir = _write_skill(tmp_path, "base", "base skill", "BASE BODY")
    leaf_dir = _write_skill(tmp_path, "leaf", "leaf skill", "LEAF BODY", depends=("base",))
    registry = SkillRegistry({"base": parse_skill(base_dir), "leaf": parse_skill(leaf_dir)})
    tracker = LoadedSkills()

    tracker.reseed((registry.closure("leaf"),))

    assert tracker.in_context == {"leaf", "base"}
    assert tracker.asked_for == {"leaf"}


def test_loaded_skills_reseed_replaces_rather_than_accumulates() -> None:
    """The window is the record: a load the window no longer carries is dropped, so a shrunk window
    never leaves the tracker claiming a workflow the model can no longer read."""
    gone = RuntimeSkill(name="gone", description="d", instructions="body")
    kept = RuntimeSkill(name="kept", description="d", instructions="body")
    tracker = LoadedSkills()

    tracker.reseed(((LoadedSkill(skill=gone),),))
    assert tracker.in_context == {"gone"}

    tracker.reseed(((LoadedSkill(skill=kept),),))
    assert tracker.in_context == {"kept"}
    assert tracker.asked_for == {"kept"}

    tracker.reseed(())
    assert tracker.in_context == set()


def test_loaded_skills_drain_yields_the_asked_for_names_and_empties_the_tracker() -> None:
    leaf = RuntimeSkill(name="leaf", description="d", instructions="LEAF", depends=("base",))
    base = RuntimeSkill(name="base", description="d", instructions="BASE")
    tracker = LoadedSkills()
    tracker.reseed(((LoadedSkill(skill=leaf), LoadedSkill(skill=base, dependency_of="leaf")),))

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
    assert [entry.skill.name for entry in chain.closure("a")] == ["a", "b", "c", "d"]

    looped = SkillRegistry(
        {
            "x": RuntimeSkill(name="x", description="x", instructions="x", depends=("y",)),
            "y": RuntimeSkill(name="y", description="y", instructions="y", depends=("x",)),
        }
    )
    assert [entry.skill.name for entry in looped.closure("x")] == ["x", "y"]


def test_closure_of_a_core_skill_returns_it() -> None:
    assert [entry.skill.name for entry in CORE_SKILL_REGISTRY.closure("sandbox")] == ["sandbox"]


def test_discover_registers_a_parent_and_its_nested_child_by_path_form(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "parent body")
    (parent_dir / "shared").mkdir()
    (parent_dir / "shared" / "tokens.md").write_text("design tokens")
    child_dir = _write_nested_child(parent_dir, "app", "a child skill", "child body")
    (child_dir / "template.txt").write_text("scaffold")

    discovered = discover_skills(parent_dir)

    assert set(discovered) == {"site", "site/app"}
    assert discovered["site"].parent is None
    assert discovered["site/app"].parent == "site"
    parent_files = {path for path, _ in discovered["site"].files}
    assert "shared/tokens.md" in parent_files
    assert not any(path.startswith("app/") for path in parent_files)
    assert ("template.txt", b"scaffold") in discovered["site/app"].files


def test_child_frontmatter_name_must_match_its_own_directory(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "parent", "p")
    child_dir = _write_nested_child(parent_dir, "app", "child", "c")
    (child_dir / "SKILL.md").write_text(_skill_md("wrong", "child", "c"))
    with pytest.raises(ValueError, match="must match its directory"):
        discover_skills(parent_dir)


def test_nesting_alone_pulls_no_parent_and_the_index_hides_the_child(tmp_path: Path) -> None:
    """Nesting names a child and nests its mount path; it never pulls the parent. A child that
    needs the parent declares `depends` — the one mechanism that pulls."""
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "p")
    _write_nested_child(parent_dir, "app", "a child skill", "c")
    registry = SkillRegistry(discover_skills(parent_dir))
    assert [entry.skill.name for entry in registry.closure("site/app")] == ["site/app"]
    assert registry.index() == (("site", "a parent skill"),)


def test_a_nested_child_that_declares_its_parent_pulls_it(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "p")
    _write_nested_child(parent_dir, "app", "a child skill", "c", depends=("site",))
    registry = SkillRegistry(discover_skills(parent_dir))
    assert [entry.skill.name for entry in registry.closure("site/app")] == ["site/app", "site"]


async def test_mount_writes_a_nested_child_under_its_parent_path(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "parent", "p")
    _write_nested_child(parent_dir, "app", "child", "c")
    child = discover_skills(parent_dir)["site/app"]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    await mount_skill(_Sandbox(), child)
    assert "/workspace/.skills/site/app/SKILL.md" in written


class _RecordingSandbox:
    """Records every mounted path exactly as the carrier would receive it. The real carrier writes
    with `write_bytes` after `mkdir(exist_ok=True)`, so a repeat mount overwrites its own paths and
    touches nothing else — this stands in for that, never asserting on a fake for behaviour."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files if files is not None else {}

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


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

    for entry in registry.closure("leaf"):
        await mount_skill(sandbox, entry.skill)

    assert set(sandbox.files) == {
        f"/workspace/.skills/{name}/{path}"
        for name in ("base", "leaf")
        for path in ("SKILL.md", "notes.md", "scripts/run.py", "scripts/templates/seed.xml")
    }
    assert sandbox.files["/workspace/.skills/base/scripts/templates/seed.xml"] == b"<base/>"


async def test_loading_a_skill_then_pulling_it_as_a_dependency_remounts_it_cleanly(
    tmp_path: Path,
) -> None:
    """Loading a skill directly and later loading something that `depends` on it mounts it twice.
    The second write is an overwrite, not a conflict — no error, and its files are intact."""
    _bundle(tmp_path, "base", "base body")
    _bundle(tmp_path, "leaf", "leaf body", depends=("base",))
    registry = SkillRegistry({name: parse_skill(tmp_path / name) for name in ("base", "leaf")})
    sandbox = _RecordingSandbox()

    for entry in registry.closure("base"):
        await mount_skill(sandbox, entry.skill)
    first = dict(sandbox.files)
    for entry in registry.closure("leaf"):
        await mount_skill(sandbox, entry.skill)

    assert first.items() <= sandbox.files.items()
    assert sandbox.files["/workspace/.skills/base/notes.md"] == b"base notes"
    assert "/workspace/.skills/leaf/notes.md" in sandbox.files


async def test_a_later_load_leaves_everything_else_in_the_workspace_alone(tmp_path: Path) -> None:
    """A mount writes only the paths the skill declares. Work the agent produced — anywhere in the
    workspace, including inside a mounted skill's own directory — survives a later load."""
    _bundle(tmp_path, "base", "base body")
    skill = parse_skill(tmp_path / "base")
    produced = {
        "/workspace/report.docx": b"the deliverable",
        "/workspace/.skills/base/scratch.md": b"agent scratch",
    }
    sandbox = _RecordingSandbox(dict(produced))

    await mount_skill(sandbox, skill)
    await mount_skill(sandbox, skill)

    assert produced.items() <= sandbox.files.items()
    assert sandbox.files["/workspace/.skills/base/SKILL.md"] == skill.raw_skill_md.encode()


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
        return [entry.skill.name for entry in reg.closure(name)]

    assert loaded(registry(s=("s",)), "s") == ["s"]
    assert loaded(registry(x=("y",), y=("x",)), "x") == ["x", "y"]
    assert loaded(registry(a=("b",), b=("c",), c=("a",)), "a") == ["a", "b", "c"]
    entered = registry(head=("ring",), ring=("spoke",), spoke=("ring",))
    assert loaded(entered, "head") == ["head", "ring", "spoke"]

    mutual = registry(x=("y",), y=("x",))
    both = mutual.closure("x", "y")
    assert [entry.skill.name for entry in both] == ["x", "y"]
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
        assert {entry.skill.name for entry in loaded} == {"leaf", "base"}
        assert [entry.dependency_of for entry in loaded] == [None, None]

    pulled = registry.closure("leaf")
    assert [(e.skill.name, e.dependency_of) for e in pulled] == [("leaf", None), ("base", "leaf")]


def test_closure_of_no_names_is_empty() -> None:
    assert CORE_SKILL_REGISTRY.closure() == ()


def test_an_unknown_dependency_fails_loud_naming_the_missing_skill() -> None:
    registry = SkillRegistry(
        {"leaf": RuntimeSkill(name="leaf", description="d", instructions="i", depends=("ghost",))}
    )
    with pytest.raises(ValueError, match="unknown skill 'ghost'"):
        registry.closure("leaf")


def test_registry_index_lists_every_skills_name_and_description() -> None:
    registry = SkillRegistry(
        {
            "base": RuntimeSkill(name="base", description="base skill", instructions="b"),
            "leaf": RuntimeSkill(name="leaf", description="leaf skill", instructions="l"),
        }
    )
    assert registry.index() == (("base", "base skill"), ("leaf", "leaf skill"))


def test_parse_skill_content_reads_an_in_memory_file_map() -> None:
    files = {
        "SKILL.md": _skill_md("probe", "a probe skill", "Do the thing.").encode(),
        "helper.py": b"print('hi')",
    }
    skill = parse_skill_content("probe", files)
    assert skill.name == "probe"
    assert skill.description == "a probe skill"
    assert skill.instructions == "Do the thing."
    assert ("helper.py", b"print('hi')") in skill.files
    assert skill.raw_skill_md.startswith("---\n")


def test_parse_skill_content_rejects_a_missing_skill_md() -> None:
    with pytest.raises(ValueError, match="has no"):
        parse_skill_content("probe", {"notes.txt": b"loose"})


def test_parse_skill_content_rejects_a_name_that_does_not_match_its_directory() -> None:
    files = {"SKILL.md": _skill_md("declared", "x", "y").encode()}
    with pytest.raises(ValueError, match="must match its directory"):
        parse_skill_content("mismatch", files)


def test_merged_with_appends_user_skills_beside_the_core_floor() -> None:
    user = RuntimeSkill(name="greet", description="a user skill", instructions="say hi")
    merged = CORE_SKILL_REGISTRY.merged_with((user,))
    assert merged.named("greet") is user
    assert set(CORE_SKILL_NAMES) <= set(merged.by_name)
    assert ("greet", "a user skill") in merged.index()


def test_merged_with_never_lets_a_user_skill_shadow_a_core_skill() -> None:
    impostor = RuntimeSkill(name="sandbox", description="hijacked", instructions="evil")
    merged = CORE_SKILL_REGISTRY.merged_with((impostor,))
    assert merged.named("sandbox") is CORE_SKILL_REGISTRY.named("sandbox")
    assert merged.named("sandbox").description != "hijacked"


async def test_mount_writes_the_verbatim_skill_md_and_assets_under_the_workspace() -> None:
    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=(("data/notes.txt", b"kept"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )
    await mount_skill(_Sandbox(), skill)
    assert written["/workspace/.skills/probe/SKILL.md"] == b"---\nname: probe\n---\nbody\n"
    assert written["/workspace/.skills/probe/data/notes.txt"] == b"kept"


@pytest.mark.parametrize("key", ["../../evil.md", "data/../../../evil.md", "/workspace/evil.md"])
async def test_mount_refuses_a_file_key_that_climbs_out_of_the_skill(key: str) -> None:
    """A file key is contained at `.skills/<name>/`, not at the workspace: a key climbing to
    `/workspace/evil.md` is inside the workspace and still refused, because a saved skill would
    otherwise rewrite that file on every load, in every conversation of its agent."""
    sandbox = _RecordingSandbox()
    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=((key, b"planted"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )

    with pytest.raises(ContainmentError):
        await mount_skill(sandbox, skill)

    assert not any(path.endswith("evil.md") for path in sandbox.files)


async def test_mount_refuses_a_planted_symlink_inside_the_mount(tmp_path: Path) -> None:
    """Through a real carrier: the agent can write inside its own mounted skill directory, so it can
    leave a link there between loads. The next mount is refused at the linked component rather than
    writing the skill's file into whatever the link points at."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    session = SandboxSession(
        carrier=carrier,
        handle=await carrier.create(
            SandboxSpec(
                conversation_id=uuid4(),
                image_ref="ufo-sandbox:latest",
                workspace_host_path=str(workspace),
                proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
                run_token="run-token",
            )
        ),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".skills" / "probe").mkdir(parents=True)
    (workspace / ".skills" / "probe" / "data").symlink_to(outside)
    skill = RuntimeSkill(
        name="probe",
        description="d",
        instructions="i",
        files=(("data/notes.txt", b"kept"),),
        raw_skill_md="---\nname: probe\n---\nbody\n",
    )

    with pytest.raises(ContainmentError):
        await mount_skill(session, skill)

    assert list(outside.iterdir()) == []
