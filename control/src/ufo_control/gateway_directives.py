"""The server-driven directive wire rendered by the ufo terminal client."""

from collections.abc import Mapping

PROMPT = ">"


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


def first_run_install(headers: Mapping[str, str]) -> bytes:
    """A fresh `curl | sh` (x-ufo-installed != "1") gets `install` prepended to its first screen:
    the shell downloads itself into ~/.ufo/bin, adds it to PATH, then keeps rendering the same
    session. The shell reports x-ufo-installed=1 once that binary exists, so the trigger
    self-limits."""
    if header_value(headers, "x-ufo-installed") == "1":
        return b""
    return directive("install")
