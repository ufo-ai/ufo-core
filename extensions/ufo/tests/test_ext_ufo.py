import asyncio
import base64
import hashlib
import hmac
import json
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
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
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
    history_directives,
    resolve_workspace,
    stream_directives,
)
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
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
from ufo.durability import replay_safe_client
from ufo.ext.loader import skill_registry
from ufo.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.hub import (
    Absorbed,
    CostTick,
    InProcessHub,
    Parked,
    Resumed,
    SkillLoad,
    SubagentActivity,
    Terminal,
    ToolCall,
)
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.sandbox.terminal import TerminalOpFailed
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
    assert directives_for(Resumed(attempt="attempt-one"), False) == (
        b"note\tthe service restarted; this turn resumed\n",
    )
    run = SubagentActivity(
        turn_id=UUID(int=1),
        parent_turn_id=UUID(int=2),
        conversation_id=UUID(int=3),
        profile="general_purpose",
        name="UK sports news",
    )
    assert directives_for(run, False) == ()
    assert directives_for(
        run.model_copy(update={"tool": "bash", "description": "listing"}), False
    ) == (b"note\tUK sports news: running bash: listing\n",)
    assert directives_for(run.model_copy(update={"name": "", "skill": "demo"}), False) == (
        b"note\tgeneral_purpose: loading skill: demo\n",
    )
    assert directives_for(run.model_copy(update={"status": "done"}), False) == ()
    assert directives_for(CostTick(cost_micro_usd=55_000, tokens=3000), False) == (
        b"status\t3000 tok - $0.055000\n",
    )


def test_a_drain_names_the_member_arrivals_it_folded() -> None:
    """The turn has taken up what the member sent into it, so the client settles the rows it is
    holding. A drain that folded nothing of the member's says nothing."""
    first, second = uuid4(), uuid4()
    assert directives_for(Absorbed(arrivals=(first, second)), streamed=True) == (
        f"absorbed\t{first}\t{second}\n".encode(),
    )
    assert directives_for(Absorbed(arrivals=()), streamed=True) == ()


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
    assert directives_for(Parked(message="over cap"), False) == (b"say\tover cap\n", b"ask\t>\n")


def test_a_cancel_divides_on_whether_it_carries_words() -> None:
    """A member's stop cancels with no text, so the turn ends and the prompt returns — the session
    is the member's, and they stopped a turn rather than left. An admission refusal cancels with its
    reason, which the member reads before the client exits."""
    stopped = Terminal(frame=TerminalFrame(status="cancelled"))
    assert directives_for(stopped, streamed=True) == (b"say\tcancelled\n", b"ask\t>\n")
    refused = Terminal(frame=TerminalFrame(status="cancelled", text="Over the daily cap."))
    assert directives_for(refused, streamed=True) == (b"say\tOver the daily cap.\n", b"exit\t0\n")
    assert directives_for(refused, streamed=True, exits=False) == (
        b"say\tOver the daily cap.\n",
        b"ask\t>\n",
    ), "a stream that did not admit the refused turn keeps the session"


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

    gated = [
        line async for line in stream_directives(aclosing(frames()), 5.0, fulfilled, turn_id=TURN)
    ]
    assert not any(line.startswith(b"secret\t") for line in gated)
    partial = [
        line async for line in stream_directives(aclosing(frames()), 5.0, token_only, turn_id=TURN)
    ]
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
        b"ask\t>\n",
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
        connect_message="[Complete the connection](https://oauth.test/a)",
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

    lines = [
        line async for line in stream_directives(aclosing(frames()), 5.0, files=files, turn_id=TURN)
    ]
    assert reads == 1
    assert lines == [
        b"txt\tpartial\n",
        b"file\tone.txt\t3\thttps://ufo.test/artifacts/1\n",
        b"ask\t>\n",
        b"since\t77777777-7777-4777-8777-777777777777\tc2\n",
        b"listen\t2\n",
    ]


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

    lines = [
        line
        async for line in stream_directives(aclosing(frames()), 5.0, connect=connect, turn_id=TURN)
    ]
    assert lines == [
        b"say\tUse the connection control.\n",
        b"say\t[Complete the connection](https://oauth.example.test/authorize)\n",
        b"ask\t>\n",
        b"since\t77777777-7777-4777-8777-777777777777\tc1\n",
        b"listen\t2\n",
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


TURN = UUID("77777777-7777-4777-8777-777777777777")


async def _feed(frames: list[tuple[str, object]]) -> AsyncIterator[tuple[str, object]]:
    for item in frames:
        yield item


async def test_hold_expires_and_the_stream_ends_with_poll() -> None:
    assert HOLD_SECONDS == 85.0
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_Never()), hold_seconds=0.05, turn_id=TURN
            )
        ]
    )
    lines = _lines(out)
    assert lines[0] == ["txt", "working"]
    assert lines[-1] == ["poll", "1"]


