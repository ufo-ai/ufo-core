"""Skills: the value object, the SKILL.md parser, the load-time registry, and mounting into the
sandbox.

A skill is a folder of files — a `SKILL.md` (YAML frontmatter + markdown workflow) plus any assets.
A skill folder MAY nest child skills: an immediate subdirectory that itself holds a `SKILL.md` is a
child, registered under the path-form name `<parent>/<child-dir>` and mounting nested under the
parent. Nesting is naming only: a child that needs its parent's files says so with `depends`, the
one mechanism that pulls another skill in. Core's own skills teach its builtins and the house style
every other skill defaults to, held to a fixed set by `CORE_SKILL_NAMES` and a CI gate. Packs
contribute more through the manifest `skills` point, which the loader aggregates with core's into
one `SkillRegistry` per boot.

`load_skill` resolves the named skill and its transitive `depends` through `SkillRegistry.closure`,
then for each: mounts its files into the conversation's workspace under `.skills/<name>/` — inside
the scoped subtree the sandbox permits, never the framework paths above it — and injects its
`SKILL.md` workflow under a header saying whether the agent asked for it or a dependency pulled it.
One tree of everything mounted closes the load, once for the whole closure rather than per skill. So
a load costs the workflows it pulled and the paths to their files, never a restated catalog entry or
a prefix repeated once per bundled file. A workflow already in the context is not injected a second
time: `LoadedSkills` tracks what the window holds, so a repeat load re-mounts the files and names
the skill in one line instead of paying for its instructions again."""

from collections.abc import Awaitable, Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path, PurePosixPath

import yaml

from ufo.o11y import log
from ufo.sandbox.containment import contained_relative
from ufo.sandbox.session import WORKSPACE_DIR, SandboxSession

SKILL_MD = "SKILL.md"
FRONTMATTER_FENCE = "---\n"
SKILLS_MOUNT_DIR = f"{WORKSPACE_DIR}/.skills"
CORE_SKILLS_ROOT = Path(__file__).parent
CORE_SKILL_NAMES = frozenset({"sandbox", "create-application", "ufo-style"})
TREE_INDENT = "  "
SKILL_HEADER_PREFIX = "# Skill: "
DEPENDENCY_SUFFIX = " (dependency of {puller})"
SKILL_BLOCK_SEPARATOR = "\n\n---\n\n"
ALREADY_LOADED_NOTE = "Already in context above, not repeated: {names}"
SUGGESTION_LIMIT = 5


def skill_mount_root(name: str) -> str:
    """Where a skill's files live in the workspace — the root every one of its file keys is confined
    at, both where a skill is saved and where a later load mounts it."""
    return f"{SKILLS_MOUNT_DIR}/{name}"


@dataclass(frozen=True)
class SkillCard:
    """One skill's routing datum: what selection, search, and closure resolution walk — never the
    workflow body. A card and a `RuntimeSkill` are two concepts, not two forms of one: the card is
    projected to columns where a member skill is saved, and the `RuntimeSkill` for that skill
    exists only inside a load, materialized from its stored files. `pinned` is the member's
    always-show mark; deploy skills carry no pin."""

    name: str
    description: str
    depends: tuple[str, ...] = ()
    pinned: bool = False


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
        return skill_mount_root(self.name)

    def card(self) -> SkillCard:
        """This skill's routing view — what closure resolution and search walk for a deploy skill,
        so both tiers resolve over one shape."""
        return SkillCard(name=self.name, description=self.description, depends=self.depends)


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
        pulled = (
            ""
            if self.dependency_of is None
            else DEPENDENCY_SUFFIX.format(puller=self.dependency_of)
        )
        return f"{SKILL_HEADER_PREFIX}{self.skill.name}{pulled}\n\n{self.skill.instructions}"


@dataclass(frozen=True)
class LoadedRef:
    """One skill in a resolved load, by its routing card — what `closure` yields before any body is
    read. `dependency_of` names the skill whose `depends` pulled it, `None` when the agent asked for
    it by name; `materialize` turns a ref into the `LoadedSkill` a mount needs."""

    card: SkillCard
    dependency_of: str | None = None


