"""The extension store: search a catalog, pin installs into the lockfile, reverse them.

The catalog is the deploy's index of available extensions; `selfhost ext` reads it and writes the
lockfile the loader boots against. Installing pins the installed extension's digest; a catalog entry
marked `disabled` is bundle-only — `selfhost bundle` may pin it, `install` refuses it. Extensions
are Python packages: the store indexes them and pins their digest, so an entry the environment has
not installed cannot be pinned."""

import tomllib
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from selfhost.ext.loader import (
    ExtensionPin,
    Lockfile,
    discovered,
    extension_digest,
    read_lockfile,
    write_lockfile,
)


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    disabled: bool = False


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extensions: tuple[CatalogEntry, ...] = ()


@dataclass(frozen=True)
class StoreListing:
    """One search result: a catalog entry and whether the lockfile already pins it."""

    name: str
    version: str
    disabled: bool
    installed: bool


def read_catalog(path: Path) -> Catalog:
    return Catalog.model_validate(tomllib.loads(path.read_text()))


def selfhost_version() -> str:
    return version("selfhost")


def pin_for(name: str) -> ExtensionPin:
    """The pin for an installed extension: its manifest version and its source digest. Fails loud if
    the store indexes a name the environment has not actually installed."""
    found = discovered().get(name)
    if found is None:
        raise RuntimeError(f"extension {name!r} is not installed in this environment")
    manifest, entry = found
    return ExtensionPin(name=name, version=manifest.version, digest=extension_digest(entry))


@dataclass(frozen=True)
class ExtensionStore:
    """Search/install/remove over one catalog and one lockfile."""

    catalog: Catalog
    lockfile: Path

    def search(self, query: str) -> tuple[StoreListing, ...]:
        pinned = {pin.name for pin in self._pins()}
        return tuple(
            StoreListing(
                name=entry.name,
                version=entry.version,
                disabled=entry.disabled,
                installed=entry.name in pinned,
            )
            for entry in self.catalog.extensions
            if query in entry.name
        )

    def install(self, name: str) -> ExtensionPin:
        entry = next((e for e in self.catalog.extensions if e.name == name), None)
        if entry is None:
            raise ValueError(f"{name!r} is not in the store catalog")
        if entry.disabled:
            raise ValueError(
                f"{name!r} is bundle-only (disabled in the store); pin it with `selfhost bundle`"
            )
        pin = pin_for(name)
        self._write((*(p for p in self._pins() if p.name != name), pin))
        return pin

    def remove(self, name: str) -> None:
        pins = self._pins()
        if not any(pin.name == name for pin in pins):
            raise ValueError(f"{name!r} is not installed")
        self._write(tuple(pin for pin in pins if pin.name != name))

    def _pins(self) -> tuple[ExtensionPin, ...]:
        return read_lockfile(self.lockfile).extensions if self.lockfile.exists() else ()

    def _write(self, pins: tuple[ExtensionPin, ...]) -> None:
        anchor = (
            read_lockfile(self.lockfile).selfhost_version
            if self.lockfile.exists()
            else selfhost_version()
        )
        write_lockfile(self.lockfile, Lockfile(selfhost_version=anchor, extensions=pins))