async def test_a_stream_that_will_be_resumed_names_where_it_got_to() -> None:
    """Every end that the client reconnects from carries the cursor of the last frame rendered, so
    the tail it opens next starts after that frame instead of at the ring's first. Without it every
    reconnect re-renders what the terminal already printed — and an op ends the stream, so a turn
    that runs three tools prints its first note three times."""
    frames = _feed([("7", ToolCall(tool="glob", preview="", description="Listing the folder"))])
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(frames), hold_seconds=0.05, turn_id=TURN, since="4"
            )
        ]
    )
    lines = _lines(out)
    assert lines == [
        ["note", "running glob: Listing the folder"],
        ["since", str(TURN), "7"],
        ["poll", "1"],
    ]


async def test_a_stream_resumed_from_its_cursor_states_the_cursor_it_was_given() -> None:
    """A hold that renders nothing new still names where the client is, so a quiet reconnect does
    not walk the cursor backwards and replay the turn from its first frame."""
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_feed([])), hold_seconds=0.05, turn_id=TURN, since="9"
            )
        ]
    )
    assert _lines(out) == [["since", str(TURN), "9"], ["poll", "1"]]


async def test_a_terminal_frame_ends_with_its_cursor_and_a_listen() -> None:
    """A turn's own end leaves the prompt, and the conversation can be woken without the member —
    so the terminal names where the client got to and tells it to reconnect on the listen
    interval, never on a `poll` that would read as a turn still running."""
    frames = _feed(
        [("1", TextDelta(text="echo:1")), ("2", Terminal(frame=TerminalFrame(status="done")))]
    )
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(frames), hold_seconds=HOLD_SECONDS, turn_id=TURN
            )
        ]
    )
    assert _lines(out) == [
        ["txt", "echo:1"],
        ["ask", ">"],
        ["since", str(TURN), "2"],
        ["listen", "2"],
    ]


async def test_a_cancel_that_carries_words_exits_without_a_listen() -> None:
    """An admission refusal ends the client session, so nothing is left to reconnect."""
    refused = Terminal(frame=TerminalFrame(status="cancelled", text="Over the daily cap."))
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_feed([("1", refused)])), hold_seconds=HOLD_SECONDS, turn_id=TURN
            )
        ]
    )
    assert _lines(out) == [["say", "Over the daily cap."], ["exit", "0"]]


async def test_a_worded_cancel_on_a_listen_stream_prompts_and_keeps_listening() -> None:
    """The refused turn was not this member's act — a scheduled firing a cap refused — so their
    idle terminal reads the reason and stays open, still listening."""
    refused = Terminal(frame=TerminalFrame(status="cancelled", text="Over the daily cap."))
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_feed([("1", refused)])),
                hold_seconds=HOLD_SECONDS,
                turn_id=TURN,
                exits=False,
            )
        ]
    )
    assert _lines(out) == [
        ["say", "Over the daily cap."],
        ["ask", ">"],
        ["since", str(TURN), "1"],
        ["listen", "2"],
    ]


async def test_a_park_ends_without_a_listen() -> None:
    """A parked turn resumes under its own id, so a listen bounce would re-render its notice on
    every reconnect; the park keeps the parked shape and the member's next message resumes."""
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_feed([("1", Parked(message="over cap"))])),
                hold_seconds=HOLD_SECONDS,
                turn_id=TURN,
            )
        ]
    )
    assert _lines(out) == [["say", "over cap"], ["ask", ">"]]


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
    dbos_client = replay_safe_client(config.database.system_url)
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
    dbos_client = replay_safe_client(config.database.system_url)
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
    dbos_client = replay_safe_client(config.database.system_url)
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
        assert ["ask", ">"] in lines
        assert lines[-1] == ["listen", "2"]
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


