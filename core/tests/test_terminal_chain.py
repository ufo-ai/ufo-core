"""The terminal-as-sandbox chain end to end, nothing doubled but the model.

The shipped shell client (`control/src/ufo_control/client/ufo`) runs as a real subprocess against a
real uvicorn serving the shared `ufo` surface over a real socket. Its message admits a real turn on
the session DBOS worker; the StandIn model answers with one `bash` tool call; the turn's exec op
rides the held stream back to the client as a `run` directive; the client executes it under real
`osascript` in the directory it was launched from; the reply POST resolves the op and resumes the
tail; the second model round streams the answer to the client's stdout. The proof is a file in the
member's own directory, written by nothing but that chain.

Headless drive: the client is started in its own session (no controlling tty), so `ask` falls to
its stdin arm (`client:303-309`) and EOF after the answer is the clean exit. The terminal-op
timeouts are tightened because the post-answer workspace-changes scan finds the member gone — by
design it waits out the arrival grace and fails logged — and the drain in teardown must see the
workflow settle inside its own 30s bound."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from ufo_ext_index_default import DefaultIndex
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    NO_SUBAGENTS,
    UNREACHED_AMBIENT_REPLY,
    no_user_skills,
)
from ufo_testsupport.tables import reset_workspace_data
from ufo_testsupport.workflows import drain_workflows

from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config, DatabaseConfig
from ufo.connectors import ConnectorRegistry
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.durability import replay_safe_client
from ufo.ext.loader import skill_registry
from ufo.hub import InProcessHub
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
)
from ufo.models.registry import ModelRegistry
from ufo.sandbox import terminal
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, Usage
from ufo.serve import _mount_shared_surfaces

OWNER_EMAIL = "owner@example.com"
TOKEN_SECRET = "terminal-chain-token-secret"
CHANNEL = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
PROOF_FILENAME = "PROOF.txt"
PROOF_CONTENT = b"hello"
ANSWER_TEXT = "the proof file is written"
CLIENT_TIMEOUT_SECONDS = 180
CLIENT_RELATIVE = Path("control/src/ufo_control/client/ufo")
SERVER_START_TIMEOUT_SECONDS = 10.0
SERVER_POLL_SECONDS = 0.02
SERVER_STOP_GRACE_SECONDS = 5
SERVER_STOP_TIMEOUT_SECONDS = 20.0
ARRIVAL_GRACE_SECONDS = 5.0
OP_TIMEOUT_SECONDS = 15
OP_DEADLINE_SLACK_SECONDS = 5.0

requires_osascript = pytest.mark.skipif(
    shutil.which("osascript") is None, reason="osascript is not installed"
)


def _client_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / CLIENT_RELATIVE
        if candidate.is_file():
            return candidate
    raise AssertionError(f"{CLIENT_RELATIVE} not found above {__file__}")


def _served_client(tmp_path: Path) -> Path:
    """The client a member actually runs: the shipped script with the op programs injected at the
    marker, exactly as the gateway serves it. Run raw, the marker is inert and the op arms hold no
    program to run."""
    stamped = (
        _client_script().read_text().replace("# ufo:programs", terminal.client_program_bundle(), 1)
    )
    served = tmp_path / "ufo"
    served.write_text(stamped)
    return served


@dataclass(frozen=True)
class ProofModel:
    """First round (the founding message is plain text): one `bash` call writing the sentinel into
    `/workspace`. Second round (the tool result arrived as blocks): the answer and its usage."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if isinstance(request.messages[-1].content, str):
            yield ToolCallStart(id="b1", name="bash")
            yield ToolCallDelta(
                id="b1",
                partial_json=json.dumps(
                    {
                        "command": f"printf hello > /workspace/{PROOF_FILENAME}",
                        "user_description": "writing the proof file",
                    }
                ),
            )
            yield Usage(input_tokens=5, output_tokens=5)
            return
        yield TextDelta(text=ANSWER_TEXT)
        yield Usage(input_tokens=7, output_tokens=3)


