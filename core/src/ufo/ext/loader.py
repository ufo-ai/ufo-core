"""Discover installed extensions: entry points → validated Manifests, and the turn's tool set.

Each `ufo.extension` entry point is a zero-arg callable returning a Manifest; the loader
collects them, rejects duplicate names, and hands the tuple to the derivations that read it.
Discovery is the only path in — extensions declare, they never call a registration API.

The lockfile is the deploy's pinned extension set: when it exists, only the extensions it pins load,
and each must match its pinned digest or boot fails loud — tamper and drift are refused, not run.
With no lockfile the deploy is in dev mode and every discovered extension is active. `ufoctl ext`
and `ufoctl bundle` write this file; `load_manifests` reads it, so the set the operator pinned is
exactly what every derivation (tools, jobs, routes, proxy rules) sees.

`turn_tools` reads the active manifests into the set a turn dispatches against and the owning
ExtensionContext for each extension tool; `turn_hooks` reads them into the turn's reactive
`HookChain` — every declared hook bound to its extension's scoped context, grouped by event."""

import asyncio
import hashlib
import importlib.util
import os
from dataclasses import dataclass, field, replace
from importlib.machinery import ModuleSpec
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.agents import AGENT_OBJECT
from ufo.artifacts import ARTIFACT_OBJECT
from ufo.audience import SHARED_AUDIENCE, Audience
from ufo.connectors import CliCredential
from ufo.conversations import CONVERSATION_OBJECT
from ufo.credential_kind import (
    CREDENTIAL_DESCRIPTION,
    CREDENTIAL_GUIDANCE,
    CREDENTIAL_KIND,
    CredentialObjects,
    CredentialSpec,
)
from ufo.credentials import CredentialStore, HostChoice
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.manifest import (
    CredentialSlot,
    Deny,
    HookContext,
    HookEvent,
    HookPayload,
    HookSpec,
    InjectContext,
    Manifest,
    MemorySearchProviderSpec,
    ModifyInput,
    ModifyOutput,
    Pack,
    PostToolUse,
    PostToolUseFailure,
    PreToolUse,
    SubagentProfile,
    declared_slots,
)
from ufo.indexing import EmbedClient, IndexBackend
from ufo.members import MEMBER_OBJECT
from ufo.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemorySearch
from ufo.o11y import log
from ufo.objects import BoundKind, ObjectKind, ObjectVerbs, object_registry
from ufo.schema.records import Agent, Turn
from ufo.skills.runtime import (
    CORE_SKILLS_BY_NAME,
    RuntimeSkill,
    SkillRegistry,
    discover_skills,
)
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.registry import ToolDef, ToolRegistry

CORE_OBJECT_KINDS: tuple[BoundKind, ...] = (
    BoundKind(kind=AGENT_OBJECT, extension=None, context=None),
    BoundKind(kind=ARTIFACT_OBJECT, extension=None, context=None),
    BoundKind(kind=CONVERSATION_OBJECT, extension=None, context=None),
    BoundKind(kind=MEMBER_OBJECT, extension=None, context=None),
)
EXTENSION_ENTRY_POINT_GROUP = "ufo.extension"
PACK_ENTRY_POINT_GROUP = "ufo.pack"
LOCKFILE_PATH_ENV = "UFO_LOCKFILE"
DEFAULT_LOCKFILE_PATH = Path("ufo.lock")
DIGEST_PREFIX = "sha256:"
MIGRATIONS_DIRNAME = "migrations"
HOOK_TIMEOUT_SECONDS = 5.0
TURN_HOOK_EVENTS: tuple[HookEvent, ...] = (
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "user_prompt_submit",
    "stop",
    "pre_compact",
    "post_compact",
)
GATING_EVENTS: frozenset[HookEvent] = frozenset({"pre_tool_use", "user_prompt_submit"})
ALLOWED_OUTCOMES: dict[HookEvent, tuple[type, ...]] = {
    "pre_tool_use": (Deny, ModifyInput),
    "post_tool_use": (ModifyOutput, InjectContext),
    "post_tool_use_failure": (),
    "user_prompt_submit": (Deny, InjectContext),
    "stop": (),
    "pre_compact": (),
    "post_compact": (),
    "page_change": (),
}


