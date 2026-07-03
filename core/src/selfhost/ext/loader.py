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
ExtensionContext for each extension tool."""

import hashlib
import importlib.util
import os
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from selfhost.credentials import CredentialStore
from selfhost.ext.context import ExtensionContext, context_for
from selfhost.ext.manifest import Manifest
from selfhost.memory.service import MemoryService
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.registry import ToolDef, ToolRegistry

EXTENSION_ENTRY_POINT_GROUP = "selfhost.extension"
LOCKFILE_PATH_ENV = "SELFHOST_LOCKFILE"
DEFAULT_LOCKFILE_PATH = Path("selfhost.lock")
DIGEST_PREFIX = "sha256:"


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
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name].read_bytes())
        digest.update(b"\0")
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
    tools — and, per extension tool, the workspace-scoped ExtensionContext its handler receives. A
    builtin has no entry, so the engine dispatches it with ext=None. An extension that declares
    tools without a credential key set fails loud, since its context needs the credential store."""
    tools: list[ToolDef] = list(BUILTIN_TOOLS)
    ext_by_tool: dict[str, ExtensionContext] = {}
    for manifest in manifests:
        if not manifest.tools:
            continue
        if credential_store is None:
            raise RuntimeError(
                f"extension {manifest.name!r} declares tools but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(workspace_id, manifest.name, declared, credential_store, memory)
        for tool in manifest.tools:
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
