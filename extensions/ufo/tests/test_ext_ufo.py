import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import Headers
from starlette.requests import Request as StarletteRequest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_ext_ufo.surface import (
    HOLD_SECONDS,
    PROMPT,
    SharedFile,
    _utf8_header,
    directive,
    directives_for,
    resolve_workspace,
    stream_directives,
)
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    NO_SUBAGENTS,
    UNREACHED_AMBIENT_REPLY,
    no_user_skills,
)

from ufo.artifact_url import verify_artifact_url
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import (
    CredentialRequestState,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.hub import CostTick, InProcessHub, Parked, SkillLoad, Terminal, ToolCall
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.sandbox.terminal import TerminalOpFailed, client_program_bundle
from ufo.schema import tables
from ufo.schema.records import CredentialPrompt, CredentialRequest, TerminalFrame, Usage
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.surfaces import ConnectRequest, SurfaceAuth
from ufo.serve import _mount_shared_surfaces

SECRET = "ufo-token-secret"
STREAM_TIMEOUT_SECONDS = 30


def _mint(secret: str, workspace_id: UUID, email: str, exp: int) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"ws": str(workspace_id), "email": email, "exp": exp}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _future() -> int:
    return int(datetime.now(tz=UTC).timestamp()) + 3600


def _lines(body: bytes) -> list[list[str]]:
    return [line.split("\t") for line in body.decode().splitlines()]


def test_directive_escapes_tabs_newlines_and_backslashes() -> None:
    assert directive("say", "hello") == b"say\thello\n"
    assert directive("txt", "a\tb\nc\\d\r") == b"txt\ta\\tb\\nc\\\\d\n"
    assert directive("ask", PROMPT) == b"ask\t>\n"
    assert directive("poll", "1") == b"poll\t1\n"


def test_frame_map_covers_every_live_frame() -> None:
    assert directives_for(TextDelta(text="hi"), streamed=False) == (b"txt\thi\n",)
    assert directives_for(TextDelta(text=""), streamed=False) == ()
    assert directives_for(ToolCall(tool="bash", preview="ls", description=""), False) == (
        b"note\trunning bash: ls\n",
    )
    assert directives_for(ToolCall(tool="bash", preview="", description="listing"), False) == (
        b"note\trunning bash: listing\n",
    )
    assert directives_for(SkillLoad(skill="demo"), False) == (b"note\tloading skill: demo\n",)
    assert directives_for(CostTick(cost_micro_usd=55_000, tokens=3000), False) == (
        b"status\t3000 tok - $0.055000\n",
    )


