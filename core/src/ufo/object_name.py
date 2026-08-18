"""One object's identity — the kind and name grammars every object obeys, and the ref that pairs
them.

It lives apart from `ufo.objects` because an object is named below the object system too — a
generated name a kind derives from provider data must be conformant by construction, and a
terminal frame names what a turn created — so the modules that mint and carry a ref cannot import
the verbs that read it."""

import re

from pydantic import BaseModel, ConfigDict, field_validator

KIND_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
OBJECT_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
OBJECT_NAME_MAX_LENGTH = 64


class InvalidName(ValueError):
    """The object name violates the one grammar every kind shares."""


def validate_object_name(name: str) -> None:
    """Raise `InvalidName` unless `name` is expressible as an `ObjectRef` name. A write that
    persists a row under a caller-supplied name calls this, so the name is refused where it is
    supplied rather than at the read that would render a link to it."""
    if len(name) > OBJECT_NAME_MAX_LENGTH or not OBJECT_NAME_PATTERN.fullmatch(name):
        raise InvalidName(
            f"object name {name!r} must match {OBJECT_NAME_PATTERN.pattern} "
            f"(at most {OBJECT_NAME_MAX_LENGTH} chars)"
        )


class ObjectRef(BaseModel):
    """One object's canonical identity: a registered kind, that kind's own object name, and the
    stable agent name when an agent-scoped ref crosses the main-agent control boundary. The
    agent stays a separate field, never an alternate encoding of the object name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    agent: str | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if not KIND_NAME_PATTERN.fullmatch(value):
            raise ValueError(f"object ref kind {value!r} must match {KIND_NAME_PATTERN.pattern}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if len(value) > OBJECT_NAME_MAX_LENGTH or not OBJECT_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                f"object ref name {value!r} must match {OBJECT_NAME_PATTERN.pattern} "
                f"(at most {OBJECT_NAME_MAX_LENGTH} chars)"
            )
        return value

    def __str__(self) -> str:
        return f"{self.kind}/{self.name}"
