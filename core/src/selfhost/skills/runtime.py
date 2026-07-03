"""Core skills: the value object, the SKILL.md parser, and mounting into the sandbox.

A skill is a folder of files — a `SKILL.md` (YAML frontmatter + markdown workflow) plus any assets.
Core ships exactly three, teaching its own builtins: `sandbox`, `memory`, `delegation`; a CI gate
holds that set. `load_skill` resolves a skill and its `depends` closure through `skill_tree`, then
`mount_skill` writes each into the conversation's workspace under `.skills/<name>/` — inside the
scoped subtree the sandbox permits, never the framework paths above it — so the agent reads the
mounted `SKILL.md` and follows it. The instructions ride the tool result too, so the workflow is in
front of the model the moment it loads."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from selfhost.sandbox.session import WORKSPACE_DIR, SandboxSession

SKILL_MD = "SKILL.md"
FRONTMATTER_FENCE = "---\n"
SKILLS_MOUNT_DIR = f"{WORKSPACE_DIR}/.skills"
CORE_SKILLS_ROOT = Path(__file__).parent
CORE_SKILL_NAMES = frozenset({"sandbox", "memory", "delegation"})


@dataclass(frozen=True)
class RuntimeSkill:
    """One parsed skill: its identity and workflow from the frontmatter/body, the raw `SKILL.md`
    mounted verbatim (no round-trip drift), and any bundled asset files. `depends` names the sibling
    skills that load with it."""

    name: str
    description: str
    instructions: str
    depends: tuple[str, ...] = ()
    files: tuple[tuple[str, bytes], ...] = ()
    raw_skill_md: str = ""

    def mounted_files(self) -> dict[str, bytes]:
        return {SKILL_MD: self.raw_skill_md.encode(), **dict(self.files)}

    def mount_root(self) -> str:
        return f"{SKILLS_MOUNT_DIR}/{self.name}"

    def prompt_body(self) -> str:
        sections = [f"# Skill: {self.name}", self.description, self.instructions]
        if self.files:
            listing = "\n".join(f"- {self.mount_root()}/{path}" for path, _ in self.files)
            sections.append(f"Bundled files:\n{listing}")
        return "\n\n".join(section for section in sections if section)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith(FRONTMATTER_FENCE):
        raise ValueError(f"{SKILL_MD} must open with a YAML frontmatter block")
    metadata, fence, body = text[len(FRONTMATTER_FENCE) :].partition(f"\n{FRONTMATTER_FENCE}")
    if not fence:
        raise ValueError(f"{SKILL_MD} frontmatter is not terminated")
    return metadata, body


def parse_skill(skill_dir: Path) -> RuntimeSkill:
    raw = (skill_dir / SKILL_MD).read_text()
    metadata, body = _split_frontmatter(raw)
    front = yaml.safe_load(metadata) or {}
    name = front["name"]
    if name != skill_dir.name:
        raise ValueError(f"skill name {name!r} must match its directory {skill_dir.name!r}")
    depends = tuple(front.get("metadata", {}).get("depends", ()))
    files = tuple(
        (str(path.relative_to(skill_dir)), path.read_bytes())
        for path in sorted(skill_dir.rglob("*"))
        if path.is_file() and path.name != SKILL_MD
    )
    return RuntimeSkill(
        name=name,
        description=front["description"],
        instructions=body.strip(),
        depends=depends,
        files=files,
        raw_skill_md=raw,
    )


def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]:
    skill_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith((".", "_"))
    )
    return {skill.name: skill for skill in (parse_skill(path) for path in skill_dirs)}


CORE_SKILLS_BY_NAME = _load_core_skills(CORE_SKILLS_ROOT)
CORE_SKILLS: tuple[RuntimeSkill, ...] = tuple(CORE_SKILLS_BY_NAME.values())


def skill_named(name: str) -> RuntimeSkill:
    try:
        return CORE_SKILLS_BY_NAME[name]
    except KeyError as error:
        available = ", ".join(sorted(CORE_SKILLS_BY_NAME)) or "none"
        raise ValueError(f"unknown skill {name!r} (available: {available})") from error


def skill_tree(name: str) -> tuple[RuntimeSkill, ...]:
    """The skill plus its transitive `depends`, each once, dependencies before the skill that
    names them — the set `load_skill` mounts for one request."""
    loaded: dict[str, RuntimeSkill] = {}

    def add(skill: RuntimeSkill) -> None:
        if skill.name in loaded:
            return
        for dependency in skill.depends:
            add(skill_named(dependency))
        loaded[skill.name] = skill

    add(skill_named(name))
    return tuple(loaded.values())


async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None:
    root = skill.mount_root()
    for path, content in skill.mounted_files().items():
        await sandbox.write_file(f"{root}/{path}", content)
