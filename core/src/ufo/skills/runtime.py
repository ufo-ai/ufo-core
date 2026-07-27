"""Skills: the value object, the SKILL.md parser, the load-time registry, and mounting into the
sandbox.

A skill is a folder of files — a `SKILL.md` (YAML frontmatter + markdown workflow) plus any assets.
A skill folder MAY nest child skills: an immediate subdirectory that itself holds a `SKILL.md` is a
child, registered under the path-form name `<parent>/<child-dir>` and mounting nested under the
parent. Nesting is naming only: a child that needs its parent's files says so with `depends`, the
one mechanism that pulls another skill in. Core's own skills teach its builtins, held to a fixed set
by `CORE_SKILL_NAMES` and a CI gate. Packs contribute more through the manifest `skills` point,
which the loader aggregates with core's into one `SkillRegistry` per boot.

`load_skill` resolves the named skill and its transitive `depends` through `SkillRegistry.closure`,
then for each: mounts its files into the conversation's workspace under `.skills/<name>/` — inside
the scoped subtree the sandbox permits, never the framework paths above it — and injects its
`SKILL.md` workflow under a header saying whether the agent asked for it or a dependency pulled it.
One tree of everything mounted closes the load, once for the whole closure rather than per skill. So
a load costs the workflows it pulled and the paths to their files, never a restated catalog entry or
a prefix repeated once per bundled file."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from ufo.o11y import log
from ufo.sandbox.session import WORKSPACE_DIR, SandboxSession

SKILL_MD = "SKILL.md"
FRONTMATTER_FENCE = "---\n"
SKILLS_MOUNT_DIR = f"{WORKSPACE_DIR}/.skills"
CORE_SKILLS_ROOT = Path(__file__).parent
CORE_SKILL_NAMES = frozenset({"sandbox", "delegation"})
TREE_INDENT = "  "


@dataclass(frozen=True)
class RuntimeSkill:
    """One parsed skill: its identity and workflow from the frontmatter/body, the raw `SKILL.md`
    mounted verbatim (no round-trip drift), and any bundled asset files. `name` is the registry
    name — plain for a top-level skill, the path form `<parent>/<child-dir>` for a child. `parent`,
    when set, is the enclosing skill the child's name and mount path nest under — it carries no
    pull. `depends` names the skills that mount alongside this one."""

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


@dataclass(frozen=True)
class LoadedSkill:
    """One skill in a load, with the skill whose `depends` pulled it — `None` when the agent asked
    for this one by name. The header says which, so the workflow the agent chose is never confused
    with one that rode along behind it, and a chain names the link that pulled each hop."""

    skill: RuntimeSkill
    dependency_of: str | None = None

    def prompt_body(self) -> str:
        """What one skill contributes to the context: a header naming it and how it got here, then
        its `SKILL.md` workflow. Nothing else — the frontmatter's `description` and `depends` are
        load-time routing metadata, not instructions the agent acts on, and no bundled file's
        content is ever injected. A file is reached by its mounted path, which the tree lists."""
        pulled = f" (dependency of {self.dependency_of})" if self.dependency_of is not None else ""
        return f"# Skill: {self.skill.name}{pulled}\n\n{self.skill.instructions}"


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


def parse_skill_content(
    dir_name: str,
    files: Mapping[str, bytes],
    registry_name: str | None = None,
    parent: str | None = None,
) -> RuntimeSkill:
    """Parse a skill from its files in memory — the `SKILL.md` text (keyed `SKILL.md`) plus any
    bundled assets — so a skill read out of the sandbox and persisted as bytes validates the same
    way a skill on disk does. `dir_name` is the folder the skill claims: the frontmatter `name` must
    match it, as the disk parser requires. Fails loud on a missing or malformed `SKILL.md`."""
    raw_bytes = files.get(SKILL_MD)
    if raw_bytes is None:
        raise ValueError(f"skill {dir_name!r} has no {SKILL_MD}")
    raw = raw_bytes.decode()
    metadata, body = _split_frontmatter(raw)
    front = yaml.safe_load(metadata) or {}
    name = front["name"]
    if name != dir_name:
        raise ValueError(f"skill name {name!r} must match its directory {dir_name!r}")
    depends = tuple(front.get("metadata", {}).get("depends", ()))
    assets = tuple(
        (path, content)
        for path, content in sorted(files.items())
        if PurePosixPath(path).name != SKILL_MD
    )
    return RuntimeSkill(
        name=registry_name or name,
        description=front["description"],
        instructions=body.strip(),
        depends=depends,
        parent=parent,
        files=assets,
        raw_skill_md=raw,
    )


def parse_skill(
    skill_dir: Path, registry_name: str | None = None, parent: str | None = None
) -> RuntimeSkill:
    child_dirs = {child.name for child in _child_skill_dirs(skill_dir)}
    files = {
        str(path.relative_to(skill_dir)): path.read_bytes()
        for path in sorted(skill_dir.rglob("*"))
        if path.is_file() and path.relative_to(skill_dir).parts[0] not in child_dirs
    }
    return parse_skill_content(skill_dir.name, files, registry_name, parent)


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
    active manifests. `load_skill` resolves what one load pulls through `closure`; the system
    prompt's `{{skill_index}}` renders `index`. A name collision (a pack shadowing another skill) is
    refused where the registry is built, so a lookup here is always unambiguous."""

    by_name: dict[str, RuntimeSkill]

    def named(self, name: str) -> RuntimeSkill:
        try:
            return self.by_name[name]
        except KeyError as error:
            available = ", ".join(sorted(self.by_name)) or "none"
            raise ValueError(f"unknown skill {name!r} (available: {available})") from error

    def closure(self, *names: str) -> tuple[LoadedSkill, ...]:
        """One load of the named skills: every name first in the order given, then the transitive
        `depends` of each, once apiece and paired with the skill that pulled it. `depends` is the
        only pull — a child skill reaches its parent by declaring it, never by nesting — so this is
        both the set mounted and the order injected, the asked-for workflows leading. A named skill
        is always direct, never labelled a dependency, even when another named skill also depends on
        it: seeding every name before the walk is what makes that hold whatever order they arrive
        in. Claiming a skill before walking its dependencies keeps the walk cycle-safe, so a
        member-authored cycle (A↔B, or a self-dep) yields each skill once, not a RecursionError."""
        loaded: dict[str, LoadedSkill] = {
            name: LoadedSkill(skill=self.named(name)) for name in dict.fromkeys(names)
        }

        def add(skill: RuntimeSkill, dependency_of: str | None) -> None:
            if skill.name in loaded:
                return
            loaded[skill.name] = LoadedSkill(skill=skill, dependency_of=dependency_of)
            for dependency in skill.depends:
                add(self.named(dependency), skill.name)

        for name in dict.fromkeys(names):
            for dependency in self.named(name).depends:
                add(self.named(dependency), name)
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

    def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> "SkillRegistry":
        """This registry (core + active packs) plus a workspace's saved user-skills, appended last.
        A user-skill is user-controlled text mounted into the agent's own context, so it may never
        shadow a core or pack skill: the base always wins on a name collision and the user-skill is
        dropped with a log. The save path refuses a colliding name up front, so this guard is the
        structural backstop that makes the no-shadow invariant hold even against a stale row."""
        by_name = dict(self.by_name)
        for skill in user_skills:
            if skill.name in by_name:
                log("skill.user_shadow_refused", skill=skill.name)
                continue
            by_name[skill.name] = skill
        return SkillRegistry(by_name)


