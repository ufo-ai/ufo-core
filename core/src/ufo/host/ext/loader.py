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
`HookChain` — every declared hook bound to its extension's scoped context, grouped by event; and
`connection_hooks` reads them into the `ConnectionHookChain` the connect flow publishes a landed
connection to."""

import asyncio
import hashlib
import importlib.util
import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ufo.blob import WorkspaceBlobStore
from ufo.harness.o11y import log, warn
from ufo.host.ext.extension_kind import (
    EXTENSION_DESCRIPTION,
    EXTENSION_GUIDANCE,
    EXTENSION_KIND,
    ExtensionObjects,
    ExtensionSpec,
    named_extensions,
)
from ufo.host.kinds.artifacts import artifact_object
from ufo.host.kinds.conversations import CONVERSATION_OBJECT
from ufo.host.kinds.credential_kind import (
    CREDENTIAL_DESCRIPTION,
    CREDENTIAL_GUIDANCE,
    CREDENTIAL_KIND,
    CredentialObjects,
    CredentialSpec,
)
from ufo.host.kinds.members import MEMBER_OBJECT
from ufo.host.kinds.surface_kind import (
    SURFACE_DESCRIPTION,
    SURFACE_GUIDANCE,
    SURFACE_KIND,
    SurfaceObjects,
    SurfaceObjectSpec,
    registered_surfaces,
)
from ufo.host.kinds.workspace_kind import WORKSPACE_OBJECT
from ufo.host.tools.builtins import BUILTIN_ACTIONS, BUILTIN_TOOLS
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.access.grants import ConnectionRecorded
from ufo.runtime.authority import WORKSPACE_AUTHORITY, ExecutionAuthority
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.hooks import (
    CONNECTION_RECORDED,
    HOOK_TIMEOUT_SECONDS,
    TURN_HOOK_EVENTS,
    BoundHook,
    HookChain,
)
from ufo.runtime.ext.manifest import (
    CredentialSlot,
    HookContext,
    HookEvent,
    Manifest,
    MemberSkillsSpec,
    MemorySearchProviderSpec,
    NotRegisteredError,
    Pack,
    SubagentProfile,
    declared_slots,
)
from ufo.runtime.ext.surface import TurnTailer
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.kinds.agents import AGENT_OBJECT
from ufo.runtime.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemorySearch
from ufo.runtime.object_views import frame_admissible_ids
from ufo.runtime.objects import (
    BoundAction,
    BoundKind,
    ObjectKind,
    ObjectVerbs,
    action_registry,
    object_registry,
)
from ufo.runtime.skills.runtime import (
    CORE_SKILLS_BY_NAME,
    RuntimeSkill,
    SkillCard,
    SkillMaterializer,
    SkillRegistry,
    discover_skills,
)
from ufo.runtime.tools.registry import ToolDef, ToolRegistry
from ufo.runtime.turns.audience import Audience

CORE_OBJECT_KINDS: tuple[BoundKind, ...] = (
    BoundKind(kind=AGENT_OBJECT, extension=None, context=None),
    BoundKind(kind=CONVERSATION_OBJECT, extension=None, context=None),
    BoundKind(kind=MEMBER_OBJECT, extension=None, context=None),
    BoundKind(kind=WORKSPACE_OBJECT, extension=None, context=None),
)
EXTENSION_ENTRY_POINT_GROUP = "ufo.extension"
PACK_ENTRY_POINT_GROUP = "ufo.pack"
LOCKFILE_PATH_ENV = "UFO_LOCKFILE"
DEFAULT_LOCKFILE_PATH = Path("ufo.lock")
DIGEST_PREFIX = "sha256:"
MIGRATIONS_DIRNAME = "migrations"


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
        if manifest.member_context_read and (entry.dist is None or entry.dist.name != "ufo"):
            raise ValueError(
                f"third-party extension {manifest.name!r} cannot declare privileged capabilities"
            )
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
    return extension_content_digest({name: path.read_bytes() for name, path in files.items()})


def extension_content_digest(files: Mapping[str, bytes]) -> str:
    """Hash the named files that make one installed extension package."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(hashlib.sha256(name.encode()).digest())
        digest.update(hashlib.sha256(files[name]).digest())
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
    return tuple(active.values()) if pack is None else _pack_manifests(pack, active)


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
            prompt_sections=found.prompt_sections,
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
    blob: WorkspaceBlobStore | None = None,
    *,
    audience: Audience,
    public_base_url: str | None = None,
    home_surface: str | None = None,
    artifact_token_secret: str = "",
    member_context_authority: ExecutionAuthority = WORKSPACE_AUTHORITY,
    member_context_blob: WorkspaceBlobStore | None = None,
) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]:
    """The full tool set a turn dispatches against — core builtins plus every extension's declared
    tools and connector tools — the workspace-scoped ExtensionContext each extension tool's
    handler receives, and the ObjectVerbs the engine resolves object calls through. A builtin has
    no entry, so the engine dispatches it with ext=None. A connector
    tool is scoped to its declaring extension exactly as a plain tool is, so its egress reaches the
    provider host under that extension's context. Only an extension that declares credential slots
    needs the credential key — a tool-only extension with no slots (a todo list) builds its context
    with none; a slot-declaring extension with no key set fails loud. Installation registration is
    limited to the surfaces that same manifest declares. Declared object kinds join one registry
    behind the six object verbs, each kind's store dispatching under its own extension's context
    exactly as its tools do. A declared tool whose `bound` names a kind is an object action: it
    joins the action registry under the same context instead of the wire set, validated only after
    every manifest is collected so extension order cannot fail a cross-extension attachment.
    `public_base_url` and `home_surface` are the two halves of a link into
    the browser portal, so a tool answering with somewhere for the member to go renders it through
    `ctx.home_url` instead of assembling core's mount path itself."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    ext_by_tool: dict[str, ExtensionContext] = {}
    bound_kinds: list[BoundKind] = list(CORE_OBJECT_KINDS)
    bound_actions: list[BoundAction] = [
        BoundAction(action=action, extension=None, context=None) for action in BUILTIN_ACTIONS
    ]
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
            blob=blob,
            surfaces=frozenset(surface.name for surface in manifest.surfaces),
            addressed_surfaces=frozenset(
                surface.name for surface in manifest.surfaces if surface.addressed
            ),
            credential_sources=tuple(
                (slot.name, slot.source) for slot in manifest.credentials if slot.source is not None
            ),
            credential_store=credential_store,
            audience=audience,
            public_base_url=public_base_url,
            home_surface=home_surface,
            artifact_token_secret=artifact_token_secret,
            member_context_read=manifest.member_context_read,
            member_context_authority=member_context_authority,
            member_context_blob=member_context_blob,
        )
        for tool in declared_tools:
            if tool.bound is not None:
                bound_actions.append(
                    BoundAction(action=tool, extension=manifest.name, context=context)
                )
                continue
            tools.append(tool)
            ext_by_tool[tool.name] = context
        bound_kinds.extend(
            BoundKind(kind=kind, extension=manifest.name, context=context)
            for kind in manifest.objects
        )
    bound_kinds.extend(
        core_object_kinds(
            manifests,
            credential_store,
            public_base_url=public_base_url,
            artifact_token_secret=artifact_token_secret,
        )
    )
    kinds = object_registry(tuple(bound_kinds))
    verbs = ObjectVerbs(kinds, action_registry(tuple(bound_actions), kinds))
    tools.extend(verbs.tools())
    return tuple(tools), ext_by_tool, verbs


@dataclass(frozen=True)
class MemberObjectRegistry:
    """The deploy's kinds and actions bound for member reads outside a turn: what the portal
    projects its rows and its controls from."""

    kinds: dict[str, BoundKind]
    actions: dict[str, dict[str, BoundAction]]


def member_object_registry(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None = None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    *,
    public_base_url: str | None = None,
    artifact_token_secret: str = "",
) -> MemberObjectRegistry:
    """The deploy's object kinds and actions bound for member reads outside a turn — the portal's
    registry. The same kinds and the same boot validation as `turn_tools`, but each extension
    context is workspace-ambient rather than audience-scoped: a member read carries no
    conversation. Bound actions pass the same registration gates here, in this flavor, so a portal
    build fails loud exactly where a turn build would, and ride out beside the kinds for the
    portal's action projection."""
    bound: list[BoundKind] = list(CORE_OBJECT_KINDS)
    actions: list[BoundAction] = [
        BoundAction(action=action, extension=None, context=None) for action in BUILTIN_ACTIONS
    ]
    for manifest in manifests:
        declared_actions = tuple(
            tool
            for tool in (
                *manifest.tools,
                *(tool for connector in manifest.connectors for tool in connector.tools),
            )
            if tool.bound is not None
        )
        if not manifest.objects and not declared_actions:
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
            addressed_surfaces=frozenset(
                surface.name for surface in manifest.surfaces if surface.addressed
            ),
            public_base_url=public_base_url,
            artifact_token_secret=artifact_token_secret,
        )
        actions.extend(
            BoundAction(action=tool, extension=manifest.name, context=context)
            for tool in declared_actions
        )
        bound.extend(
            BoundKind(kind=kind, extension=manifest.name, context=context)
            for kind in manifest.objects
        )
    bound.extend(
        core_object_kinds(
            manifests,
            credential_store,
            public_base_url=public_base_url,
            artifact_token_secret=artifact_token_secret,
        )
    )
    kinds = object_registry(tuple(bound))
    return MemberObjectRegistry(kinds=kinds, actions=action_registry(tuple(actions), kinds))