def test_terminal_frame_maps_by_status_and_streamed() -> None:
    done = Terminal(frame=TerminalFrame(status="done", text="line one\nline two"))
    assert directives_for(done, streamed=False) == (
        b"say\tline one\n",
        b"say\tline two\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True) == (b"ask\t>\n",)
    failed = Terminal(
        frame=TerminalFrame(
            status="failed",
            error_class="UnicodeEncodeError",
            error_message="'utf-8' codec could not encode the response",
        )
    )
    assert directives_for(failed, streamed=True) == (
        b"say\tThe agent could not complete the request. Try again.\n",
        b"ask\t>\n",
    )
    invalid_credential = Terminal(
        frame=TerminalFrame(
            status="failed",
            error_class="CredentialValueInvalid",
            error_message=(
                "model 'anthropic.claude-opus-5' key contains non-ASCII characters: "
                "env AWS_BEARER_TOKEN_BEDROCK or the workspace's 'bedrock_api_key' BYOK slot "
                "holds a value the provider wire cannot carry."
            ),
        )
    )
    assert directives_for(invalid_credential, streamed=True) == (
        b"say\tmodel 'anthropic.claude-opus-5' key contains non-ASCII characters: "
        b"env AWS_BEARER_TOKEN_BEDROCK or the workspace's 'bedrock_api_key' BYOK slot "
        b"holds a value the provider wire cannot carry.\n",
        b"ask\t>\n",
    )
    cancelled = Terminal(frame=TerminalFrame(status="cancelled"))
    assert directives_for(cancelled, streamed=True) == (b"say\tcancelled\n", b"exit\t0\n")
    assert directives_for(Parked(message="over cap"), False) == (b"say\tover cap\n", b"ask\t>\n")


def _request() -> CredentialRequest:
    return CredentialRequest(
        reason="Connecting Slack needs two values.",
        prompts=(
            CredentialPrompt(slot="slack_bot_token", prompt="Bot User OAuth Token"),
            CredentialPrompt(slot="slack_signing_secret", prompt="Signing Secret"),
        ),
        sealed="sealed-opaque",
    )


def test_pending_credential_prompts_render_individually() -> None:
    """A done turn prompts exactly the still-unanswered slots — all, one, or none — so a stored
    sibling never re-prompts while a missing one keeps asking."""
    request = _request()
    done = Terminal(frame=TerminalFrame(status="done", text="t", credential_request=request))
    assert directives_for(done, streamed=True, collect=request.prompts) == (
        b"secret\tsealed-opaque\tslack_bot_token\tBot User OAuth Token\n",
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True, collect=request.prompts[1:]) == (
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True) == (b"ask\t>\n",)


async def test_stream_gates_each_secret_prompt_on_the_pending_check() -> None:
    def frames() -> AsyncIterator[tuple[str, Terminal]]:
        async def gen() -> AsyncIterator[tuple[str, Terminal]]:
            frame = TerminalFrame(status="done", text="t", credential_request=_request())
            yield ("c1", Terminal(frame=frame))

        return gen()

    async def fulfilled(sealed: str, slot: str) -> bool:
        assert sealed == "sealed-opaque"
        return False

    async def token_only(sealed: str, slot: str) -> bool:
        return slot == "slack_bot_token"

    gated = [line async for line in stream_directives(aclosing(frames()), 5.0, fulfilled)]
    assert not any(line.startswith(b"secret\t") for line in gated)
    partial = [line async for line in stream_directives(aclosing(frames()), 5.0, token_only)]
    secrets = [line for line in partial if line.startswith(b"secret\t")]
    assert secrets == [b"secret\tsealed-opaque\tslack_bot_token\tBot User OAuth Token\n"]


def test_a_shared_file_renders_after_the_answer_on_every_terminal_status() -> None:
    """The upload commits during the turn, so a turn that shared a file and then failed or was
    cancelled still owes the member the link — the silent drop this repairs."""
    files = (
        SharedFile(filename="report.pdf", size_bytes=2048, url="https://ufo.test/artifacts/a"),
        SharedFile(filename="chart.png", size_bytes=91, url="https://ufo.test/artifacts/b"),
    )
    done = Terminal(frame=TerminalFrame(status="done", text="here it is"))
    assert directives_for(done, streamed=False, files=files) == (
        b"say\there it is\n",
        b"file\treport.pdf\t2048\thttps://ufo.test/artifacts/a\n",
        b"file\tchart.png\t91\thttps://ufo.test/artifacts/b\n",
        b"ask\t>\n",
    )
    failed = Terminal(frame=TerminalFrame(status="failed"))
    assert directives_for(failed, streamed=True, files=files[:1]) == (
        b"say\tThe agent could not complete the request. Try again.\n",
        b"file\treport.pdf\t2048\thttps://ufo.test/artifacts/a\n",
        b"ask\t>\n",
    )
    cancelled = Terminal(frame=TerminalFrame(status="cancelled"))
    assert directives_for(cancelled, streamed=True, files=files[:1]) == (
        b"say\tcancelled\n",
        b"file\treport.pdf\t2048\thttps://ufo.test/artifacts/a\n",
        b"exit\t0\n",
    )
    assert directives_for(done, streamed=True) == (b"ask\t>\n",)


def test_a_file_with_no_mintable_link_still_names_itself() -> None:
    """No token secret or no public base URL is the local-dev case: name the file rather than drop
    it, the way Slack degrades."""
    unlinked = (SharedFile(filename="notes.md", size_bytes=17, url=""),)
    done = Terminal(frame=TerminalFrame(status="done", text="t"))
    assert directives_for(done, streamed=True, files=unlinked) == (
        b"file\tnotes.md\t17\t\n",
        b"ask\t>\n",
    )


def test_a_shared_file_precedes_the_secret_and_connect_lines() -> None:
    """One order for the closing block, so the shell renders the file next to the answer it belongs
    to rather than after an unrelated prompt."""
    request = _request()
    done = Terminal(frame=TerminalFrame(status="done", text="t", credential_request=request))
    lines = directives_for(
        done,
        streamed=True,
        collect=request.prompts[:1],
        connect_message="Complete the connection: https://oauth.test/a",
        files=(SharedFile(filename="k.csv", size_bytes=4, url="https://ufo.test/artifacts/k"),),
    )
    assert [line.split(b"\t")[0] for line in lines] == [b"file", b"secret", b"say", b"ask"]


async def test_only_a_terminal_frame_reads_what_the_turn_shared() -> None:
    """The rows land during the turn, so reading before it ends would report a partial set — and a
    read per streamed token would be one query per delta."""
    reads = 0

    async def files() -> tuple[SharedFile, ...]:
        nonlocal reads
        reads += 1
        return (SharedFile(filename="one.txt", size_bytes=3, url="https://ufo.test/artifacts/1"),)

    async def frames() -> AsyncIterator[tuple[str, TextDelta | Terminal]]:
        yield ("c1", TextDelta(text="partial"))
        yield ("c2", Terminal(frame=TerminalFrame(status="done", text="partial")))

    lines = [line async for line in stream_directives(aclosing(frames()), 5.0, files=files)]
    assert reads == 1
    assert lines == [
        b"txt\tpartial\n",
        b"file\tone.txt\t3\thttps://ufo.test/artifacts/1\n",
        b"ask\t>\n",
    ]


CLIENT_RELATIVE = Path("control/src/ufo_control/client/ufo")


def _client_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / CLIENT_RELATIVE
        if candidate.is_file():
            return candidate
    raise AssertionError(f"{CLIENT_RELATIVE} not found above {__file__}")


def _shell_slice(start: str, end: str) -> str:
    """Source lifted out of the shipped client verbatim, so a test exercises the script's own code
    rather than a copy of it that could drift."""
    source = _client_script().read_text()
    first = source.index(start)
    return source[first : source.index(end, first)]


def _render_in_shell(line: bytes) -> str:
    arm = _shell_slice("      file)\n", "      exit)")
    harness = f"""
TAB=$(printf '\\t')
FX=0
DIM='' RESET=''
line_break() {{ :; }}
dyn_erase() {{ :; }}
flush_open() {{ :; }}
dyn_paint() {{ :; }}
while IFS="$TAB" read -r verb rest; do
  case "$verb" in
{arm}
  esac
done
"""
    done = subprocess.run(
        ["sh", "-c", harness], input=line, capture_output=True, check=True, timeout=30
    )
    return done.stdout.decode().strip()


def test_the_shell_client_renders_the_file_directive_the_surface_emits() -> None:
    """Producer and consumer in one assertion: the bytes `directive` writes are fed to the shipped
    client's own `file)` arm."""
    linked = directive("file", "report.pdf", "2048", "https://ufo.test/artifacts/download?token=t")
    assert (
        _render_in_shell(linked)
        == "shared report.pdf (2048 bytes) https://ufo.test/artifacts/download?token=t"
    )


def test_the_shell_client_splits_a_linkless_file_without_reading_the_size_as_a_url() -> None:
    """`read` strips a trailing IFS tab, so an unconfigured link arrives as two fields and a naive
    third-field split would print the byte count where the URL belongs."""
    assert _render_in_shell(directive("file", "notes.md", "17", "")) == "shared notes.md (17 bytes)"


OSASCRIPT = shutil.which("osascript")
NODE = shutil.which("node")
requires_osascript = pytest.mark.skipif(OSASCRIPT is None, reason="osascript is not installed")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _exec_params(argv: list[str], env: dict[str, str] | None = None) -> str:
    """The exec op's params — the JSON the relay lands as `op.json` for the bundled `exec.js` to
    read. The program never rides the wire; the client holds it and the directive names it."""
    return json.dumps({"argv": argv, "env": env or {}})


def _relay_shell(
    setup: str, line: bytes, osa: str | None = OSASCRIPT, node: str | None = NODE
) -> subprocess.CompletedProcess[bytes]:
    """Drive both shipped `run` arms verbatim: the client extracts its bundled programs, the
    directive arm parses `line`, then the `run)` dispatch runs the op naming a bundled program under
    the selected runtime, and the harness reports the reply the loop would post. The bundle is the
    production `client_program_bundle()` written into a scratch `UFO_HOME`, so the op runs the
    byte-identical program a served client holds, and `run_js` is lifted from the client so the
    interpreter selection is the shipped one. `setup` seeds the variables (`WORKDIR`, `UFO_CWD`, and
    the surface state and stub `curl` for the copy arms)."""
    directive_arm = _shell_slice("      run)\n", "      token)")
    dispatch_arm = _shell_slice("      run)\n        # Only exec", "      exit)")
    run_js = _shell_slice("run_js() {", "\npost() {")
    home = tempfile.mkdtemp(prefix="ufo-home.")
    harness = f"""set -eu
TAB=$(printf '\\t')
OSA='{osa or ""}'
NODE='{node or ""}'
UFO_HOME='{home}'
{client_program_bundle()}
{run_js}
SEND_OP='' SEND_OP_ERR='' SEND_OP_BODY=''
{setup}
NEXT=end
IFS="$TAB" read -r verb rest
case "$verb" in
{directive_arm}
esac
case "$NEXT" in
{dispatch_arm}
esac
printf 'SEND_OP=%s\\n' "$SEND_OP"
printf 'SEND_OP_ERR=%s\\n' "$SEND_OP_ERR"
printf 'SEND_OP_BODY=%s\\n' "$SEND_OP_BODY"
"""
    return subprocess.run(["sh", "-c", harness], input=line, capture_output=True, timeout=60)


def _op_fields(done: subprocess.CompletedProcess[bytes]) -> dict[str, str]:
    assert done.returncode == 0, done.stderr.decode()
    fields: dict[str, str] = {}
    for row in done.stdout.decode().splitlines():
        key, _, value = row.partition("=")
        fields[key] = value
    return fields


@requires_osascript
def test_an_exec_op_packages_its_stdout_as_hex(tmp_path: Path) -> None:
    """The op runs under the real committed `exec.js`: emit shell-quotes the argv, the command
    runs, and package hands back the captured stdout as hex — the reply the loop posts."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive(
        "run", "a" * 32, "exec", "exec", "10", "", _exec_params(["printf", "%s", "hi"])
    )
    fields = _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    assert fields["SEND_OP"] == "a" * 32
    assert fields["SEND_OP_ERR"] == ""
    reply = json.loads((work / "reply.json").read_text())
    assert bytes.fromhex(reply["stdout_hex"]) == b"hi"
    assert reply["exit_code"] == 0


@requires_osascript
def test_a_package_reply_is_exactly_one_json_object(tmp_path: Path) -> None:
    """osascript echoes a `run` handler's return value to stdout when it is not undefined; the
    shipped trailer returns main()'s value and package `print`s its JSON then returns undefined, so
    the reply is one object with no trailing echo line — a second line would corrupt the JSON."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive(
        "run", "a" * 32, "exec", "exec", "10", "", _exec_params(["printf", "%s", "hi"])
    )
    _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    text = (work / "reply.json").read_text()
    assert text.endswith("\n")
    assert text.count("\n") == 1
    assert set(json.loads(text)) == {"exit_code", "stdout_hex", "stderr_hex"}


