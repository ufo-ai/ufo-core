"""Discover installed extensions: entry points → validated Manifests.

Each `selfhost.extension` entry point is a zero-arg callable returning a Manifest; the loader
collects them, rejects duplicate names, and hands the tuple to the derivations that read it.
Discovery is the only path in — extensions declare, they never call a registration API."""

from importlib.metadata import entry_points

from selfhost.ext.manifest import Manifest

EXTENSION_ENTRY_POINT_GROUP = "selfhost.extension"


def load_manifests() -> tuple[Manifest, ...]:
    manifests = tuple(entry.load()() for entry in entry_points(group=EXTENSION_ENTRY_POINT_GROUP))
    names = [manifest.name for manifest in manifests]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate extension names: {', '.join(duplicates)}")
    return manifests
