"""ufoctl's operator verbs: init's refusals, the portal browser handoff, and the config boot."""

import os
import re
import threading
import tomllib
import webbrowser
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from ufo.cli import (
    CORE_VERSIONS_DIR,
    DEFAULT_CONFIG,
    MIGRATION_HEAD_FILENAME,
    BrowserHandoff,
    _load_dotenv,
    init,
    new_migration,
    portal,
)
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import core_migration_head
from ufo.host.ext.loader import load_manifests
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.surface import SurfaceSpec
from ufo.serve import _connect_redirect_uri, _validate_requires

BROWSER_JOIN_SECONDS = 5.0


def test_load_dotenv_makes_file_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "UFO_TEST_EMPTY=\n"
        "UFO_TEST_SET=from-file\n"
        'UFO_TEST_QUOTED="quoted value"\n'
        "export UFO_TEST_EXPORTED=exported\n"
        "\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UFO_TEST_SET", "from-env")
    monkeypatch.setenv("UFO_TEST_EMPTY", "from-env")
    monkeypatch.delenv("UFO_TEST_QUOTED", raising=False)
    monkeypatch.delenv("UFO_TEST_EXPORTED", raising=False)

    _load_dotenv()

    assert os.environ["UFO_TEST_SET"] == "from-file"
    assert os.environ["UFO_TEST_EMPTY"] == ""
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


def test_init_refuses_an_owner_address_that_is_not_one_local_at_domain() -> None:
    """`--email` is the one unvalidated way an address reached a member row: the owner row is
    seated, admin, and undeletable, so a typo would leave a workspace whose own domain matches no
    teammate. The option answers the same shape rule the write enforces, and says so in one line
    instead of raising out of the workspace insert."""
    for address in ["jane doe", "root", "a@b@example.com", "trailing@"]:
        result = CliRunner().invoke(init, ["--email", address])
        assert result.exit_code == 2
        assert f"{address!r} is not one local@domain address." in result.output


def test_new_migration_stamps_the_id_and_chains_onto_core_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two branches scaffolding on the same day take different ids without asking each other, so
    neither is renumbered to land. The stamp orders nothing: `down_revision` names core's head, and
    that is what the single-head gate reads."""
    monkeypatch.setattr("ufo.cli.CORE_VERSIONS_DIR", tmp_path)

    result = CliRunner().invoke(new_migration, ["probe_ledger_split"])

    assert result.exit_code == 0, result.output
    written = list(tmp_path.glob("*.py"))
    assert len(written) == 1
    stamp, _, slug = written[0].stem.partition("_")
    assert re.fullmatch(r"\d{14}", stamp)
    assert slug == "probe_ledger_split"
    body = written[0].read_text()
    assert f'revision: str = "{stamp}"' in body
    assert f'down_revision: str | None = "{core_migration_head()}"' in body


def test_new_migration_advances_the_head_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head file is the merge tripwire: two branches chaining onto the same parent both rewrite
    its one line, so the second merge conflicts on GitHub instead of forking main's graph."""
    monkeypatch.setattr("ufo.cli.CORE_VERSIONS_DIR", tmp_path)

    result = CliRunner().invoke(new_migration, ["probe_ledger_split"])

    assert result.exit_code == 0, result.output
    [written] = tmp_path.glob("*.py")
    assert (tmp_path / MIGRATION_HEAD_FILENAME).read_text() == f"{written.stem.split('_')[0]}\n"


def test_head_file_names_the_graphs_core_head() -> None:
    """The file is derived from the graph, so only this assertion keeps it honest: a migration
    added or repointed without rewriting it lands here, not in a teammate's merge conflict."""
    recorded = (CORE_VERSIONS_DIR / MIGRATION_HEAD_FILENAME).read_text()
    assert recorded == f"{core_migration_head()}\n", (
        f"{CORE_VERSIONS_DIR / MIGRATION_HEAD_FILENAME} must hold core's head "
        f"{core_migration_head()} — rewrite it in the change that moved the head"
    )


def test_new_migration_refuses_a_slug_that_is_not_snake_case() -> None:
    """The slug becomes a module name the tests import by filename."""
    for slug in ["Ledger Split", "ledger-split", "0114_ledger"]:
        result = CliRunner().invoke(new_migration, [slug])
        assert result.exit_code == 2
        assert f"{slug!r} is not a snake_case name." in result.output


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
    """The verb spends the bearer `init` wrote; without one there is nothing to hand a browser."""

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


def test_turn_steps_echo_renders_each_step_and_its_rebuilt_messages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import UTC, datetime

    from ufo.cli import _echo_turn_steps
    from ufo.harness.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
    from ufo.runtime.ext.surface import TurnStep

    steps = (
        TurnStep(
            number=1,
            kind="model",
            name="model round",
            function_name="TurnEngine._stream_once",
            started_at=datetime(2026, 4, 26, 15, 0, tzinfo=UTC),
            completed_at=datetime(2026, 4, 26, 15, 0, 6, tzinfo=UTC),
            duration_ms=6000,
            messages=(
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text="checking"),
                        ToolUseBlock(id="c1", name="bash", input={"command": "pwd"}),
                    ),
                ),
            ),
        ),
        TurnStep(
            number=2,
            kind="tool",
            name="bash",
            function_name="TurnEngine._dispatch_step",
            started_at=None,
            completed_at=None,
            duration_ms=None,
            messages=(
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(tool_use_id="c1", content="/workspace", is_error=False),
                    ),
                ),
            ),
        ),
    )
    _echo_turn_steps(steps)
    out = capsys.readouterr().out
    assert "1. [model] model round  TurnEngine._stream_once  6000ms" in out
    assert "assistant: checking" in out
    assert "call bash" in out and '"command":"pwd"' in out
    assert "result: /workspace" in out


def test_turn_steps_echo_reports_an_empty_log(capsys: pytest.CaptureFixture[str]) -> None:
    from ufo.cli import _echo_turn_steps

    _echo_turn_steps(())
    assert capsys.readouterr().out.strip() == "no recorded steps"