@requires_osascript
def test_an_exec_op_recovers_an_escaped_payload(tmp_path: Path) -> None:
    """The params ride one directive field, so a newline and a backslash in the argv are escaped on
    the wire; `printf '%b'` restores them byte-for-byte into `op.json` before the program reads."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive(
        "run", "a" * 32, "exec", "exec", "10", "", _exec_params(["printf", "%s", "a\nb\\c"])
    )
    _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    reply = json.loads((work / "reply.json").read_text())
    assert bytes.fromhex(reply["stdout_hex"]) == b"a\nb\\c"


@requires_osascript
def test_an_exec_op_that_outlives_its_timeout_is_killed(tmp_path: Path) -> None:
    """The watchdog kills the command's process group at `timeout_s`, so the packaged exit code is
    nonzero and the arm returns near the deadline rather than at the command's own end."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive("run", "b" * 32, "exec", "exec", "1", "", _exec_params(["sleep", "30"]))
    started = time.monotonic()
    done = _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line)
    elapsed = time.monotonic() - started
    _op_fields(done)
    reply = json.loads((work / "reply.json").read_text())
    assert reply["exit_code"] != 0
    assert elapsed < 15


@requires_osascript
def test_a_backgrounded_child_does_not_hold_the_exec_arm(tmp_path: Path) -> None:
    """A command that exits on its own leaves its group alone, so a server it backgrounds survives
    and the arm returns at the command's exit — not when the descendant finally ends."""
    work = tmp_path / "work"
    work.mkdir()
    params = _exec_params(["sh", "-c", "sleep 10 & echo up"])
    line = directive("run", "c" * 32, "exec", "exec", "30", "", params)
    started = time.monotonic()
    done = _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line)
    elapsed = time.monotonic() - started
    _op_fields(done)
    reply = json.loads((work / "reply.json").read_text())
    assert bytes.fromhex(reply["stdout_hex"]) == b"up\n"
    assert reply["exit_code"] == 0
    assert elapsed < 5


def test_a_write_op_lands_the_staged_body_atomically(tmp_path: Path) -> None:
    """The op's body is fetched into a staged temp and `mv`d onto the target — an atomic rename into
    a directory the arm creates — and a success answers with an empty reply body."""
    work = tmp_path / "work"
    work.mkdir()
    body = b"payload\x00bytes\nno-trailing-newline"
    fixture = tmp_path / "fixture.bin"
    fixture.write_bytes(body)
    target = tmp_path / "sub" / "out.bin"
    setup = f"""WORKDIR='{work}'
UFO_CWD='{tmp_path}'
TOKEN=tok
WORKSPACE_URL=https://ws.test
UFO_CHANNEL=chan
curl() {{
  _out=''
  while [ $# -gt 0 ]; do
    if [ "$1" = -o ]; then _out=$2; shift 2; else shift; fi
  done
  cp '{fixture}' "$_out"
}}"""
    line = directive("run", "d" * 32, "write", "", "60", str(target), "")
    fields = _op_fields(_relay_shell(setup, line))
    assert fields["SEND_OP"] == "d" * 32
    assert fields["SEND_OP_ERR"] == ""
    assert fields["SEND_OP_BODY"] == ""
    assert target.read_bytes() == body
    assert not (work / "staged").exists()


@requires_osascript
def test_a_read_op_answers_with_the_file_as_the_reply_body(tmp_path: Path) -> None:
    """A regular file at the path is the reply body itself — the arm hands the loop its path so
    `post` streams it with `--data-binary`."""
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "doc.txt"
    source.write_bytes(b"contents")
    line = directive("run", "e" * 32, "read", "", "60", str(source), "")
    fields = _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    assert fields["SEND_OP"] == "e" * 32
    assert fields["SEND_OP_ERR"] == ""
    assert fields["SEND_OP_BODY"] == str(source)


def test_a_read_of_a_missing_path_is_an_enoent_error_reply(tmp_path: Path) -> None:
    """A path holding no regular file answers `ENOENT`, carried as `x-ufo-op-err` so a copy-out's
    failure never rides the empty reply body."""
    work = tmp_path / "work"
    work.mkdir()
    missing = tmp_path / "nope.txt"
    line = directive("run", "e" * 32, "read", "", "60", str(missing), "")
    fields = _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    assert fields["SEND_OP"] == "e" * 32
    assert fields["SEND_OP_ERR"] == f"ENOENT: {missing}"
    assert fields["SEND_OP_BODY"] == ""


def test_an_unknown_op_kind_is_named_in_the_error_reply(tmp_path: Path) -> None:
    """An old client told to do something a newer server invented answers so, rather than hanging
    the turn on a kind it cannot run."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive("run", "f" * 32, "teleport", "", "60", "", "")
    fields = _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    assert fields["SEND_OP"] == "f" * 32
    assert fields["SEND_OP_ERR"] == "unknown op kind: teleport"


def test_a_run_op_without_any_runtime_is_an_enosys_error_reply(tmp_path: Path) -> None:
    """A machine with neither osascript nor node cannot run ops and sends no `x-ufo-cwd`, so the
    server never binds it; a run directive arriving anyway is refused rather than silently dropped,
    and the refusal names the runtime to install."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive("run", "0" * 32, "exec", "exec", "10", "", _exec_params(["printf", "hi"]))
    fields = _op_fields(
        _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line, osa="", node="")
    )
    assert fields["SEND_OP"] == "0" * 32
    assert fields["SEND_OP_ERR"] == (
        "ENOSYS: this machine has no JavaScript runtime; install node from https://nodejs.org"
    )


