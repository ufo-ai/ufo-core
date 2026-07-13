"""The chat stream renderer: the live cost meter may never corrupt streamed text."""

import io
import json
import os
from pathlib import Path

import httpx
import pytest

from ufo.cli import _load_dotenv, _stream_turn, _TurnDisplay

DONE_FRAME: dict[str, object] = {
    "status": "done",
    "text": "Hi! How can I help you today?",
    "model": "claude-opus-4-8",
    "tokens": 1834,
    "cost_micro_usd": 9430,
}


def tty_display(buffer: io.StringIO) -> _TurnDisplay:
    return _TurnDisplay(out=buffer, err=buffer, tty=True)


def screen(raw: str) -> list[str]:
    """What a terminal shows after interpreting \\r, \\n, and CSI sequences (\\x1b[K clears to
    end of line; other finals, e.g. styling, render nothing)."""
    lines, row, col = [""], 0, 0
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\x1b":
            final = i + 2
            while not raw[final].isalpha():
                final += 1
            if raw[final] == "K":
                lines[row] = lines[row][:col]
            i = final + 1
            continue
        if char == "\r":
            col = 0
        elif char == "\n":
            row += 1
            col = 0
            if row == len(lines):
                lines.append("")
        else:
            padded = lines[row].ljust(col)
            lines[row] = padded[:col] + char + padded[col + 1 :]
            col += 1
        i += 1
    return lines


def test_cost_tick_after_streamed_text_never_overwrites_it() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Hi! How can I help you today?")
    display.tick(1834, 9430)
    display.terminal(DONE_FRAME)
    assert screen(buffer.getvalue()) == [
        "Hi! How can I help you today?",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


def test_meter_redraws_in_place_while_no_text_streams() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.tick(1000, 5000)
    display.tick(2000, 10000)
    assert screen(buffer.getvalue()) == ["2000 tok · $0.010000"]
    display.text("Answer")
    display.terminal(DONE_FRAME)
    assert screen(buffer.getvalue()) == [
        "Answer",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


def test_multi_round_text_survives_interleaved_ticks() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Round one.")
    display.tick(500, 2500)
    display.text("Round two.")
    display.tick(1834, 9430)
    display.terminal(DONE_FRAME)
    assert screen(buffer.getvalue()) == [
        "Round one.",
        "Round two.",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


def test_meter_is_suppressed_when_streams_diverge() -> None:
    out, err = io.StringIO(), io.StringIO()
    display = _TurnDisplay(out=out, err=err, tty=False)
    for round_tokens in (100, 200, 300):
        display.text(f"Round of {round_tokens}. ")
        display.tick(round_tokens, round_tokens * 5)
    display.terminal(DONE_FRAME)
    assert err.getvalue() == ""
    assert screen(out.getvalue()) == [
        "Round of 100. Round of 200. Round of 300. ",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


def test_activity_note_streams_on_its_own_line_without_corrupting_text_or_meter() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Working. ")
    display.tick(500, 2500)
    display.activity('running bash: {"command":"echo hi"}')
    display.text("Answer")
    display.terminal(DONE_FRAME)
    assert screen(buffer.getvalue()) == [
        "Working. ",
        'running bash: {"command":"echo hi"}',
        "Answer",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


async def test_stream_renders_connect_and_error_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"turn_id": "turn-1"})
        frames = (
            {"connect_url": "https://oauth.example.test/authorize"},
            {"connect_error": "Connection request unavailable"},
            {"frame": DONE_FRAME},
        )
        return httpx.Response(
            200,
            content=b"".join(json.dumps(frame).encode() + b"\n" for frame in frames),
        )

    monkeypatch.setattr(
        "ufo.cli.httpx.AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs["base_url"],
            timeout=kwargs["timeout"],
        ),
    )
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("ufo.cli.sys.stdout", out)
    monkeypatch.setattr("ufo.cli.sys.stderr", err)
    current: dict[str, str] = {}

    await _stream_turn("http://ufo.test", {}, "connect", current)

    assert current == {"turn_id": "turn-1"}
    assert out.getvalue().splitlines() == [
        "Connect account: https://oauth.example.test/authorize",
        "Connection request unavailable",
        "Hi! How can I help you today?",
        "claude-opus-4-8 · 1834 tok · $0.009430",
    ]
    assert err.getvalue() == ""


def test_cancelled_erases_pending_meter() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.tick(1000, 5000)
    display.terminal({"status": "cancelled", "text": "stopped by user"})
    assert screen(buffer.getvalue()) == ["stopped by user", ""]


def test_load_dotenv_fills_unset_vars_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` is the smooth default: it fills what the environment leaves unset — an exported var
    wins, a `#` comment and blank line are skipped, and a leading `export` and surrounding quotes on
    the value are stripped — so a developer's exports and the file both work."""
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "UFO_TEST_SET=from-file\n"
        'UFO_TEST_QUOTED="quoted value"\n'
        "export UFO_TEST_EXPORTED=exported\n"
        "\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UFO_TEST_SET", "from-env")
    monkeypatch.delenv("UFO_TEST_QUOTED", raising=False)
    monkeypatch.delenv("UFO_TEST_EXPORTED", raising=False)
    _load_dotenv()
    assert os.environ["UFO_TEST_SET"] == "from-env"
    assert os.environ["UFO_TEST_QUOTED"] == "quoted value"
    assert os.environ["UFO_TEST_EXPORTED"] == "exported"