async def _seed_running_turn(
    workspace_id: UUID, conversation_id: UUID, member_id: UUID, seq: int = 1
) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
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
                seq=seq,
                status="running",
                inbound="list files in this dir",
                admission_source="member",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_a_resumed_stream_does_not_reprint_what_the_terminal_already_showed(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The duplication the cursor closes, through the real route. An op ends the stream and the
    reply reconnects, so a turn calling two tools printed its first note three times. A reconnect
    carrying the cursor it was given resumes after that frame and prints nothing twice; one naming
    another turn is dropped, and the ring is replayed whole — a turn the terminal has printed
    nothing of wants exactly that."""
    client, workspace_id = ufo
    hub = runtime[1]
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)

    async def _end_when_tailed() -> None:
        while not (held := hub._turns.get(turn_id)) or not held.subscribers:  # noqa: ASYNC110
            await asyncio.sleep(0)
        await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done", text="Listed.")))

    async def _resume(named: UUID) -> list[list[str]]:
        """One reconnect onto a turn whose note is in the ring, carrying a cursor for `named`. The
        ring is dropped when a stream's last subscriber leaves, so each attempt publishes its
        own."""
        printed = await hub.publish(
            turn_id, ToolCall(tool="glob", preview="", description="Listing the folder")
        )
        ending = asyncio.ensure_future(_end_when_tailed())
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                response = await client.post(
                    "/surface/ufo/main",
                    content=b"",
                    headers={
                        "authorization": f"Bearer {token}",
                        "x-ufo-since": f"{named}:{printed}",
                    },
                )
        finally:
            ending.cancel()
        assert response.status_code == 200
        return _lines(response.content)

    resumed = await _resume(turn_id)
    stale = await _resume(uuid4())

    assert [line[0] for line in resumed] == ["say", "ask", "since", "listen"]
    assert resumed[0] == ["say", "Listed."]
    assert ["note", "running glob: Listing the folder"] in stale


async def _post_unsend(
    client: AsyncClient, token: str, arrival: str, body: bytes = b""
) -> Response:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        return await client.post(
            "/surface/ufo/main",
            content=body,
            headers={"authorization": f"Bearer {token}", "x-ufo-unsend": arrival},
        )


async def _post_stop(client: AsyncClient, token: str, body: bytes = b"") -> Response:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        return await client.post(
            "/surface/ufo/main",
            content=body,
            headers={"authorization": f"Bearer {token}", "x-ufo-stop": "1"},
        )


async def test_a_stop_ends_the_running_turn_and_returns_the_prompt(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """Esc on the member's keyboard: the stop header ends the conversation's running turn and the
    same request resumes its tail, which replays to the cancelled terminal — so the member reads
    that it stopped and keeps the session, rather than the client exiting."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)

    response = await _post_stop(client, token)

    assert response.status_code == 200
    lines = _lines(response.content)
    assert lines[:2] == [["say", "cancelled"], ["ask", ">"]]
    assert lines[2][:2] == ["since", str(turn_id)]
    assert lines[3] == ["listen", "2"]
    stopped, status = await _sole_turn(workspace_id)
    assert (stopped, status) == (turn_id, "cancelled")


async def test_an_unsend_retracts_the_pending_message(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """Up on a queued row: the message the member sent mid-turn comes back into their hands — the
    pending arrival is deleted, so no turn can fold it and a later stop founds nothing on it."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    sent = await _post_send(client, token, b"never mind", uuid4())
    arrival = _lines(sent.content)[0][3]

    retracted = await _post_unsend(client, token, arrival)

    assert retracted.status_code == 200
    assert retracted.content == b""
    async with workspace_tx() as connection:
        pending = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.inbound_message)
                .where(tables.inbound_message.c.conversation_id == conversation_id)
            )
        ).scalar_one()
    assert pending == 0

    again = await _post_unsend(client, token, arrival)
    assert again.status_code == 409
    assert turn_id is not None


async def test_an_unsend_of_a_taken_up_message_is_refused(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """A message a turn already folded is out of the member's hands: the retraction answers 409
    and the row stays consumed, so the transcript never loses a message the agent read."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    sent = await _post_send(client, token, b"too late", uuid4())
    arrival = _lines(sent.content)[0][3]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.inbound_message)
            .values(consumed_turn_id=turn_id)
            .where(tables.inbound_message.c.id == UUID(arrival))
        )

    refused = await _post_unsend(client, token, arrival)

    assert refused.status_code == 409
    async with workspace_tx() as connection:
        consumed = (
            await connection.execute(
                sa.select(tables.inbound_message.c.consumed_turn_id).where(
                    tables.inbound_message.c.id == UUID(arrival)
                )
            )
        ).scalar_one()
    assert consumed == turn_id