@dataclass(eq=False)
class LoadedSkills:
    """Which skills' workflows the model's context already holds, so `load_skill` never pays for the
    same instructions twice. Derived from the window rather than accumulated: each load in it is
    re-expanded through the registry, so the tracker is right across the turns of one conversation
    (an earlier turn's load is still in the transcript), across a compaction that dropped those
    bodies, and across a DBOS replay that never re-ran the handler. `asked_for` is the subset the
    agent named itself — what a compaction summary carries past the boundary, since re-loading one
    of those brings its dependencies back with it."""

    in_context: set[str] = field(default_factory=set)
    asked_for: set[str] = field(default_factory=set)

    def reseed(
        self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...] = ()
    ) -> None:
        """Replace the tracker with the skills the given loads put in front of the model — one
        registry closure per load, the same entries `loaded_context` rendered for it, so what the
        tracker claims is what the model was handed rather than what some text says. Each load is a
        card closure, never materialized bodies: the tracker needs names, and re-reading a member
        skill's stored files on every round would put the corpus back on the hot path.

        `preloaded` is a subagent's `preload_skills` closure. Those workflows render into the
        child's system prompt rather than a tool result, and unlike a transcript body they outlive
        a compaction, so they stay in context for the whole turn — but the child never asked for
        them, so they stay out of `asked_for` and out of the summary telling it what to load."""
        self.reset()
        for entries in loads:
            for entry in entries:
                self.in_context.add(entry.card.name)
                if entry.dependency_of is None:
                    self.asked_for.add(entry.card.name)
        self.in_context.update(entry.skill.name for entry in preloaded)

    def drain(self) -> tuple[str, ...]:
        """The names the agent asked for, clearing the tracker — what a boundary that drops every
        workflow body from the window carries forward for the agent to re-load."""
        names = tuple(sorted(self.asked_for))
        self.reset()
        return names

    def reset(self) -> None:
        self.in_context.clear()
        self.asked_for.clear()


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


SkillMaterializer = Callable[[str], Awaitable[RuntimeSkill | None]]


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill a turn can load, in two tiers. The deploy tier (`by_name`) is core's skills plus
    each active pack's contributed skills and any generated skills — nothing a member action
    changes, so the system prompt's `{{skill_index}}` renders `index()` over it byte-identically
    across workspaces. The member tier (`member_cards`) is the bound agent's saved skills as
    routing cards, joined per turn by `with_member`: `closure` resolves over cards from both tiers,
    and `materialize` turns a resolved closure into mountable skills — deploy from `by_name`,
    member through the `materializer`, which reads exactly the named rows. A name collision (a pack
    shadowing another skill) is refused where the deploy tier is built, so a lookup here is always
    unambiguous."""

    by_name: dict[str, RuntimeSkill]
    member_cards: dict[str, SkillCard] = field(default_factory=dict)
    materializer: SkillMaterializer | None = None

    def named(self, name: str) -> RuntimeSkill:
        try:
            return self.by_name[name]
        except KeyError as error:
            raise self._unknown(name) from error

    def _unknown(self, name: str) -> ValueError:
        close = get_close_matches(name, sorted(self.known_names()), n=SUGGESTION_LIMIT)
        hint = f" (closest: {', '.join(close)})" if close else ""
        return ValueError(f"unknown skill {name!r}{hint}")

    def _card(self, name: str) -> SkillCard:
        deploy = self.by_name.get(name)
        if deploy is not None:
            return deploy.card()
        member = self.member_cards.get(name)
        if member is None:
            raise self._unknown(name)
        return member

    def known_names(self) -> frozenset[str]:
        """Every name a load can resolve — the deploy tier plus the member cards. The claimable-name
        set a save checks a new skill against."""
        return frozenset(self.by_name) | frozenset(self.member_cards)

    def all_cards(self) -> tuple[SkillCard, ...]:
        """Every loadable skill's routing card, deploy tier first — what `skill_search` scores."""
        return (
            *(skill.card() for skill in self.by_name.values()),
            *self.member_cards.values(),
        )

    def closure(self, *names: str) -> tuple[LoadedRef, ...]:
        """One load of the named skills, resolved over routing cards — never a stored body: every
        name first in the order given, then the transitive `depends` of each, once apiece and
        paired with the skill that pulled it. `depends` is the only pull — a child skill reaches
        its parent by declaring it, never by nesting — so this is both the set mounted and the
        order injected, the asked-for workflows leading. A named skill is always direct, never
        labelled a dependency, even when another named skill also depends on it: seeding every name
        before the walk is what makes that hold whatever order they arrive in. Claiming a skill
        before walking its dependencies keeps the walk cycle-safe, so a member-authored cycle
        (A↔B, or a self-dep) yields each skill once, not a RecursionError."""
        refs: dict[str, LoadedRef] = {
            name: LoadedRef(card=self._card(name)) for name in dict.fromkeys(names)
        }

        def add(card: SkillCard, dependency_of: str | None) -> None:
            if card.name in refs:
                return
            refs[card.name] = LoadedRef(card=card, dependency_of=dependency_of)
            for dependency in card.depends:
                add(self._card(dependency), card.name)

        for name in dict.fromkeys(names):
            for dependency in refs[name].card.depends:
                add(self._card(dependency), name)
        return tuple(refs.values())

    async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]:
        """The mountable skills a resolved closure names, in closure order: a deploy skill from
        `by_name`, a member skill through the materializer — one stored-row read per name, the only
        place a member skill's bytes are touched. A member ref whose row vanished between the card
        projection and this read fails loud naming the skill."""
        loaded: list[LoadedSkill] = []
        for ref in refs:
            name = ref.card.name
            skill = self.by_name.get(name)
            if skill is None and self.materializer is not None:
                skill = await self.materializer(name)
            if skill is None:
                raise ValueError(f"skill {name!r} is no longer available")
            if skill.name != name:
                raise ValueError(f"materializing {name!r} returned skill {skill.name!r}")
            loaded.append(LoadedSkill(skill=skill, dependency_of=ref.dependency_of))
        return tuple(loaded)

    def index(self) -> tuple[tuple[str, str], ...]:
        """The loadable-skill index the `{{skill_index}}` slot renders: each TOP-LEVEL deploy
        skill's name and description, in registration order (core first, then packs in load order).
        Child skills are reached through their parent's instructions, not this index; member skills
        render into the turn message, never here, so a save cannot move the system prompt."""
        return tuple(
            (skill.name, skill.description)
            for skill in self.by_name.values()
            if skill.parent is None
        )

    def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> "SkillRegistry":
        """This registry plus deploy-controlled generated skills (the spawn catalog, the setup
        skill), appended to the deploy tier. The base wins on a name collision and the newcomer is
        dropped with a log; a member card colliding with a name added here is dropped the same way,
        so the deploy tier always shadows the member tier whatever order the turn composed them."""
        by_name = dict(self.by_name)
        for skill in generated:
            if skill.name in by_name:
                log("skill.generated_shadow_refused", skill=skill.name)
                continue
            by_name[skill.name] = skill
        member_cards = dict(self.member_cards)
        for name in self.member_cards:
            if name in by_name:
                log("skill.member_shadow_refused", skill=name)
                del member_cards[name]
        return SkillRegistry(by_name, member_cards=member_cards, materializer=self.materializer)

    def with_member(
        self, cards: Sequence[SkillCard], materialize: SkillMaterializer
    ) -> "SkillRegistry":
        """This registry with the bound agent's saved skills as its member tier. A member skill is
        member-controlled text mounted into the agent's own context, so it may never shadow a
        deploy skill: the deploy tier always wins on a name collision and the card is dropped with
        a log. The save path refuses a colliding name up front, so this guard is the structural
        backstop that makes the no-shadow invariant hold even against a stale row."""
        member_cards: dict[str, SkillCard] = {}
        for card in cards:
            if card.name in self.by_name:
                log("skill.member_shadow_refused", skill=card.name)
                continue
            member_cards[card.name] = card
        return SkillRegistry(self.by_name, member_cards=member_cards, materializer=materialize)


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