CORE_SKILL_REGISTRY = SkillRegistry(dict(CORE_SKILLS_BY_NAME))


def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str:
    """Everything a load mounted, as one indented tree under the mount dir: each directory named
    once and each file named by its own segment, so a bundle of eighty files costs eighty short
    lines instead of eighty repetitions of the same prefix. One tree for the whole closure, not one
    per skill — a nested child's files land under the parent's directory, where they in fact are."""
    lines = [f"{SKILLS_MOUNT_DIR}/"]
    directories: set[tuple[str, ...]] = set()
    mounted = sorted(
        f"{entry.skill.name}/{path}" for entry in loaded for path in entry.skill.mounted_files()
    )
    for path in mounted:
        parts = PurePosixPath(path).parts
        for depth in range(len(parts) - 1):
            branch = parts[: depth + 1]
            if branch not in directories:
                directories.add(branch)
                lines.append(f"{TREE_INDENT * (depth + 1)}{parts[depth]}/")
        lines.append(f"{TREE_INDENT * len(parts)}{parts[-1]}")
    return "\n".join(lines)


def loaded_context(loaded: tuple[LoadedSkill, ...]) -> str:
    """What one load puts in front of the model: each skill's header and workflow in closure order —
    the asked-for skill, then what it pulled — and one tree of everything mounted, at the end.
    Shared by `load_skill` and a subagent's `preload_skills`, so a skill reads the same each way."""
    workflows = "\n\n---\n\n".join(entry.prompt_body() for entry in loaded)
    return f"{workflows}\n\nMounted files:\n{_mounted_tree(loaded)}"


async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None:
    root = skill.mount_root()
    for path, content in skill.mounted_files().items():
        await sandbox.write_file(f"{root}/{path}", content)
