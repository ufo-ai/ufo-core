"""The directive wires' one vocabulary home and golden fixture.

`WORKSPACE_WIRE` and `ONBOARD_WIRE` are the closed verb tables `gates.py` holds every producer and
client to. `fixture_rows()` renders one line per verb through the real producers — the ufo
surface's codec for the workspace wire, the gateway's for the onboarding wire — and the checked-in
copy at `FIXTURE_PATH` is what each client's own test suite replays, so a wire change is a fixture
diff that runs every client's tests. Regenerate: `uv run python -m ufo_testsupport.wire_fixture`.
"""

import json
from pathlib import Path

from ufo_control.gateway_directives import directive as gateway_directive
from ufo_ext_ufo.surface import directive as surface_directive

WORKSPACE_WIRE = frozenset(
    {
        "txt",
        "note",
        "status",
        "say",
        "you",
        "ask",
        "exit",
        "file",
        "secret",
        "since",
        "poll",
        "run",
        "install",
    }
)
ONBOARD_WIRE = frozenset(
    {"say", "ask", "choose", "exit", "token", "workspace", "debugger", "install"}
)
FIXTURE_PATH = Path(__file__).parents[2] / "client" / "tests" / "fixtures" / "directives.jsonl"
CODEC_TORTURE = "tab\there \\ back\\slash and\nnewline — ufo"

WORKSPACE_FIELDS: dict[str, tuple[str, ...]] = {
    "txt": (CODEC_TORTURE,),
    "note": ("running bash: ls",),
    "status": ("12 tok - $0.000110",),
    "say": (CODEC_TORTURE,),
    "you": ("what I said",),
    "ask": (">",),
    "exit": ("0",),
    "file": ("quarterly report.pdf", "2048", "https://ws.example/artifacts/a?exp=1&sig=2"),
    "secret": ("sealed-blob", "exa_api_key", "Paste your Exa key"),
    "since": ("turn-1", "cursor-9"),
    "poll": ("1",),
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
            "line": gateway_directive(verb, *fields).decode().removesuffix("\n"),
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