@requires_node
def test_an_exec_op_runs_under_node_when_osascript_is_absent(tmp_path: Path) -> None:
    """The Linux/unix-terminal arm: with no osascript, the dispatch picks `<op>.node.js` and runs it
    under node, and the exec op still packages its stdout as hex — the same reply the osascript arm
    posts."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive(
        "run", "a" * 32, "exec", "exec", "10", "", _exec_params(["printf", "%s", "hi"])
    )
    fields = _op_fields(
        _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line, osa="", node=NODE)
    )
    assert fields["SEND_OP"] == "a" * 32
    assert fields["SEND_OP_ERR"] == ""
    reply = json.loads((work / "reply.json").read_text())
    assert bytes.fromhex(reply["stdout_hex"]) == b"hi"
    assert reply["exit_code"] == 0


@requires_node
def test_a_fileop_runs_under_node_when_osascript_is_absent(tmp_path: Path) -> None:
    """A fileop with no osascript runs `<op>.node.js` under node: the edit op mutates the file and
    answers one JSON object the loop posts as the reply body."""
    work = tmp_path / "work"
    work.mkdir()
    subject = tmp_path / "subject.txt"
    subject.write_bytes(b"alpha beta\n")
    edit = {
        "path": str(subject),
        "edits": [
            {
                "old_string_b64": base64.b64encode(b"beta", altchars=b"-_").decode(),
                "new_string_b64": base64.b64encode(b"BETA", altchars=b"-_").decode(),
                "replace_all": False,
            }
        ],
    }
    line = directive("run", "b" * 32, "fileop", "edit", "60", "", json.dumps(edit))
    fields = _op_fields(
        _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line, osa="", node=NODE)
    )
    assert fields["SEND_OP"] == "b" * 32
    assert fields["SEND_OP_ERR"] == ""
    reply = json.loads((work / "reply.json").read_text())
    assert reply["replacements"] == 1
    assert subject.read_bytes() == b"alpha BETA\n"


def test_a_run_op_naming_an_unbundled_program_under_node_is_an_enoent_error_reply(
    tmp_path: Path,
) -> None:
    """With no osascript, the missing-program refusal looks for `<op>.node.js`; a program this
    bundle predates is refused the same way the osascript arm refuses a missing `<op>.js`, so the
    guard does not depend on a runtime being present."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive("run", "1" * 32, "fileop", "quantum", "60", "", "{}")
    fields = _op_fields(
        _relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line, osa="", node=NODE or "/x")
    )
    assert fields["SEND_OP"] == "1" * 32
    assert fields["SEND_OP_ERR"] == "ENOENT: no bundled program for quantum"


@requires_osascript
def test_a_run_op_naming_an_unbundled_program_is_an_enoent_error_reply(tmp_path: Path) -> None:
    """The directive names its program and the client runs its own bundled copy; a newer server
    naming a program this client's bundle predates is refused rather than run, so the turn ends on a
    clean error instead of hanging or executing the wrong file."""
    work = tmp_path / "work"
    work.mkdir()
    line = directive("run", "1" * 32, "fileop", "quantum", "60", "", "{}")
    fields = _op_fields(_relay_shell(f"WORKDIR='{work}'\nUFO_CWD='{tmp_path}'", line))
    assert fields["SEND_OP"] == "1" * 32
    assert fields["SEND_OP_ERR"] == "ENOENT: no bundled program for quantum"


