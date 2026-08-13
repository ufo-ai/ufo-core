"""The server-driven directive wire rendered by the ufo terminal client."""

import os
from collections.abc import Mapping

PROMPT = ">"
CLIENT_VERSION_ENV = "UFO_CLIENT_VERSION"


def directive(verb: str, *fields: str) -> bytes:
    escaped = [
        field.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "").replace("\n", "\\n")
        for field in fields
    ]
    return ("\t".join([verb, *escaped]) + "\n").encode()


def render(*lines: bytes) -> bytes:
    return b"".join(lines)


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def client_install(headers: Mapping[str, str]) -> bytes:
    """`install` prepended to the screen for a client that is not installed (a fresh `curl | sh`,
    x-ufo-installed != "1") or one whose x-ufo-script version differs from the client this deploy
    serves (`UFO_CLIENT_VERSION`, unset in local dev). The client installs or updates itself and
    reports the served version afterwards, so the trigger self-limits."""
    if header_value(headers, "x-ufo-installed") != "1":
        return directive("install")
    served = os.environ.get(CLIENT_VERSION_ENV, "")
    if served and (header_value(headers, "x-ufo-script") or "").strip() != served:
        return directive("install")
    return b""
