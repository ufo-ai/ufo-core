import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from starlette.datastructures import Headers
from starlette.requests import Request as StarletteRequest
from ufo_ext_context_rollover.manifest import manifest as rollover_manifest
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_ext_ufo.surface import (
    HOLD_SECONDS,
    PROMPT,
    SharedFile,
    _run,
    _utf8_header,
    directive,
    directives_for,
    history_directives,
    resolve_workspace,
    stream_directives,
    terminal_runtime_id,
)
from ufo_testsupport.index import default_index
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.harness.sandbox.terminal import TerminalOp, TerminalOpFailed
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import (
    CredentialRequestState,
    CredentialStore,
    seal_credential_request,
)
from ufo.runtime.access.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.runtime.ext.manifest import CredentialSlot, Manifest
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    ArtifactsChanged,
    CostTick,
    Created,
    InProcessHub,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SourceRef,
    Sources,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    AskQuestion,
    AskUserInput,
    CredentialPrompt,
    CredentialRequest,
    QuestionOption,
    TerminalFrame,
    Usage,
)
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.record import frame_event, frame_payload
from ufo.sdk.surfaces import ConnectRequest, SurfaceAuth, with_agent_detail
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

SECRET = "ufo-token-secret"
STREAM_TIMEOUT_SECONDS = 30
HELD_STREAM_SECONDS = 3.0
STREAM_GATE = StreamGate()


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


def _frame_text(line: list[str]) -> str:
    """A frame line's `text`, read back through the escaping the directive codec applied."""
    raw = (
        line[2].replace("\\\\", "\0").replace("\\n", "\n").replace("\\t", "\t").replace("\0", "\\")
    )
    return json.loads(raw)["text"] or ""


def _answer(lines: list[list[str]]) -> str:
    """The words a member read: the message frames that streamed, else the done frame's text, the
    way the client draws them; a `say` line is a failure's or a question's words."""
    streamed = "".join(_frame_text(line) for line in lines if line[:2] == ["frame", "message"])
    stated = "".join(_frame_text(line) for line in lines if line[:2] == ["frame", "terminal"])
    said = "".join(line[1] for line in lines if line[0] == "say")
    return (streamed or stated) + said


def test_directive_escapes_tabs_newlines_and_backslashes() -> None:
    assert directive("say", "hello") == b"say\thello\n"
    assert directive("say", "a\tb\nc\\d\r") == b"say\ta\\tb\\nc\\\\d\n"
    assert directive("ask", PROMPT) == b"ask\t>\n"
    assert directive("poll", "1") == b"poll\t1\n"


def _frame(frame: LiveFrame) -> bytes:
    event = frame_event(frame)
    assert event is not None
    return directive("frame", event, frame_payload(frame))


def test_frame_map_covers_every_live_frame() -> None:
    assert directives_for(TextDelta(text="hi")) == (b'frame\tmessage\t{"text":"hi"}\n',)
    assert directives_for(TextDelta(text="")) == ()
    listing = Activity(text="Listing the workspace.")
    assert directives_for(listing) == (_frame(listing),)
    assert directives_for(ArtifactsChanged(), False) == ()
    made = Created(refs=(ObjectRef(kind="agent", name="payroll-inbox"),))
    assert directives_for(made) == (_frame(made),)
    resumed = Resumed(attempt="attempt-one")
    assert directives_for(resumed) == (_frame(resumed),)
    consulted = Sources(
        items=(
            SourceRef(kind="web", title="Pricing", url="https://northwind.example/pricing"),
            SourceRef(kind="web", title="Support", url="https://northwind.example/support"),
            SourceRef(kind="workspace", title="Order form", ref="page/2f1c", provider="notion"),
        )
    )
    assert directives_for(consulted) == (_frame(consulted),)
    assert directives_for(Sources(items=()), False) == ()
    run = SubagentActivity(
        turn_id=UUID(int=1),
        parent_turn_id=UUID(int=2),
        conversation_id=UUID(int=3),
        profile="general_purpose",
        name="UK sports news",
    )
    assert directives_for(run) == (_frame(run),)
    working = run.model_copy(update={"activity": "Listing the workspace."})
    assert directives_for(working) == (_frame(working),)
    assert directives_for(run.model_copy(update={"status": "done"}), False) == (
        _frame(run.model_copy(update={"status": "done"})),
    )
    priced = CostTick(cost_micro_usd=55_000, tokens=3000)
    assert directives_for(priced) == (_frame(priced),)
    comment = Reply(
        id=UUID(int=4),
        text="You [commented](https://ufo.test/surface/web#/c/thread): follow up",
        is_comment=True,
    )
    assert directives_for(comment) == (_frame(comment),)
    assert directives_for(comment, False, comments=False) == ()
    assert directives_for(Reply(id=UUID(int=5), text=""), False) == ()


def test_a_drain_names_the_member_arrivals_it_folded() -> None:
    """The turn has taken up what the member sent into it, so the client settles the rows it is
    holding. A drain that folded nothing of the member's says nothing."""
    first, second = uuid4(), uuid4()
    drained = Absorbed(arrivals=(first, second))
    assert directives_for(drained) == (_frame(drained),)
    assert directives_for(Absorbed(arrivals=())) == ()