async def test_an_unsend_speaks_only_for_its_own_member(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """One member cannot retract another's words: the row survives and the answer refuses."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "other@example.com")
    owner_token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, owner_token)
    await _seed_running_turn(workspace_id, conversation_id, member_id)
    sent = await _post_send(client, owner_token, b"mine", uuid4())
    arrival = _lines(sent.content)[0][3]
    other_token = _mint(SECRET, workspace_id, "other@example.com", _future())

    refused = await _post_unsend(client, other_token, arrival)

    assert refused.status_code in (404, 409)
    async with workspace_tx() as connection:
        survives = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.inbound_message)
                .where(tables.inbound_message.c.id == UUID(arrival))
            )
        ).scalar_one()
    assert survives == 1


async def test_an_unsend_refuses_a_body_and_a_bad_id(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _linked_conversation(client, workspace_id, token)

    carrying = await _post_unsend(client, token, str(uuid4()), body=b"and this")
    assert carrying.status_code == 400

    malformed = await _post_unsend(client, token, "not-a-uuid")
    assert malformed.status_code == 400

    empty = await _post_unsend(client, token, "")
    assert empty.status_code == 400, "an empty header is an unsend, never a resume"


async def test_a_stop_founds_the_next_turn_on_the_message_already_sent(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """Esc after a mid-turn send: the cancel ends the running turn and the message the member had
    already sent founds the next one. The stop's own tail ends on an immediate `poll` because the
    conversation moved on, and the reconnect resumes on the new turn — whose first frame names the
    arrival, so the row the client held as pending settles as the new turn's own message."""
    client, workspace_id = ufo
    hub = runtime[1]
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    sent = await _post_send(client, token, b"do this instead", uuid4())
    arrival = UUID(_lines(sent.content)[0][3])

    response = await _post_stop(client, token)

    assert response.status_code == 200
    lines = _lines(response.content)
    assert lines[:2] == [["say", "cancelled"], ["ask", ">"]]
    assert lines[2][:2] == ["since", str(turn_id)]
    assert lines[3] == ["poll", "0"]
    assert len(lines) == 4
    async with workspace_tx() as connection:
        new_turn = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.status, tables.turn.c.inbound)
                .where(
                    tables.turn.c.conversation_id == conversation_id,
                    tables.turn.c.id != turn_id,
                )
                .order_by(tables.turn.c.seq.desc())
            )
        ).one()
    assert new_turn.status in ("queued", "running")
    assert new_turn.inbound == "do this instead"

    held = asyncio.ensure_future(_post(client, "main", token, b""))
    await _tailing(hub, new_turn.id)
    await hub.publish(
        new_turn.id, Terminal(frame=TerminalFrame(status="done", text="Done instead."))
    )
    resumed = await held
    assert ["absorbed", str(arrival)] in resumed
    assert resumed.index(["absorbed", str(arrival)]) < resumed.index(["say", "Done instead."])


async def test_a_stop_with_no_live_turn_resumes_the_tail(ufo: tuple[AsyncClient, UUID]) -> None:
    """A press with nothing running is not an error: a conversation holding no turn prompts, and one
    whose turn already ended re-reads that answer and leaves it done."""
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    empty = await _post_stop(client, token)
    assert empty.status_code == 200
    assert _lines(empty.content) == [["ask", ">"]]
    assert await _turn_count(workspace_id) == 0

    await _post(client, "main", token, b"hello")
    finished = await _post_stop(client, token)

    assert finished.status_code == 200
    answer = "".join(f for verb, *rest in _lines(finished.content) if verb == "say" for f in rest)
    assert "echo:1" in answer
    assert ["ask", ">"] in _lines(finished.content)
    assert _lines(finished.content)[-1] == ["listen", "2"]
    assert (await _sole_turn(workspace_id))[1] == "done"