def frame_admissible(
    manifests: tuple[Manifest, ...], registry: MemberObjectRegistry
) -> frozenset[str]:
    """The callables an embedded app page may post over this deploy — every global tool and bound
    action whose presentation says `frame`, a global by its tool name and an action by its
    canonical id. The action lane checks it on a frame-originated post."""
    declared = (
        tool
        for manifest in manifests
        for tool in (
            *manifest.tools,
            *(tool for connector in manifest.connectors for tool in connector.tools),
        )
    )
    return frozenset(frame_admissible_ids((*BUILTIN_TOOLS, *declared), registry.actions))


def core_object_kinds(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None = None,
    *,
    public_base_url: str | None = None,
    artifact_token_secret: str = "",
) -> tuple[BoundKind, ...]:
    """The kinds core itself registers over the active manifest set, bound with no extension
    context — their handlers read the ambient workspace directly. `credential` projects every
    manifest's declared slots, and reads a keyed slot's live host through the store so a read
    reports the host the wire uses; `extension` projects the manifests themselves, so two rendering
    one object name fail loud here at boot; `surface` projects every manifest's registered
    surfaces beside the workspace's installation bindings, refusing a duplicate surface name the
    same way; `artifact` takes the deploy's public base and artifact secret so its listing rows
    publish signed links."""
    credential = ObjectKind(
        name=CREDENTIAL_KIND,
        description=CREDENTIAL_DESCRIPTION,
        guidance=CREDENTIAL_GUIDANCE,
        spec_model=CredentialSpec,
        store=CredentialObjects(slots=declared_slots(manifests), credentials=credential_store),
        list_fields=frozenset({"extension", "filled"}),
    )
    extension = ObjectKind(
        name=EXTENSION_KIND,
        description=EXTENSION_DESCRIPTION,
        guidance=EXTENSION_GUIDANCE,
        spec_model=ExtensionSpec,
        store=ExtensionObjects(extensions=named_extensions(manifests)),
        list_fields=frozenset({"version", "tool_count", "credential_slot_count"}),
    )
    surface = ObjectKind(
        name=SURFACE_KIND,
        description=SURFACE_DESCRIPTION,
        guidance=SURFACE_GUIDANCE,
        spec_model=SurfaceObjectSpec,
        store=SurfaceObjects(surfaces=registered_surfaces(manifests)),
        list_fields=frozenset({"extension", "addressed", "durable", "home", "bound"}),
    )
    artifact = artifact_object(
        public_base_url=public_base_url, artifact_token_secret=artifact_token_secret
    )
    return (
        BoundKind(kind=artifact, extension=None, context=None),
        BoundKind(kind=credential, extension=None, context=None),
        BoundKind(kind=extension, extension=None, context=None),
        BoundKind(kind=surface, extension=None, context=None),
    )


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
    bundled_names = frozenset(by_name)
    for skill in generated:
        if skill.name in by_name:
            raise ValueError(f"generated skill {skill.name!r} is already registered")
        by_name[skill.name] = skill
    return SkillRegistry(by_name, bundled_names=bundled_names)


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


