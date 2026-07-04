"""CDP wire types and the narrowing the browser engine reads Chrome's JSON with.

The engine speaks the Chrome DevTools Protocol — JSON in, JSON out — so a recursive `Json` alias and
a handful of guard-clause narrowers (`as_map`/`as_str`/`as_int`/`as_list`) are the browser package's
shared primitives: an unexpected wire shape raises `ValidationError` at the boundary rather than
propagating as an untyped value. `BrowserCdp` is the endpoint an endpoint provider yields; the
default engine connects any CDP URL the provider gives it, so the provider is the seam that lets a
sandbox-hosted or a remote (browserbase) Chrome slot in without touching the engine."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]
type JsonDict = JsonObject


class ValidationError(ValueError):
    """A wire value cannot exist in the shape the engine requires — surfaced to the model as a
    recoverable tool error, never a crash."""


def as_map(value: Json | None, path: str) -> JsonDict:
    match value:
        case None:
            return {}
        case dict():
            return value
        case _:
            raise ValidationError(f"{path} must be an object")


def as_str(value: Json | None, path: str) -> str:
    match value:
        case str() if value:
            return value
        case _:
            raise ValidationError(f"{path} must be a non-empty string")


def as_int(value: Json | None, path: str) -> int:
    match value:
        case int():
            return value
        case _:
            raise ValidationError(f"{path} must be an integer")


def as_list(value: Json | None, path: str) -> list[Json]:
    match value:
        case list():
            return value
        case None:
            return []
        case _:
            raise ValidationError(f"{path} must be a list")


@dataclass(frozen=True)
class BrowserCdp:
    """A resolvable CDP endpoint: the URL the engine connects to plus any headers the connection
    carries (a traffic token for a sandbox-mapped port, an auth header for a remote provider)."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


type BrowserCdpProvider = Callable[[], Awaitable[BrowserCdp]]