class NotRegisteredError(RuntimeError):
    """A backend-selection seam was handed a name no active extension registers. Distinct from the
    other boot-fail-loud RuntimeErrors these seams raise (a name collision, a key-gated factory
    declining) so a caller — the registration-evidence test — asserts on the type, not on a message
    substring a reword could silently drift past."""


class ExtensionPin(BaseModel):
    """One extension the lockfile pins: its name, the version pinned, and the digest of its
    installed source the loader re-checks at boot."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    digest: str


class Lockfile(BaseModel):
    """The deploy's pinned components — the ufo version that anchors dependencies and each
    pinned extension. Crosses the boundary between the `ufoctl ext`/`bundle` writers and the boot
    reader as a file, so it validates at construction both ways."""

    model_config = ConfigDict(extra="forbid")
    ufo_version: str
    extensions: tuple[ExtensionPin, ...] = ()


def lockfile_path() -> Path:
    return Path(os.environ.get(LOCKFILE_PATH_ENV, str(DEFAULT_LOCKFILE_PATH)))


def read_lockfile(path: Path) -> Lockfile:
    return Lockfile.model_validate_json(path.read_text())


def write_lockfile(path: Path, lockfile: Lockfile) -> None:
    path.write_text(lockfile.model_dump_json(indent=2) + "\n")


def discovered() -> dict[str, tuple[Manifest, EntryPoint]]:
    """Every extension installed in this environment, keyed by manifest name, with the entry point
    its digest is computed from. Duplicate manifest names are rejected here so no reader downstream
    has to."""
    found: dict[str, tuple[Manifest, EntryPoint]] = {}
    for entry in entry_points(group=EXTENSION_ENTRY_POINT_GROUP):
        manifest = entry.load()()
        if manifest.name in found:
            raise ValueError(f"duplicate extension name: {manifest.name}")
        found[manifest.name] = (manifest, entry)
    return found


def discovered_packs() -> dict[str, Pack]:
    """Every pack installed in this environment, keyed by pack name — discovered through the
    `ufo.pack` entry-point group exactly as extensions are through `ufo.extension`, each a
    zero-arg callable returning a Pack. Duplicate pack names are rejected here so no reader
    downstream has to."""
    found: dict[str, Pack] = {}
    for entry in entry_points(group=PACK_ENTRY_POINT_GROUP):
        pack = entry.load()()
        if pack.name in found:
            raise ValueError(f"duplicate pack name: {pack.name}")
        found[pack.name] = pack
    return found


def _entry_spec(entry: EntryPoint) -> ModuleSpec:
    top = entry.module.split(".", 1)[0]
    spec = importlib.util.find_spec(top)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"extension package {top!r} has no importable source")
    return spec


def _package_dir(spec: ModuleSpec) -> Path:
    """The directory an extension's migrations sit beside: the package directory for a multi-file
    extension, the entry module's parent for a single-file one."""
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    return Path(spec.origin).parent  # type: ignore[arg-type]


