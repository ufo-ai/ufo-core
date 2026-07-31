"""The chat stream renderer: the live status meter may never corrupt streamed text, and the held
`ufo` surface directive stream parses to the right screen and reconnect signal."""

import io
import os
from pathlib import Path

import click
import httpx
import pytest
from click.testing import CliRunner

from ufo.cli import _ChatStream, _load_dotenv, _run_turn, _TurnDisplay, _unescape, init
from ufo.config import BlobConfig, Config, DatabaseConfig


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


def test_status_meter_after_streamed_text_never_overwrites_it() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Hi! How can I help you today?")
    display.meter("1834 tok · $0.009430")
    display.close()
    assert screen(buffer.getvalue()) == ["Hi! How can I help you today?", ""]


def test_meter_redraws_in_place_while_no_text_streams() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.meter("1000 tok")
    display.meter("2000 tok")
    assert screen(buffer.getvalue()) == ["2000 tok"]


def test_multi_round_text_survives_interleaved_meters() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Round one.")
    display.meter("500 tok")
    display.text("Round two.")
    display.meter("1834 tok")
    display.close()
    assert screen(buffer.getvalue()) == ["Round one.", "Round two.", ""]


def test_meter_is_suppressed_when_streams_diverge() -> None:
    out, err = io.StringIO(), io.StringIO()
    display = _TurnDisplay(out=out, err=err, tty=False)
    for tokens in (100, 200, 300):
        display.text(f"Round of {tokens}. ")
        display.meter(f"{tokens} tok")
    display.close()
    assert err.getvalue() == ""
    assert screen(out.getvalue()) == ["Round of 100. Round of 200. Round of 300. ", ""]


def test_activity_note_streams_on_its_own_line_without_corrupting_text_or_meter() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.text("Working. ")
    display.meter("500 tok")
    display.activity('running bash: {"command":"echo hi"}')
    display.text("Answer")
    display.close()
    assert screen(buffer.getvalue()) == [
        "Working. ",
        'running bash: {"command":"echo hi"}',
        "Answer",
        "",
    ]


def test_say_line_prints_after_erasing_a_pending_meter() -> None:
    buffer = io.StringIO()
    display = tty_display(buffer)
    display.meter("1000 tok")
    display.line("cancelled")
    display.close()
    assert screen(buffer.getvalue()) == ["cancelled", ""]


def test_unescape_reverses_directive_escaping() -> None:
    assert _unescape("plain") == "plain"
    assert _unescape("line one\\nline two") == "line one\nline two"
    assert _unescape("a\\tb") == "a\tb"
    assert _unescape("path\\\\to") == "path\\to"


async def _drain_lines(display: _TurnDisplay, *directives: str) -> object:
    """Run `_drain` over a held stream that emits `directives` as tab-directive lines."""
    body = "".join(directives).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fleet"
    ) as client:
        return await _ChatStream(
            client=client, path="/surface/ufo/main", headers={}, display=display
        )._drain("hi")


async def test_drain_renders_a_done_stream_and_signals_no_reconnect() -> None:
    out = io.StringIO()
    display = _TurnDisplay(out=out, err=out, tty=False)
    pending = await _drain_lines(
        display,
        "txt\tHello\n",
        "note\trunning bash: echo hi\n",
        "status\t7 tok · $0.000110\n",
        "say\tComplete the connection: https://oauth.example.test\n",
        "ask\t>\n",
    )
    display.close()
    assert pending.poll_seconds is None
    assert pending.secrets == []
    assert screen(out.getvalue()) == [
        "Hello",
        "running bash: echo hi",
        "Complete the connection: https://oauth.example.test",
        "",
    ]


async def test_drain_reports_a_poll_reconnect_and_collects_secret_prompts() -> None:
    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    polled = await _drain_lines(display, "txt\tworking\n", "poll\t2\n")
    assert polled.poll_seconds == 2.0

    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    with_secret = await _drain_lines(
        display, "secret\tsealed-blob\texa_api_key\tPaste your Exa key\n", "ask\t>\n"
    )
    assert with_secret.poll_seconds is None
    assert with_secret.secrets == [("sealed-blob", "exa_api_key", "Paste your Exa key")]


async def test_drain_fails_loud_on_a_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"unauthorized")

    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fleet"
    ) as client:
        with pytest.raises(click.ClickException, match="401"):
            await _ChatStream(
                client=client, path="/surface/ufo/main", headers={}, display=display
            )._drain("hi")


async def test_drain_fails_loud_on_an_unknown_directive() -> None:
    """A protocol drift (a new verb, or a malformed `secret` line) fails loud rather than vanishing
    silently — the surface and this client must not disagree unnoticed."""
    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    with pytest.raises(click.ClickException, match="unexpected directive"):
        await _drain_lines(display, "txt\thi\n", "mystery\tpayload\n")

    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    with pytest.raises(click.ClickException, match="unexpected directive"):
        await _drain_lines(display, "secret\tsealed\tonly-two-fields\n")


async def test_drain_fails_loud_on_a_non_numeric_poll() -> None:
    """A malformed `poll` value fails loud as a clean ClickException — never a raw ValueError
    traceback the way an unguarded `float()` would leak."""
    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    with pytest.raises(click.ClickException, match="unexpected directive"):
        await _drain_lines(display, "txt\tworking\n", "poll\tabc\n")


async def test_fulfill_secret_fails_loud_on_a_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected credential (non-200) raises rather than silently swallowing — the member gets a
    clear failure, not nothing."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "the-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"say\tnot stored - stale seal\n")

    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fleet"
    ) as client:
        with pytest.raises(click.ClickException, match="could not store exa_api_key"):
            await _ChatStream(
                client=client, path="/surface/ufo/main", headers={}, display=display
            )._fulfill_secret("sealed", "exa_api_key", "Key?")


def test_run_turn_surfaces_a_dropped_connection_as_a_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport error mid-turn is a clean failure, not a raw traceback — the turn keeps running
    on the fleet and the next message resumes tailing it."""

    async def drop(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("ufo.cli._stream_turn", drop)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///unused.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/unused")),
    )
    with pytest.raises(click.ClickException, match="lost connection to serve"):
        _run_turn(config, "token", "channel", "hi")


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


@pytest.mark.parametrize("address", ["jane doe", "root", "a@b@example.com", "trailing@"])
def test_init_refuses_an_owner_address_that_is_not_one_local_at_domain(address: str) -> None:
    """`--email` is the one unvalidated way an address reached a member row: the owner row is
    seated, admin, and undeletable, so a typo would leave a workspace whose own domain matches no
    teammate. The option answers the same shape rule the write enforces, and says so in one line
    instead of raising out of the workspace insert."""
    result = CliRunner().invoke(init, ["--email", address])
    assert result.exit_code == 2
    assert f"{address!r} is not one local@domain address." in result.output
