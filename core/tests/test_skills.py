from pathlib import Path

import pytest

from selfhost.skills.runtime import (
    CORE_SKILL_NAMES,
    CORE_SKILL_REGISTRY,
    CORE_SKILLS,
    RuntimeSkill,
    SkillRegistry,
    mount_skill,
    parse_skill,
)


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


def test_registry_index_lists_every_skills_name_and_description() -> None:
    registry = SkillRegistry(
        {
            "base": RuntimeSkill(name="base", description="base skill", instructions="b"),
            "leaf": RuntimeSkill(name="leaf", description="leaf skill", instructions="l"),
        }
    )
    assert registry.index() == (("base", "base skill"), ("leaf", "leaf skill"))


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
