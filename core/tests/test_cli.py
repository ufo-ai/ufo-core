"""The chat stream renderer: the live status meter may never corrupt streamed text, and the held
`ufo` surface directive stream parses to the right screen and reconnect signal."""

import io
import os
import threading
import tomllib
import webbrowser
from collections.abc import Callable
from pathlib import Path

import click
import httpx
import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from ufo.cli import (
    DEFAULT_CONFIG,
    BrowserHandoff,
    _ChatStream,
    _load_dotenv,
    _run_turn,
    _TurnDisplay,
    _unescape,
    init,
    portal,
)
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.credentials import CredentialStore
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import Manifest
from ufo.ext.surface import SurfaceSpec
from ufo.serve import _connect_redirect_uri, _validate_requires

BROWSER_JOIN_SECONDS = 5.0


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


async def test_a_held_stream_names_its_cursor_and_the_reconnect_carries_it() -> None:
    """`since` precedes every end this client comes back from, so it has to survive the verb and
    ride the next request. Failing loud on it stops the turn at its first reconnect — this client
    errors on an unknown verb — and dropping it reprints everything the turn has already shown."""
    turn = "77777777-7777-4777-8777-777777777777"
    pages = [
        f"txt\tworking\nsince\t{turn}\t12\npoll\t0\n".encode(),
        b"txt\t, done\nask\t>\n",
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=pages[min(len(seen) - 1, len(pages) - 1)])

    out = io.StringIO()
    display = _TurnDisplay(out=out, err=out, tty=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fleet"
    ) as client:
        await _ChatStream(client=client, path="/surface/ufo/main", headers={}, display=display).run(
            "hi"
        )

    assert len(seen) == 2
    assert "x-ufo-since" not in seen[0].headers
    assert seen[1].headers["x-ufo-since"] == f"{turn}:12"
    assert screen(out.getvalue()) == ["working, done", ""]


async def test_drain_renders_a_shared_file_with_and_without_a_link() -> None:
    """`ufoctl chat` must know `file` or every shared file becomes a hard client error — this client
    fails loud on an unknown verb where the shell client silently drops it."""
    out = io.StringIO()
    display = _TurnDisplay(out=out, err=out, tty=False)
    pending = await _drain_lines(
        display,
        "say\there is the report\n",
        "file\treport.pdf\t2048\thttps://ufo.test/artifacts/download?token=t\n",
        "file\tnotes.md\t17\t\n",
        "ask\t>\n",
    )
    display.close()
    assert pending.poll_seconds is None
    assert screen(out.getvalue()) == [
        "here is the report",
        "shared report.pdf (2048 bytes) https://ufo.test/artifacts/download?token=t",
        "shared notes.md (17 bytes)",
        "",
    ]


async def test_drain_fails_loud_on_a_malformed_file_directive() -> None:
    """A `file` line short of its three fields is protocol drift, not something to render
    half-rendered — the guard is the arity, matching `secret`."""
    display = _TurnDisplay(out=io.StringIO(), err=io.StringIO(), tty=False)
    with pytest.raises(click.ClickException, match="unexpected directive"):
        await _drain_lines(display, "file\treport.pdf\t2048\n")


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