def _post_args(
    send_op: str, send_op_err: str, send_op_body: str, *, osa: str, workspace: str, node: str = ""
) -> tuple[list[str], str]:
    """The arguments the shipped `post` hands `curl` for one call, plus the op state it leaves
    behind. `curl` is stubbed to record its argv; every other variable `post` reads is seeded."""
    post_arm = _shell_slice("post() {", "\nline_break()")
    harness = f"""set -eu
curl() {{ for _a in "$@"; do printf 'ARG=%s\\n' "$_a"; done; }}
TOKEN=tok
SID=sid
BIN=/nonexistent
TTY=1
UFO_SCRIPT_VERSION=test
UFO_URL=https://onboard.test
WORKSPACE_URL='{workspace}'
UFO_CHANNEL=chan
OSA='{osa}'
NODE='{node}'
UFO_CWD=/home/me/proj
SEND_OP='{send_op}'
SEND_OP_ERR='{send_op_err}'
SEND_OP_BODY='{send_op_body}'
{post_arm}
post 'the message'
printf 'LEFT=%s|%s|%s\\n' "$SEND_OP" "$SEND_OP_ERR" "$SEND_OP_BODY"
"""
    done = subprocess.run(["sh", "-c", harness], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    rows = done.stdout.splitlines()
    args = [row[len("ARG=") :] for row in rows if row.startswith("ARG=")]
    left = next(row[len("LEFT=") :] for row in rows if row.startswith("LEFT="))
    return args, left


def test_post_sends_an_op_reply_body_under_the_op_header() -> None:
    """A successful op's reply is the staged body, addressed by `x-ufo-op`, and the terminal's cwd
    rides every post so the surface re-binds the slot on the reconnect the reply POST is."""
    args, left = _post_args(
        "d" * 32, "", "/tmp/reply.json", osa="/usr/bin/osascript", workspace=WORKSPACE_BASE
    )
    assert "x-ufo-op: " + "d" * 32 in args
    assert "@/tmp/reply.json" in args
    assert "x-ufo-cwd: /home/me/proj" in args
    assert not any(arg.startswith("x-ufo-op-err:") for arg in args)
    # post never clears the op state — it runs in `turn`'s background subshell, so the reset must
    # happen in the parent after the fork (see the two-turns regression below), not here.
    assert left == "dddddddddddddddddddddddddddddddd||/tmp/reply.json"


def test_post_sends_an_op_failure_as_a_header_with_no_body() -> None:
    """A failed op answers with `x-ufo-op-err` and an empty body — a read's failure never rides the
    body, because a read's body is the file."""
    args, left = _post_args(
        "e" * 32, "ENOENT: /x", "", osa="/usr/bin/osascript", workspace=WORKSPACE_BASE
    )
    assert "x-ufo-op: " + "e" * 32 in args
    assert "x-ufo-op-err: ENOENT: /x" in args
    assert left == "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee|ENOENT: /x|"


def test_post_omits_the_cwd_header_when_no_runtime_is_present() -> None:
    """A machine that cannot run ops never offers to: with neither osascript nor node, `post` sends
    no `x-ufo-cwd`, so the surface leaves the conversation on the deploy's own carrier."""
    args, _ = _post_args("", "", "", osa="", node="", workspace=WORKSPACE_BASE)
    assert not any(arg.startswith("x-ufo-cwd:") for arg in args)
    assert "the message" in args


def test_post_sends_the_cwd_header_when_only_node_is_present() -> None:
    """A Linux client has no osascript but runs ops under node, so it offers its cwd and the surface
    binds the terminal — the header rides on either runtime, not on osascript alone."""
    args, _ = _post_args("", "", "", osa="", node="/usr/bin/node", workspace=WORKSPACE_BASE)
    assert "x-ufo-cwd: /home/me/proj" in args


def test_the_op_reply_state_is_cleared_after_the_fork_so_the_next_message_is_a_turn() -> None:
    """The regression the review found: `post` runs in `turn`'s background subshell, so its own
    reset could never clear the parent — an op reply's `SEND_OP` would ride every later message,
    posting a spent op id with an empty body and dropping what the member typed. The fork lines run
    verbatim from the shipped `turn`; the parent clears after the fork, so a second message posts as
    a turn."""
    fork_and_clear = _shell_slice('  post "$1" > "$FIFO" &', "\n\n  TAB=")
    harness = f"""set -eu
FIFO=/dev/null
spin_start() {{ :; }}
# Stand in for the backgrounded post, recording the op state the fork handed it — the child sees
# the values set before the fork, exactly as the real post does.
post() {{ printf '%s\\n' "$SEND_OP" > "$SEEN"; }}
SEND_OP=opid SEND_OP_ERR='' SEND_OP_BODY=/tmp/reply
{fork_and_clear}
wait
printf 'CHILD_SAW=%s\\n' "$(cat "$SEEN")"
printf 'PARENT_LEFT=%s\\n' "$SEND_OP"
"""
    with tempfile.TemporaryDirectory() as work:
        seen = Path(work) / "seen"
        done = subprocess.run(
            ["sh", "-c", harness, "sh", "the message"],
            capture_output=True,
            text=True,
            env={**os.environ, "SEEN": str(seen)},
            timeout=30,
        )
    assert done.returncode == 0, done.stderr
    rows = dict(row.split("=", 1) for row in done.stdout.splitlines() if "=" in row)
    assert rows["CHILD_SAW"] == "opid"
    assert rows["PARENT_LEFT"] == ""


WORKSPACE_BASE = "https://acme.ufo.test"


def _channel_shell_slices() -> str:
    return "\n".join(
        (
            _shell_slice('WORKSPACE_URL="', "\nUFO_HOME="),
            _shell_slice("die()", "\nterm_size()"),
            _shell_slice("new_conversation()", "\nlaunch_channel()"),
            _shell_slice("launch_channel()", "\nresume_line()"),
        )
    )


def _channel_environ(environment: str | None, workspace: str) -> dict[str, str]:
    environ = dict(os.environ)
    environ.pop("UFO_CHANNEL", None)
    environ["WORKSPACE_URL"] = workspace
    if environment is not None:
        environ["UFO_CHANNEL"] = environment
    return environ


def _launch_in_shell(
    *args: str, environment: str | None = None, workspace: str = WORKSPACE_BASE
) -> subprocess.CompletedProcess[str]:
    """The shipped client's channel arm, run the way a launch runs it, printing the channel the
    launch settled on. The channel is the conversation key the surface reads off the path."""
    arm = _shell_slice("    --resume)", "  WORKDIR=")
    harness = f"""set -eu
{_channel_shell_slices()}
main() {{
  case "${{1:-}}" in
{arm}
  launch_channel
  printf '%s\\n' "$UFO_CHANNEL"
}}
main "$@"
"""
    return subprocess.run(
        ["sh", "-c", harness, "ufo", *args],
        capture_output=True,
        text=True,
        env=_channel_environ(environment, workspace),
        timeout=30,
    )


def test_a_launch_is_a_new_conversation() -> None:
    """The channel is the conversation, so a launch that reused one would hand every member a
    single thread that never ends."""
    first = _launch_in_shell().stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{32}", first) is not None
    assert _launch_in_shell().stdout.strip() != first


def test_resume_continues_the_conversation_it_names() -> None:
    launched = _launch_in_shell("--resume", "9f2cb410")
    assert launched.returncode == 0
    assert launched.stdout.strip() == "9f2cb410"


def test_resume_without_a_conversation_is_refused() -> None:
    """A missing id would otherwise open a conversation named by the next word on the line."""
    launched = _launch_in_shell("--resume")
    assert launched.returncode == 1
    assert launched.stderr.strip() == "ufo: --resume needs a conversation id."


def test_an_environment_channel_holds_the_conversation_a_caller_named() -> None:
    """`UFO_CHANNEL` wins over the fresh conversation, so a scripted caller keeps the thread it
    pins; `--resume` wins over `UFO_CHANNEL`, because the line the member typed is the later
    word."""
    assert _launch_in_shell(environment="ops").stdout.strip() == "ops"
    assert _launch_in_shell("--resume", "9f2cb410", environment="ops").stdout.strip() == "9f2cb410"


def test_onboarding_launches_share_one_channel() -> None:
    """The gateway keys its live claim on (channel, session), so a member who quits mid-onboarding
    and relaunches must land back on that claim rather than at the email prompt."""
    assert _launch_in_shell(workspace="").stdout.strip() == "onboard"
    assert _launch_in_shell(workspace="").stdout.strip() == "onboard"


def _sign_in_shell(environment: str | None = None) -> str:
    """A launch with no workspace, then the `workspace` directive the sign-in cap lands: the
    client's own launch arm sets the channel and its own `workspace)` arm answers the directive."""
    arm = _shell_slice("      workspace)\n", "      install)")
    harness = f"""set -eu
TAB=$(printf '\\t')
UFO_HOME=$(mktemp -d "${{TMPDIR:-/tmp}}/ufo-test.XXXXXX")
WORKSPACE="$UFO_HOME/workspace"
{_channel_shell_slices()}
launch_channel
while IFS="$TAB" read -r verb rest; do
  case "$verb" in
{arm}
  esac
done
printf '%s\\n' "$UFO_CHANNEL"
rm -rf "$UFO_HOME"
"""
    done = subprocess.run(
        ["sh", "-c", harness],
        input=directive("workspace", WORKSPACE_BASE),
        capture_output=True,
        check=True,
        env=_channel_environ(environment, ""),
        timeout=30,
    )
    return done.stdout.decode().strip()


def test_signing_in_opens_a_fresh_conversation() -> None:
    """The onboarding channel never becomes a conversation: the `workspace` directive moves the
    member onto a fresh one, and the answer they type next opens it."""
    assert re.fullmatch(r"[0-9a-f]{32}", _sign_in_shell()) is not None


def test_a_pinned_channel_survives_sign_in() -> None:
    assert _sign_in_shell(environment="ops") == "ops"


def _leave_in_shell(channel: str, workspace: str, call: str) -> subprocess.CompletedProcess[str]:
    """The client's own leaving lines, run under the state a launch leaves them in."""
    say = _shell_slice("say()", "\ndie()")
    erase = _shell_slice("dyn_erase()", "\nflush_open()")
    line = _shell_slice("resume_line()", "\ninterrupt()")
    trap = _shell_slice("interrupt()", "\nmain()")
    harness = f"""set -eu
TTY=1
DYN_H=0
BOLD='' DIM='' RESET=''
UFO_CHANNEL='{channel}'
WORKSPACE_URL='{workspace}'
{say}
{erase}
{line}
{trap}
{call}
"""
    return subprocess.run(["sh", "-c", harness], capture_output=True, text=True, timeout=30)


def test_the_exit_line_names_the_conversation_to_resume() -> None:
    left = _leave_in_shell("9f2cb410", "https://acme.ufo.test", "resume_line")
    assert left.returncode == 0
    assert left.stdout.splitlines() == ["", "Resume this conversation: ufo --resume 9f2cb410"]


def test_no_conversation_is_named_before_a_workspace_answers() -> None:
    """Onboarding runs on the same channel, but the member holds no conversation until the
    `workspace` directive lands, so there is nothing to resume yet."""
    assert _leave_in_shell("9f2cb410", "", "resume_line").stdout == ""


def test_an_interrupt_names_the_conversation_and_still_leaves_on_130() -> None:
    """130 is the shell's own answer to Ctrl-C, and every caller of `ufo` reads it; the line the
    member needs on the way out rides in front of that code, never over it."""
    left = _leave_in_shell("9f2cb410", "https://acme.ufo.test", "interrupt")
    assert left.returncode == 130
    assert left.stdout.splitlines() == ["", "", "Resume this conversation: ufo --resume 9f2cb410"]


async def test_stream_privately_renders_a_connect_handoff() -> None:
    async def frames() -> AsyncIterator[tuple[str, Terminal]]:
        yield (
            "c1",
            Terminal(
                frame=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(provider="github", requester_member_id=uuid4()),
                )
            ),
        )

    async def connect() -> str:
        return "https://oauth.example.test/authorize"

    lines = [line async for line in stream_directives(aclosing(frames()), 5.0, connect=connect)]
    assert lines == [
        b"say\tUse the connection control.\n",
        b"say\tComplete the connection: https://oauth.example.test/authorize\n",
        b"ask\t>\n",
    ]