def extension_digest(entry: EntryPoint) -> str:
    """The digest that pins an extension: sha256 over every source file of the installed package the
    entry point belongs to — not only its entry module — so editing any file in a multi-file
    extension changes the digest and a pinned deploy refuses to run it. Bytecode caches, which are
    machine-specific and rebuilt on import, are excluded so the digest is stable across machines."""
    spec = _entry_spec(entry)
    if spec.submodule_search_locations:
        root = Path(next(iter(spec.submodule_search_locations)))
        files = {
            path.relative_to(root).as_posix(): path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
    else:
        origin = Path(spec.origin)  # type: ignore[arg-type]
        files = {origin.name: origin}
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(hashlib.sha256(name.encode()).digest())
        digest.update(hashlib.sha256(files[name].read_bytes()).digest())
    return DIGEST_PREFIX + digest.hexdigest()


def migration_locations(pack: str | None = None) -> tuple[str, ...]:
    """The `migrations/` directory of each active extension — the alembic version locations
    `apply_migrations` layers over core's own. Each is a self-contained branch of revision files
    that attaches to core through the `depends_on` its base declares, so `upgrade heads` brings the
    deploy to core's head plus each pinned extension's — one head per owner. Only the active set
    contributes (via `load_manifests`, narrowed to `pack` when one is active), so an
    installed-but-inactive extension adds no tables; a pack's own manifest has no entry point and
    owns no tables, so it contributes no location."""
    installed = discovered()
    locations: list[str] = []
    for manifest in load_manifests(pack):
        found = installed.get(manifest.name)
        if found is None:
            continue
        migrations = _package_dir(_entry_spec(found[1])) / MIGRATIONS_DIRNAME
        if migrations.is_dir():
            locations.append(str(migrations))
    return tuple(locations)


def load_manifests(pack: str | None = None) -> tuple[Manifest, ...]:
    """The active extension set. With a lockfile present it is exactly the pinned extensions, each
    verified against its pinned digest (a missing or drifted extension fails loud); with none, every
    discovered extension is active. When `pack` names an installed pack the set narrows to exactly
    the extensions that pack bundles plus the pack's own manifest, so activating one pack brings a
    coherent config up together."""
    installed = discovered()
    path = lockfile_path()
    if not path.exists():
        active = {manifest.name: manifest for manifest, _ in installed.values()}
    else:
        active = {}
        for pin in read_lockfile(path).extensions:
            found = installed.get(pin.name)
            if found is None:
                raise RuntimeError(f"lockfile pins extension {pin.name!r} but it is not installed")
            manifest, entry = found
            actual = extension_digest(entry)
            if actual != pin.digest:
                raise RuntimeError(
                    f"extension {pin.name!r} digest {actual} does not match pinned {pin.digest}"
                )
            active[manifest.name] = manifest
    if pack is None:
        return tuple(active.values())
    return _pack_manifests(pack, active)


def _pack_manifests(pack: str, active: dict[str, Manifest]) -> tuple[Manifest, ...]:
    """Narrow the active set to the named pack: the manifests of exactly the extensions it bundles
    (each must be installed and active, else boot fails loud) followed by one manifest carrying the
    pack's own skills and onboarding steps. A pack's pack-level contributions ride the same
    manifest-consuming paths an extension's do; a pack name colliding with a bundled extension's
    fails loud."""
    declared = discovered_packs()
    found = declared.get(pack)
    if found is None:
        raise RuntimeError(
            f"config selects pack {pack!r} but no pack registers it (have {sorted(declared)})"
        )
    manifests: list[Manifest] = []
    for name in found.extensions:
        manifest = active.get(name)
        if manifest is None:
            raise RuntimeError(
                f"pack {pack!r} bundles extension {name!r} but it is not installed and active"
            )
        manifests.append(manifest)
    if found.name in {manifest.name for manifest in manifests}:
        raise RuntimeError(
            f"pack {found.name!r} collides with a bundled extension of the same name"
        )
    manifests.append(
        Manifest(
            name=found.name,
            version=found.version,
            skills=found.skills,
            onboarding_steps=found.onboarding_steps,
        )
    )
    return tuple(manifests)


def connector_clis(manifests: tuple[Manifest, ...]) -> dict[str, CliCredential]:
    """Each installed connector's declared CLI credential, keyed by provider — the map the engine
    reads to export each usable grant's sentinel env and the egress proxy's per-turn resolver folds
    into its forward rules, both live from the current deploy's manifests."""
    return {
        connector.oauth.provider: connector.cli
        for manifest in manifests
        for connector in manifest.connectors
        if connector.cli is not None
    }


def injecting_slots(manifests: tuple[Manifest, ...]) -> tuple[CredentialSlot, ...]:
    """Every declared slot the egress proxy swaps onto the wire — the deploy's keyed providers, read
    live from the current manifests by the proxy's rule resolver and by the engine that exports each
    filled slot's sentinel into the sandbox. Both roles collect them through here, so what would
    silently mis-authenticate is refused in one place, because a row is meant to cost no code and no
    test: nothing else would stop the next one from taking a name already in use.

    A `sentinel` two slots share draws whichever secret matches first. **One sandbox variable
    carries one value**, so every exported name — a slot's `env`, a host choice's `env`, and a
    connector's `CliCredential.env`, all merged into one dict per sandbox — is claimed in a single
    namespace beside what it carries; a second claimant carrying anything else is refused, since the
    later export silently wins the merge. Claims carrying the *same* value stay legal, which is what
    lets both Datadog keys export `DD_HOST`: they name one `HostChoice`, and a frozen value object
    compares by every field it has rather than by a tuple someone listed. A host choice must also
    name a slot some installed extension declares, since no writer fills an undeclared slot — every
    one gates on the declared set — so a typo would pin the host to the default forever. A host is
    metered once however many keys reach it, so slots that can reach one host must agree on the
    dimension. The claim spans every host a declaration could resolve to — a fixed host, or every
    host in a choice — because the derivation groups by the host it *resolved*: two rows aliasing
    one literal through different declarations would otherwise drop the later dimension silently."""
    slots = tuple(
        slot
        for manifest in manifests
        for slot in manifest.credentials
        if slot.injection is not None
    )
    declared = {slot.name for manifest in manifests for slot in manifest.credentials}
    exported: dict[str, tuple[str, object]] = {
        cli.env: (f"connector {provider!r}'s CLI credential", f"the {provider!r} grant sentinel")
        for provider, cli in connector_clis(manifests).items()
    }
    sentinels: dict[str, str] = {}
    dimensions: dict[str, tuple[str, str]] = {}
    for slot in slots:
        target = slot.injection
        if target is None:
            continue
        owner = sentinels.setdefault(target.sentinel, slot.name)
        if owner != slot.name:
            raise RuntimeError(
                f"credential slots {owner!r} and {slot.name!r} both declare sentinel "
                f"{target.sentinel!r}; a shared sentinel draws whichever secret matches first"
            )
        if target.dimension is not None:
            reachable = (target.host,) if isinstance(target.host, str) else target.host.hosts
            for host in reachable:
                metered = dimensions.setdefault(host, (slot.name, target.dimension))
                if metered[1] != target.dimension:
                    raise RuntimeError(
                        f"credential slots {metered[0]!r} and {slot.name!r} can both reach "
                        f"{host!r} but meter it as {metered[1]!r} and {target.dimension!r}; a host "
                        "is metered once, so the later dimension would be dropped"
                    )
        claims: list[tuple[str, object]] = []
        if target.env is not None:
            claims.append((target.env, f"the sentinel of slot {slot.name!r}"))
        if isinstance(target.host, HostChoice):
            if target.host.slot not in declared:
                raise RuntimeError(
                    f"credential slot {slot.name!r} selects its host through slot "
                    f"{target.host.slot!r}, which no installed extension declares; nothing can "
                    "fill it, so the host would stay the declared default forever"
                )
            if target.host.env is not None:
                claims.append((target.host.env, target.host))
        for name, carries in claims:
            holder, held = exported.setdefault(name, (f"credential slot {slot.name!r}", carries))
            if held != carries:
                raise RuntimeError(
                    f"{holder} exports env {name!r} into the sandbox carrying {held}, and "
                    f"credential slot {slot.name!r} exports it carrying {carries}; one variable "
                    "carries one value, and the later export would silently win the merge"
                )
    return slots


def turn_tools(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    *,
    audience: Audience,
) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext]]:
    """The full tool set a turn dispatches against — core builtins plus every extension's declared
    tools and connector tools — and, per extension tool, the workspace-scoped ExtensionContext its
    handler receives. A builtin has no entry, so the engine dispatches it with ext=None. A connector
    tool is scoped to its declaring extension exactly as a plain tool is, so its egress reaches the
    provider host under that extension's context. Only an extension that declares credential slots
    needs the credential key — a tool-only extension with no slots (a todo list) builds its context
    with none; a slot-declaring extension with no key set fails loud. Installation registration is
    limited to the surfaces that same manifest declares. Declared object kinds join one registry
    behind the five object verbs, each kind's store dispatching under its own extension's context
    exactly as its tools do."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    ext_by_tool: dict[str, ExtensionContext] = {}
    bound_kinds: list[BoundKind] = list(CORE_OBJECT_KINDS)
    for manifest in manifests:
        declared_tools = (
            *manifest.tools,
            *(tool for connector in manifest.connectors for tool in connector.tools),
        )
        if not declared_tools and not manifest.objects:
            continue
        declared = frozenset(slot.name for slot in manifest.credentials)
        if declared and credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares credential slots {sorted(declared)} "
                "but no credential key is set"
            )
        context = context_for(
            manifest.name,
            declared,
            index,
            embed,
            surfaces=frozenset(surface.name for surface in manifest.surfaces),
            audience=audience,
        )
        for tool in declared_tools:
            tools.append(tool)
            ext_by_tool[tool.name] = context
        bound_kinds.extend(
            BoundKind(kind=kind, extension=manifest.name, context=context)
            for kind in manifest.objects
        )
    bound_kinds.extend(core_object_kinds(manifests, credential_store))
    tools.extend(ObjectVerbs(object_registry(tuple(bound_kinds))).tools())
    return tuple(tools), ext_by_tool


def member_object_registry(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None = None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
) -> dict[str, BoundKind]:
    """The deploy's object kinds bound for member reads outside a turn — the portal's registry.
    The same kinds and the same boot validation as `turn_tools`, but each extension context is
    workspace-ambient rather than audience-scoped: a member read carries no conversation."""
    bound: list[BoundKind] = list(CORE_OBJECT_KINDS)
    for manifest in manifests:
        if not manifest.objects:
            continue
        declared = frozenset(slot.name for slot in manifest.credentials)
        if declared and credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares credential slots {sorted(declared)} "
                "but no credential key is set"
            )
        context = context_for(
            manifest.name,
            declared,
            index,
            embed,
            surfaces=frozenset(surface.name for surface in manifest.surfaces),
        )
        bound.extend(
            BoundKind(kind=kind, extension=manifest.name, context=context)
            for kind in manifest.objects
        )
    bound.extend(core_object_kinds(manifests, credential_store))
    return object_registry(tuple(bound))


def core_object_kinds(
    manifests: tuple[Manifest, ...], credential_store: CredentialStore | None = None
) -> tuple[BoundKind, ...]:
    """The kinds core itself registers, bound with no extension context — their handlers read the
    ambient workspace directly. `credential` projects every active manifest's declared slots, and
    reads a keyed slot's live host through the store so a read reports the host the wire uses."""
    slots = declared_slots(manifests)
    kind = ObjectKind(
        name=CREDENTIAL_KIND,
        description=CREDENTIAL_DESCRIPTION,
        guidance=CREDENTIAL_GUIDANCE,
        spec_model=CredentialSpec,
        store=CredentialObjects(slots=slots, credentials=credential_store),
    )
    return (BoundKind(kind=kind, extension=None, context=None),)


