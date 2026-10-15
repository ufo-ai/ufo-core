"""The terminal-as-sandbox chain end to end, nothing doubled but the model and the client binary.

A wire driver speaking the client's exact protocol — the same headers, directives, op replies, and
`since` cursor the Rust client sends — runs against a real uvicorn serving the shared `ufo` surface
over a real socket. Its message admits a real turn on the session DBOS worker; the StandIn model
answers with one `bash` tool call; the turn's exec op rides the held stream back as a `run`
directive; the driver executes it as a subprocess in the directory it posted from and answers with
the base64 reply the native client sends; the reply POST resolves the op and resumes the tail; the
second model round streams the answer. The proof is a file in the member's own directory, written
by nothing but that chain. The client binary's own half of the protocol is pinned by the crate's
tests in `client/`.

The terminal-op timeouts are tightened because the post-answer workspace-changes scan finds the
member gone — by design it waits out the arrival grace and fails logged — and the drain in
teardown must see the workflow settle inside its own 30s bound.

This runs the in-process transport. The cross-pod transport's equivalent — a turn admitted on the
pod that does NOT hold the connection still landing the op on the member's machine — is proven at
the transport-and-carrier layer in `extensions/redis_hub/tests/integration/test_redis_terminal.py`
(two `RedisTerminals` sharing one Redis, `TerminalCarrier` on the turn pod writing the connection
pod's directory), because standing up two real serve instances with a load balancer and one shared
DBOS worker in a single test buys flakiness, not coverage the two-instance transport suite lacks."""

import asyncio
import base64
import http.client
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
from ufo_ext_context_rollover.manifest import manifest as rollover_manifest
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_testsupport.index import default_index
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)
from ufo_testsupport.tables import reset_workspace_data
from ufo_testsupport.workflows import drain_workflows

from ufo.blob import FilesystemBlobStore
from ufo.config import Config, DatabaseConfig
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox import terminal
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.hub import InProcessHub
from ufo.runtime.subagents import SubagentRegistry
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, Usage
from ufo.serve import _mount_shared_surfaces

OWNER_EMAIL = "owner@example.com"
TOKEN_SECRET = "terminal-chain-token-secret"
CHANNEL = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
PROOF_FILENAME = "PROOF.txt"
PROOF_CONTENT = b"hello"
pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
ANSWER_TEXT = "the proof file is written"
CLIENT_TIMEOUT_SECONDS = 180
SERVER_START_TIMEOUT_SECONDS = 10.0
SERVER_POLL_SECONDS = 0.02
SERVER_STOP_GRACE_SECONDS = 5
SERVER_STOP_TIMEOUT_SECONDS = 20.0
ARRIVAL_GRACE_SECONDS = 5.0
OP_TIMEOUT_SECONDS = 15
OP_DEADLINE_SLACK_SECONDS = 5.0


def _unescape(field: str) -> str:
    out: list[str] = []
    characters = iter(field)
    for character in characters:
        if character != "\\":
            out.append(character)
            continue
        match next(characters, None):
            case "t":
                out.append("\t")
            case "n":
                out.append("\n")
            case "\\" | None:
                out.append("\\")
            case other:
                out.append(f"\\{other}")
    return "".join(out)


@dataclass(frozen=True)
class _WireClient:
    port: int
    token: str
    cwd: str

    def run(self, message: str) -> str:
        deadline = time.monotonic() + CLIENT_TIMEOUT_SECONDS
        transcript: list[str] = []
        since = ""
        body: bytes | None = message.encode()
        op_reply: tuple[str, bytes] | None = None
        while time.monotonic() < deadline:
            lines = self._post(body, op_reply, since)
            body, op_reply = b"", None
            poll_seconds = None
            done = False
            for raw in lines:
                verb, *fields = (_unescape(part) for part in raw.split("\t"))
                match verb:
                    case "say" | "note":
                        transcript.append(fields[0] if fields else "")
                    case "frame" if fields[:1] == ["message"]:
                        transcript.append(json.loads(fields[1])["text"])
                    case "since" if len(fields) >= 2:
                        since = f"{fields[0]}:{fields[1]}"
                    case "run":
                        op_reply = (fields[0], self._exec(fields[5]))
                    case "poll":
                        poll_seconds = float(fields[0]) if fields else 1.0
                    case "ask" | "exit":
                        done = True
                    case _:
                        pass
            if done:
                return "".join(f"{line}\n" for line in transcript)
            if op_reply is None and poll_seconds is not None:
                time.sleep(poll_seconds)
        raise AssertionError(f"the turn did not cap within {CLIENT_TIMEOUT_SECONDS}s")

    def _post(
        self, body: bytes | None, op_reply: tuple[str, bytes] | None, since: str
    ) -> list[str]:
        headers = {
            "authorization": f"Bearer {self.token}",
            "content-type": "text/plain",
            "x-ufo-session": "wire-chain",
            "x-ufo-tty": "1",
            "x-ufo-cwd": self.cwd,
        }
        if since:
            headers["x-ufo-since"] = since
        if op_reply is not None:
            headers["x-ufo-op"] = op_reply[0]
            body = op_reply[1]
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            connection.request("POST", f"/surface/ufo/{CHANNEL}", body=body, headers=headers)
            response = connection.getresponse()
            assert response.status == 200, response.read().decode()
            return [line.decode() for line in response.read().splitlines() if line]
        finally:
            connection.close()

    def _exec(self, params_field: str) -> bytes:
        params = json.loads(params_field)
        done = subprocess.run(
            params["argv"],
            cwd=self.cwd,
            env={**os.environ, **params.get("env", {})},
            capture_output=True,
            timeout=OP_TIMEOUT_SECONDS,
        )
        return json.dumps(
            {
                "exit_code": done.returncode,
                "stdout_b64": base64.b64encode(done.stdout).decode(),
                "stderr_b64": base64.b64encode(done.stderr).decode(),
            }
        ).encode()


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
    """A live threaded uvicorn mounting the shared `ufo` surface over the session DBOS worker and
    a ProofModel runtime, on an ephemeral port the shell client dials."""
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
                manifests=(rollover_manifest(),),
                environment=HostEnvironment(
                    manifests=(rollover_manifest(),),
                    credentials=None,
                    index=default_index(),
                    embed=StubEmbed(),
                ),
                registry=PROOF_REGISTRY,
                skills=skill_registry(()),
                credentials=None,
                index=default_index(),
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
            member_skill_listing=no_member_skills,
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


def test_a_wire_client_lands_a_bash_turn_in_its_own_directory(
    terminal_server: tuple[UUID, int], tmp_path: Path
) -> None:
    workspace_id, port = terminal_server
    token = mint_token(TOKEN_SECRET, str(workspace_id), OWNER_EMAIL, timedelta(hours=1))
    project = tmp_path / "project"
    project.mkdir()
    bound = str(project.resolve())

    transcript = _WireClient(port=port, token=token, cwd=bound).run("write a proof file")

    assert (project / PROOF_FILENAME).read_bytes() == PROOF_CONTENT
    assert f"Workspace: {bound}" in transcript
    assert ANSWER_TEXT in transcript
    conversation = asyncio.run(_conversation_row(workspace_id))
    assert conversation.sandbox_handle == f"client:{bound}"