def test_valid_token_verifies_to_its_lowered_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "Owner@Example.com", _future())
    assert verify_token(token, workspace_id) == "owner@example.com"


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future() - 7200)
    assert verify_token(token, workspace_id) is None


def test_token_for_another_workspace_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    token = _mint(SECRET, uuid4(), "owner@example.com", _future())
    assert verify_token(token, uuid4()) is None


def test_tampered_signature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    payload, _, signature = token.partition(".")
    forged = f"{payload}.{signature[:-1]}{'0' if signature[-1] != '0' else '1'}"
    assert verify_token(forged, workspace_id) is None
    assert verify_token("not-a-token", workspace_id) is None
    assert (
        verify_token(
            _mint("other-secret", workspace_id, "owner@example.com", _future()), workspace_id
        )
        is None
    )


def test_workspace_claim_returns_the_signed_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared fleet resolves scope from the signed claim itself — no pinned workspace to match
    against. A valid token yields its `ws` uuid; forged, expired, or non-uuid yields None."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    assert workspace_claim(token) == workspace_id
    assert workspace_claim(_mint(SECRET, workspace_id, "o@x.com", _future() - 7200)) is None
    assert workspace_claim(_mint("other-secret", workspace_id, "o@x.com", _future())) is None
    assert workspace_claim("not-a-token") is None


def _get_request(headers: dict[str, str]) -> StarletteRequest:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return StarletteRequest({"type": "http", "method": "POST", "headers": raw})