def test_load_dotenv_carries_a_pem_across_its_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GitHub App key is a PEM, and `.env` is where a local deploy keeps its secrets, so the
    format has to hold one — otherwise the key needs a second home the runtime does not read."""
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\nline two\n-----END RSA PRIVATE KEY-----"
    (tmp_path / ".env").write_text(f'BEFORE=head\nUFO_TEST_PEM="{pem}"\nAFTER=tail\n')
    monkeypatch.chdir(tmp_path)
    for name in ("BEFORE", "UFO_TEST_PEM", "AFTER"):
        monkeypatch.delenv(name, raising=False)

    _load_dotenv()

    assert os.environ["UFO_TEST_PEM"] == pem
    assert os.environ["AFTER"] == "tail"


def test_load_dotenv_fails_loud_on_a_quote_that_never_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text('UFO_TEST_OPEN="-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n')
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="never closes"):
        _load_dotenv()


@pytest.mark.parametrize("address", ["jane doe", "root", "a@b@example.com", "trailing@"])
def test_init_refuses_an_owner_address_that_is_not_one_local_at_domain(address: str) -> None:
    """`--email` is the one unvalidated way an address reached a member row: the owner row is
    seated, admin, and undeletable, so a typo would leave a workspace whose own domain matches no
    teammate. The option answers the same shape rule the write enforces, and says so in one line
    instead of raising out of the workspace insert."""
    result = CliRunner().invoke(init, ["--email", address])
    assert result.exit_code == 2
    assert f"{address!r} is not one local@domain address." in result.output


def _handoff_page(fetch: Callable[[str], None], monkeypatch: pytest.MonkeyPatch) -> str:
    """Drive one handoff: the browser stand-in runs on its own thread, because the page is served
    by the loop `open()` is sitting in."""
    handoff = BrowserHandoff(portal_url="http://127.0.0.1:8710/surface/web", token="bearer.value")
    opened: list[str] = []
    browser = threading.Thread(target=lambda: fetch(opened[0]))

    def browse(url: str) -> bool:
        opened.append(url)
        browser.start()
        return True

    monkeypatch.setattr(webbrowser, "open", browse)
    handoff.open()
    browser.join(BROWSER_JOIN_SECONDS)
    return opened[0]


def test_the_browser_handoff_posts_the_bearer_and_keeps_it_out_of_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-hosted node's door: the browser is sent to a local page whose form posts the bearer
    to the portal — the same POST the hosted sign-in card makes. The bearer is in the body, and
    the URL the browser was handed carries nothing but the one-shot path."""
    served: list[httpx.Response] = []
    missed: list[httpx.Response] = []

    def visit(url: str) -> None:
        missed.append(httpx.get(f"{url}-guessed"))
        served.append(httpx.get(url))

    url = _handoff_page(visit, monkeypatch)

    assert "bearer.value" not in url
    assert missed[0].status_code == 404
    page = served[0].text
    assert '<form id="open" method="post" action="http://127.0.0.1:8710/surface/web">' in page
    assert '<input type="hidden" name="token" value="bearer.value">' in page
    assert ">Open your workspace</button>" in page


def test_the_browser_handoff_closes_the_listener_behind_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bearer is readable by anything that can reach the port, so the port outlives exactly one
    delivery — the page cannot be fetched a second time."""
    url = _handoff_page(lambda visited: httpx.get(visited), monkeypatch)

    with pytest.raises(httpx.ConnectError):
        httpx.get(url)


def test_portal_refuses_before_init_has_minted_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verb spends the bearer `init` wrote; without one there is nothing to hand a browser, and
    the fix is the same line `chat` gives."""

    async def identify(_request: object, _auth: object) -> None:
        return None

    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr(
        "ufo.cli.load_config",
        lambda: Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///unused.db"),
            blob=BlobConfig(backend="filesystem", root=Path("/tmp/unused")),
        ),
    )
    monkeypatch.setattr(
        "ufo.cli.load_manifests",
        lambda _pack: (
            Manifest(
                name="web",
                version="0.1.0",
                surfaces=(SurfaceSpec(name="web", routes=(), identify=identify, home=True),),
            ),
        ),
    )

    result = CliRunner().invoke(portal)

    assert result.exit_code == 1
    assert "no CLI token — run `ufoctl init` first" in result.output


def test_the_config_init_writes_boots_the_pack_it_names() -> None:
    """`ufoctl init` writes DEFAULT_CONFIG and `ufoctl serve` is the next command a member runs, so
    every seam that pack's extensions require must already be answered by what init wrote."""
    config = Config.model_validate(tomllib.loads(DEFAULT_CONFIG))
    manifests = load_manifests(config.pack.name)

    _validate_requires(config, manifests, CredentialStore(fernet=Fernet(Fernet.generate_key())))

    providers = {c.oauth.provider: c.oauth for m in manifests for c in m.connectors}
    assert providers, "the pack registers no connector, so this proves nothing about connect"
    assert _connect_redirect_uri(config, providers).startswith(f"{config.connect.public_base_url}/")