PROOF_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(spec, client=lambda spec, key: ProofModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _ThreadedServer:
    """A real uvicorn server on a background thread so the shell client's curl reaches it over a
    real socket — the `test_cli_e2e` shape."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                timeout_graceful_shutdown=SERVER_STOP_GRACE_SECONDS,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn server did not start")
            time.sleep(SERVER_POLL_SECONDS)

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)

    @property
    def stopped(self) -> bool:
        return not self._thread.is_alive()


async def _bootstrap_workspace() -> UUID:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await reset_workspace_data(connection)
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=OWNER_EMAIL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=DEFAULT_AGENT_NAME,
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _drain(system_url: str) -> None:
    client = replay_safe_client(system_url)
    try:
        await drain_workflows(client)
    finally:
        await asyncio.to_thread(client.destroy)


async def _conversation_row(workspace_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.id, tables.conversation.c.sandbox_handle).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key == f"{OWNER_EMAIL}:{CHANNEL}",
                )
            )
        ).one()


@pytest.fixture
def terminal_server(
    dbos_launched: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[UUID, int]]:
    """A live threaded uvicorn mounting the shared `ufo` surface over the session DBOS worker and a
    ProofModel runtime, on an ephemeral port the shell client dials. The app database is this
    fixture's own copy of the session template on sqlite, the wiped shared database on postgres.
    Teardown drains the turn's workflow before disposing anything: the post-answer changes scan
    outlives the client by the (tightened) arrival grace."""
    session_config = dbos_launched
    config = session_config
    if session_config.database.url.startswith("sqlite"):
        private = tmp_path / "private.db"
        shutil.copy(make_url(session_config.database.url).database, private)
        config = session_config.model_copy(
            update={
                "database": DatabaseConfig(
                    url=f"sqlite+aiosqlite:///{private}",
                    system_url=session_config.database.system_url,
                )
            }
        )
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setattr(terminal, "ARRIVAL_GRACE_SECONDS", ARRIVAL_GRACE_SECONDS)
    monkeypatch.setattr(terminal, "DEFAULT_EXEC_TIMEOUT_SECONDS", OP_TIMEOUT_SECONDS)
    monkeypatch.setattr(terminal, "OP_DEADLINE_SLACK_SECONDS", OP_DEADLINE_SLACK_SECONDS)
    init_db(config.database.url)
    server = None
    dbos_client = None
    try:
        workspace_id = asyncio.run(_bootstrap_workspace())

        hub = InProcessHub()
        blob = FilesystemBlobStore(root=config.blob.root)
        dbos_client = replay_safe_client(config.database.system_url)
        sandboxes = ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        )
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
                run_tokens=RunTokenCodec(TOKEN_SECRET.encode()),
                dbos=dbos_client,
                invoker_for=invoker_factory(dbos_client),
                subagents=SubagentRegistry(()),
                subagent_grants={},
                manifests=(),
                registry=PROOF_REGISTRY,
                skills=skill_registry(()),
                credentials=None,
                index=DefaultIndex(transaction=workspace_tx),
                embed=StubEmbed(),
                artifact_token_secret="",
            )
        )

        port = _free_port()
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
        server = _ThreadedServer(app, port)
        server.start()

        yield workspace_id, port
    finally:
        stopped = True
        if server is not None:
            server.stop()
            stopped = server.stopped
        asyncio.run(_drain(config.database.system_url))
        if dbos_client is not None:
            dbos_client.destroy()
        loop_queue.reset_runtime()
        asyncio.run(dispose_db())
        assert stopped, (
            f"uvicorn server did not stop within {SERVER_STOP_TIMEOUT_SECONDS}s of should_exit"
        )


@requires_osascript
def test_the_shipped_client_lands_a_bash_turn_in_its_own_directory(
    terminal_server: tuple[UUID, int], tmp_path: Path
) -> None:
    """The whole chain in one run of the real client: a signed-in `$UFO_HOME`, a pinned channel, a
    message posted from a project directory; the surface claims the terminal binding, the turn's
    `bash` op executes via `osascript` in that directory, and the answer streams back — proven by
    the sentinel file's bytes, the client's stdout, and the conversation row's `client:` handle."""
    workspace_id, port = terminal_server
    home = tmp_path / "home"
    home.mkdir()
    token = mint_token(TOKEN_SECRET, str(workspace_id), OWNER_EMAIL, timedelta(hours=1))
    (home / "credentials").write_text(f"{token}\n")
    (home / "workspace").write_text(f"http://127.0.0.1:{port}\n")
    project = tmp_path / "project"
    project.mkdir()
    bound = str(project.resolve())
    env = {**os.environ, "UFO_HOME": str(home), "UFO_CHANNEL": CHANNEL}
    env.pop("WORKSPACE_URL", None)

    done = subprocess.run(
        ["sh", str(_served_client(tmp_path)), "write a proof file"],
        cwd=bound,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        start_new_session=True,
        timeout=CLIENT_TIMEOUT_SECONDS,
    )

    assert done.returncode == 0, done.stderr.decode()
    assert (project / PROOF_FILENAME).read_bytes() == PROOF_CONTENT
    stdout = done.stdout.decode()
    assert f"Workspace: {bound}" in stdout
    assert ANSWER_TEXT in stdout
    conversation = asyncio.run(_conversation_row(workspace_id))
    assert conversation.sandbox_handle == f"client:{bound}"