def test_a_cancel_divides_on_whether_it_carries_words() -> None:
    """A member's stop cancels with no text, so the turn ends and the prompt returns — the
    session is the member's, and they stopped a turn rather than left."""
    stopped = Terminal(frame=TerminalFrame(status="cancelled"))
    assert directives_for(stopped) == (
        _frame(stopped),
        b"say\tcancelled\n",
        b"ask\t>\n",
    )
    refused = Terminal(frame=TerminalFrame(status="cancelled", text="Over the daily cap."))
    assert directives_for(refused) == (
        _frame(refused),
        b"say\tOver the daily cap.\n",
        b"exit\t0\n",
    )
    assert directives_for(refused, exits=False) == (
        _frame(refused),
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
    crossed = Terminal(frame=TerminalFrame(status="done", text="t"))
    assert directives_for(done, collect=request.prompts) == (
        _frame(crossed),
        b"secret\tsealed-opaque\tslack_bot_token\tBot User OAuth Token\n",
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done, collect=request.prompts[1:]) == (
        _frame(crossed),
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done) == (_frame(crossed), b"ask\t>\n")


def test_a_terminal_renders_a_structured_permission_question_for_its_target() -> None:
    target_member_id = uuid4()
    question = AskUserInput(
        title="Permission required",
        questions=(
            AskQuestion(
                question="Allow this request?",
                options=(
                    QuestionOption(label="Allow"),
                    QuestionOption(label="Deny"),
                    QuestionOption(label="Always Allow"),
                ),
            ),
        ),
        target_member_id=target_member_id,
    )

    assert directives_for(
        Terminal(frame=TerminalFrame(status="done", question=question)),
        viewer_member_id=target_member_id,
    ) == (
        _frame(Terminal(frame=TerminalFrame(status="done"))),
        b"say\tPermission required\n",
        b"choose\tAllow this request?\tAllow\tDeny\tAlways Allow\n",
        b"ask\t>\n",
    )


def test_a_terminal_renders_one_time_authorization_choices() -> None:
    target_member_id = uuid4()
    authorization_id = uuid4()
    question = AskUserInput(
        title="Permission required",
        questions=(
            AskQuestion(
                question="Allow this request?",
                options=(
                    QuestionOption(
                        label="Allow once",
                        description="Read issues from the selected GitHub account this time.",
                        authorization_choice="allow",
                    ),
                    QuestionOption(
                        label="Deny",
                        description="Do not read the account.",
                        authorization_choice="deny",
                    ),
                ),
            ),
        ),
        target_member_id=target_member_id,
        authorization_id=authorization_id,
    )

    assert directives_for(
        Terminal(frame=TerminalFrame(status="done", question=question)),
        viewer_member_id=target_member_id,
    ) == (
        _frame(Terminal(frame=TerminalFrame(status="done"))),
        b"say\tPermission required\n",
        (
            f"authorize\t{authorization_id}\tAllow this request?"
            "\tallow\tAllow once\tRead issues from the selected GitHub account this time."
            "\tdeny\tDeny\tDo not read the account.\n"
        ).encode(),
        b"ask\t>\n",
    )


def test_a_terminal_transports_the_scoped_always_authorization_choice() -> None:
    target_member_id = uuid4()
    authorization_id = uuid4()
    question = AskUserInput(
        title="GitHub approval",
        questions=(
            AskQuestion(
                question="Read issues from the selected GitHub account?",
                options=(
                    QuestionOption(
                        label="Allow once",
                        description="Read issues this time.",
                        authorization_choice="allow",
                    ),
                    QuestionOption(
                        label="Deny",
                        description="Do not read issues.",
                        authorization_choice="deny",
                    ),
                    QuestionOption(
                        label="Always allow issue reads",
                        description="Read issues from this GitHub account without asking again.",
                        authorization_choice="always",
                    ),
                ),
            ),
        ),
        target_member_id=target_member_id,
        authorization_id=authorization_id,
    )

    assert directives_for(
        Terminal(frame=TerminalFrame(status="done", question=question)),
        viewer_member_id=target_member_id,
    ) == (
        _frame(Terminal(frame=TerminalFrame(status="done"))),
        b"say\tGitHub approval\n",
        (
            f"authorize\t{authorization_id}\tRead issues from the selected GitHub account?"
            "\tallow\tAllow once\tRead issues this time."
            "\tdeny\tDeny\tDo not read issues."
            "\talways\tAlways allow issue reads"
            "\tRead issues from this GitHub account without asking again.\n"
        ).encode(),
        b"ask\t>\n",
    )


def test_a_terminal_hides_a_structured_permission_question_from_another_member() -> None:
    question = AskUserInput(
        title="Permission required",
        questions=(AskQuestion(question="Allow this request?"),),
        target_member_id=uuid4(),
    )

    assert directives_for(
        Terminal(frame=TerminalFrame(status="done", question=question)),
        viewer_member_id=uuid4(),
    ) == (
        _frame(Terminal(frame=TerminalFrame(status="done"))),
        b"ask\t>\n",
    )


def test_the_terminal_frame_crosses_without_its_question_and_credential_request() -> None:
    """Both ride their own directives, gated to the member they name, so the frame a second member
    tails carries neither the targeted prompt nor the seal."""
    question = AskUserInput(
        title="Permission required",
        questions=(AskQuestion(question="Allow this request?"),),
        target_member_id=uuid4(),
    )
    done = Terminal(
        frame=TerminalFrame(
            status="done", text="t", question=question, credential_request=_request()
        )
    )
    lines = _lines(b"".join(directives_for(done, viewer_member_id=uuid4())))
    assert [line[:2] for line in lines] == [["frame", "terminal"], ["ask", ">"]]
    crossed = json.loads(lines[0][2])
    assert (crossed["status"], crossed["text"]) == ("done", "t")
    assert (crossed["question"], crossed["credential_request"]) == (None, None)
    assert "sealed-opaque" not in lines[0][2]
    assert "Allow this request?" not in lines[0][2]


def test_a_terminal_preserves_structured_question_answer_modes() -> None:
    question = AskUserInput(
        title="Access details",
        questions=(
            AskQuestion(
                question="Explain access",
                options=(QuestionOption(label="A suggestion"),),
                multi_select=True,
                free_text_only=True,
            ),
            AskQuestion(
                question="Select services",
                options=(
                    QuestionOption(label="Mail"),
                    QuestionOption(label="Calendar"),
                ),
                multi_select=True,
            ),
        ),
    )

    assert directives_for(Terminal(frame=TerminalFrame(status="done", question=question))) == (
        _frame(Terminal(frame=TerminalFrame(status="done"))),
        b"say\tAccess details\n",
        b"choose\tExplain access\n",
        b"choose_many\tSelect services\tMail\tCalendar\n",
        b"ask\t>\n",
    )


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


def test_a_shared_file_precedes_the_secret_and_connect_lines() -> None:
    """One order for the closing block, so the shell renders the file next to the answer it belongs
    to rather than after an unrelated prompt."""
    request = _request()
    done = Terminal(frame=TerminalFrame(status="done", text="t", credential_request=request))
    lines = directives_for(
        done,
        collect=request.prompts[:1],
        connect_message="[Complete the connection](https://oauth.test/a)",
        files=(SharedFile(filename="k.csv", size_bytes=4, url="https://ufo.test/artifacts/k"),),
    )
    assert [line.split(b"\t")[0] for line in lines] == [
        b"frame",
        b"file",
        b"secret",
        b"say",
        b"ask",
    ]


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
        _frame(TextDelta(text="partial")),
        _frame(Terminal(frame=TerminalFrame(status="done", text="partial"))),
        b"file\tone.txt\t3\thttps://ufo.test/artifacts/1\n",
        b"ask\t>\n",
        b"since\t77777777-7777-4777-8777-777777777777\tc2\n",
        b"listen\t2\n",
    ]


async def test_a_done_frame_is_never_said_its_text_is_the_answer() -> None:

    async def files() -> tuple[SharedFile, ...]:
        return ()

    async def frames() -> AsyncIterator[tuple[str, Terminal]]:
        yield (
            "c1",
            Terminal(frame=TerminalFrame(status="done", text="Here it is:\n\nOne.\n\nTwo.")),
        )

    lines = [
        line async for line in stream_directives(aclosing(frames()), 5.0, files=files, turn_id=TURN)
    ]
    assert lines[0] == _frame(
        Terminal(frame=TerminalFrame(status="done", text="Here it is:\n\nOne.\n\nTwo."))
    )
    assert lines[1:] == [
        b"ask\t>\n",
        directive("since", str(TURN), "c1"),
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
    assert lines[0].startswith(b"frame\tterminal\t")
    assert lines[1:] == [
        b"say\t[Complete the connection](https://oauth.example.test/authorize)\n",
        b"ask\t>\n",
        b"since\t77777777-7777-4777-8777-777777777777\tc1\n",
        b"listen\t2\n",
    ]


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


async def test_an_unseated_members_bearer_cannot_read_or_write_terminal_state(ufo) -> None:
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "removed@example.com")
    token = _mint(SECRET, workspace_id, "removed@example.com", _future())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == member_id)
            .values(seated_at=None, updated_at=sa.func.now())
        )
    auth = {"authorization": f"Bearer {token}"}

    skills = await client.get("/surface/ufo/main/skills", headers=auth)
    environment = await client.post(
        "/surface/ufo/environment/document",
        content=b"main: {}",
        headers=auth,
    )

    assert (skills.status_code, environment.status_code) == (401, 401)


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


