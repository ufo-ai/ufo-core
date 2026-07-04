"""Discover installed extensions: entry points → validated Manifests, and the turn's tool set.

Each `selfhost.extension` entry point is a zero-arg callable returning a Manifest; the loader
collects them, rejects duplicate names, and hands the tuple to the derivations that read it.
Discovery is the only path in — extensions declare, they never call a registration API.

The lockfile is the deploy's pinned extension set: when it exists, only the extensions it pins load,
and each must match its pinned digest or boot fails loud — tamper and drift are refused, not run.
With no lockfile the deploy is in dev mode and every discovered extension is active. `selfhost ext`
and `selfhost bundle` write this file; `load_manifests` reads it, so the set the operator pinned is
exactly what every derivation (tools, jobs, routes, proxy rules) sees.

`turn_tools` reads the active manifests into the set a turn dispatches against and the owning
ExtensionContext for each extension tool; `turn_hooks` reads them into the turn's reactive
`HookChain` — every declared hook bound to its extension's scoped context, grouped by event."""

import asyncio
import hashlib
import importlib.util
import os
from dataclasses import dataclass, replace
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from selfhost.credentials import CredentialStore
from selfhost.ext.context import ExtensionContext, context_for
from selfhost.ext.manifest import (
    Deny,
    HookContext,
    HookEvent,
    HookPayload,
    HookSpec,
    InjectContext,
    Manifest,
    ModifyInput,
    ModifyOutput,
    PostToolUse,
    PreToolUse,
    SubagentProfile,
)
from selfhost.memory.embed import EmbedClient
from selfhost.memory.index import IndexBackend, index_backend_for
from selfhost.memory.service import MemoryService
from selfhost.o11y import log
from selfhost.schema.records import Agent, Turn
from selfhost.skills.runtime import CORE_SKILLS_BY_NAME, RuntimeSkill, SkillRegistry, parse_skill
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.registry import ToolDef, ToolRegistry

EXTENSION_ENTRY_POINT_GROUP = "selfhost.extension"
LOCKFILE_PATH_ENV = "SELFHOST_LOCKFILE"
DEFAULT_LOCKFILE_PATH = Path("selfhost.lock")
DIGEST_PREFIX = "sha256:"
HOOK_TIMEOUT_SECONDS = 5.0
GATING_EVENTS: frozenset[HookEvent] = frozenset({"pre_tool_use", "on_inbound"})
ALLOWED_OUTCOMES: dict[HookEvent, tuple[type, ...]] = {
    "pre_tool_use": (Deny, ModifyInput),
    "post_tool_use": (ModifyOutput, InjectContext),
    "on_inbound": (Deny, InjectContext),
}