def skill_registry(
    manifests: tuple[Manifest, ...], generated: tuple[RuntimeSkill, ...] = ()
) -> SkillRegistry:
    """The loadable-skill set for the deploy: core's skills plus every active pack's contributed
    skills — each spec's parent and its nested children — parsed from disk once at boot (sync file
    I/O, off the loop), plus any boot-`generated` skills (the model-catalog rendered from the live
    registry). A skill whose name collides with one already registered is refused loud here, so no
    reader downstream — the `load_skill` resolver or the `{{skill_index}}` render — has to
    disambiguate."""
    by_name: dict[str, RuntimeSkill] = dict(CORE_SKILLS_BY_NAME)
    for manifest in manifests:
        for spec in manifest.skills:
            for skill in discover_skills(spec.path).values():
                if skill.name in by_name:
                    raise ValueError(
                        f"extension {manifest.name!r} contributes skill {skill.name!r}, "
                        f"which is already registered"
                    )
                by_name[skill.name] = skill
    for skill in generated:
        if skill.name in by_name:
            raise ValueError(f"generated skill {skill.name!r} is already registered")
        by_name[skill.name] = skill
    return SkillRegistry(by_name)


def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]:
    """Every subagent profile the active extensions declare, in load order — the set `serve` builds
    the SubagentRegistry from. A duplicate name across extensions is rejected by the registry at
    construction, so boot fails loud rather than shadowing one profile with another."""
    return tuple(profile for manifest in manifests for profile in manifest.subagents)