async def test_a_stop_carrying_a_message_is_refused(ufo: tuple[AsyncClient, UUID]) -> None:
    """One request is one act: a stop admits nothing, so a body arriving with it is a client that
    would have its message swallowed by the cancel."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)

    refused = await _post_stop(client, token, b"stop and do this instead")

    assert refused.status_code == 400
    assert refused.text == "a stop admits no message"
    assert await _sole_turn(workspace_id) == (turn_id, "running")


async def _post(
    client: AsyncClient,
    channel: str,
    token: str,
    body: bytes,
    timezone: str | None = None,
) -> list[list[str]]:
    headers = {"authorization": f"Bearer {token}"}
    if timezone is not None:
        headers["x-ufo-timezone"] = timezone
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(
            f"/surface/ufo/{channel}",
            content=body,
            headers=headers,
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
    assert lines[-1][0] in ("ask", "exit", "listen")
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
    await _post(client, "main", token, b"hello", timezone="America/Los_Angeles")
    async with workspace_tx() as connection:
        context, timezone = (
            await connection.execute(
                sa.select(tables.turn.c.context, tables.member.c.timezone)
                .select_from(
                    tables.turn.join(
                        tables.member,
                        tables.turn.c.speaker_member_id == tables.member.c.id,
                    )
                )
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).one()
    assert context == {
        "sender": "owner@example.com",
        "timezone": "America/Los_Angeles",
        "question": None,
        "source": "ufo cli (owner@example.com)",
    }
    assert timezone == "America/Los_Angeles"


async def test_a_reported_timezone_lands_on_the_turn_and_an_unknown_one_drops(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    for channel, zone in (("kept", "America/New_York"), ("dropped", "Mars/Olympus_Mons")):
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            response = await client.post(
                f"/surface/ufo/{channel}",
                content=b"hello",
                headers={"authorization": f"Bearer {token}", "x-ufo-timezone": zone},
            )
        assert response.status_code == 200
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        sent = await client.post(
            "/surface/ufo/sent",
            content=b"hello",
            headers={
                "authorization": f"Bearer {token}",
                "x-ufo-send": "1",
                "x-ufo-send-id": str(uuid4()),
                "x-ufo-timezone": "Europe/Berlin",
            },
        )
    assert sent.status_code == 200
    await _post(client, "sent", token, b"")
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.conversation.c.queue_key, tables.turn.c.context)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).all()
    zones = {row.queue_key: row.context["timezone"] for row in rows}
    assert zones == {
        "owner@example.com:kept": "America/New_York",
        "owner@example.com:dropped": None,
        "owner@example.com:sent": "Europe/Berlin",
    }


async def _post_send(
    client: AsyncClient, token: str, body: bytes, send_id: UUID, cwd: str | None = None
) -> Response:
    headers = {
        "authorization": f"Bearer {token}",
        "x-ufo-send": "1",
        "x-ufo-send-id": str(send_id),
    }
    if cwd is not None:
        headers["x-ufo-cwd"] = cwd
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        return await client.post("/surface/ufo/main", content=body, headers=headers)


async def _arrivals(workspace_id: UUID) -> list[tuple[UUID, str]]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.inbound_message.c.id, tables.inbound_message.c.body).where(
                    tables.inbound_message.c.workspace_id == workspace_id
                )
            )
        ).all()
    return [(row.id, row.body) for row in rows]


async def _tailing(hub: InProcessHub, turn_id: UUID) -> None:
    while not (ring := hub._turns.get(turn_id)) or not ring.subscribers:  # noqa: ASYNC110
        await asyncio.sleep(0)


async def test_a_send_joins_the_running_turn_and_leaves_the_held_stream_its_ops(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    tmp_path: Path,
) -> None:
    """A member speaking while their turn runs. The send admits onto that turn and answers with the
    ack — the turn, `0` because another delivery opened its run, and the arrival the drain will
    name — while the stream they are already holding goes untouched: the send connects no terminal,
    so the op the turn asks for after it still reaches the watcher that stream owns."""
    client, workspace_id = ufo
    hub, terminals = runtime[1], runtime[3].terminals
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    cwd = str(tmp_path / "proj")
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    held = asyncio.ensure_future(_post_terminal(client, "main", token, b"", cwd))
    await _tailing(hub, turn_id)

    sent = await _post_send(client, token, b"and the tests too", uuid4())

    assert sent.status_code == 200
    assert not held.done()
    verb, named, opened, arrival = _lines(sent.content)[0]
    assert (verb, named, opened) == ("sent", str(turn_id), "0")
    assert await _arrivals(workspace_id) == [(UUID(arrival), "and the tests too")]

    reading = asyncio.ensure_future(
        terminals.send(conversation_id, "read", 30, arg=f"{cwd}/notes.txt")
    )
    lines = await held
    assert [line[0] for line in lines] == ["since", "run"]
    assert terminals.resolve(conversation_id, lines[1][1], b"notes")
    assert await reading == b"notes"


async def test_the_tail_names_the_arrival_the_ack_promised(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The other half of the mid-turn send: the id the ack named is the id the held stream reads
    back when the turn folds that row into its window, so the client settles the row it was holding
    against the agent actually taking the message up."""
    client, workspace_id = ufo
    hub = runtime[1]
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    held = asyncio.ensure_future(_post(client, "main", token, b""))
    await _tailing(hub, turn_id)
    sent = await _post_send(client, token, b"and the tests too", uuid4())
    arrival = UUID(_lines(sent.content)[0][3])

    await hub.publish(turn_id, Absorbed(arrivals=(arrival,)))
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done", text="Both done.")))

    lines = await held
    assert lines[:3] == [["absorbed", str(arrival)], ["say", "Both done."], ["ask", ">"]]
    assert lines[3][0] == "since"
    assert lines[4] == ["listen", "2"]