async def test_a_stream_that_will_be_resumed_names_where_it_got_to() -> None:
    """Every end that the client reconnects from carries the cursor of the last frame rendered,
    so the tail it opens next starts after that frame instead of at the ring's first."""
    frames = _feed([("7", Activity(text="Listing the folder."))])
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
        *_lines(_frame(Activity(text="Listing the folder."))),
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
    assert _lines(out) == [*_lines(_frame(refused)), ["say", "Over the daily cap."], ["exit", "0"]]


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
        *_lines(_frame(refused)),
        ["say", "Over the daily cap."],
        ["ask", ">"],
        ["since", str(TURN), "1"],
        ["listen", "2"],
    ]


async def test_a_park_ends_without_a_listen() -> None:
    """A parked turn resumes under its own id, so a listen bounce would re-render its notice on
    every reconnect; the park keeps the parked shape and the member's next message resumes."""
    parked = Parked(message="over cap")
    out = b"".join(
        [
            chunk
            async for chunk in stream_directives(
                aclosing(_feed([("1", parked)])), hold_seconds=HOLD_SECONDS, turn_id=TURN
            )
        ]
    )
    assert _lines(out) == [*_lines(_frame(parked)), ["say", "over cap"], ["ask", ">"]]


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


BOT_SLOT = "probe_bot_token"
SIGNING_SLOT = "probe_signing_secret"
# The surface admits a slot because a mounted manifest declares it, so the proof declares its own
# rather than borrowing whichever shipped extension happens to hold two.
SLOT_MANIFEST = Manifest(
    name="probe_slots",
    version="0",
    credentials=(
        CredentialSlot(name=BOT_SLOT, description="Bot User OAuth Token"),
        CredentialSlot(name=SIGNING_SLOT, description="Signing Secret"),
    ),
)


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
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _seed_member(workspace_id: UUID, email: str, *, is_admin: bool = False) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=is_admin,
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
            hub=GatingHub(hub, STREAM_GATE),
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"ufo-test-run-token-secret"),
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
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=default_index(),
            embed=StubEmbed(),
            artifact_token_secret=SECRET,
        )
    )
    yield config, hub, blob, sandboxes
    dbos_client.destroy()
    loop_queue.reset_runtime()


@pytest.fixture
def stream_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disarm the delta gate for this test and wire the release its consumers fire."""
    STREAM_GATE.reset()
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )


@asynccontextmanager
async def _ufo_client(
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> AsyncIterator[AsyncClient]:
    config, hub, blob, sandboxes = runtime
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
        member_skill_listing=no_member_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:
        yield client
    dbos_client.destroy()


@pytest.fixture
async def ufo(
    db: None,
    stream_gate: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = await _seed_workspace()
    async with _ufo_client(runtime) as client:
        yield client, workspace_id


@pytest.fixture
async def shared_ufo(
    db: None,
    stream_gate: None,
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
        member_skill_listing=no_member_skills,
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
    ws_a, ws_b = await _seed_workspace(), await _seed_workspace()
    await _seed_member(ws_a, "a@example.com")
    await _seed_member(ws_b, "b@example.com")
    token_a = _mint(SECRET, ws_a, "a@example.com", _future())
    token_b = _mint(SECRET, ws_b, "b@example.com", _future())
    lines_a = await _post(shared_ufo, "main", token_a, b"hi a")
    lines_b = await _post(shared_ufo, "main", token_b, b"hi b")
    for lines in (lines_a, lines_b):
        assert "echo:1" in _answer(lines)
        assert ["ask", ">"] in lines
        assert lines[-1] == ["listen", "2"]
    turn_a, status_a = await _sole_turn(ws_a)
    turn_b, status_b = await _sole_turn(ws_b)
    assert (status_a, status_b) == ("done", "done")
    assert turn_a != turn_b


async def test_a_runtime_config_that_cannot_run_or_would_widen_is_refused(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    refused = (
        {"x-ufo-model": "no-such-model"},
        {"x-ufo-model": "claude-sonnet-5", "x-ufo-internet": "on"},
        {"x-ufo-environment": "http://127.0.0.1:9"},
        {"x-ufo-environment": "not-a-digest"},
        {"x-ufo-model": "claude-sonnet-5", "x-ufo-environment": "http://127.0.0.1:9"},
    )

    for headers in refused:
        response = await client.post(
            "/surface/ufo/refused",
            content=b"never admitted",
            headers={"authorization": f"Bearer {token}", **headers},
        )
        assert response.status_code == 400, response.text

    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert turns == 0


async def test_the_document_store_refuses_junk_and_no_bearer(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    unauthenticated = await client.post(
        "/surface/ufo/environment/document", content=b"main:\n  prompt:\n    text: x\n"
    )
    assert unauthenticated.status_code == 401
    junk = await client.post(
        "/surface/ufo/environment/document",
        content=b'{"grant_tools": ["bash"]}',
        headers={"authorization": f"Bearer {token}"},
    )
    assert junk.status_code == 400


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
    reply reconnects, so a turn calling two tools printed its first note three times."""
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
        """One reconnect onto a turn whose note is in the ring, carrying a cursor for `named`."""
        printed = await hub.publish(turn_id, Activity(text="Listing the folder."))
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

    assert [line[0] for line in resumed] == ["frame", "ask", "since", "listen"]
    assert json.loads(resumed[0][2])["text"] == "Listed."
    assert _lines(_frame(Activity(text="Listing the folder.")))[0] in stale


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
    assert "echo:1" in _answer(_lines(finished.content))
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
    assert "echo:1" in _answer(lines)
    assert lines[-1][0] in ("ask", "exit", "listen")
    async with workspace_tx() as connection:
        turn_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
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
    assert lines[0] == ["sent", str(turn_id), "1", ""]
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
        "authorization_id": None,
        "authorization_choice": None,
        "source": "ufo cli (owner@example.com)",
        "reply_reaches": "ufo",
    }
    assert timezone == "America/Los_Angeles"


async def test_authorization_headers_land_as_structured_turn_context_on_both_send_paths(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    first = uuid4()
    second = uuid4()
    headers = {
        "authorization": f"Bearer {token}",
        "x-ufo-authorization-id": str(first),
        "x-ufo-authorization-choice": "allow",
    }
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        streamed = await client.post("/surface/ufo/streamed", content=b"Proceed", headers=headers)
    assert streamed.status_code == 200
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        sent = await client.post(
            "/surface/ufo/sent-authorization",
            content=b"Keep allowing",
            headers={
                "authorization": f"Bearer {token}",
                "x-ufo-send": "1",
                "x-ufo-send-id": str(uuid4()),
                "x-ufo-authorization-id": str(second),
                "x-ufo-authorization-choice": "always",
            },
        )
    assert sent.status_code == 200
    await _post(client, "sent-authorization", token, b"")
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.conversation.c.queue_key, tables.turn.c.context)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).all()
    answers = {
        row.queue_key: (row.context["authorization_id"], row.context["authorization_choice"])
        for row in rows
    }
    assert answers == {
        "owner@example.com:streamed": (str(first), "allow"),
        "owner@example.com:sent-authorization": (str(second), "always"),
    }