def durable_surfaces(manifests: tuple[Manifest, ...]) -> frozenset[str]:
    """The surface names whose replies deliver through the writeback poller — a surface is durable
    exactly when it declares a `post` handler. Admission registers a writeback row for every turn
    entering one of these surfaces' conversations."""
    return frozenset(
        spec.name for manifest in manifests for spec in manifest.surfaces if spec.post is not None
    )


def turn_subagent_grants(manifests: tuple[Manifest, ...]) -> dict[str, frozenset[str]]:
    """The tools each extension exposes to a subagent profile it does not own, unioned by target
    profile across every manifest — the map the turn loop folds onto a profile's own `tool_names`
    before intersecting with the live tool set. A grant only widens; an unknown profile or an
    uninstalled tool degrades silently at the intersection, so a capability attaches its tools to
    the profiles the research docs list without the profile hard-coding foreign names."""
    grants: dict[str, set[str]] = {}
    for manifest in manifests:
        for grant in manifest.subagent_tool_grants:
            grants.setdefault(grant.profile, set()).update(grant.tool_names)
    return {profile: frozenset(names) for profile, names in grants.items()}


async def turn_runtime_skills(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
) -> tuple[RuntimeSkill, ...]:
    """Every runtime skill active extensions provide for the bound agent, flattened in load order.
    Each provider runs under the turn's workspace and agent scope with its ExtensionContext. An
    extension providing runtime skills without a credential key set fails loud."""
    skills: list[RuntimeSkill] = []
    for manifest in manifests:
        if manifest.runtime_skills is None:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} provides runtime skills but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed)
        skills.extend(await manifest.runtime_skills(context))
    return tuple(skills)