async def test_resolve_workspace_reads_the_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """SurfaceSpec.identify: the workspace a request's bearer claims, or None to reject — the shared
    fleet binds it before the handler runs. A missing or non-bearer authorization yields None."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    auth = SurfaceAuth(_credentials=None, _declared=frozenset(), _surface="ufo")
    assert (
        await resolve_workspace(_get_request({"authorization": f"Bearer {token}"}), auth)
        == workspace_id
    )
    assert await resolve_workspace(_get_request({}), auth) is None
    assert await resolve_workspace(_get_request({"authorization": token}), auth) is None


@dataclass(frozen=True)
class _Never:
    """A tail that yields one non-terminal frame then blocks forever — the turn that outruns the
    hold, so the stream must end on `poll`."""

    async def __anext__(self) -> tuple[str, TextDelta]:
        if not getattr(self, "_sent", False):
            object.__setattr__(self, "_sent", True)
            return "1", TextDelta(text="working")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def __aiter__(self) -> "_Never":
        return self

    async def aclose(self) -> None: ...


async def _feed(frames: list[tuple[str, object]]) -> AsyncIterator[tuple[str, object]]:
    for item in frames:
        yield item


async def test_hold_expires_and_the_stream_ends_with_poll() -> None:
    assert HOLD_SECONDS == 85.0
    out = b"".join(
        [chunk async for chunk in stream_directives(aclosing(_Never()), hold_seconds=0.05)]
    )
    lines = _lines(out)
    assert lines[0] == ["txt", "working"]
    assert lines[-1] == ["poll", "1"]


async def test_terminal_frame_closes_the_stream_without_polling() -> None:
    frames = _feed(
        [("1", TextDelta(text="echo:1")), ("2", Terminal(frame=TerminalFrame(status="done")))]
    )
    out = b"".join(
        [chunk async for chunk in stream_directives(aclosing(frames), hold_seconds=HOLD_SECONDS)]
    )
    lines = _lines(out)
    assert lines == [["txt", "echo:1"], ["ask", ">"]]


@dataclass(frozen=True)
class StandInModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


STANDIN_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(spec, client=lambda spec, key: StandInModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


@dataclass(frozen=True)
class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


@dataclass(frozen=True)
class StubConnectProvider:
    provider: str = "github"
    host: str = "api.github.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://oauth.example.test/authorize?state={state}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id="github-account")


async def _seed_workspace() -> UUID:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _seed_member(workspace_id: UUID, email: str) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


@pytest.fixture(scope="session")
def runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox]]:
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=config.blob.root.parent / "workspaces",
    )
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=sandboxes,
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"ufo-test-run-token-secret"),
            dbos=dbos_client,
            invoker_for=invoker_factory(dbos_client),
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret=SECRET,
        )
    )
    yield config, hub, blob, sandboxes
    dbos_client.destroy()
    loop_queue.reset_runtime()


@pytest.fixture
async def ufo(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
        subagents=NO_SUBAGENTS,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:
        yield client, workspace_id
    dbos_client.destroy()


@pytest.fixture
async def shared_ufo(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """The ufo surface on the shared fleet: mounted with no boot-pinned workspace, so every request
    scopes itself from its bearer through `_mount_shared_surfaces`. One app, every workspace."""
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
        subagents=NO_SUBAGENTS,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        yield client
    dbos_client.destroy()


async def _sole_turn(workspace_id: UUID) -> tuple[UUID, str]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.status).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).one()
    return row.id, row.status


async def test_shared_fleet_scopes_each_turn_to_its_token_workspace(
    shared_ufo: AsyncClient,
) -> None:
    """No boot-pinned workspace: `_mount_shared_surfaces` resolves each request's workspace from its
    signed `{ws,email}` bearer, binds it, and admits the turn under exactly that workspace — two
    workspaces through one mounted app, each scoped by its token, and the live turn streams to
    done."""
    ws_a, ws_b = await _seed_workspace(), await _seed_workspace()
    await _seed_member(ws_a, "a@example.com")
    await _seed_member(ws_b, "b@example.com")
    token_a = _mint(SECRET, ws_a, "a@example.com", _future())
    token_b = _mint(SECRET, ws_b, "b@example.com", _future())
    lines_a = await _post(shared_ufo, "main", token_a, b"hi a")
    lines_b = await _post(shared_ufo, "main", token_b, b"hi b")
    for lines in (lines_a, lines_b):
        answer = "".join(f for verb, *rest in lines if verb in ("txt", "say") for f in rest)
        assert "echo:1" in answer
        assert lines[-1] == ["ask", ">"]
    turn_a, status_a = await _sole_turn(ws_a)
    turn_b, status_b = await _sole_turn(ws_b)
    assert (status_a, status_b) == ("done", "done")
    assert turn_a != turn_b


async def test_shared_fleet_rejects_a_forged_or_missing_bearer(shared_ufo: AsyncClient) -> None:
    """A token this fleet's secret did not sign, and no token at all, are both 401 before any turn
    is admitted — the signed workspace claim is the only authority the shared fleet trusts."""
    ws = await _seed_workspace()
    forged = _mint("wrong-secret", ws, "a@example.com", _future())
    denied = await shared_ufo.post(
        "/surface/ufo/main", content=b"hi", headers={"authorization": f"Bearer {forged}"}
    )
    missing = await shared_ufo.post("/surface/ufo/main", content=b"hi")
    assert denied.status_code == 401
    assert missing.status_code == 401
    assert await _turn_count(ws) == 0


async def _post(client: AsyncClient, channel: str, token: str, body: bytes) -> list[list[str]]:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(
            f"/surface/ufo/{channel}",
            content=body,
            headers={"authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return _lines(response.content)


async def _turn_count(workspace_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()


async def test_message_admits_a_turn_streams_it_and_links_the_member(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    lines = await _post(client, "main", token, b"hello")
    answer = "".join(field for verb, *rest in lines if verb in ("txt", "say") for field in rest)
    assert "echo:1" in answer
    assert lines[-1][0] in ("ask", "exit")
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "ufo",
                    tables.surface_identity.c.external_id == "owner@example.com",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id, tables.conversation.c.queue_key).where(
                    tables.conversation.c.surface == "ufo"
                )
            )
        ).one()
    assert linked.member_id == member_id
    assert conversation.member_id == member_id
    assert conversation.queue_key == "owner@example.com:main"


async def test_admitted_turn_carries_the_member_and_the_terminal_as_its_source(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    async with workspace_tx() as connection:
        context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert context == {
        "sender": "owner@example.com",
        "timezone": None,
        "source": "ufo cli (owner@example.com)",
    }


ARTIFACT_SECRET = "artifact-token-secret"
ARTIFACT_BASE_URL = "https://ufo.example.test"


@pytest.fixture
async def ufo_delivering_artifacts(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    """The `ufo` fixture with artifact delivery configured — a token secret and a public base, the
    two things `artifact_link` needs before it will mint an absolute URL."""
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        ARTIFACT_SECRET,
        ARTIFACT_BASE_URL,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
        subagents=NO_SUBAGENTS,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:
        yield client, workspace_id
    dbos_client.destroy()


async def _seed_shared_artifact(workspace_id: UUID, turn_id: UUID, filename: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                workspace_id=workspace_id,
                blob_key=f"artifacts/{turn_id}/{filename}",
                filename=filename,
                subject=None,
                media_type="application/pdf",
                size_bytes=2048,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_a_shared_file_reaches_the_terminal_as_an_openable_link(
    ufo_delivering_artifacts: tuple[AsyncClient, UUID],
) -> None:
    """The whole chain #884 reports broken: a row in `shared_artifact` becomes a `file` directive on
    the wire carrying an absolute URL the member can open. Read on the reconnect that re-tails the
    finished turn, which is how the shell drains a turn that outran its hold."""
    client, workspace_id = ufo_delivering_artifacts
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"share the report")
    turn_id, _status = await _sole_turn(workspace_id)
    await _seed_shared_artifact(workspace_id, turn_id, "report.pdf")

    lines = await _post(client, "main", token, b"")

    shared = [fields for verb, *fields in lines if verb == "file"]
    assert len(shared) == 1
    filename, size_bytes, url = shared[0]
    assert (filename, size_bytes) == ("report.pdf", "2048")
    assert url.startswith(f"{ARTIFACT_BASE_URL}/artifacts/{turn_id}/report.pdf?")
    query = parse_qs(urlsplit(url).query)
    claims = verify_artifact_url(
        ARTIFACT_SECRET,
        str(turn_id),
        "report.pdf",
        query["exp"][0],
        query["sig"][0],
        "",
        datetime.now(UTC),
    )
    assert claims.blob_key == f"artifacts/{turn_id}/report.pdf"
    assert lines[-1] == ["ask", ">"]


async def test_a_deploy_that_mints_no_link_still_names_the_shared_file(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """The `ufo` fixture configures no artifact secret and no public base, which is the local-dev
    deploy: the member learns the file exists instead of nothing at all."""
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"share the notes")
    turn_id, _status = await _sole_turn(workspace_id)
    await _seed_shared_artifact(workspace_id, turn_id, "notes.md")

    lines = await _post(client, "main", token, b"")

    assert [fields for verb, *fields in lines if verb == "file"] == [["notes.md", "2048", ""]]


async def test_empty_body_polls_without_admitting_a_turn(ufo: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    assert await _turn_count(workspace_id) == 1
    polled = await _post(client, "main", token, b"")
    assert await _turn_count(workspace_id) == 1
    answer = "".join(field for verb, *rest in polled if verb in ("txt", "say") for field in rest)
    assert "echo:1" in answer
    assert polled[-1][0] in ("ask", "exit")


async def test_empty_body_privately_opens_the_latest_connect_handoff(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    assert await _post(client, "main", token, b"") == [["ask", ">"]]
    flow = ConnectFlow(
        providers={"github": StubConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    turn_id = uuid4()
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.surface == "ufo",
                )
            )
        ).scalar_one()
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect github",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(
                        provider="github", requester_member_id=member_id
                    ),
                ).model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    try:
        lines = await _post(client, "main", token, b"")
    finally:
        install_connect_flow(None)
    assert lines[0] == ["say", "Use the connection control."]
    assert lines[1][0] == "say"
    assert lines[1][1].startswith("Complete the connection: https://oauth.example.test/authorize")
    assert lines[2] == ["ask", ">"]
    async with workspace_tx() as connection:
        memoized_url = (
            await connection.execute(
                sa.select(tables.turn.c.connect_authorization_url).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).scalar_one()
    assert memoized_url in lines[1][1]


async def test_unknown_bearer_is_rejected(ufo: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = ufo
    forged = _mint("wrong-secret", workspace_id, "owner@example.com", _future())
    denied = await client.post(
        "/surface/ufo/main", content=b"hi", headers={"authorization": f"Bearer {forged}"}
    )
    assert denied.status_code == 401
    missing = await client.post("/surface/ufo/main", content=b"hi")
    assert missing.status_code == 401


async def test_email_matching_no_member_gets_an_unlinked_conversation(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    token = _mint(SECRET, workspace_id, "stranger@example.com", _future())
    polled = await _post(client, "main", token, b"")
    assert polled[-1] == ["ask", ">"]
    assert await _turn_count(workspace_id) == 0
    async with workspace_tx() as connection:
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.surface == "ufo"
                )
            )
        ).one()
        identity = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "ufo"
                )
            )
        ).one_or_none()
    assert conversation.member_id is None
    assert identity is None


async def test_secret_fulfillment_lands_in_the_store_never_the_transcript(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other end of the `secret` directive: the shell POSTs each privately-entered value with
    the sealed request, the surface verifies the seal and writes the encrypted slot, and no turn is
    admitted — the secret never becomes a message. Only the member the request was sealed for may
    fulfill it, only for slots it named, under the size bound."""
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    owner = await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "late@example.com")
    sealed = seal_credential_request(
        store.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=owner,
            slots=("slack_bot_token", "slack_signing_secret"),
        ),
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        store,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
        subagents=NO_SUBAGENTS,
    )
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    foreign = _mint(SECRET, workspace_id, "late@example.com", _future())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:

            async def send(bearer: str, slot: str, value: bytes):
                return await client.post(
                    "/surface/ufo/main",
                    content=value,
                    headers={
                        "authorization": f"Bearer {bearer}",
                        "x-ufo-secret": sealed,
                        "x-ufo-slot": slot,
                    },
                )

            last_first = await send(token, "slack_signing_secret", b"shhh")
            assert last_first.status_code == 200
            assert last_first.content == b"say\tstored slack_signing_secret\n"
            first = await send(token, "slack_bot_token", b"xoxb-real")
            assert first.status_code == 200
            assert first.content == b"say\tstored slack_bot_token\n"
            assert await store.get(workspace_id, "slack_bot_token") == "xoxb-real"
            assert await store.get(workspace_id, "slack_signing_secret") == "shhh"
            assert await _turn_count(workspace_id) == 0
            denied = await send(foreign, "slack_bot_token", b"xoxb-evil")
            assert denied.status_code == 403
            assert await store.get(workspace_id, "slack_bot_token") == "xoxb-real"
            offslot = await send(token, "unrelated_slot", b"v")
            assert offslot.status_code == 403
            oversized = await send(token, "slack_bot_token", b"x" * 5000)
            assert oversized.status_code == 413
            garbage = await send(token, "slack_bot_token", b"")
            assert garbage.status_code == 400
    finally:
        dbos_client.destroy()