async def test_a_resent_delivery_admits_once_and_names_the_same_turn(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """The client retries a send whose answer the link dropped. The `x-ufo-send-id` it repeats is
    the delivery's identity, so the message lands once and the retry reads back the same ack — the
    turn it joined and the arrival still waiting in it."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    turn_id = await _seed_running_turn(workspace_id, conversation_id, member_id)
    send_id = uuid4()

    first = await _post_send(client, token, b"and the tests too", send_id)
    resent = await _post_send(client, token, b"and the tests too", send_id)

    assert first.content == resent.content
    assert _lines(first.content)[0][:3] == ["sent", str(turn_id), "0"]
    assert await _arrivals(workspace_id) == [
        (UUID(_lines(first.content)[0][3]), "and the tests too")
    ]
    assert await _turn_count(workspace_id) == 1


async def test_a_send_with_nothing_running_opens_a_turn_the_stream_reads(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """A send is not a mid-turn special case: with nothing running it founds a turn like any other
    message, says so with `1`, and names no arrival — there is no queue row, the message is the
    turn's own inbound. The member's next stream tails that turn to its answer."""
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())

    sent = await _post_send(client, token, b"hello", uuid4())

    assert _lines(sent.content) == [["sent", str((await _sole_turn(workspace_id))[0]), "1", ""]]
    lines = await _post(client, "main", token, b"")
    answer = "".join(field for verb, *rest in lines if verb in ("txt", "say") for field in rest)
    assert "echo:1" in answer
    assert ["ask", ">"] in lines
    assert lines[-1] == ["listen", "2"]


async def test_a_resent_send_that_founded_a_turn_names_it_without_reopening_it(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """The retry of a send that founded a turn: the message stays said once and the turn stays the
    one it founded, but the run was opened by the delivery before it — so the ack names the turn and
    claims neither the run nor an arrival, which is the answer every later delivery of that message
    gets."""
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    send_id = uuid4()

    founded = await _post_send(client, token, b"hello", send_id)
    resent = await _post_send(client, token, b"hello", send_id)

    turn_id = (await _sole_turn(workspace_id))[0]
    assert _lines(founded.content) == [["sent", str(turn_id), "1", ""]]
    assert _lines(resent.content) == [["sent", str(turn_id), "0", ""]]
    assert await _turn_count(workspace_id) == 1
    assert await _arrivals(workspace_id) == []


async def test_a_first_send_claims_the_terminal_it_stands_in(
    ufo: tuple[AsyncClient, UUID], tmp_path: Path
) -> None:
    """A send standing in a directory claims the conversation's terminal exactly as an admitting
    stream does — the binding is made at admission, and a member whose first message is a send is
    owed the same agent workspace — and names it after the ack. The claim fills an empty handle
    only, so the next send finds the conversation bound and says nothing."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    cwd = str(tmp_path / "proj")
    conversation_id = await _linked_conversation(client, workspace_id, token)
    await _seed_running_turn(workspace_id, conversation_id, member_id)

    first = await _post_send(client, token, b"hello", uuid4(), cwd=cwd)
    second = await _post_send(client, token, b"and again", uuid4(), cwd=cwd)

    assert _lines(first.content)[0][0] == "sent"
    assert _lines(first.content)[1] == ["note", f"Workspace: {cwd}"]
    assert [line[0] for line in _lines(second.content)] == ["sent"]


async def test_a_send_states_what_it_needs_and_admits_nothing_without_it(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """A send is one message under one delivery id: a body it cannot identify, or no body at all, is
    a client that would have its message doubled by a retry or say nothing at all."""
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        unkeyed = await client.post(
            "/surface/ufo/main",
            content=b"hello",
            headers={"authorization": f"Bearer {token}", "x-ufo-send": "1"},
        )
    empty = await _post_send(client, token, b"", uuid4())

    assert (unkeyed.status_code, unkeyed.text) == (400, "x-ufo-send-id must be a uuid")
    assert (empty.status_code, empty.text) == (400, "a send carries a message")
    assert await _turn_count(workspace_id) == 0


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
    dbos_client = replay_safe_client(config.database.system_url)
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
        query["ws"][0],
        datetime.now(UTC),
    )
    assert claims.blob_key == f"artifacts/{turn_id}/report.pdf"
    assert ["ask", ">"] in lines


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


async def test_a_stale_client_is_told_to_install_except_on_an_op_reply(
    ufo: tuple[AsyncClient, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    monkeypatch.setenv("UFO_CLIENT_VERSION", "9.9.9")
    headers = {"authorization": f"Bearer {token}", "x-ufo-script": "1a2b3c4d5e6f"}
    streamed = await client.post("/surface/ufo/stale", content=b"hello", headers=headers)
    assert _lines(streamed.content)[0] == ["install"]
    replied = await client.post(
        "/surface/ufo/stale", content=b"", headers={**headers, "x-ufo-op": "deadbeef"}
    )
    assert ["install"] not in _lines(replied.content)
    current = await client.post(
        "/surface/ufo/stale", content=b"", headers={**headers, "x-ufo-script": "9.9.9"}
    )
    assert ["install"] not in _lines(current.content)


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
    assert ["ask", ">"] in polled
    assert polled[-1] == ["listen", "2"]


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
    assert lines[1][1].startswith("[Complete the connection](https://oauth.example.test/authorize")
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
    dbos_client = replay_safe_client(config.database.system_url)
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


def test_history_replays_member_and_agent_lines_leaving_the_tail_its_reply() -> None:
    from ufo.models.interface import Message, TextBlock
    from ufo.transcript import Conversation

    marker = "00aabbcc"
    fenced = f"<member_message_{marker}>\nwhat is up\n</member_message_{marker}>"
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=fenced),
            Message(role="assistant", content=(TextBlock(text="not much"),)),
            Message(role="user", content=(TextBlock(text="tell me more"),)),
            Message(role="assistant", content=(TextBlock(text="the latest reply"),)),
        ),
    )
    lines = history_directives(conversation)
    assert lines == (
        b"you\twhat is up\n",
        b"say\tnot much\n",
        b"you\ttell me more\n",
    )