async def test_invalid_authorization_headers_admit_no_turn(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    cases = (
        {"x-ufo-authorization-id": str(uuid4())},
        {"x-ufo-authorization-choice": "allow"},
        {"x-ufo-authorization-id": "not-a-uuid", "x-ufo-authorization-choice": "deny"},
        {
            "x-ufo-authorization-id": str(uuid4()),
            "x-ufo-authorization-choice": "approve",
        },
    )
    for index, extra in enumerate(cases):
        response = await client.post(
            f"/surface/ufo/invalid-{index}",
            content=b"Allow",
            headers={"authorization": f"Bearer {token}", **extra},
        )
        assert response.status_code == 400
    assert await _turn_count(workspace_id) == 0


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


async def test_a_resent_send_that_founded_a_turn_names_it_without_reopening_it(
    ufo: tuple[AsyncClient, UUID],
) -> None:
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
CLIENT_BINARY_URL = "https://downloads.example.test/ufo"


@pytest.fixture
async def ufo_delivering_artifacts(
    db: None,
    stream_gate: None,
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
        member_skill_listing=no_member_skills,
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


async def test_a_client_finishes_its_admitted_turn_across_a_deploy(
    db: None,
    stream_gate: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("UFO_CLIENT_BINARY_URL", CLIENT_BINARY_URL)
    workspace_id = await _seed_workspace()
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    headers = {"authorization": f"Bearer {token}", "x-ufo-script": "1.2.3"}
    monkeypatch.setenv("UFO_CLIENT_VERSION", "1.2.3")
    async with _ufo_client(runtime) as before:
        STREAM_GATE.arm()
        admitted = await before.post(
            "/surface/ufo/deploy",
            content=b"hello",
            headers={**headers, "x-ufo-send": "1", "x-ufo-send-id": str(uuid4())},
        )
    turn_id = _lines(admitted.content)[0][1]

    monkeypatch.setenv("UFO_CLIENT_VERSION", "1.2.4")
    async with _ufo_client(runtime) as after:
        finished = await after.post(
            "/surface/ufo/deploy",
            content=b"",
            headers={**headers, "x-ufo-since": f"{turn_id}:"},
        )
        lines = _lines(finished.content)
        assert not [line for line in lines if line[0] == "install"]
        assert ["ask", ">"] in lines
        refused = await after.post(
            "/surface/ufo/deploy",
            content=b"next",
            headers={**headers, "x-ufo-send": "1", "x-ufo-send-id": str(uuid4())},
        )
        next_session = await after.post("/surface/ufo/deploy", content=b"next", headers=headers)

    assert refused.status_code == 409
    assert _lines(next_session.content) == [
        ["install", CLIENT_BINARY_URL],
        ["say", "Updated ufo. Run ufo again."],
    ]
    assert await _turn_count(workspace_id) == 1


async def test_a_deploy_serving_no_client_never_holds_one_back(
    db: None,
    stream_gate: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    monkeypatch.delenv("UFO_CLIENT_VERSION", raising=False)
    monkeypatch.delenv("UFO_CLIENT_BINARY_URL", raising=False)
    workspace_id = await _seed_workspace()
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    headers = {"authorization": f"Bearer {token}", "x-ufo-script": "0.0.1"}
    async with _ufo_client(runtime) as client:
        sent = await client.post(
            "/surface/ufo/standalone",
            content=b"hello",
            headers={**headers, "x-ufo-send": "1", "x-ufo-send-id": str(uuid4())},
        )
        resumed = await client.post("/surface/ufo/standalone", content=b"", headers=headers)

    assert sent.status_code == 200
    assert _lines(sent.content)[0][0] == "sent"
    assert not [line for line in _lines(resumed.content) if line[0] == "install"]
    assert await _turn_count(workspace_id) == 1


@pytest.mark.parametrize(
    ("named", "value"),
    [("UFO_CLIENT_VERSION", "1.2.3"), ("UFO_CLIENT_BINARY_URL", CLIENT_BINARY_URL)],
)
def test_a_deploy_naming_half_a_client_release_fails_at_boot(
    named: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CLIENT_VERSION", raising=False)
    monkeypatch.delenv("UFO_CLIENT_BINARY_URL", raising=False)
    monkeypatch.setenv(named, value)
    with pytest.raises(RuntimeError, match="UFO_CLIENT_VERSION and UFO_CLIENT_BINARY_URL"):
        ufo_manifest()


async def test_empty_body_polls_without_admitting_a_turn(ufo: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    assert await _turn_count(workspace_id) == 1
    polled = await _post(client, "main", token, b"")
    assert await _turn_count(workspace_id) == 1
    assert "echo:1" in _answer(polled)
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
    assert lines[0][:2] == ["frame", "terminal"]
    assert _frame_text(lines[0]) == "Use the connection control."
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
    stream_gate: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id = await _seed_workspace()
    owner = await _seed_member(workspace_id, "owner@example.com", is_admin=True)
    await _seed_member(workspace_id, "late@example.com")
    sealed = seal_credential_request(
        store.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=owner,
            slots=(BOT_SLOT, SIGNING_SLOT),
        ),
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(), SLOT_MANIFEST),
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
        member_skill_listing=no_member_skills,
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

            last_first = await send(token, SIGNING_SLOT, b"shhh")
            assert last_first.status_code == 200
            assert last_first.content == f"say\tstored {SIGNING_SLOT}\n".encode()
            first = await send(token, BOT_SLOT, b"xoxb-real")
            assert first.status_code == 200
            assert first.content == f"say\tstored {BOT_SLOT}\n".encode()
            assert await store.get(workspace_id, BOT_SLOT) == "xoxb-real"
            assert await store.get(workspace_id, SIGNING_SLOT) == "shhh"
            assert await _turn_count(workspace_id) == 0
            denied = await send(foreign, BOT_SLOT, b"xoxb-evil")
            assert denied.status_code == 403
            assert await store.get(workspace_id, BOT_SLOT) == "xoxb-real"
            offslot = await send(token, "unrelated_slot", b"v")
            assert offslot.status_code == 403
            oversized = await send(token, BOT_SLOT, b"x" * 5000)
            assert oversized.status_code == 413
            garbage = await send(token, BOT_SLOT, b"")
            assert garbage.status_code == 400
    finally:
        dbos_client.destroy()


def test_a_terminal_refusal_reaches_the_member_in_its_own_words() -> None:
    gone = Terminal(
        frame=TerminalFrame(
            status="failed",
            text="",
            error_class="TerminalGone",
            error_message="this conversation's workspace is /Users/m/proj; the connected "
            "terminal is at /Users/m/other",
        )
    )
    lines = _lines(b"".join(directives_for(gone)))
    assert lines[0][:2] == ["frame", "terminal"]
    assert lines[1][0] == "say" and "/Users/m/proj" in lines[1][1]


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
    empty-body post — the state a first contact leaves, without admitting a turn."""
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
    latin-1, so a non-ASCII directory would bind mojibaked and every file op would miss."""
    real_cwd = "/Users/josé/proj"
    request = cast(
        StarletteRequest,
        SimpleNamespace(headers=Headers(raw=[(b"x-ufo-cwd", real_cwd.encode("utf-8"))])),
    )
    assert _utf8_header(request, "x-ufo-cwd") == real_cwd
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


def _framed(text: str, ref: str = "142f1dd4-8c24-5faa-9c20-b760d238ecb1") -> str:
    """A member inbound as the engine writes it: the <context> tag naming its message ref, then the
    words."""
    return (
        "<context>\n"
        f"message_ref: {ref}\n"
        "time: Sunday 2026-08-16 23:04 EDT\n"
        "sender: alex@example.com\n"
        "source: ufo cli (alex@example.com)\n"
        "</context>\n"
        f"{text}"
    )


def test_history_replays_member_and_agent_lines_leaving_the_tail_its_reply() -> None:
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation

    marker = "00aabbcc"
    fenced = _framed(f"<member_message_{marker}>\nwhat is up\n</member_message_{marker}>")
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=fenced),
            Message(role="assistant", content=(TextBlock(text="not much"),)),
            Message(role="user", content=(TextBlock(text=_framed("tell me more")),)),
            Message(role="assistant", content=(TextBlock(text="the latest reply"),)),
        ),
    )
    lines = history_directives(conversation, frozenset(), frozenset())
    assert lines == (
        b"you\twhat is up\n",
        b"say\tnot much\n",
        b"you\ttell me more\n",
    )


def test_history_strips_the_prompt_envelope_from_member_lines() -> None:
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation

    composed = (
        "<context>\n"
        "message_ref: 142f1dd4-8c24-5faa-9c20-b760d238ecb1\n"
        "time: Sunday 2026-08-16 23:04 EDT\n"
        "sender: alex@example.com\n"
        "source: ufo cli (alex@example.com)\n"
        "</context>\n"
        "sup\n"
        "\n<injected_context>\nRelevant memory:\n- a fact worth recalling\n</injected_context>"
    )
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=composed),
            Message(role="assistant", content=(TextBlock(text="not much"),)),
            Message(role="user", content=(TextBlock(text=_framed("tell me more")),)),
            Message(role="assistant", content=(TextBlock(text="the latest reply"),)),
        ),
    )
    lines = history_directives(conversation, frozenset(), frozenset())
    assert lines == (
        b"you\tsup\n",
        b"say\tnot much\n",
        b"you\ttell me more\n",
    )