async def turn_member_skills(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    *,
    agent_name: str,
) -> tuple[tuple[SkillCard, ...], SkillMaterializer]:
    """The bound agent's member tier: every active extension's saved-skill routing cards, in load
    order, and one materializer that routes a name back to the provider that contributed it (an
    unknown name answers None). A card whose `agents` targeting excludes `agent_name` is left out
    whole — it neither routes nor loads on this agent's turns. Each provider runs under the turn's
    workspace and agent scope with its ExtensionContext. An extension providing member skills
    without a credential key set fails loud; a name two providers both claim keeps the first and
    drops the rest with a log — a duplicate row may never cost the agent its turns."""
    cards: list[SkillCard] = []
    providers: dict[str, tuple[MemberSkillsSpec, ExtensionContext]] = {}
    for manifest in manifests:
        if manifest.member_skills is None:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} provides member skills but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed)
        for card in await manifest.member_skills.cards(context):
            if card.agents and agent_name not in card.agents:
                continue
            if card.name in providers:
                log("skill.member_card_collision", skill=card.name, extension=manifest.name)
                continue
            providers[card.name] = (manifest.member_skills, context)
            cards.append(card)

    async def materialize(name: str) -> RuntimeSkill | None:
        provided = providers.get(name)
        if provided is None:
            return None
        spec, context = provided
        return await spec.materialize(context, name)

    return tuple(cards), materialize