def test_a_terminal_refusal_reaches_the_member_in_its_own_words() -> None:
    """`TerminalGone` names the directory the conversation is bound to and where the terminal
    stands — the member's own machine, the member's own fact — so the failure passes through where
    an internal error stays behind the generic line."""
    gone = Terminal(
        frame=TerminalFrame(
            status="failed",
            text="",
            error_class="TerminalGone",
            error_message="this conversation's workspace is /Users/m/proj; the connected "
            "terminal is at /Users/m/other",
        )
    )
    lines = _lines(b"".join(directives_for(gone, streamed=False)))
    assert lines[0][0] == "say" and "/Users/m/proj" in lines[0][1]


async def _post_terminal(
    client: AsyncClient,
    channel: str,
    token: str,
    body: bytes,
    cwd: str,
    op: str | None = None,
    err: str | None = None,
) -> list[list[str]]:
    headers = {"authorization": f"Bearer {token}", "x-ufo-cwd": cwd}
    if op is not None:
        headers["x-ufo-op"] = op
    if err is not None:
        headers["x-ufo-op-err"] = err
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(f"/surface/ufo/{channel}", content=body, headers=headers)
    assert response.status_code == 200
    return _lines(response.content)


async def _linked_conversation(client: AsyncClient, workspace_id: UUID, token: str) -> UUID:
    """The conversation the `ufo` surface keys `main` to, created and its member linked by one
    empty-body post — the state a first contact leaves, without admitting a turn. Its op routes are
    then exercised against a rendezvous the test binds directly."""
    assert await _post(client, "main", token, b"") == [["ask", ">"]]
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.surface == "ufo",
                )
            )
        ).one()
    return row.id


async def test_a_write_op_serves_its_staged_bytes_only_to_the_binding_member(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    tmp_path: Path,
) -> None:
    """The op read projection: the bytes a write op sends down are served by `GET …/op/<id>` to the
    member the binding names, refused to anyone else, and the reply posted under `x-ufo-op` resolves
    the sender — the surface half of the rendezvous, exercised through the real routes."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    terminals = runtime[3].terminals
    cwd = str(tmp_path / "proj")
    conversation_id = await _linked_conversation(client, workspace_id, token)

    terminals.connect(conversation_id, cwd, member_id)
    try:
        sending = asyncio.ensure_future(
            terminals.send(conversation_id, "write", 30, arg=f"{cwd}/inbox.txt", body=b"payload")
        )
        while (op := terminals.in_flight(conversation_id)) is None:  # noqa: ASYNC110
            await asyncio.sleep(0)

        served = await client.get(
            f"/surface/ufo/main/op/{op.op_id}",
            headers={"authorization": f"Bearer {token}"},
        )
        assert served.status_code == 200 and served.content == b"payload"

        await _seed_member(workspace_id, "other@example.com")
        foreign = _mint(SECRET, workspace_id, "other@example.com", _future())
        refused = await client.get(
            f"/surface/ufo/main/op/{op.op_id}",
            headers={"authorization": f"Bearer {foreign}"},
        )
        assert refused.status_code == 404

        await _post_terminal(client, "main", token, b"", cwd, op=op.op_id)
        assert await sending == b""
    finally:
        terminals.disconnect(conversation_id)


def test_a_non_ascii_cwd_is_recovered_as_utf8_not_mojibake() -> None:
    """The client sends `pwd -P` as raw UTF-8 header bytes; the ASGI server decodes each byte
    latin-1, so a non-ASCII directory would bind mojibaked and every file op would miss. The surface
    recovers the UTF-8 the client meant. Built from the exact raw bytes the wire carries, through a
    real Starlette `Headers`, so the latin-1 decode under test is the production one."""
    real_cwd = "/Users/josé/proj"
    request = cast(
        StarletteRequest,
        SimpleNamespace(headers=Headers(raw=[(b"x-ufo-cwd", real_cwd.encode("utf-8"))])),
    )
    assert _utf8_header(request, "x-ufo-cwd") == real_cwd
    # A plain ASCII path is invariant through the round trip.
    ascii_request = cast(
        StarletteRequest,
        SimpleNamespace(headers=Headers(raw=[(b"x-ufo-cwd", b"/Users/alex/proj")])),
    )
    assert _utf8_header(ascii_request, "x-ufo-cwd") == "/Users/alex/proj"


async def test_an_op_error_reply_fails_the_op_with_the_terminals_words(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    tmp_path: Path,
) -> None:
    """A reply posted with `x-ufo-op-err` fails the waiting op with the terminal's own words,
    routed through the real POST handler and the member gate."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    terminals = runtime[3].terminals
    cwd = str(tmp_path / "proj")
    conversation_id = await _linked_conversation(client, workspace_id, token)
    terminals.connect(conversation_id, cwd, member_id)
    try:
        sending = asyncio.ensure_future(
            terminals.send(conversation_id, "read", 30, arg=f"{cwd}/absent.txt")
        )
        while (op := terminals.in_flight(conversation_id)) is None:  # noqa: ASYNC110
            await asyncio.sleep(0)
        await _post_terminal(
            client, "main", token, b"", cwd, op=op.op_id, err=f"ENOENT: {cwd}/absent.txt"
        )
        with pytest.raises(TerminalOpFailed, match="ENOENT"):
            await sending
    finally:
        terminals.disconnect(conversation_id)