def test_history_counts_no_step_for_a_call_that_never_dispatched() -> None:
    from ufo.harness.models.interface import Message, TextBlock, ToolUseBlock
    from ufo.runtime.turns.transcript import Conversation

    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=_framed("try it")),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="I cannot run that."),
                    ToolUseBlock(id="call-1", name="bash", input={}),
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="Nothing ran."),)),
            Message(role="user", content=_framed("understood")),
        ),
    )
    lines = history_directives(conversation, frozenset(), frozenset())
    assert lines == (
        b"you\ttry it\n",
        b"note\tCompleted 1 step\n",
        b"say\tNothing ran.\n",
        b"you\tunderstood\n",
    )


def test_history_budget_keeps_the_newest_messages() -> None:
    from ufo.harness.models.interface import Message
    from ufo.runtime.turns.transcript import Conversation

    old = Message(role="user", content=_framed("a" * 30_000))
    new = Message(role="user", content=_framed("the recent one"))
    conversation = Conversation(seq=1, messages=(old, new))
    lines = history_directives(conversation, frozenset(), frozenset())
    assert lines == (b"you\tthe recent one\n",)


def test_history_leaves_only_the_latest_reply_to_the_tail() -> None:
    """A reply a machine's admission followed is history like any other: only the last reply —
    the one the tail replays — is left off, not every reply since the member last spoke."""
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation

    spawned = "6bff4092-5ffc-55f4-8af1-c34089930bf4"
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=_framed("look into the pager rotation")),
            Message(role="assistant", content=(TextBlock(text="Spawning a look."),)),
            Message(
                role="user",
                content=_framed('<spawn_result status="done">Nate</spawn_result>', ref=spawned),
            ),
            Message(role="assistant", content=(TextBlock(text="Nate owns the pager."),)),
        ),
    )
    assert history_directives(conversation, frozenset({spawned}), frozenset()) == (
        b"you\tlook into the pager rotation\n",
        b"say\tSpawning a look.\n",
    )


def test_history_hides_what_no_member_said() -> None:
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation

    spawned = "6bff4092-5ffc-55f4-8af1-c34089930bf4"
    fired = "0f3c2a1e-1111-5222-8333-444455556666"
    woken = "7a1b2c3d-2222-5333-8444-555566667777"
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content=_framed("look into the pager rotation")),
            Message(role="assistant", content=(TextBlock(text="Spawning a look."),)),
            Message(
                role="user",
                content=_framed(
                    '<spawn_result target="profile:coding" spawn_id="x" status="done">'
                    '{"result":"Nate owns it"}</spawn_result>',
                    ref=spawned,
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="Nate owns the pager."),)),
            Message(
                role="user", content="<interrupted_turn>The turn above ended.</interrupted_turn>"
            ),
            Message(role="assistant", content=(TextBlock(text="Picking up where it stopped."),)),
            Message(
                role="user",
                content=_framed(
                    "<scheduled_task> scheduled_fire: 2026-09-06T15:17:00Z </scheduled_task> check",
                    ref=fired,
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="Checked."),)),
            Message(
                role="user",
                content=_framed(
                    with_agent_detail("github: Fix the build updated", "object_get refs: page/1."),
                    ref=woken,
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="Reviewed."),)),
            Message(role="user", content=_framed("thanks")),
        ),
    )
    lines = history_directives(conversation, frozenset({spawned, fired}), frozenset({woken}))
    assert lines == (
        b"you\tlook into the pager rotation\n",
        b"say\tSpawning a look.\n",
        b"say\tNate owns the pager.\n",
        b"say\tPicking up where it stopped.\n",
        b"say\tChecked.\n",
        b"fired\tgithub: Fix the build updated\n",
        b"say\tReviewed.\n",
        b"you\tthanks\n",
    )


async def test_a_fresh_resume_replays_history_and_a_poll_does_not(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation, encode, transcript_key

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
            Message(role="user", content=_framed("what is up")),
            Message(role="assistant", content=(TextBlock(text="the reply"),)),
        ),
    )
    await blob.put(transcript_key(conversation_id), encode(conversation))

    fresh = await _post(client, "main", token, b"")
    assert ["you", "what is up"] in fresh
    said = [
        line
        for line in fresh
        if (line[0] == "say" and line[1] == "the reply")
        or (line[:2] == ["frame", "terminal"] and _frame_text(line) == "the reply")
    ]
    assert len(said) == 1, f"the tail carries the reply once, history never doubles it: {fresh}"

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
    assert "the reply" in [
        _frame_text(line) for line in polled if line[:2] == ["frame", "terminal"]
    ], f"an unmarked reconnect may hold a mid-turn cursor, so it drains the durable end: {polled}"
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


