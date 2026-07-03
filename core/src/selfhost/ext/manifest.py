"""What an extension declares: the Manifest and its value objects.

An extension's entry point returns a Manifest — a frozen bundle of declared points the loader reads
and derives from, never a registration API it calls. The Manifest grows unit by unit: each declared
point lands beside the consumer that gives it meaning, never before it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionTarget:
    """The wire-injection descriptor a credential slot may carry: on the wire to `host`, the proxy
    swaps the `sentinel` value of `header` for the real secret. A set `dimension` also meters it."""

    host: str
    header: str
    sentinel: str
    dimension: str | None = None


@dataclass(frozen=True)
class CredentialSlot:
    """A named secret an extension needs. With an InjectionTarget the proxy swaps it onto the
    wire so the sandbox never holds it; without one it is readable only in-process."""

    name: str
    description: str
    injection: InjectionTarget | None = None


@dataclass(frozen=True)
class Manifest:
    """What one extension declares, returned by its `selfhost.extension` entry point."""

    name: str
    version: str
    credentials: tuple[CredentialSlot, ...] = ()
