"""Discover installed extensions: entry points → validated Manifests, and the turn's tool set.

Each `selfhost.extension` entry point is a zero-arg callable returning a Manifest; the loader
collects them, rejects duplicate names, and hands the tuple to the derivations that read it.
Discovery is the only path in — extensions declare, they never call a registration API.
`turn_tools` reads those manifests into the set a turn dispatches against and the owning
ExtensionContext for each extension tool."""

from importlib.metadata import entry_points
from uuid import UUID

from selfhost.credentials import CredentialStore
from selfhost.ext.context import ExtensionContext, context_for
from selfhost.ext.manifest import Manifest
from selfhost.memory.service import MemoryService
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.registry import ToolDef, ToolRegistry

EXTENSION_ENTRY_POINT_GROUP = "selfhost.extension"


def load_manifests() -> tuple[Manifest, ...]:
    manifests = tuple(entry.load()() for entry in entry_points(group=EXTENSION_ENTRY_POINT_GROUP))
    names = [manifest.name for manifest in manifests]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate extension names: {', '.join(duplicates)}")
    return manifests


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