def loaded_context(
    loaded: tuple[LoadedSkill, ...], in_context: Container[str] = frozenset()
) -> str:
    """What one load puts in front of the model: each skill's header and workflow in closure order —
    the asked-for skill, then what it pulled — and one tree of everything mounted, at the end. A
    skill named in `in_context` is already in front of the model, so it contributes its name to one
    note instead of its workflow a second time; suppression is per skill, so loading a skill whose
    dependency is already there still injects the one workflow that is new. Every skill in the
    closure still mounts and still appears in the tree, so the files a repeat load rewrites are
    reachable whatever the agent did to them. Shared by `load_skill` and a subagent's
    `preload_skills`, so a skill reads the same each way."""
    blocks = [entry.prompt_body() for entry in loaded if entry.skill.name not in in_context]
    if repeated := tuple(entry.skill.name for entry in loaded if entry.skill.name in in_context):
        blocks.append(ALREADY_LOADED_NOTE.format(names=", ".join(repeated)))
    return SKILL_BLOCK_SEPARATOR.join(blocks) + f"\n\nMounted files:\n{_mounted_tree(loaded)}"


async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None:
    """Write a skill's files under its own mount root. A file key is authored input — a member's
    saved skill carries whatever keys it was applied with — so each is confined at `.skills/<name>/`
    and not merely at the workspace: a key climbing out of the mount is still inside the
    workspace, which would make a saved skill a durable write primitive over the agent's own
    files, rewritten on every load in every conversation of that agent."""
    root = skill.mount_root()
    for path, content in skill.mounted_files().items():
        await sandbox.write_file(contained_relative(path, root), content)