DEFAULT_BACKEND = "default"


def index_backend(
    manifests: tuple[Manifest, ...],
    configured: str | None,
    credential_store: CredentialStore | None,
) -> IndexBackend:
    """The workspace's index backend: the named backend an extension contributes through its
    `indexes` Manifest point, or — the config knob unset — the base-pinned `index_default`
    extension registering name `"default"` (SQLite FTS5 + local cosine, Postgres tsvector +
    pgvector). No extension registering the selected name fails loud; a backend declaring credential
    slots with no credential key set fails loud, since its factory reads its BYOK key in-process."""
    name = configured or DEFAULT_BACKEND
    for manifest in manifests:
        for spec in manifest.indexes:
            if spec.name != name:
                continue
            declared = frozenset(slot.name for slot in manifest.credentials)
            if declared and credential_store is None:
                raise RuntimeError(f"index backend {name!r} needs a credential key but none is set")
            context = context_for(manifest.name, declared)
            return spec.factory(context)
    raise NotRegisteredError(f"config selects index backend {name!r} but no extension registers it")


def embed_backend(
    manifests: tuple[Manifest, ...],
    configured: str | None,
    credential_store: CredentialStore | None,
) -> EmbedClient:
    """The deploy's embed client: the named backend an extension contributes through its `embeds`
    Manifest point, or — the config knob unset — the base-pinned `embed_openai` extension
    registering name `"default"`. Resolved once at boot and threaded onto the contexts the index,
    the memory tools, and the derivation jobs receive. No extension registering the selected name
    fails loud; a backend declaring credential slots with no credential key set fails loud."""
    name = configured or DEFAULT_BACKEND
    for manifest in manifests:
        for spec in manifest.embeds:
            if spec.name != name:
                continue
            declared = frozenset(slot.name for slot in manifest.credentials)
            if declared and credential_store is None:
                raise RuntimeError(f"embed backend {name!r} needs a credential key but none is set")
            context = context_for(manifest.name, declared)
            return spec.factory(context)
    raise NotRegisteredError(f"config selects embed backend {name!r} but no extension registers it")


