from pathlib import Path

import pytest

from ufo.skills.runtime import (
    CORE_SKILL_NAMES,
    CORE_SKILL_REGISTRY,
    CORE_SKILLS,
    RuntimeSkill,
    SkillRegistry,
    discover_skills,
    mount_skill,
    parse_skill,
    parse_skill_content,
)


def _write_nested_child(parent_dir: Path, name: str, description: str, body: str) -> Path:
    child_dir = parent_dir / name
    child_dir.mkdir()
    (child_dir / "SKILL.md").write_text(_skill_md(name, description, body))
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


def test_core_ships_exactly_the_three_fixed_skills() -> None:
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


def test_skill_tree_resolves_dependencies_before_the_skill_that_names_them(
    tmp_path: Path,
) -> None:
    base = parse_skill(_write_skill(tmp_path, "base", "base skill", "base body"))
    leaf = parse_skill(_write_skill(tmp_path, "leaf", "leaf skill", "leaf body", depends=("base",)))
    registry = SkillRegistry({"base": base, "leaf": leaf})
    assert [skill.name for skill in registry.tree("leaf")] == ["base", "leaf"]


def test_skill_tree_of_a_core_skill_returns_it() -> None:
    assert [skill.name for skill in CORE_SKILL_REGISTRY.tree("delegation")] == ["delegation"]


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


def test_tree_of_a_nested_child_pulls_its_parent_first_and_index_hides_it(tmp_path: Path) -> None:
    parent_dir = _write_skill(tmp_path, "site", "a parent skill", "p")
    _write_nested_child(parent_dir, "app", "a child skill", "c")
    registry = SkillRegistry(discover_skills(parent_dir))
    assert [skill.name for skill in registry.tree("site/app")] == ["site", "site/app"]
    assert registry.index() == (("site", "a parent skill"),)


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
