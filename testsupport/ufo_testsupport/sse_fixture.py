"""The web SSE projection's golden fixture.

One row per event the chat stream can deliver — every `LiveFrame` kind through the real `_sse`,
every synthesized event through the real `_event` — checked in where the frontend's vitest suite
replays it, so a projection change is a fixture diff that runs the browser-side tests.
Regenerate: `uv run python -m ufo_testsupport.sse_fixture`.
"""

import json
from pathlib import Path
from uuid import UUID

from ufo_ext_web.surface import _event, _sse

from ufo.hub import (
    Absorbed,
    CostTick,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SkillLoad,
    SubagentActivity,
    Terminal,
    ToolCall,
)
from ufo.models.interface import TextDelta
from ufo.schema.records import TerminalFrame

SSE_FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "extensions"
    / "web"
    / "frontend"
    / "tests"
    / "fixtures"
    / "sse.json"
)
ARRIVAL_ID = UUID("88888888-8888-4888-8888-888888888888")
REPLY_ID = UUID("66666666-6666-4666-8666-666666666666")
CONVERSATION_ID = "55555555-5555-4555-8555-555555555555"
ATTEMPT = "44444444444444444444444444444444"


def sse_frames() -> dict[type, LiveFrame]:
    return {
        TextDelta: TextDelta(text="Looking at the workspace…"),
        ToolCall: ToolCall(tool="bash", preview="ls -la", description="Listing the workspace"),
        SkillLoad: SkillLoad(skill="calendar-triage"),
        SubagentActivity: SubagentActivity(
            turn_id=UUID("99999999-9999-4999-8999-999999999999"),
            parent_turn_id=UUID("77777777-7777-4777-8777-777777777777"),
            conversation_id=UUID(CONVERSATION_ID),
            profile="deep_research",
            name="Calendar check",
            tool="fetch_url",
            description="Reading the calendar",
        ),
        CostTick: CostTick(cost_micro_usd=110, tokens=12),
        Absorbed: Absorbed(arrivals=(ARRIVAL_ID,)),
        Resumed: Resumed(attempt=ATTEMPT),
        Reply: Reply(
            id=REPLY_ID,
            message_ref=ARRIVAL_ID,
            text="Filed the launch issue as metalcraftai/ufo#1801.",
        ),
        Terminal: Terminal(
            frame=TerminalFrame(
                status="done",
                text="Looked it over.",
                tokens=12,
                cost_micro_usd=110,
                cache_percent=40,
                model="claude-opus-4-8",
            )
        ),
        Parked: Parked(message="Spend cap reached; the turn resumes when the cap is raised."),
    }


def _synthesized() -> list[bytes]:
    return [
        _event(
            "subagent",
            {
                "profile": "deep_research",
                "name": "Calendar check",
                "conversation_id": CONVERSATION_ID,
                "events": [{"kind": "note", "text": "checking the calendar"}],
                "output": "found it",
                "subagents": [],
            },
        ),
        _event(
            "connect_error",
            {"message": "Connection request unavailable; ask me to connect again."},
        ),
        _event("connect", {"url": "https://connect.example/authorize?state=abc"}),
        _event(
            "credentials",
            {
                "reason": "Perplexity search needs a key",
                "sealed": "sealed-blob",
                "prompts": [{"slot": "perplexity_api_key", "prompt": "Paste your Perplexity key"}],
            },
        ),
        _event(
            "files",
            {
                "files": [
                    {
                        "filename": "quarterly report.pdf",
                        "subject": "report",
                        "media_type": "application/pdf",
                        "size_bytes": 2048,
                        "url": "https://ws.example/artifacts/a?exp=1&sig=2",
                        "preview_url": None,
                    }
                ]
            },
        ),
    ]


def _row(raw: bytes) -> dict[str, str]:
    event = "message"
    data = ""
    for line in raw.decode().splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        if line.startswith("data: "):
            data = line.removeprefix("data: ")
    return {"event": event, "data": data}


def sse_rows() -> list[dict[str, str]]:
    frames = sse_frames()
    live = [
        frames[TextDelta],
        frames[ToolCall],
        frames[SkillLoad],
        frames[SubagentActivity],
        frames[CostTick],
        frames[Reply],
        frames[Absorbed],
        frames[Resumed],
    ]
    rows = [_row(_sse("", frame)) for frame in live]
    rows.extend(_row(raw) for raw in _synthesized())
    rows.append(_row(_sse("", frames[Terminal])))
    rows.append(_row(_sse("", frames[Parked])))
    return rows


def rendered_sse() -> str:
    return json.dumps(sse_rows(), indent=2, ensure_ascii=False) + "\n"


def write_sse_fixture() -> None:
    SSE_FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SSE_FIXTURE_PATH.write_text(rendered_sse())


if __name__ == "__main__":
    write_sse_fixture()
    print(f"wrote {SSE_FIXTURE_PATH}")