def memory_search(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    name: str = DEFAULT_MEMORY_SEARCH_PROVIDER,
) -> MemorySearch | None:
    """Build one named provider with its declaring extension's scoped context."""
    providers: list[tuple[Manifest, MemorySearchProviderSpec]] = []
    for manifest in manifests:
        providers.extend((manifest, spec) for spec in manifest.memory_search if spec.name == name)
    if not providers:
        return None
    if len(providers) > 1:
        raise RuntimeError(
            f"two extensions register memory search provider {name!r}: "
            + ", ".join(sorted(manifest.name for manifest, _spec in providers))
        )
    manifest, spec = providers[0]
    declared = frozenset(slot.name for slot in manifest.credentials)
    if declared and credential_store is None:
        raise RuntimeError(
            f"memory search provider {manifest.name!r} declares credential slots "
            "but no credential key is set"
        )
    return MemorySearch(spec.build(context_for(manifest.name, declared, index, embed)))


def validate_ext_tools(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
) -> None:
    """Fail loud at boot on a misconfigured extension — a tool whose name collides with a builtin or
    another extension, an object kind that collides or fails the registration gates, or a
    tools-declaring extension with no credential key — so a deploy fails to start rather than
    coming up healthy and then failing every turn that builds the registry. Deploy-level: it checks
    the tool defs, kind gates, and key presence, never builds a per-workspace context (a shared
    fleet has no workspace at boot; the turn builds each tool's context per request)."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    for manifest in manifests:
        declared_tools = (
            *manifest.tools,
            *(tool for connector in manifest.connectors for tool in connector.tools),
        )
        if not declared_tools:
            continue
        if manifest.credentials and credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares credential slots "
                f"{sorted(slot.name for slot in manifest.credentials)} but no credential key is set"
            )
        tools.extend(declared_tools)
    registry = object_registry(
        (
            *CORE_OBJECT_KINDS,
            *(
                BoundKind(kind=kind, extension=manifest.name, context=None)
                for manifest in manifests
                for kind in manifest.objects
            ),
            *core_object_kinds(manifests),
        )
    )
    tools.extend(ObjectVerbs(registry).tools())
    ToolRegistry(tuple(tools))


class HookOutcomeNotAllowed(TypeError):
    """A hook returned an outcome its event does not permit (a Deny from post_tool_use, a
    ModifyInput from user_prompt_submit); treated as the hook malfunctioning under the failure
    policy."""


@dataclass(frozen=True)
class BoundHook:
    spec: HookSpec
    ext: ExtensionContext


@dataclass(frozen=True)
class HookResolution:
    """The folded outcome of firing an event's hooks. `denied` is set (short-circuit) the moment a
    hook Denies; otherwise `tool_input`/`output` carry the left-to-right ModifyInput/ModifyOutput
    fold (each hook saw the prior's) and `injected` concatenates every InjectContext in order.
    `failed_closed` names the class of the fault behind a denial a gating hook produced by failing
    rather than by deciding — the two are one refusal to the caller and two different events to an
    operator, so what reports the refusal can tell them apart."""

    denied: str | None = None
    failed_closed: str | None = None
    tool_input: BaseModel | None = None
    output: str | None = None
    injected: str = ""


@dataclass(frozen=True)
class HookChain:
    """The turn's reactive hooks, grouped by event in lockfile pin order. `fire` runs one event's
    hooks and folds their outcomes into a HookResolution the engine applies at the fire point. Only
    turn-lifecycle events live here; the data-plane page_change event is driven by the core
    page-change runner in the jobs role, never the turn chain."""

    hooks: dict[HookEvent, tuple[BoundHook, ...]] = field(default_factory=dict)
    audience: Audience = SHARED_AUDIENCE

    def __post_init__(self) -> None:
        bound = tuple(hook for hooks in self.hooks.values() for hook in hooks)
        if any(hook.ext.audience != self.audience for hook in bound):
            raise ValueError("hook and chain audiences differ")

    async def fire(
        self,
        event: HookEvent,
        payload: HookPayload,
        turn: Turn | None,
        agent: Agent | None,
        speaker_member_id: UUID | None,
    ) -> HookResolution:
        """Run every hook bound to `event` in order and fold their outcomes. Any Deny denies and
        short-circuits (later hooks skip); ModifyInput/ModifyOutput fold left-to-right so each hook
        sees the prior's result; InjectContext concatenates in order. Composition trust is the pin
        alone — no hook can admit a tool grants withheld. A gating hook (pre_tool_use,
        user_prompt_submit) that raises or exceeds the timeout fails closed to a Deny (fail loud); a
        non-gating hook (post_tool_use and every observe event) that raises — or returns an outcome
        its event does not permit — is swallowed with a log, never failing the turn."""
        bound = self.hooks.get(event, ())
        gating = event in GATING_EVENTS
        tool_input = payload.tool_input if isinstance(payload, PreToolUse) else None
        output = payload.output if isinstance(payload, PostToolUse) else None
        injected: list[str] = []
        for hook in bound:
            assert self.audience is not None
            current: HookPayload
            match payload:
                case PreToolUse() | PostToolUse() | PostToolUseFailure() if hook.spec.tools and (
                    payload.tool_name not in hook.spec.tools
                ):
                    continue
                case PreToolUse():
                    assert tool_input is not None
                    current = replace(payload, tool_input=tool_input)
                case PostToolUse():
                    assert output is not None
                    current = replace(payload, output=output)
                case _:
                    current = payload
            context = HookContext(
                ext=hook.ext,
                payload=current,
                turn=turn,
                agent=agent,
                audience=self.audience,
                speaker_member_id=speaker_member_id,
            )
            try:
                async with asyncio.timeout(HOOK_TIMEOUT_SECONDS):
                    outcome = await hook.spec.handler(context)
                if outcome is not None and not isinstance(outcome, ALLOWED_OUTCOMES[event]):
                    raise HookOutcomeNotAllowed(f"{event} hook returned {type(outcome).__name__}")
            except Exception as error:
                if gating:
                    return HookResolution(
                        denied=(
                            f"hook {hook.ext.store.extension!r} failed closed on {event}: "
                            f"{type(error).__name__}"
                        ),
                        failed_closed=type(error).__name__,
                    )
                log(
                    "hook.swallowed",
                    extension=hook.ext.store.extension,
                    hook_event=event,
                    error_class=type(error).__name__,
                )
                continue
            match outcome:
                case Deny(reason=reason):
                    return HookResolution(denied=reason)
                case ModifyInput(tool_input=new_input):
                    tool_input = new_input
                case ModifyOutput(output=new_output):
                    output = new_output
                case InjectContext(text=text):
                    injected.append(text)
                case None:
                    continue
        return HookResolution(tool_input=tool_input, output=output, injected="\n".join(injected))


def turn_hooks(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    *,
    audience: Audience,
) -> HookChain:
    """The turn's reactive hook chain — every declared turn-lifecycle hook bound to its extension's
    workspace-scoped ExtensionContext (the same handle its tools and jobs receive), grouped by
    event in the order `load_manifests` returns (lockfile pin order). The data-plane page_change
    event is deliberately excluded — the core page-change runner binds and drives it in the jobs
    role with the model wired, never here. An extension that declares hooks without a credential key
    set fails loud, since its context needs the credential store."""
    grouped: dict[HookEvent, list[BoundHook]] = {event: [] for event in TURN_HOOK_EVENTS}
    for manifest in manifests:
        if not manifest.hooks:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares hooks but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed, audience=audience)
        for spec in manifest.hooks:
            if spec.event == "page_change":
                continue
            grouped[spec.event].append(BoundHook(spec=spec, ext=context))
    return HookChain(
        hooks={event: tuple(bound) for event, bound in grouped.items()},
        audience=audience,
    )