async def member_skill_listing(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
) -> tuple[RuntimeSkill, ...]:
    """Every provider's member skills materialized whole — the portal's management listing read:
    one store read per provider through `materialize_all`, a corrupt row skipped by the provider
    with a log rather than failing the page. The same credential gate as `turn_member_skills`, and
    the same collision rule: a name two providers claim keeps the first. No `agents` targeting is
    applied — the management page shows the workspace's whole set, targeted or not."""
    listed: dict[str, RuntimeSkill] = {}
    for manifest in manifests:
        if manifest.member_skills is None:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} provides member skills but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed)
        for skill in await manifest.member_skills.materialize_all(context):
            if skill.name in listed:
                log("skill.member_card_collision", skill=skill.name, extension=manifest.name)
                continue
            listed[skill.name] = skill
    return tuple(listed.values())


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
) -> dict[str, dict[str, BoundAction]]:
    """Fail loud at boot on a misconfigured extension — a tool whose name collides with a builtin or
    another extension, an object kind or bound action that collides or fails the registration
    gates, or a tools-declaring extension with no credential key — so a deploy fails to start
    rather than coming up healthy and then failing every turn that builds the registry.
    Deploy-level: it checks the tool defs, kind and action gates, and key presence, never builds a
    per-workspace context (a shared fleet has no workspace at boot; the turn builds each tool's
    context per request). Returns the validated action registry, context-free — the deploy's
    action names and flags for surfaces that answer capability questions outside a turn (the
    sandbox tool bridge)."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    actions: list[BoundAction] = [
        BoundAction(action=action, extension=None, context=None) for action in BUILTIN_ACTIONS
    ]
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
        for tool in declared_tools:
            if tool.bound is not None:
                actions.append(BoundAction(action=tool, extension=manifest.name, context=None))
                continue
            tools.append(tool)
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
    validated = action_registry(tuple(actions), registry)
    tools.extend(ObjectVerbs(registry, validated).tools())
    ToolRegistry(tuple(tools))
    return validated


async def turn_workspace_facts(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    *,
    audience: Audience,
) -> tuple[str, ...]:
    """The lines every declared workspace fact says this workspace holds, in manifest order.

    Each read runs with the declaring extension's own scoped context — the same handle its tools and
    hooks receive, surfaces and credential slots included — so an extension answers for the leg its
    own connect act writes and core names no provider. A read that raises drops its own line and
    nothing else: the block decorates the prompt, and a database hiccup must not cost the member
    their turn."""
    lines: list[str] = []
    for manifest in manifests:
        if not manifest.workspace_facts:
            continue
        context = context_for(
            manifest.name,
            frozenset(slot.name for slot in manifest.credentials),
            surfaces=frozenset(surface.name for surface in manifest.surfaces),
            addressed_surfaces=frozenset(
                surface.name for surface in manifest.surfaces if surface.addressed
            ),
            credential_store=credential_store,
            audience=audience,
        )
        for fact in manifest.workspace_facts:
            try:
                held = await fact.holds(context)
            except Exception as error:
                warn(
                    "workspace_fact.unread",
                    extension=manifest.name,
                    fact=fact.name,
                    error_class=type(error).__name__,
                )
                continue
            if held:
                lines.append(fact.line)
    return tuple(lines)


def turn_hooks(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    tailer: TurnTailer | None = None,
    *,
    audience: Audience,
    public_base_url: str | None = None,
) -> HookChain:
    """The turn's reactive hook chain — every declared turn-lifecycle hook bound to its extension's
    workspace-scoped ExtensionContext (the same handle its tools and jobs receive), grouped by
    event in the order `load_manifests` returns (lockfile pin order). The data-plane page_change
    event is deliberately excluded — the core page-change runner binds and drives it in the jobs
    role with the model wired, never here. The `tailer` is the loop's own, so a hook watching the
    turn it fires under reads the frames that turn publishes, and `public_base_url` is the deploy's
    externally reachable base, which a hook rendering a link a member opens cannot reach any other
    way. Its surfaces are declared exactly as a tool's context declares them, so a hook reads the
    installation its own surface bound. An extension that declares hooks without a credential key
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
        context = context_for(
            manifest.name,
            declared,
            index,
            embed,
            surfaces=frozenset(surface.name for surface in manifest.surfaces),
            addressed_surfaces=frozenset(
                surface.name for surface in manifest.surfaces if surface.addressed
            ),
            tailer=tailer,
            audience=audience,
            public_base_url=public_base_url,
        )
        for spec in manifest.hooks:
            if spec.event not in TURN_HOOK_EVENTS:
                continue
            grouped[spec.event].append(BoundHook(spec=spec, ext=context))
    return HookChain(
        hooks={event: tuple(bound) for event, bound in grouped.items()},
        audience=audience,
    )