def test_history_strips_the_prompt_envelope_from_member_lines() -> None:
    from ufo.models.interface import Message, TextBlock
    from ufo.transcript import Conversation

    composed = (
        "<context>\n"
        "message_ref: 142f1dd4-8c24-5faa-9c20-b760d238ecb1\n"
        "time: Sunday 2026-08-16 23:04 EDT\n"
        "sender: alex@metalcraft.ai\n"
        "source: ufo cli (alex@metalcraft.ai)\n"
        "</context>\n"
        "sup\n"
        "\n<injected_context>\nRelevant memory:\n- a fact worth recalling\n</injected_context>"
    )
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=composed),
            Message(role="assistant", content=(TextBlock(text="not much"),)),
            Message(role="user", content=(TextBlock(text="tell me more"),)),
            Message(role="assistant", content=(TextBlock(text="the latest reply"),)),
        ),
    )
    lines = history_directives(conversation)
    assert lines == (
        b"you\tsup\n",
        b"say\tnot much\n",
        b"you\ttell me more\n",
    )


def test_history_states_a_turns_steps_over_the_reply_they_produced() -> None:
    from ufo.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
    from ufo.transcript import Conversation

    marker = "00aabbcc"
    fenced = f"<member_message_{marker}>\nwhen is the meeting\n</member_message_{marker}>"
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=fenced),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Reading the notes first."),
                    ToolUseBlock(id="call-1", name="read", input={}),
                    ToolUseBlock(id="call-2", name="load_skill", input={"name": "office/pptx"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="call-1", content="notes", activity=True),
                    ToolResultBlock(tool_use_id="call-2", content="loaded", activity=True),
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="The meeting is at four."),)),
            Message(role="user", content=(TextBlock(text="thanks"),)),
        ),
    )
    lines = history_directives(conversation)
    assert lines == (
        b"you\twhen is the meeting\n",
        b"note\tCompleted 3 steps\n",
        b"say\tThe meeting is at four.\n",
        b"you\tthanks\n",
    )


def test_history_says_no_words_for_a_turn_cut_after_its_narration() -> None:
    from ufo.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
    from ufo.transcript import Conversation

    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="read the notes"),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Reading the notes first."),
                    ToolUseBlock(id="call-1", name="read", input={}),
                ),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id="call-1", content="notes", activity=True),),
            ),
            Message(role="user", content="and again"),
        ),
    )
    lines = history_directives(conversation)
    assert lines == (
        b"you\tread the notes\n",
        b"note\tCompleted 2 steps\n",
        b"you\tand again\n",
    )


def test_history_counts_no_step_for_a_call_that_never_dispatched() -> None:
    from ufo.models.interface import Message, TextBlock, ToolUseBlock
    from ufo.transcript import Conversation

    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="try it"),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="I cannot run that."),
                    ToolUseBlock(id="call-1", name="bash", input={}),
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="Nothing ran."),)),
            Message(role="user", content="understood"),
        ),
    )
    lines = history_directives(conversation)
    assert lines == (
        b"you\ttry it\n",
        b"note\tCompleted 1 step\n",
        b"say\tNothing ran.\n",
        b"you\tunderstood\n",
    )


def test_history_budget_keeps_the_newest_messages() -> None:
    from ufo.models.interface import Message
    from ufo.transcript import Conversation

    old = Message(role="user", content="a" * 30_000)
    new = Message(role="user", content="the recent one")
    conversation = Conversation(seq=1, messages=(old, new))
    lines = history_directives(conversation)
    assert lines == (b"you\tthe recent one\n",)