async def test_a_prompted_stream_says_the_work_the_turn_left_running(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    turn_id, status = await _sole_turn(workspace_id)
    assert status == "done"
    launched = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.detached_task).values(
                workspace_id=workspace_id,
                turn_id=turn_id,
                conversation_id=conversation_id,
                sandbox_conversation_id=conversation_id,
                task="t1",
                runtime_base="/runs/x/tasks/t1",
                follow_until=datetime.now(UTC) + timedelta(hours=1),
                created_at=launched,
            )
        )
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(detached_until=datetime.now(UTC) + timedelta(hours=1))
        )
    working = ["working", "Awaiting a background task", f"{launched.timestamp():.0f}"]

    polled = await _post(client, "main", token, b"")
    assert polled[-2:] == [working, ["listen", "2"]], polled

    headers = {
        "authorization": f"Bearer {token}",
        "x-ufo-since": f"{turn_id}:",
        "x-ufo-listen": "1",
    }
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        bounced = await client.post("/surface/ufo/main", content=b"", headers=headers)
    assert _lines(bounced.content) == [["since", str(turn_id), ""], working, ["listen", "2"]]

    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.detached_task).where(tables.detached_task.c.turn_id == turn_id)
        )
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(detached_until=None)
        )
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        cleared = await client.post("/surface/ufo/main", content=b"", headers=headers)
    assert _lines(cleared.content) == [["since", str(turn_id), ""], ["listen", "2"]]


async def test_a_marked_bounce_renders_a_refused_wakeup_without_exiting(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    """A cap refuses a turn the member never founded — a scheduled firing, say — and the idle
    terminal's next bounce renders the refusal."""
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


async def test_a_member_downloads_a_file_from_their_own_channels_workspace(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id = ufo
    _config, _hub, _blob, sandboxes = runtime
    await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "peer@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    bearer = {"authorization": f"Bearer {token}"}

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        admitted = await client.post("/surface/ufo/artifacts", content=b"hello", headers=bearer)
    assert admitted.status_code == 200
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key == "owner@example.com:artifacts",
                )
            )
        ).scalar_one()
    with ws(workspace_id):
        await sandboxes.write(conversation_id, "report/out.txt", b"hello world")

    served = await client.get("/surface/ufo/artifacts/file/report/out.txt", headers=bearer)
    assert served.status_code == 200
    assert served.content == b"hello world"
    assert served.headers["content-type"] == "application/octet-stream"

    absent = await client.get("/surface/ufo/artifacts/file/report/absent.txt", headers=bearer)
    assert absent.status_code == 404
    escaping = await client.get("/surface/ufo/artifacts/file/../messages.json.lz4", headers=bearer)
    assert escaping.status_code == 404
    silent = await client.get("/surface/ufo/never-spoke/file/report/out.txt", headers=bearer)
    assert silent.status_code == 404
    naked = await client.get("/surface/ufo/artifacts/file/report/out.txt")
    assert naked.status_code == 401

    peer = _mint(SECRET, workspace_id, "peer@example.com", _future())
    other = await client.get(
        "/surface/ufo/artifacts/file/report/out.txt",
        headers={"authorization": f"Bearer {peer}"},
    )
    assert other.status_code == 404


def test_terminal_runtime_id_matches_the_client() -> None:
    assert terminal_runtime_id("conversation") == "8b34dbc2c05eb4d7e25d48efeace8245"