class ExtensionPin(BaseModel):
    """One extension the lockfile pins: its name, the version pinned, and the digest of its
    installed source the loader re-checks at boot."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    digest: str


class Lockfile(BaseModel):
    """The deploy's pinned components — the selfhost version that anchors dependencies and each
    pinned extension. Crosses the boundary between the `selfhost ext`/`bundle` writers and the boot
    reader as a file, so it validates at construction both ways."""

    model_config = ConfigDict(extra="forbid")
    selfhost_version: str
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


def extension_digest(entry: EntryPoint) -> str:
    """The digest that pins an extension: sha256 over every source file of the installed package the
    entry point belongs to — not only its entry module — so editing any file in a multi-file
    extension changes the digest and a pinned deploy refuses to run it. Bytecode caches, which are
    machine-specific and rebuilt on import, are excluded so the digest is stable across machines."""
    top = entry.module.split(".", 1)[0]
    spec = importlib.util.find_spec(top)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"extension package {top!r} has no importable source")
    if spec.submodule_search_locations:
        root = Path(next(iter(spec.submodule_search_locations)))
        files = {
            path.relative_to(root).as_posix(): path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
    else:
        origin = Path(spec.origin)
        files = {origin.name: origin}
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(hashlib.sha256(name.encode()).digest())
        digest.update(hashlib.sha256(files[name].read_bytes()).digest())
    return DIGEST_PREFIX + digest.hexdigest()


def load_manifests() -> tuple[Manifest, ...]:
    """The active extension set. With a lockfile present it is exactly the pinned extensions, each
    verified against its pinned digest (a missing or drifted extension fails loud); with none, every
    discovered extension is active."""
    installed = discovered()
    path = lockfile_path()
    if not path.exists():
        return tuple(manifest for manifest, _ in installed.values())
    manifests: list[Manifest] = []
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
        manifests.append(manifest)
    return tuple(manifests)


def turn_tools(
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credential_store: CredentialStore | None,
    memory: MemoryService | None = None,
) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext]]:
    """The full tool set a turn dispatches against — core builtins plus every extension's declared
    tools and connector tools — and, per extension tool, the workspace-scoped ExtensionContext its
    handler receives. A builtin has no entry, so the engine dispatches it with ext=None. A connector
    tool is scoped to its declaring extension exactly as a plain tool is, so its egress reaches the
    provider host under that extension's context. An extension that declares tools without a
    credential key set fails loud, since its context needs the credential store."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    ext_by_tool: dict[str, ExtensionContext] = {}
    for manifest in manifests:
        declared_tools = (
            *manifest.tools,
            *(tool for connector in manifest.connectors for tool in connector.tools),
        )
        if not declared_tools:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares tools but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(workspace_id, manifest.name, declared, credential_store, memory)
        for tool in declared_tools:
            tools.append(tool)
            ext_by_tool[tool.name] = context
    return tuple(tools), ext_by_tool


def skill_registry(manifests: tuple[Manifest, ...]) -> SkillRegistry:
    """The loadable-skill set for the deploy: the core three plus every active pack's contributed
    skills, parsed from disk once at boot (sync file I/O, off the loop). A pack skill whose name
    collides with a core skill or another pack's is refused loud here, so no reader downstream — the
    `load_skill` resolver or the `{{skill_index}}` render — has to disambiguate."""
    by_name: dict[str, RuntimeSkill] = dict(CORE_SKILLS_BY_NAME)
    for manifest in manifests:
        for spec in manifest.skills:
            skill = parse_skill(spec.path)
            if skill.name in by_name:
                raise ValueError(
                    f"extension {manifest.name!r} contributes skill {skill.name!r}, "
                    f"which is already registered"
                )
            by_name[skill.name] = skill
    return SkillRegistry(by_name)


def turn_subagents(manifests: tuple[Manifest, ...]) -> tuple[SubagentProfile, ...]:
    """Every subagent profile the active extensions declare, in load order — the set `serve` builds
    the SubagentRegistry from. A duplicate name across extensions is rejected by the registry at
    construction, so boot fails loud rather than shadowing one profile with another."""
    return tuple(profile for manifest in manifests for profile in manifest.subagents)


def index_backend(
    manifests: tuple[Manifest, ...],
    configured: str | None,
    database_url: str,
    embed: EmbedClient,
    workspace_id: UUID,
    credential_store: CredentialStore | None,
) -> IndexBackend:
    """The workspace's index backend: the deploy's dialect-native default (config
    `memory.index_backend` unset) or the named backend an extension contributes through its
    `indexes` Manifest point, layered over that default. A configured name no extension registers
    fails loud; a named backend with no credential key set fails loud, since its factory reads a
    credential reader scoped to the declaring extension's slots for its BYOK key."""
    if configured is None:
        return index_backend_for(database_url, embed)
    for manifest in manifests:
        for spec in manifest.indexes:
            if spec.name != configured:
                continue
            if credential_store is None:
                raise RuntimeError(
                    f"index backend {configured!r} needs a credential key but none is set"
                )
            declared = frozenset(slot.name for slot in manifest.credentials)
            context = context_for(workspace_id, manifest.name, declared, credential_store)
            return spec.factory(embed, context.credentials)
    raise RuntimeError(f"config selects index backend {configured!r} but no extension registers it")