@dataclass(frozen=True)
class ConnectionHookChain:
    """The control-plane counterpart of the turn chain: the `connection_recorded` hooks a landed
    connection reaches, in lockfile pin order. The connect flow publishes inside the request that
    completed the handoff, with the workspace bound and the connection committed, so an extension
    creates what the connection implies — a connected account's feeds — before the member reads the
    callback. Observe-only: the connection is recorded whatever a handler makes of it, so a handler
    that raises or outlives the hook timeout is logged and swallowed, and the extension's own job
    retries the creation it did not finish."""

    hooks: tuple[BoundHook, ...] = ()

    async def fire(self, connection: ConnectionRecorded) -> None:
        for hook in self.hooks:
            try:
                async with asyncio.timeout(HOOK_TIMEOUT_SECONDS):
                    await hook.spec.handler(HookContext(ext=hook.ext, payload=connection))
            except Exception as error:
                log(
                    "hook.swallowed",
                    extension=hook.ext.store.extension,
                    hook_event=CONNECTION_RECORDED,
                    error_class=type(error).__name__,
                )


def connection_hooks(
    manifests: tuple[Manifest, ...],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
) -> ConnectionHookChain:
    """The chain the connect flow publishes to — every declared `connection_recorded` hook bound to
    its extension's workspace-scoped ExtensionContext, the same handle its jobs receive, so the
    connect-time creation and the job that retries it run identical code. An extension that declares
    hooks without a credential key set fails loud, since its context needs the credential store."""
    bound: list[BoundHook] = []
    for manifest in manifests:
        specs = tuple(spec for spec in manifest.hooks if spec.event == CONNECTION_RECORDED)
        if not specs:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares hooks but no credential key is set"
            )
        context = context_for(
            manifest.name,
            frozenset(slot.name for slot in manifest.credentials),
            index,
            embed,
        )
        bound.extend(BoundHook(spec=spec, ext=context) for spec in specs)
    return ConnectionHookChain(hooks=tuple(bound))