async def _seed_conversation_row(
    workspace_id: UUID,
    agent_id: UUID,
    *,
    surface: str,
    queue_key: str,
    audience: str,
    member_id: UUID | None,
    title: str,
    surface_label: str | None = None,
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=queue_key,
                member_id=member_id,
                audience=audience,
                surface_label=surface_label,
                title=title,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _seed_done_turn(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    *,
    inbound: str,
    speaker_member_id: UUID | None,
    admission_source: str = "member",
    sender: str | None = None,
    seq: int = 1,
    reply: str = "ok",
    question: AskUserInput | None = None,
) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status="done",
                inbound=inbound,
                admission_source=admission_source,
                speaker_member_id=speaker_member_id,
                context=None if sender is None else {"sender": sender},
                terminal=TerminalFrame(status="done", text=reply, question=question).model_dump(
                    mode="json"
                ),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_a_joined_terminal_routes_a_targeted_question_to_its_member(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "peer@example.com")
    main = await _main_agent_id(workspace_id)
    shared = await _seed_conversation_row(
        workspace_id,
        main,
        surface="slack",
        queue_key="C1:1.0",
        audience="shared",
        member_id=None,
        title="Deploy approval",
        surface_label="#eng",
    )
    question = AskUserInput(
        title="Permission required",
        questions=(
            AskQuestion(
                question="Allow this request?",
                options=(QuestionOption(label="Allow"), QuestionOption(label="Deny")),
            ),
        ),
        target_member_id=owner,
    )
    await _seed_done_turn(
        workspace_id,
        shared,
        main,
        inbound="Deploy it",
        speaker_member_id=owner,
        reply="Waiting for a decision.",
        question=question,
    )

    async def open_as(email: str) -> list[list[str]]:
        token = _mint(SECRET, workspace_id, email, _future())
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            response = await client.post(
                f"/surface/ufo/conversation/{shared}",
                content=b"",
                headers={"authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        return _lines(response.content)

    target_lines = await open_as("owner@example.com")
    peer_lines = await open_as("peer@example.com")

    assert ["say", "Permission required"] in target_lines
    assert ["choose", "Allow this request?", "Allow", "Deny"] in target_lines
    assert ["say", "Permission required"] not in peer_lines
    assert not any(line[0] in {"choose", "choose_many"} for line in peer_lines)
    assert "Waiting for a decision." in [
        _frame_text(line) for line in peer_lines if line[:2] == ["frame", "terminal"]
    ]
    assert not any("Allow this request?" in field for line in peer_lines for field in line)


async def test_terminal_authorization_carries_identity_and_choices_only_to_target(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "peer@example.com")
    main = await _main_agent_id(workspace_id)
    shared = await _seed_conversation_row(
        workspace_id,
        main,
        surface="slack",
        queue_key="C1:2.0",
        audience="shared",
        member_id=None,
        title="Database access",
        surface_label="#eng",
    )
    authorization_id = uuid4()
    question = AskUserInput(
        title="Permission required",
        questions=(
            AskQuestion(
                question="Run this database change?",
                options=(
                    QuestionOption(label="Proceed once", authorization_choice="allow"),
                    QuestionOption(label="Do not proceed", authorization_choice="deny"),
                    QuestionOption(label="Proceed every time", authorization_choice="always"),
                ),
            ),
        ),
        target_member_id=owner,
        authorization_id=authorization_id,
    )
    await _seed_done_turn(
        workspace_id,
        shared,
        main,
        inbound="Change it",
        speaker_member_id=owner,
        reply="Waiting for a decision.",
        question=question,
    )

    async def open_as(email: str) -> list[list[str]]:
        token = _mint(SECRET, workspace_id, email, _future())
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            response = await client.post(
                f"/surface/ufo/conversation/{shared}",
                content=b"",
                headers={"authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        return _lines(response.content)

    target_lines = await open_as("owner@example.com")
    peer_lines = await open_as("peer@example.com")
    assert [
        "authorize",
        str(authorization_id),
        "Run this database change?",
        "allow",
        "Proceed once",
        "",
        "deny",
        "Do not proceed",
        "",
        "always",
        "Proceed every time",
        "",
    ] in target_lines
    assert not any(line[0] == "authorize" for line in peer_lines)
    assert ["say", "Permission required"] not in peer_lines


async def _seed_agent(
    workspace_id: UUID, name: str, *, visibility: str, owner_member_id: UUID | None = None
) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=False,
                visibility=visibility,
                owner_member_id=owner_member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _main_agent_id(workspace_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id, tables.agent.c.is_main
                )
            )
        ).scalar_one()


async def test_conversations_lists_every_surface_and_agent_the_member_reaches(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    nate = await _seed_member(workspace_id, "nate@example.com")
    main = await _main_agent_id(workspace_id)
    owned = await _seed_agent(workspace_id, "notes", visibility="private", owner_member_id=owner)
    walled = await _seed_agent(workspace_id, "finance", visibility="private", owner_member_id=nate)
    mine = str(conversation_audience(owner))
    web = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/owner@example.com/1",
        audience=mine,
        member_id=owner,
        title="Deploy plan",
    )
    await _seed_done_turn(workspace_id, web, main, inbound="Deploy plan", speaker_member_id=owner)
    slack = await _seed_conversation_row(
        workspace_id,
        main,
        surface="slack",
        queue_key="C1:1.0",
        audience="shared",
        member_id=None,
        title="Who owns the pager",
        surface_label="#eng",
    )
    await _seed_done_turn(
        workspace_id,
        slack,
        main,
        inbound="Who owns the pager",
        speaker_member_id=nate,
        sender="Nate Ford (nate@example.com)",
    )
    terminal = await _seed_conversation_row(
        workspace_id,
        main,
        surface="ufo",
        queue_key="owner@example.com:abc123",
        audience=mine,
        member_id=owner,
        title="list files",
    )
    await _seed_done_turn(
        workspace_id, terminal, main, inbound="list files", speaker_member_id=owner
    )
    texts = await _seed_conversation_row(
        workspace_id,
        main,
        surface="imessage",
        queue_key="+15551234567",
        audience=mine,
        member_id=owner,
        title="remind me at 5",
    )
    await _seed_done_turn(
        workspace_id, texts, main, inbound="remind me at 5", speaker_member_id=owner
    )
    notes = await _seed_conversation_row(
        workspace_id,
        owned,
        surface="web",
        queue_key=f"{owned}/owner@example.com/1",
        audience=mine,
        member_id=owner,
        title="Meeting notes",
    )
    await _seed_done_turn(
        workspace_id, notes, owned, inbound="Meeting notes", speaker_member_id=owner
    )
    lane = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"intent/{main}/owner@example.com",
        audience=mine,
        member_id=owner,
        title="Portal actions",
    )
    await _seed_done_turn(
        workspace_id, lane, main, inbound="{}", speaker_member_id=owner, admission_source="intent"
    )
    theirs = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/nate@example.com/1",
        audience=str(conversation_audience(nate)),
        member_id=nate,
        title="Nate's plan",
    )
    await _seed_done_turn(workspace_id, theirs, main, inbound="Nate's plan", speaker_member_id=nate)
    ledger = await _seed_conversation_row(
        workspace_id,
        walled,
        surface="web",
        queue_key=f"{walled}/owner@example.com/1",
        audience=mine,
        member_id=owner,
        title="Ledger",
    )
    await _seed_done_turn(workspace_id, ledger, walled, inbound="Ledger", speaker_member_id=owner)
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())

    listed = await client.get(
        "/surface/ufo/conversations", headers={"authorization": f"Bearer {token}"}
    )

    assert listed.status_code == 200
    rows = {row["id"]: row for row in listed.json()["conversations"]}
    assert set(rows) == {str(web), str(slack), str(terminal), str(texts), str(notes)}
    assert rows[str(web)] == {
        "id": str(web),
        "title": "Deploy plan",
        "surface": "web",
        "surface_label": None,
        "speaker": None,
        "agent": "assistant",
        "main": True,
        "last_at": rows[str(web)]["last_at"],
        "postable": True,
        "channel": None,
        "turn": "idle",
        "unread": False,
    }
    assert rows[str(slack)]["speaker"] == "Nate Ford"
    assert rows[str(slack)]["surface_label"] == "#eng"
    assert rows[str(slack)]["postable"] is True
    assert rows[str(slack)]["unread"] is True
    assert rows[str(terminal)]["channel"] == "abc123"
    assert rows[str(texts)]["postable"] is False
    assert rows[str(notes)]["agent"] == "notes"
    assert rows[str(notes)]["main"] is False

    searched = await client.get(
        "/surface/ufo/conversations",
        params={"q": "pager"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert [row["id"] for row in searched.json()["conversations"]] == [str(slack)]

    stranger = _mint(SECRET, workspace_id, "stranger@example.com", _future())
    unseated = await client.get(
        "/surface/ufo/conversations", headers={"authorization": f"Bearer {stranger}"}
    )
    assert unseated.status_code == 403
    naked = await client.get("/surface/ufo/conversations")
    assert naked.status_code == 401


async def test_a_conversation_opened_by_id_replays_then_admits_a_comment(
    ufo: tuple[AsyncClient, UUID],
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """Opening a Slack thread from the terminal reads like a resume: history as `you` and `say`,
    then the prompt."""
    from ufo.harness.models.interface import Message, TextBlock
    from ufo.runtime.turns.transcript import Conversation, encode, transcript_key

    client, workspace_id = ufo
    blob = runtime[2]
    owner = await _seed_member(workspace_id, "owner@example.com")
    nate = await _seed_member(workspace_id, "nate@example.com")
    main = await _main_agent_id(workspace_id)
    slack = await _seed_conversation_row(
        workspace_id,
        main,
        surface="slack",
        queue_key="C1:1.0",
        audience="shared",
        member_id=None,
        title="Who owns the pager",
        surface_label="#eng",
    )
    await _seed_done_turn(
        workspace_id,
        slack,
        main,
        inbound="Who owns the pager",
        speaker_member_id=nate,
        sender="Nate Ford (nate@example.com)",
        reply="Nate does.",
    )
    await blob.put(
        transcript_key(slack),
        encode(
            Conversation(
                seq=1,
                messages=(
                    Message(role="user", content=_framed("Who owns the pager")),
                    Message(role="assistant", content=(TextBlock(text="Nate does."),)),
                ),
            )
        ),
    )
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    bearer = {"authorization": f"Bearer {token}"}

    async def unread() -> bool:
        listed = await client.get("/surface/ufo/conversations", headers=bearer)
        return {row["id"]: row["unread"] for row in listed.json()["conversations"]}[str(slack)]

    assert await unread() is True
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        opened = await client.post(
            f"/surface/ufo/conversation/{slack}", content=b"", headers=bearer
        )
    assert opened.status_code == 200
    replayed = _lines(opened.content)
    assert replayed[0] == ["you", "Who owns the pager"]
    assert "Nate does." in [
        _frame_text(line) for line in replayed if line[:2] == ["frame", "terminal"]
    ]
    assert ["ask", ">"] in replayed
    assert await unread() is False, "holding the stream read the conversation"

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        posted = await client.post(
            f"/surface/ufo/conversation/{slack}", content=b"I do, this week.", headers=bearer
        )
    assert posted.status_code == 200
    lines = _lines(posted.content)
    assert lines[0][0] == "sent"
    assert "echo:" in _answer(lines)
    assert not any("commented" in field for line in lines for field in line), lines
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(tables.turn.c.speaker_member_id, tables.turn.c.admission_source).where(
                    tables.turn.c.conversation_id == slack, tables.turn.c.seq == 2
                )
            )
        ).one()
        comment = (
            await connection.execute(
                sa.select(tables.mid_turn_reply.c.text, tables.mid_turn_reply.c.round_index).where(
                    tables.mid_turn_reply.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (turn.speaker_member_id, turn.admission_source) == (owner, "member")
    assert (comment.text, comment.round_index) == (
        "owner@example.com commented: I do, this week.",
        -1,
    )


async def test_a_held_stream_reads_the_conversation_again_when_it_ends(
    ufo: tuple[AsyncClient, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cursor the list draws unread against moves twice on one stream: when the member opens
    it and again when its last line is rendered."""
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    main = await _main_agent_id(workspace_id)
    web = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/owner@example.com/1",
        audience=str(conversation_audience(owner)),
        member_id=owner,
        title="Deploy plan",
    )
    turn = await _seed_running_turn(workspace_id, web, owner)
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    bearer = {"authorization": f"Bearer {token}"}
    monkeypatch.setattr("ufo_ext_ufo.surface.HOLD_SECONDS", HELD_STREAM_SECONDS)

    async def read_at() -> datetime:
        async with workspace_tx() as connection:
            cursor: datetime = (
                await connection.execute(
                    sa.select(tables.conversation_read.c.read_at).where(
                        tables.conversation_read.c.conversation_id == web,
                        tables.conversation_read.c.member_id == owner,
                    )
                )
            ).scalar_one()
        return cursor if cursor.tzinfo is not None else cursor.replace(tzinfo=UTC)

    held = asyncio.ensure_future(
        client.post(f"/surface/ufo/conversation/{web}", content=b"", headers=bearer)
    )
    await asyncio.sleep(HELD_STREAM_SECONDS / 3)
    opened = await read_at()
    moved = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn).values(updated_at=moved)
        )
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        assert (await held).status_code == 200

    assert opened < moved, "the turn moved after the stream opened"
    listed = await client.get("/surface/ufo/conversations", headers=bearer)
    rows = {row["id"]: row for row in listed.json()["conversations"]}
    assert rows[str(web)]["unread"] is False, "the ended stream read what it rendered"


async def test_a_conversation_opened_by_id_admits_plainly_into_the_members_own_web_chat(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    main = await _main_agent_id(workspace_id)
    web = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/owner@example.com/1",
        audience=str(conversation_audience(owner)),
        member_id=owner,
        title="Deploy plan",
    )
    await _seed_done_turn(workspace_id, web, main, inbound="Deploy plan", speaker_member_id=owner)
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())

    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        posted = await client.post(
            f"/surface/ufo/conversation/{web}",
            content=b"ship it",
            headers={"authorization": f"Bearer {token}"},
        )

    assert posted.status_code == 200
    async with workspace_tx() as connection:
        comments = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.mid_turn_reply))
        ).scalar_one()
        seqs = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.seq).where(tables.turn.c.conversation_id == web)
                )
            )
            .scalars()
            .all()
        )
    assert comments == 0
    assert sorted(seqs) == [1, 2]


async def test_a_conversation_opened_by_id_admits_plainly_into_a_shared_web_chat(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    nate = await _seed_member(workspace_id, "nate@example.com")
    main = await _main_agent_id(workspace_id)
    web = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/nate@example.com/2",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        title="Launch checklist",
    )
    await _seed_done_turn(
        workspace_id, web, main, inbound="Launch checklist", speaker_member_id=nate
    )
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    bearer = {"authorization": f"Bearer {token}"}

    listed = await client.get("/surface/ufo/conversations", headers=bearer)
    assert listed.status_code == 200
    rows = {row["id"]: row for row in listed.json()["conversations"]}
    assert rows[str(web)]["postable"] is True
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        posted = await client.post(
            f"/surface/ufo/conversation/{web}", content=b"add the rollback step", headers=bearer
        )

    assert posted.status_code == 200
    async with workspace_tx() as connection:
        comments = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.mid_turn_reply))
        ).scalar_one()
        seqs = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.seq).where(tables.turn.c.conversation_id == web)
                )
            )
            .scalars()
            .all()
        )
    assert comments == 0
    assert sorted(seqs) == [1, 2]


async def test_a_conversation_opened_by_id_refuses_what_the_wall_and_the_surface_forbid(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    owner = await _seed_member(workspace_id, "owner@example.com")
    nate = await _seed_member(workspace_id, "nate@example.com")
    main = await _main_agent_id(workspace_id)
    walled = await _seed_agent(workspace_id, "finance", visibility="private", owner_member_id=nate)
    mine = str(conversation_audience(owner))
    theirs = await _seed_conversation_row(
        workspace_id,
        main,
        surface="web",
        queue_key=f"{main}/nate@example.com/1",
        audience=str(conversation_audience(nate)),
        member_id=nate,
        title="Nate's plan",
    )
    await _seed_done_turn(workspace_id, theirs, main, inbound="Nate's plan", speaker_member_id=nate)
    ledger = await _seed_conversation_row(
        workspace_id,
        walled,
        surface="web",
        queue_key=f"{walled}/owner@example.com/1",
        audience=mine,
        member_id=owner,
        title="Ledger",
    )
    await _seed_done_turn(workspace_id, ledger, walled, inbound="Ledger", speaker_member_id=owner)
    texts = await _seed_conversation_row(
        workspace_id,
        main,
        surface="imessage",
        queue_key="+15551234567",
        audience=mine,
        member_id=owner,
        title="remind me at 5",
    )
    await _seed_done_turn(
        workspace_id, texts, main, inbound="remind me at 5", speaker_member_id=owner
    )
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    bearer = {"authorization": f"Bearer {token}"}

    async def post(conversation: str, body: bytes, **extra: str) -> Response:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            return await client.post(
                f"/surface/ufo/conversation/{conversation}",
                content=body,
                headers={
                    **bearer,
                    **{key.replace("_", "-"): value for key, value in extra.items()},
                },
            )

    assert (await post(str(theirs), b"")).status_code == 404
    assert (await post(str(ledger), b"")).status_code == 404
    assert (await post("not-a-uuid", b"")).status_code == 404
    opened = await post(str(texts), b"")
    assert opened.status_code == 200
    assert ["ask", ">"] in _lines(opened.content)
    refused = await post(str(texts), b"at 6 instead")
    assert refused.status_code == 403
    assert refused.text == "This conversation is read-only here. Reply in iMessage to continue it."
    claimed = await post(str(texts), b"", x_ufo_cwd="/tmp")
    assert claimed.status_code == 400
    stranger = _mint(SECRET, workspace_id, "stranger@example.com", _future())
    assert (await post(str(texts), b"", authorization=f"Bearer {stranger}")).status_code == 403
    assert await _turn_count(workspace_id) == 3


def test_a_run_directive_names_the_call_its_op_serves() -> None:
    """The op rides down with the tool call it serves, so the client binds the call's label to the
    row that ran it; an op the runtime issued for itself names no call."""
    op = TerminalOp(
        op_id="op-1",
        kind="exec",
        timeout_s=120,
        name="exec",
        params='{"argv":["ls"]}',
        call_id="c1",
    )
    assert _run(op) == b'run\top-1\texec\texec\t120\t\t{"argv":["ls"]}\tc1\n'
    runtime = TerminalOp(op_id="op-2", kind="write", timeout_s=30, arg="/p/a")
    assert _run(runtime) == b"run\top-2\twrite\t\t30\t/p/a\t\t\n"
