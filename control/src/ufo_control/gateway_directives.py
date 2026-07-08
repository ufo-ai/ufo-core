"""The server-driven directive wire the `ufo` client renders.

`client/ufo` is a pure renderer: every onboarding screen is decided here and streamed back as
tab-separated directive lines the shell reads. `directive` renders one line with the client's exact
escaping; `first_run_install` prepends the self-install directive until the shell reports it holds
the binary. Copy-adapted from metalcraft's `gateway/channels/ufo.py`."""

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