def validate_ext_tools(
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credential_store: CredentialStore | None,
) -> None:
    """Fail loud at boot on a misconfigured extension — a tool whose name collides with a builtin or
    another extension, or a tools-declaring extension with no credential key — so a deploy fails to
    start rather than coming up healthy and then failing every turn that builds the registry."""
    tools, _ = turn_tools(manifests, workspace_id, credential_store)
    ToolRegistry(tools)


class HookOutcomeNotAllowed(TypeError):
    """A hook returned an outcome its event does not permit (a Deny from post_tool_use, a
    ModifyInput from on_inbound); treated as the hook malfunctioning under the failure policy."""


@dataclass(frozen=True)
class BoundHook:
    spec: HookSpec
    ext: ExtensionContext


@dataclass(frozen=True)
class HookResolution:
    """The folded outcome of firing an event's hooks. `denied` is set (short-circuit) the moment a
    hook Denies; otherwise `tool_input`/`output` carry the left-to-right ModifyInput/ModifyOutput
    fold (each hook saw the prior's) and `injected` concatenates every InjectContext in order."""

    denied: str | None = None
    tool_input: BaseModel | None = None
    output: str | None = None
    injected: str = ""


@dataclass(frozen=True)
class HookChain:
    """The turn's reactive hooks, grouped by event in lockfile pin order. `fire` runs one event's
    hooks and folds their outcomes into a HookResolution the engine applies at the fire point."""

    pre_tool_use: tuple[BoundHook, ...] = ()
    post_tool_use: tuple[BoundHook, ...] = ()
    on_inbound: tuple[BoundHook, ...] = ()

    async def fire(
        self,
        event: HookEvent,
        payload: HookPayload,
        turn: Turn,
        agent: Agent,
        member_id: UUID | None,
    ) -> HookResolution:
        """Run every hook bound to `event` in order and fold their outcomes. Any Deny denies and
        short-circuits (later hooks skip); ModifyInput/ModifyOutput fold left-to-right so each hook
        sees the prior's result; InjectContext concatenates in order. Composition trust is the pin
        alone — no hook can admit a tool grants withheld. A gating hook (pre_tool_use, on_inbound)
        that raises or exceeds the timeout fails closed to a Deny (fail loud); an observe hook
        (post_tool_use) that raises is swallowed with a log, never failing the turn."""
        bound = {
            "pre_tool_use": self.pre_tool_use,
            "post_tool_use": self.post_tool_use,
            "on_inbound": self.on_inbound,
        }[event]
        gating = event in GATING_EVENTS
        tool_input = payload.tool_input if isinstance(payload, PreToolUse) else None
        output = payload.output if isinstance(payload, PostToolUse) else None
        injected: list[str] = []
        for hook in bound:
            match payload:
                case PreToolUse() | PostToolUse() if hook.spec.tools and (
                    payload.tool_name not in hook.spec.tools
                ):
                    continue
                case PreToolUse():
                    current = replace(payload, tool_input=tool_input)
                case PostToolUse():
                    current = replace(payload, output=output)
                case _:
                    current = payload
            context = HookContext(
                ext=hook.ext, turn=turn, agent=agent, member_id=member_id, payload=current
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
                        )
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
    workspace_id: UUID,
    credential_store: CredentialStore | None,
    memory: MemoryService | None = None,
) -> HookChain:
    """The turn's reactive hook chain — every declared hook bound to its extension's
    workspace-scoped ExtensionContext (the same handle its tools and jobs receive), grouped by
    event in the order `load_manifests` returns (lockfile pin order). An extension that declares
    hooks without a credential key set fails loud, since its context needs the credential store."""
    grouped: dict[HookEvent, list[BoundHook]] = {
        "pre_tool_use": [],
        "post_tool_use": [],
        "on_inbound": [],
    }
    for manifest in manifests:
        if not manifest.hooks:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares hooks but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(workspace_id, manifest.name, declared, credential_store, memory)
        for spec in manifest.hooks:
            grouped[spec.event].append(BoundHook(spec=spec, ext=context))
    return HookChain(
        pre_tool_use=tuple(grouped["pre_tool_use"]),
        post_tool_use=tuple(grouped["post_tool_use"]),
        on_inbound=tuple(grouped["on_inbound"]),
    )
