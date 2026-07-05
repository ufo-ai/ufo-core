"""Skills: the value object, the SKILL.md parser, the load-time registry, and mounting into the
sandbox.

A skill is a folder of files — a `SKILL.md` (YAML frontmatter + markdown workflow) plus any assets.
A skill folder MAY nest child skills: an immediate subdirectory that itself holds a `SKILL.md` is a
child, registered under the path-form name `<parent>/<child-dir>` and mounting nested under the
parent. Loading a child pulls its parent first, so the parent's shared files ride along. Core ships
exactly two, teaching its own builtins: `sandbox`, `delegation`; a CI gate holds that core set.
Packs contribute more through the manifest `skills` point, which the loader aggregates with core's
into one `SkillRegistry` per boot. `load_skill` resolves a skill and the closure it pulls (parent,
then `depends`) through `SkillRegistry.tree`, then `mount_skill` writes each into the conversation's
workspace under `.skills/<name>/` — inside the scoped subtree the sandbox permits, never the
framework paths above it — so the agent reads the mounted `SKILL.md` and follows it. The
instructions ride the tool result too, so the workflow is in front of the model the moment it
loads."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from selfhost.sandbox.session import WORKSPACE_DIR, SandboxSession

SKILL_MD = "SKILL.md"
FRONTMATTER_FENCE = "---\n"
SKILLS_MOUNT_DIR = f"{WORKSPACE_DIR}/.skills"
CORE_SKILLS_ROOT = Path(__file__).parent
CORE_SKILL_NAMES = frozenset({"sandbox", "delegation"})


@dataclass(frozen=True)
class RuntimeSkill:
    """One parsed skill: its identity and workflow from the frontmatter/body, the raw `SKILL.md`
    mounted verbatim (no round-trip drift), and any bundled asset files. `name` is the registry
    name — plain for a top-level skill, the path form `<parent>/<child-dir>` for a child. `parent`,
    when set, is the enclosing skill a child pulls in before itself. `depends` names the sibling
    skills that load with it."""

    name: str
    description: str
    instructions: str
    depends: tuple[str, ...] = ()
    parent: str | None = None
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


def _child_skill_dirs(skill_dir: Path) -> list[Path]:
    """Immediate subdirectories that are themselves skills — a directory holding its own `SKILL.md`.
    Their subtrees belong to the child, not the parent's bundled files."""
    return sorted(
        child for child in skill_dir.iterdir() if child.is_dir() and (child / SKILL_MD).is_file()
    )


def parse_skill(
    skill_dir: Path, registry_name: str | None = None, parent: str | None = None
) -> RuntimeSkill:
    raw = (skill_dir / SKILL_MD).read_text()
    metadata, body = _split_frontmatter(raw)
    front = yaml.safe_load(metadata) or {}
    name = front["name"]
    if name != skill_dir.name:
        raise ValueError(f"skill name {name!r} must match its directory {skill_dir.name!r}")
    depends = tuple(front.get("metadata", {}).get("depends", ()))
    child_dirs = {child.name for child in _child_skill_dirs(skill_dir)}
    files = tuple(
        (str(path.relative_to(skill_dir)), path.read_bytes())
        for path in sorted(skill_dir.rglob("*"))
        if path.is_file()
        and path.name != SKILL_MD
        and path.relative_to(skill_dir).parts[0] not in child_dirs
    )
    return RuntimeSkill(
        name=registry_name or name,
        description=front["description"],
        instructions=body.strip(),
        depends=depends,
        parent=parent,
        files=files,
        raw_skill_md=raw,
    )


def discover_skills(
    skill_dir: Path, registry_name: str | None = None, parent: str | None = None
) -> dict[str, RuntimeSkill]:
    """A skill directory and its nested child skills, flattened to a `{registry_name: skill}` map:
    this skill under its frontmatter name (or the passed path-form name), then each immediate child
    skill under `<this-name>/<child-dir>` with this skill as its `parent`, recursively. The parent's
    bundled files exclude every child subtree (a child owns its files), so the parent keeps its
    ordinary subdirs (`shared/`, `game/`, …) and sheds the child's."""
    skill = parse_skill(skill_dir, registry_name=registry_name, parent=parent)
    discovered = {skill.name: skill}
    for child_dir in _child_skill_dirs(skill_dir):
        discovered.update(
            discover_skills(
                child_dir,
                registry_name=f"{skill.name}/{child_dir.name}",
                parent=skill.name,
            )
        )
    return discovered


def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]:
    skill_dirs = sorted(
        path for path in root.iterdir() if path.is_dir() and not path.name.startswith((".", "_"))
    )
    skills: dict[str, RuntimeSkill] = {}
    for skill_dir in skill_dirs:
        skills.update(discover_skills(skill_dir))
    return skills


CORE_SKILLS_BY_NAME = _load_core_skills(CORE_SKILLS_ROOT)
CORE_SKILLS: tuple[RuntimeSkill, ...] = tuple(CORE_SKILLS_BY_NAME.values())


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill a turn can load — core's plus each active pack's contributed skills (parents and
    their nested children), keyed by registry name — built once per boot by the loader from the
    active manifests. `load_skill` resolves a name and the closure it pulls (parent, then `depends`)
    through `tree`; the system prompt's `{{skill_index}}` renders `index`. A name collision (a pack
    shadowing another skill) is refused where the registry is built, so a lookup here is always
    unambiguous."""

    by_name: dict[str, RuntimeSkill]

    def named(self, name: str) -> RuntimeSkill:
        try:
            return self.by_name[name]
        except KeyError as error:
            available = ", ".join(sorted(self.by_name)) or "none"
            raise ValueError(f"unknown skill {name!r} (available: {available})") from error

    def tree(self, name: str) -> tuple[RuntimeSkill, ...]:
        """The skill plus the skills it pulls in — its parent (and the parent's closure) first, then
        its transitive `depends`, then the skill itself, each once. The set `load_skill` mounts for
        one request: loading a child brings its parent's shared files along."""
        loaded: dict[str, RuntimeSkill] = {}

        def add(skill: RuntimeSkill) -> None:
            if skill.name in loaded:
                return
            if skill.parent is not None:
                add(self.named(skill.parent))
            for dependency in skill.depends:
                add(self.named(dependency))
            loaded[skill.name] = skill

        add(self.named(name))
        return tuple(loaded.values())

    def index(self) -> tuple[tuple[str, str], ...]:
        """The loadable-skill index the `{{skill_index}}` slot renders: each TOP-LEVEL skill's name
        and description, in registration order (core first, then packs in load order). Child skills
        are reached through their parent's instructions, not this index."""
        return tuple(
            (skill.name, skill.description)
            for skill in self.by_name.values()
            if skill.parent is None
        )


CORE_SKILL_REGISTRY = SkillRegistry(dict(CORE_SKILLS_BY_NAME))


async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None:
    root = skill.mount_root()
    for path, content in skill.mounted_files().items():
        await sandbox.write_file(f"{root}/{path}", content)
