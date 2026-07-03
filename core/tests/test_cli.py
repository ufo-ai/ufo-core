"""The chat stream renderer: the live cost meter may never corrupt streamed text."""

import io

from selfhost.cli import _TurnDisplay

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


def test_meter_is_suppressed_off_tty() -> None:
    buffer = io.StringIO()
    display = _TurnDisplay(out=buffer, err=buffer, tty=False)
    display.text("Hi!")
    display.tick(1834, 9430)
    display.terminal(DONE_FRAME)
    assert "\r" not in buffer.getvalue()
    assert screen(buffer.getvalue()) == [
        "Hi!",
        "claude-opus-4-8 · 1834 tok · $0.009430",
        "",
    ]


def test_cancelled_erases_pending_meter() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.tick(1000, 5000)
    display.terminal({"status": "cancelled", "text": "stopped by user"})
    assert screen(buffer.getvalue()) == ["stopped by user", ""]
