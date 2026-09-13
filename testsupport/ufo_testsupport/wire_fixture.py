"""The directive wires' one vocabulary home and golden fixture.

`WORKSPACE_WIRE` and `ONBOARD_WIRE` are the closed verb tables `gates.py` holds every producer and
client to. `fixture_rows()` renders one line per verb, and the checked-in copy at `FIXTURE_PATH` is
what each client's own test suite replays, so a wire change is a fixture diff that runs every
client's tests. Regenerate: `uv run python -m ufo_testsupport.wire_fixture`.

Both wires share one escaping — a field's backslash, tab, and newline, in that order, with carriage
returns dropped — so one codec renders both here. The onboarding wire's producer is the Rust gateway
(`servers/control/src/directives.rs`), which is held to these same lines by
`servers/control/tests/contract.rs::every_onboard_fixture_line_is_this_encoder`: the fixture is
generated on this side and asserted on that one, so neither half can move without the other going
red.
"""

import json
from pathlib import Path

from ufo_ext_ufo.surface import directive as surface_directive

WORKSPACE_WIRE = frozenset(
    {
        "txt",
        "note",
        "status",
        "say",
        "you",
        "sent",
        "absorbed",
        "ask",
        "choose",
        "choose_many",
        "authorize",
        "exit",
        "file",
        "runtime",
        "secret",
        "since",
        "poll",
        "listen",
        "run",
        "install",
    }
)
ONBOARD_WIRE = frozenset(
    {"say", "ask", "choose", "exit", "token", "workspace", "debugger", "install", "first"}
)
FIXTURE_PATH = Path(__file__).parents[2] / "client" / "tests" / "fixtures" / "directives.jsonl"
CODEC_TORTURE = "tab\there \\ back\\slash and\nnewline — ufo"

WORKSPACE_FIELDS: dict[str, tuple[str, ...]] = {
    "txt": (CODEC_TORTURE,),
    "note": ("Listing files.", "activity"),
    "status": ("12 tok - $0.000110",),
    "say": (CODEC_TORTURE,),
    "you": ("what I said",),
    "sent": ("turn-1", "1", "arr-1"),
    "absorbed": ("arr-1", "arr-2"),
    "ask": (">",),
    "choose": ("Allow this request?", "Allow", "Deny", "Always Allow"),
    "choose_many": ("Select services", "Mail", "Calendar"),
    "authorize": (
        "92fc2a7b-d3fe-4fb7-8096-e78f658dbda6",
        "Allow this request?",
        "allow",
        "Allow once",
        "This action only.",
        "deny",
        "Deny",
        "Do not allow this action.",
        "always",
        "Always allow for GitHub",
        "Future repository reads from this account.",
    ),
    "exit": ("0",),
    "file": ("quarterly report.pdf", "2048", "https://ws.example/artifacts/a?exp=1&sig=2"),
    "runtime": (
        json.dumps(
            {
                "runtime": {
                    "revision": "abc12345",
                    "image_digest": f"sha256:{'a' * 64}",
                    "config_digest": f"sha256:{'b' * 64}",
                    "sandbox_backend": "e2b",
                    "sandbox_digest": f"sha256:{'c' * 64}",
                },
                "model": "glm-5.3-flash",
                "reasoning": "high",
                "environment": f"sha256:{'d' * 64}",
            },
            separators=(",", ":"),
        ),
    ),
    "secret": ("sealed-blob", "perplexity_api_key", "Paste your Perplexity key"),
    "since": ("turn-1", "cursor-9"),
    "poll": ("1",),
    "listen": ("2",),
    "run": ("op-1", "exec", "exec", "120", "", '{"argv":["ls"]}'),
    "install": (),
}
ONBOARD_FIELDS: dict[str, tuple[str, ...]] = {
    "say": (CODEC_TORTURE,),
    "ask": ("Enter your work email:",),
    "choose": ("Which workspace?", "acme", "beta LLC"),
    "exit": ("0",),
    "token": ("bearer.value",),
    "workspace": ("https://ws.example",),
    "debugger": ("https://ws.example/surface/debug",),
    "install": (),
    "first": ("1",),
}


def fixture_rows() -> list[dict[str, object]]:
    return [
        {
            "wire": "workspace",
            "verb": verb,
            "fields": list(fields),
            "line": surface_directive(verb, *fields).decode().removesuffix("\n"),
        }
        for verb, fields in sorted(WORKSPACE_FIELDS.items())
    ] + [
        {
            "wire": "onboard",
            "verb": verb,
            "fields": list(fields),
            "line": surface_directive(verb, *fields).decode().removesuffix("\n"),
        }
        for verb, fields in sorted(ONBOARD_FIELDS.items())
    ]


def rendered() -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in fixture_rows()) + "\n"


def write_fixture() -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(rendered())


if __name__ == "__main__":
    write_fixture()
    print(f"wrote {FIXTURE_PATH}")
