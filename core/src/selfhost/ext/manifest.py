"""What an extension declares: the Manifest and its value objects.

An extension's entry point returns a Manifest — a frozen bundle of declared points the loader reads
and derives from, never a registration API it calls. Each point is a value object a core subsystem
consumes: tools enter the turn's registry, routes mount under the app, jobs register on the
scheduler, credential slots drive proxy injection."""

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass
from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

from selfhost.ext.context import ExtensionContext
from selfhost.tools.registry import ToolDef


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
class JobSpec:
    """Recurring or one-shot background work. `schedule` is a cron string (6 fields, seconds first)
    for recurring jobs, or None to fire once at boot; `handler` runs with the extension's scoped
    ExtensionContext, never a raw handle."""

    name: str
    schedule: str | None
    handler: Callable[[ExtensionContext], Awaitable[None]]


@dataclass(frozen=True)
class RouteSpec:
    """An HTTP endpoint an extension serves. The app mounts `handler` for `method` at
    `/ext/<name>/<path>`; each request is handed the extension's scoped ExtensionContext and the
    incoming Request, and the handler returns the Response."""

    method: Literal["GET", "POST"]
    path: str
    handler: Callable[[ExtensionContext, Request], Awaitable[Response]]


@dataclass(frozen=True)
class OnboardingStep:
    """A first-run step an extension contributes to workspace onboarding. `handler` runs once, after
    the core steps, with the extension's scoped ExtensionContext — the same handle its jobs receive,
    so a step can seed the extension's store or read a credential slot it declared."""

    name: str
    handler: Callable[[ExtensionContext], Awaitable[None]]


@dataclass(frozen=True)
class Manifest:
    """What one extension declares, returned by its `selfhost.extension` entry point."""

    name: str
    version: str
    _: KW_ONLY
    tools: tuple[ToolDef, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()
    credentials: tuple[CredentialSlot, ...] = ()
    onboarding_steps: tuple[OnboardingStep, ...] = ()