async def test_a_fresh_resume_replays_history_and_a_poll_does_not(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    from ufo.models.interface import Message, TextBlock
    from ufo.transcript import Conversation, encode, transcript_key

    client, workspace_id = ufo
    _config, _hub, blob, _sandboxes = runtime
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    assert await _post(client, "main", token, b"") == [["ask", ">"]]
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
                inbound="what is up",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", text="the reply").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="what is up"),
            Message(role="assistant", content=(TextBlock(text="the reply"),)),
        ),
    )
    await blob.put(transcript_key(conversation_id), encode(conversation))

    fresh = await _post(client, "main", token, b"")
    assert ["you", "what is up"] in fresh
    said = [line for line in fresh if line[0] == "say" and line[1] == "the reply"]
    assert len(said) == 1, f"the tail says the reply once, history never doubles it: {fresh}"

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(
            "/surface/ufo/main",
            content=b"",
            headers={
                "authorization": f"Bearer {token}",
                "x-ufo-since": f"{turn_id}:",
            },
        )
    assert response.status_code == 200
    polled = _lines(response.content)
    assert ["you", "what is up"] not in polled, f"a poll must not replay history: {polled}"
    assert ["say", "the reply"] in polled, (
        f"an unmarked reconnect may hold a mid-turn cursor, so it drains the durable end: {polled}"
    )
    assert polled[-1] == ["listen", "2"]

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        marked = await client.post(
            "/surface/ufo/main",
            content=b"",
            headers={
                "authorization": f"Bearer {token}",
                "x-ufo-since": f"{turn_id}:",
                "x-ufo-listen": "1",
            },
        )
    bounced = _lines(marked.content)
    assert bounced == [["since", str(turn_id), ""], ["listen", "2"]], (
        f"a marked idle listen answers its cursor and the interval alone: {bounced}"
    )


async def test_a_marked_bounce_renders_a_refused_wakeup_without_exiting(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """A cap refuses a turn the member never founded — a scheduled firing, say — and the idle
    terminal's next bounce renders the refusal. The member pressed nothing, so their session
    survives: the reason is said, the prompt returns, and the listen keeps them reachable."""
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    rendered = uuid4()
    refused = uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        for turn_id, seq, status, frame, inbound, source in (
            (rendered, 1, "done", TerminalFrame(status="done", text="ok"), "hello", "member"),
            (
                refused,
                2,
                "cancelled",
                TerminalFrame(status="cancelled", text="Over the daily cap."),
                "scheduled: check the feeds",
                "internal",
            ),
        ):
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=seq,
                    status=status,
                    inbound=inbound,
                    admission_source=source,
                    speaker_member_id=member_id if source == "member" else None,
                    terminal=frame.model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(
            "/surface/ufo/main",
            content=b"",
            headers={
                "authorization": f"Bearer {token}",
                "x-ufo-since": f"{rendered}:4",
                "x-ufo-listen": "1",
            },
        )
    lines = _lines(response.content)
    assert ["say", "Over the daily cap."] in lines
    assert ["exit", "0"] not in lines, f"a background refusal must not end the session: {lines}"
    assert ["ask", ">"] in lines
    assert lines[-2][:2] == ["since", str(refused)]
    assert lines[-1] == ["listen", "2"]


async def test_an_idle_listen_reconnect_prints_the_turn_the_conversation_woke_on(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The conversation speaks while nobody types — a delivered subagent result, a fired monitor —
    and the idle client's next reconnect finds the newer turn and streams it from its first frame:
    the `since` names the turn already rendered, so nothing re-says and nothing replays history."""
    client, workspace_id = ufo
    hub = runtime[1]
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    conversation_id = await _linked_conversation(client, workspace_id, token)
    rendered = uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=rendered,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="start the job",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", text="Running it.").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    headers = {
        "authorization": f"Bearer {token}",
        "x-ufo-since": f"{rendered}:3",
        "x-ufo-listen": "1",
    }

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        quiet = await client.post("/surface/ufo/main", content=b"", headers=headers)
    assert _lines(quiet.content) == [["since", str(rendered), "3"], ["listen", "2"]]

    woken = await _seed_running_turn(workspace_id, conversation_id, member_id, seq=2)

    async def _speak_when_tailed() -> None:
        await _tailing(hub, woken)
        await hub.publish(woken, TextDelta(text="the job finished"))
        await hub.publish(woken, Terminal(frame=TerminalFrame(status="done")))

    speaking = asyncio.ensure_future(_speak_when_tailed())
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            response = await client.post("/surface/ufo/main", content=b"", headers=headers)
    finally:
        speaking.cancel()
    lines = _lines(response.content)
    assert ["txt", "the job finished"] in lines
    assert ["you", "start the job"] not in lines, f"a listen never replays history: {lines}"
    assert lines[-2][:2] == ["since", str(woken)]
    assert lines[-1] == ["listen", "2"]
