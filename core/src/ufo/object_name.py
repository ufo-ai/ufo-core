"""The one grammar every object name obeys, and the guard that enforces it.

It lives apart from `ufo.objects` because a name is minted below the object system too — a
generated name a kind derives from provider data must be conformant by construction, and the
module that mints it cannot import the verbs that read it."""

import re

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
