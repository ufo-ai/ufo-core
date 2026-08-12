import asyncio
import json
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_todos as todos
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from PIL import Image
from pydantic import BaseModel, ValidationError
from ufo_ext_coding.review_checkout import CodeReviewFinding, CodeReviewOutput
from ufo_ext_connectors.manifest import manifest as connectors_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import recall_subjects
from ufo_ext_scheduled_tasks.conversation_slot import AUTOMATIONS_SLOT
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_OBJECT
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import site_object_name
from ufo_ext_sites.store import HostedSites, hosted_site
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import user_skill
from ufo_ext_sources.manifest import manifest as sources_manifest
from ufo_ext_web import community as web_community
from ufo_ext_web import panels as web_panels
from ufo_ext_web import surface as web_surface
from ufo_ext_web.audience import AUDIENCE_PREFIX, EXTENSION_WEB, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.panels import _outcome
from ufo_ext_web.surface import (
    NO_MEMBER_FAULT,
    PORTAL_BUILD,
    PORTAL_FILE,
    PORTAL_HTML,
    SESSION_COOKIE,
    SESSION_FAULT_HEADER,
    SubagentNode,
    _rendered_messages,
    _run_answer,
    _sse,
    _subagent_activity,
    load_assets,
)
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    NO_SUBAGENTS,
    UNREACHED_AMBIENT_REPLY,
    no_user_skills,
)

from ufo.accounting import record_egress_request, record_turn_usage
from ufo.agent_scope import agent as bind_agent
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import (
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore, context_for
from ufo.ext.loader import member_object_registry, skill_registry, turn_runtime_skills
from ufo.ext.surface import (
    fence_member_message,
    mint_marker,
    record_transcript_access,
)
from ufo.grants import (
    ConnectFlow,
    GrantStore,
    OAuthAccount,
    account_object_name,
    install_connect_flow,
)
from ufo.hub import InProcessHub, LiveFrame, SkillLoad, Terminal, ToolCall
from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES
from ufo.loop import queue as loop_queue
from ufo.loop.engine import FINISH_PROMPT
from ufo.loop.subagents import FINISH_CONTRACT, SubagentRegistry, subagent_system_prompt
from ufo.loop.transcript import Transcript
from ufo.members import ADD_MEMBER_GATE
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import (
    Message,
    ModelEvent,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.models.registry import ModelRegistry
from ufo.objects import OBJECT_LIST_PAGE
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.scheduling import ScheduledTask, ScheduleStore, TaskInspection
from ufo.schema import tables
from ufo.schema.records import (
    SUBAGENT_SURFACE,
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    CredentialPrompt,
    CredentialRequest,
    QuestionOption,
    TerminalFrame,
    TurnContext,
    Usage,
)
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.jobs import store_key_workspaces
from ufo.sdk.manifest import CredentialSlot, Manifest, SubagentProfile
from ufo.sdk.seats import Seats
from ufo.serve import _mount_shared_surfaces
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.surfaces import hub_tail
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.tools.context import ToolContext
from ufo.transcript import Conversation
from ufo.workspace import ws
from ufo.workspace_changes import WorkspaceChange, WorkspaceChanges

SECRET = "artifact-signing-secret"


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, format="PNG")
    return output.getvalue()


SCHEDULED_TASK_KIND_ONLY = Manifest(
    name="scheduled_tasks",
    version="0.1.0",
    objects=(SCHEDULED_TASK_OBJECT,),
    conversation_slots=(AUTOMATIONS_SLOT,),
)
SLOTTED = Manifest(
    name="stub",
    version="0",
    credentials=(
        CredentialSlot(name="acme_api_key", description="ACME API key"),
        CredentialSlot(
            name="acme_install_seal",
            description="ACME install binding",
            member_filled=False,
        ),
    ),
)


class ProbeTask(BaseModel):
    task: str


class ProbeResult(BaseModel):
    result: str


def _profile(name: str, model: str | None, rounds: int) -> SubagentProfile:
    return SubagentProfile(
        name=name,
        prompt="be focused",
        tool_names=("read",),
        input_model=ProbeTask,
        output_model=ProbeResult,
        model=model,
        max_rounds=rounds,
    )


PORTAL_SUBAGENTS = SubagentRegistry(
    (_profile("general_purpose", None, 12), _profile("deep_research", "claude-opus-4-8", 40))
)
TOKEN_SECRET = "web-token-secret"
STREAM_TIMEOUT_SECONDS = 30
STREAM_GATE = StreamGate()
CREDENTIAL_FERNET = Fernet(Fernet.generate_key())


def test_sse_tags_tool_and_skill_activity_frames() -> None:
    tool = _sse("7", ToolCall(tool="bash", preview='{"command":"ls"}'))
    assert tool.startswith(b"id: 7\nevent: tool\ndata: ")
    assert json.loads(tool.split(b"data: ", 1)[1]) == {
        "tool": "bash",
        "preview": '{"command":"ls"}',
        "description": "",
    }
    skill = _sse("", SkillLoad(skill="demo"))
    assert skill.startswith(b"event: skill\ndata: ")
    assert json.loads(skill.split(b"data: ", 1)[1]) == {"skill": "demo"}


def test_transcript_projection_keeps_tool_activity_and_elides_results() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nInspect it."),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Let me check."),
                    ToolUseBlock(
                        id="call-1",
                        name="bash",
                        input={
                            "command": "uv run pytest",
                            "user_description": "Running the focused tests",
                            "requested_by": "internal-message-ref",
                        },
                    ),
                    ToolUseBlock(id="call-2", name="load_skill", input={"name": "coding"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="call-1", content="1 passed", activity=True),
                    ToolResultBlock(tool_use_id="call-2", content="mounted", activity=True),
                ),
            ),
            Message(role="user", content="End the turn now."),
            Message(role="assistant", content=(TextBlock(text="The tests pass."),)),
        )
    )

    assert rendered == [
        {"role": "user", "text": "Inspect it."},
        {
            "role": "assistant",
            "text": "The tests pass.",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": (
                        '{"command":"uv run pytest","user_description":"Running the focused tests"}'
                    ),
                    "description": "Running the focused tests",
                },
                {
                    "kind": "skill",
                    "name": "coding",
                    "preview": "",
                    "description": "",
                },
            ],
        },
    ]


def test_subagent_activity_keeps_the_text_a_run_wrote_between_its_calls() -> None:
    """A subagent's own screen is its work in order — what it said, then what it did."""
    events = _subagent_activity(
        (
            Message(role="user", content="{}"),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="  Reading the changelog first.  "),
                    ToolUseBlock(id="call-1", name="fetch_url", input={"url": "https://x/y"}),
                    ToolUseBlock(id="call-2", name="grep", input={"pattern": "shipped"}),
                ),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id="call-1", content="…", activity=True),),
            ),
        )
    )

    assert events == [
        {"kind": "note", "text": "Reading the changelog first."},
        {
            "kind": "tool",
            "name": "fetch_url",
            "preview": '{"url":"https://x/y"}',
            "description": "",
        },
    ]


FORCE_FINISHED_PROSE = "The filing deadline is March 31."
FORCE_FINISHED_PAYLOAD = json.dumps({"result": FORCE_FINISHED_PROSE})
FORCE_FINISHED_RUN = (
    Message(role="user", content='{"task": "find the deadline"}'),
    Message(
        role="assistant",
        content=(ToolUseBlock(id="call-1", name="fetch_url", input={"url": "https://x/y"}),),
    ),
    Message(
        role="user",
        content=(ToolResultBlock(tool_use_id="call-1", content="…", activity=True),),
    ),
    Message(role="assistant", content=FORCE_FINISHED_PROSE),
    Message(role="user", content=FINISH_PROMPT),
    Message(role="assistant", content=FORCE_FINISHED_PAYLOAD),
)


def test_a_force_finished_run_states_its_prose_once() -> None:
    """A child that stops on prose is force-finished over it, so the engine appends that prose to
    the transcript and `persist_transcript` closes with the finish payload — the same words land
    durably twice. Work is read from blocks and both are string content, so the tree states the
    call it made and the answer states the prose, each exactly once."""
    events = _subagent_activity(FORCE_FINISHED_RUN)
    output = _run_answer(FORCE_FINISHED_PAYLOAD)

    assert events == [
        {
            "kind": "tool",
            "name": "fetch_url",
            "preview": '{"url":"https://x/y"}',
            "description": "",
        }
    ]
    assert output == FORCE_FINISHED_PROSE
    assert [event for event in events if event.get("text") == FORCE_FINISHED_PROSE] == []


def test_a_run_answer_reads_the_fields_it_wrote_never_the_json_carrying_them() -> None:
    assert _run_answer('{"result": "It shipped Tuesday."}') == "It shipped Tuesday."
    assert _run_answer('{"result": "It shipped Tuesday.", "confidence": 3}') == (
        "**Result** — It shipped Tuesday.\n**Confidence** — 3"
    )
    assert _run_answer("ran out of rounds") == "ran out of rounds"
    assert _run_answer("") == ""


def test_transcript_projection_does_not_move_activity_between_turns() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="call-1", name="bash", input={"command": "false"}),),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1", content="exit 1", is_error=True, activity=True
                    ),
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="It failed."),)),
            Message(role="user", content="<context>source: web</context>\nTry something else."),
            Message(role="assistant", content=(TextBlock(text="Done."),)),
        )
    )

    assert rendered == [
        {"role": "user", "text": "Run it."},
        {
            "role": "assistant",
            "text": "It failed.",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": '{"command":"false"}',
                    "description": "",
                }
            ],
        },
        {"role": "user", "text": "Try something else."},
        {"role": "assistant", "text": "Done."},
    ]


def test_transcript_projection_flushes_activity_for_an_empty_answer() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="call-1", name="bash", input={"command": "true"}),),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id="call-1", content="", activity=True),),
            ),
            Message(role="assistant", content=""),
            Message(role="user", content="<context>source: web</context>\nContinue."),
            Message(role="assistant", content="Done."),
        )
    )

    assert rendered == [
        {"role": "user", "text": "Run it."},
        {
            "role": "assistant",
            "text": "",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": '{"command":"true"}',
                    "description": "",
                }
            ],
        },
        {"role": "user", "text": "Continue."},
        {"role": "assistant", "text": "Done."},
    ]


def test_transcript_projection_bounds_trailing_activity_and_skips_rejected_calls() -> None:
    command = "é" * 100
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="accepted", name="bash", input={"command": command}),
                    ToolUseBlock(id="rejected", name="bash", input={"command": "hidden"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="accepted", content="done", activity=True),
                    ToolResultBlock(tool_use_id="rejected", content="ValueError", is_error=True),
                ),
            ),
        )
    )

    expected = json.dumps({"command": command}, separators=(",", ":"))
    assert rendered == [
        {"role": "user", "text": "Run it."},
        {
            "role": "assistant",
            "text": "",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": expected[:200] + "…",
                    "description": "",
                }
            ],
        },
    ]


def test_transcript_projection_guards_activity_labels() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="skill", name="load_skill", input={"name": 7}),
                    ToolUseBlock(id="tool", name="bash", input={"user_description": 7}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="skill", content="done", activity=True),
                    ToolResultBlock(tool_use_id="tool", content="done", activity=True),
                ),
            ),
            Message(role="assistant", content="Done."),
        )
    )

    assert rendered[-1] == {
        "role": "assistant",
        "text": "Done.",
        "events": [
            {"kind": "skill", "name": "", "preview": "", "description": ""},
            {
                "kind": "tool",
                "name": "bash",
                "preview": '{"user_description":7}',
                "description": "",
            },
        ],
    }


def test_transcript_projection_places_child_conversations_on_their_parent_replies() -> None:
    answered, failed = uuid4(), uuid4()
    answered_child, failed_child = uuid4(), uuid4()
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {answered}\n</context>\nFirst.",
            ),
            Message(role="assistant", content="Done."),
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {failed}\n</context>\nSecond.",
            ),
        ),
        {
            str(answered): [_node("general_purpose", answered_child)],
            str(failed): [_node("deep_research", failed_child)],
        },
        frozenset((str(answered), str(failed))),
    )

    assert rendered == [
        {"role": "user", "text": "First."},
        {
            "role": "assistant",
            "text": "Done.",
            "subagents": [_node("general_purpose", answered_child)],
        },
        {"role": "user", "text": "Second."},
        {
            "role": "assistant",
            "text": "",
            "subagents": [_node("deep_research", failed_child)],
        },
    ]


def _node(profile: str, conversation_id: UUID) -> SubagentNode:
    return SubagentNode(
        profile=profile,
        conversation_id=str(conversation_id),
        events=[],
        output="",
        subagents=[],
    )


@dataclass(frozen=True)
class StandInModel:
    """Echoes the round count back so a web turn runs the full queue path without a provider."""

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
class ConnectProvider:
    provider: str = "github"
    host: str = "api.github.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://oauth.example.test/authorize?state={state}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id="github-account")


async def _seed_workspace() -> tuple[UUID, UUID]:
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
                reasoning="high",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _seed_member(workspace_id: UUID, email: str, *, admin: bool = False) -> tuple[UUID, str]:
    """Seed a member and mint the signed bearer the gateway or `ufoctl init` would — the value the
    `ufo_session` cookie carries; the web surface resolves the workspace and the member email from
    it. An admin reaches every agent; anyone else reaches the main agent plus the non-main agents
    the web audience grants."""
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
    return member_id, token


async def _grant_web_access(workspace_id: UUID, agent_id: UUID, email: str) -> None:
    with ws(workspace_id):
        await web_extension().store.put(
            f"{AUDIENCE_PREFIX}{agent_id}/{email}", {"granted_by": str(uuid4())}
        )


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox]]:
    config = dbos_launched
    hub = GatingHub(InProcessHub(), STREAM_GATE)
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
            run_tokens=RunTokenCodec(b"web-test-run-token-secret"),
            dbos=dbos_client,
            invoker_for=invoker_factory(dbos_client),
            subagents=PORTAL_SUBAGENTS,
            subagent_grants={},
            manifests=(
                web_manifest(),
                connectors_manifest(),
                SCHEDULED_TASK_KIND_ONLY,
                skill_create_manifest(),
                sources_manifest(),
                todos.manifest(),
                SLOTTED,
            ),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=CredentialStore(fernet=CREDENTIAL_FERNET),
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret=SECRET,
        )
    )
    yield config, hub, blob, sandboxes
    dbos_client.destroy()
    loop_queue.reset_runtime()


def test_chat_source_links_the_portal_at_the_conversation_and_names_who_asked() -> None:
    conversation_id = uuid4()
    assert web_surface._chat_source("https://ufo.example", conversation_id, "bee@example.com") == (
        f"https://ufo.example/surface/web#/c/{conversation_id} (bee@example.com)"
    )
    assert web_surface._chat_source("https://ufo.example/", conversation_id, "bee@example.com") == (
        f"https://ufo.example/surface/web#/c/{conversation_id} (bee@example.com)"
    )
    for unset in (None, ""):
        assert web_surface._chat_source(unset, conversation_id, "bee@example.com") == (
            "ufo web (bee@example.com)"
        )


def test_chat_title_cuts_at_a_word_boundary_and_falls_back_to_filenames() -> None:
    assert web_surface._chat_title("Summarize the quarterly report", ()) == (
        "Summarize the quarterly report"
    )
    assert web_surface._chat_title("  spread   across\nlines  ", ()) == "spread across lines"
    long = "one two three four five six seven eight nine ten eleven twelve thirteen"
    cut = web_surface._chat_title(long, ())
    assert len(cut) <= 60
    assert not cut.endswith(" ")
    assert long.startswith(cut)
    assert cut == "one two three four five six seven eight nine ten eleven"
    assert web_surface._chat_title("", ("web-inbox/report.pdf", "web-inbox/data.csv")) == (
        "report.pdf, data.csv"
    )
    assert web_surface._chat_title("x" * 80, ()) == "x" * 60
    assert (
        web_surface._chat_title(
            "Run ls in your sandbox and tell me what files you see, then read them", ()
        )
        == "Run ls in your sandbox and tell me what files you see"
    )
    assert (
        web_surface._chat_title(
            "Summarize the report and send it to the team and then the board", ()
        )
        == "Summarize the report and send it to the team"
    )
    assert web_surface._chat_title("Reconsider, " + "x" * 70, ()) == "Reconsider"
    assert web_surface._chat_title("the and then of to " + "y" * 60, ()) == "the"


@pytest.fixture
async def web(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID, UUID]]:
    config, hub, blob, sandboxes = dbos_runtime
    STREAM_GATE.reset()
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.include_router(artifacts_router)
    _mount_shared_surfaces(
        app,
        (
            web_manifest(),
            todos.manifest(),
            SCHEDULED_TASK_KIND_ONLY,
            SLOTTED,
            sites_manifest(),
        ),
        CredentialStore(fernet=CREDENTIAL_FERNET),
        blob,
        sandboxes,
        hub,
        dbos_client,
        SECRET,
        "https://web",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=lambda: turn_runtime_skills(
            (skill_create_manifest(),),
            CredentialStore(fernet=CREDENTIAL_FERNET),
            DefaultIndex(transaction=workspace_tx),
            StubEmbed(),
        ),
        subagents=PORTAL_SUBAGENTS,
        objects=member_object_registry(
            (web_manifest(), SCHEDULED_TASK_KIND_ONLY, SLOTTED, sites_manifest())
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield client, workspace_id, agent_id
    dbos_client.destroy()


async def _consume(client: AsyncClient, token: str, turn_id: str) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    event: str | None = None
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET",
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        ) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    if event == "terminal":
                        return "".join(deltas), data
                    if event == "cost":
                        continue
                    deltas.append(data["text"])
                elif not line:
                    event = None
    raise AssertionError("stream ended without a terminal frame")


async def test_web_turn_round_trip_admits_streams_and_links_identity(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"hello",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    opened = admitted.json()["conversation_id"]
    assert admitted.json()["title"] == "hello"
    streamed, terminal = await _consume(client, token, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "web",
                    tables.surface_identity.c.external_id == "owner@example.com",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.surface,
                    tables.conversation.c.member_id,
                    tables.conversation.c.agent_id,
                    tables.conversation.c.queue_key,
                )
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == UUID(turn_id))
            )
        ).one()
        writeback = (
            await connection.execute(
                sa.select(tables.writeback.c.turn_id).where(
                    tables.writeback.c.turn_id == UUID(turn_id)
                )
            )
        ).one_or_none()
        context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.id == UUID(turn_id))
            )
        ).scalar_one()
    assert linked.member_id == member_id
    assert conversation.surface == "web"
    assert conversation.member_id == member_id
    assert conversation.agent_id == agent_id
    assert conversation.queue_key.startswith(f"{agent_id}/owner@example.com/")
    assert writeback is None
    assert context == {
        "sender": "owner@example.com",
        "timezone": None,
        "source": (f"https://web/surface/web#/c/{opened} (owner@example.com)"),
    }
    transcript = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={opened}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert transcript.status_code == 200
    assert transcript.json()["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "echo:1"},
    ]


def test_title_excerpt_waits_for_an_assistant_reply_and_bounds_both_sides() -> None:
    opening = Message(role="user", content="Draft the onboarding plan")
    assert web_surface._title_excerpt((opening,)) == ""
    reply = Message(role="assistant", content=(TextBlock(text="Here is the plan."),))
    assert web_surface._title_excerpt((opening, reply)) == (
        "Draft the onboarding plan\n\nHere is the plan."
    )
    long = Message(role="user", content="x" * (web_surface.TITLE_EXCERPT_CHARS + 500))
    assert web_surface._title_excerpt((long, reply)) == (
        "x" * web_surface.TITLE_EXCERPT_CHARS + "\n\nHere is the plan."
    )


async def test_chat_title_job_rewrites_the_rail_label_from_the_opening_exchange(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _, _, blob, _ = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "titles@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"Draft next week's onboarding plan for the two new engineers",
        headers=headers,
    )
    assert admitted.status_code == 200
    opened = admitted.json()["conversation_id"]
    assert admitted.json()["title"].startswith("Draft next week's onboarding plan")
    await _consume(client, token, admitted.json()["turn_id"])

    candidates = store_key_workspaces(EXTENSION_WEB, web_surface.CHAT_PENDING_PREFIX)
    assert workspace_id in await candidates()

    ctx = context_for(EXTENSION_WEB, frozenset(), blob=blob, model_resolver=STANDIN_REGISTRY)
    with ws(workspace_id):
        await web_surface.summarize_chat_titles(ctx)
        assert await ctx.store.list(web_surface.CHAT_PENDING_PREFIX) == ()

    rail = await client.get("/surface/web/api/chats", headers=headers)
    assert rail.status_code == 200
    (row,) = rail.json()["chats"]
    assert row["conversation_id"] == opened
    assert row["title"] == "echo:1"
    assert workspace_id not in await candidates()

    with ws(workspace_id):
        await web_surface.summarize_chat_titles(context_for(EXTENSION_WEB, frozenset()))


async def test_transcript_route_returns_durable_tool_activity(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Done."),
    )
    await Transcript(blob=blob, conversation_id=conversation_id).write(
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nRun it.",
                ),
                Message(
                    role="assistant",
                    content=(ToolUseBlock(id="call-1", name="bash", input={"command": "pwd"}),),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(tool_use_id="call-1", content="/workspace", activity=True),
                    ),
                ),
                Message(role="assistant", content="Done."),
            ),
        )
    )
    child_conversation, child_turn = await _seed_subagent(
        workspace_id,
        agent_id,
        member_id,
        turn_id,
        terminal_text='{"result": "Nothing is stale."}',
    )
    await Transcript(blob=blob, conversation_id=child_conversation).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="{}"),
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text="Checking the lockfile."),
                        ToolUseBlock(id="call-2", name="read", input={"path": "uv.lock"}),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="call-2", content="…", activity=True),),
                ),
            ),
        )
    )
    grandchild_conversation, _grandchild_turn = await _seed_subagent(
        workspace_id,
        agent_id,
        member_id,
        child_turn,
        terminal_text='{"result": "Nothing further."}',
        profile="deep_research",
    )

    response = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert response.status_code == 200
    assert response.json()["messages"] == [
        {"role": "user", "text": "Run it."},
        {
            "role": "assistant",
            "text": "Done.",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": '{"command":"pwd"}',
                    "description": "",
                }
            ],
            "subagents": [
                {
                    "profile": "general_purpose",
                    "conversation_id": str(child_conversation),
                    "events": [
                        {"kind": "note", "text": "Checking the lockfile."},
                        {
                            "kind": "tool",
                            "name": "read",
                            "preview": '{"path":"uv.lock"}',
                            "description": "",
                        },
                    ],
                    "output": "Nothing is stale.",
                    "subagents": [
                        {
                            "profile": "deep_research",
                            "conversation_id": str(grandchild_conversation),
                            "events": [],
                            "output": "Nothing further.",
                            "subagents": [],
                        }
                    ],
                }
            ],
        },
    ]


async def test_transcript_carries_a_running_turns_prompt_and_names_the_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A turn writes the transcript when it ends, so a reload while one runs would otherwise draw
    a conversation with the member's own message missing. The read carries the running turn's
    prompt as the message it is and names the turn for the page to tail; a conversation whose
    newest turn is committed names none, so the page never tails a finished turn."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )
    await Transcript(blob=blob, conversation_id=conversation_id).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="<context>source: web</context>\nFirst ask."),
                Message(role="assistant", content="Looked."),
            ),
        )
    )
    settled = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert settled.status_code == 200
    history = [
        {"role": "user", "text": "First ask."},
        {"role": "assistant", "text": "Looked."},
    ]
    assert settled.json() == {"messages": history}

    running = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=running,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=2,
                status="running",
                inbound="Review PR 1268.",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    mid = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert mid.status_code == 200
    assert mid.json() == {
        "messages": [*history, {"role": "user", "text": "Review PR 1268."}],
        "turn": str(running),
    }


async def test_unknown_session_token_is_rejected(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, _workspace_id, agent_id = web
    stranger = secrets.token_hex(16)
    denied = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"hi",
        headers={"cookie": f"{SESSION_COOKIE}={stranger}"},
    )
    assert denied.status_code == 401
    missing = await client.post(f"/surface/web/agents/{agent_id}/chat", content=b"hi")
    assert missing.status_code == 401


async def test_ungranted_member_reaches_the_main_agent_and_nothing_else(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every member reaches the workspace's main agent — the portal answers the way every other
    surface routes an unbound member — while a non-main agent without a grant fails closed as
    not-found on every route, including a guessed identifier (#624 acceptance)."""
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _member_id, token = await _seed_member(workspace_id, "outsider@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    index = await client.get("/surface/web/api/agents", headers=cookie)
    assert index.status_code == 200
    assert index.json() == {
        "member": {"email": "outsider@example.com", "admin": False},
        "agents": [
            {"id": str(agent_id), "name": "assistant", "main": True, "model": "claude-opus-4-8"}
        ],
        "subagents": [
            {"name": "deep_research", "model": "claude-opus-4-8"},
            {"name": "general_purpose", "model": None},
        ],
        "new_agent": None,
    }
    empty_rail = await client.get("/surface/web/api/chats", headers=cookie)
    assert empty_rail.status_code == 200
    assert empty_rail.json() == {"chats": []}
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new", content=b"hi", headers=cookie
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    reachable = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation="
        + admitted.json()["conversation_id"],
        headers=cookie,
    )
    assert reachable.status_code == 200
    for path in (f"agents/{second_agent}/chat", f"agents/{uuid4()}/chat"):
        denied = await client.post(f"/surface/web/{path}", content=b"hi", headers=cookie)
        assert denied.status_code == 404
        assert denied.text == "no such agent"
    transcript = await client.get(
        f"/surface/web/agents/{second_agent}/transcript?conversation="
        + admitted.json()["conversation_id"],
        headers=cookie,
    )
    assert transcript.status_code == 404
    assert transcript.text == "no such agent"
    async with workspace_tx() as connection:
        bound = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert bound == [agent_id]


async def test_agents_index_filters_by_grant_and_widens_for_admins(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-sonnet-5",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    admin_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert admin_view.json()["member"] == {"email": "admin@example.com", "admin": True}
    assert [(a["name"], a["main"]) for a in admin_view.json()["agents"]] == [
        ("assistant", True),
        ("ops", False),
    ]
    assert admin_view.json()["agents"] == [
        {
            "id": str(agent_id),
            "name": "assistant",
            "main": True,
            "model": "claude-opus-4-8",
            "web_audience": [],
        },
        {
            "id": str(second_agent),
            "name": "ops",
            "main": False,
            "model": "claude-sonnet-5",
            "web_audience": ["member@example.com"],
        },
    ]
    member_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_view.json()["member"] == {"email": "member@example.com", "admin": False}
    assert [a["id"] for a in member_view.json()["agents"]] == [
        str(agent_id),
        str(second_agent),
    ]
    assert all("web_audience" not in agent for agent in member_view.json()["agents"])
    roster = [
        {"name": "deep_research", "model": "claude-opus-4-8"},
        {"name": "general_purpose", "model": None},
    ]
    assert admin_view.json()["subagents"] == roster
    assert member_view.json()["subagents"] == roster
    reachable = await client.get(
        f"/surface/web/agents/{agent_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert reachable.status_code == 400
    assert reachable.text == "conversation is required"


async def test_boot_read_carries_the_create_form_for_an_admin_and_for_nobody_else(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The Agents screen draws its create act from the boot read: the `agent` kind's own spec
    schema, `prompt` among the required fields because the kind refuses a create without one, and
    the deploy's model ids for the one field the schema cannot enumerate. A member the kind admits
    no create from is sent none of it, so the act is drawn exactly where the lane honours it."""
    client, workspace_id, _agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    admin_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    form = admin_view.json()["new_agent"]
    assert form["models"] == ["auto", "claude-opus-4-8", "claude-sonnet-5"]
    assert sorted(form["spec_schema"]["properties"]) == [
        "internet_access_allowed",
        "model",
        "prompt",
        "reasoning",
    ]
    assert sorted(form["spec_schema"]["required"]) == [
        "internet_access_allowed",
        "model",
        "prompt",
        "reasoning",
    ]
    assert form["spec_schema"]["properties"]["reasoning"]["enum"] == [
        "auto",
        "off",
        "low",
        "medium",
        "high",
    ]
    assert form["spec_schema"]["properties"]["internet_access_allowed"]["title"] == (
        "Internet Access Allowed"
    )
    member_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_view.json()["new_agent"] is None


async def _seed_connection(
    workspace_id: UUID, agent_id: UUID, owner_member_id: UUID, provider: str, *, shared: bool
) -> None:
    conversation_id, connection_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=conversation_id.hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=f"{provider}-account",
                host="api.example.test",
                owner_member_id=owner_member_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                conversation_id=conversation_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_connections_panel_holds_the_member_gate_and_the_wall(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_connection(workspace_id, agent_id, member_m, "github", shared=False)
    await _seed_connection(workspace_id, agent_id, member_n, "slack", shared=True)
    await _seed_connection(workspace_id, second_agent, member_m, "asana", shared=True)
    path = f"/surface/web/agents/{agent_id}/connections"
    m_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert [
        (c["provider"], c["shared"], c["owner_email"], c["own"])
        for c in m_view.json()["connections"]
    ] == [("github", False, "m@example.com", True), ("slack", True, "n@example.com", False)]
    n_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    assert [(c["provider"], c["owner_email"], c["own"]) for c in n_view.json()["connections"]] == [
        ("slack", "n@example.com", True)
    ]
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert [(c["provider"], c["owner_email"]) for c in admin_view.json()["connections"]] == [
        ("github", "m@example.com"),
        ("slack", "n@example.com"),
    ]
    other = await client.get(
        f"/surface/web/agents/{second_agent}/connections",
        headers={"cookie": f"{SESSION_COOKIE}={token_admin}"},
    )
    assert [c["provider"] for c in other.json()["connections"]] == ["asana"]
    walled = await client.get(
        f"/surface/web/agents/{second_agent}/connections",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert walled.status_code == 404


async def test_credentials_view_reports_slots_and_never_values(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace credentials view: member-fillable declared slots with their fill state — the
    slots are the deploy's, shared across every agent, and a value never renders. An
    unauthenticated read is refused before a byte of it."""
    client, workspace_id, _agent_id = web
    _member, token = await _seed_member(workspace_id, "m@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="acme_api_key",
                ciphertext=b"super-sealed-value",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    anonymous = await client.get("/surface/web/workspace/credentials")
    assert anonymous.status_code == 401
    assert "acme_api_key" not in anonymous.text
    listed = await client.get(
        "/surface/web/workspace/credentials",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["slots"] == [
        {
            "slot": "acme_api_key",
            "name": "acme-api-key",
            "extension": "stub",
            "description": "ACME API key",
            "filled": True,
        }
    ]
    assert "sealed" not in listed.text
    assert "acme_install_seal" not in listed.text


async def test_sources_panel_gates_on_subject(web: tuple[AsyncClient, UUID, UUID]) -> None:
    """The workspace sources view: a member sees shared sources plus their own registrations, a
    shared source names its owner only to an admin or the owner (a source with no owner member
    names nobody), and an unauthenticated read is refused."""
    client, workspace_id, _agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    async with workspace_tx() as connection:
        for backend, subject, owner in (
            ("folder", "shared", None),
            ("github", f"member:{member_m}", member_m),
            ("notion", "shared", member_n),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    backend=backend,
                    config={},
                    subject=subject,
                    owner_member_id=owner,
                    next_sync_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    path = "/surface/web/workspace/sources"
    m_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert [
        (s["backend"], s["shared"], s["owner_email"], s["own"]) for s in m_view.json()["sources"]
    ] == [
        ("folder", True, None, False),
        ("github", False, "m@example.com", True),
        ("notion", True, "n@example.com", False),
    ]
    n_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    assert [(s["backend"], s["owner_email"], s["own"]) for s in n_view.json()["sources"]] == [
        ("folder", None, False),
        ("notion", "n@example.com", True),
    ]
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert [(s["backend"], s["owner_email"]) for s in admin_view.json()["sources"]] == [
        ("folder", None),
        ("github", "m@example.com"),
        ("notion", "n@example.com"),
    ]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_artifacts_view_lists_own_files_with_links_and_admins_see_all(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace artifacts view: a member reads their own conversations' shared files newest
    first, each carrying the signed TTL download link and the media type the page previews an
    image by; another member's files never list; an admin reads the workspace's."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    minted = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    shared = (
        (
            member_m,
            "m@example.com",
            (
                (f"artifacts/{uuid4()}/report.pdf", "report.pdf", "application/pdf"),
                (f"artifacts/{uuid4()}/chart.png", "chart.png", "image/png"),
            ),
        ),
        (
            member_n,
            "n@example.com",
            ((f"artifacts/{uuid4()}/notes.txt", "notes.txt", "text/plain"),),
        ),
    )
    minute = 0
    for member_id, email, files in shared:
        _conversation, turn_id = await _seed_web_turn(
            workspace_id, agent_id, member_id, email, TerminalFrame(status="done", text="ok")
        )
        for blob_key, filename, media_type in files:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.shared_artifact).values(
                        turn_id=turn_id,
                        blob_key=blob_key,
                        workspace_id=workspace_id,
                        filename=filename,
                        subject="the file",
                        media_type=media_type,
                        size_bytes=3,
                        created_at=minted + timedelta(minutes=minute),
                        updated_at=sa.func.now(),
                    )
                )
            minute += 1
    path = "/surface/web/workspace/artifacts"
    m_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})).json()
    assert [entry["filename"] for entry in m_view["artifacts"]] == ["chart.png", "report.pdf"]
    assert [entry["media_type"] for entry in m_view["artifacts"]] == [
        "image/png",
        "application/pdf",
    ]
    assert {entry["owner_email"] for entry in m_view["artifacts"]} == {"m@example.com"}
    assert m_view["artifacts"][0]["url"].startswith("https://web/")
    assert "/chart.png?exp=" in m_view["artifacts"][0]["url"]
    n_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [entry["filename"] for entry in n_view["artifacts"]] == ["notes.txt"]
    admin_view = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert [entry["filename"] for entry in admin_view["artifacts"]] == [
        "notes.txt",
        "chart.png",
        "report.pdf",
    ]
    assert [entry["owner_email"] for entry in admin_view["artifacts"]] == [
        "n@example.com",
        "m@example.com",
        "m@example.com",
    ]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_artifacts_view_searches_media_and_shared_conversations(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    shared_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="slack/shared",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
        surface_label="Project",
    )
    private_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="web/private",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
    )
    own_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="web/own",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    records = (
        (shared_conversation, "shared.png", "needle", "image/png"),
        (private_conversation, "hidden.png", "hidden", "image/png"),
        (own_conversation, "report.pdf", "needle", "application/pdf"),
        (own_conversation, "data.csv", "table", "text/csv"),
        (own_conversation, "archive.zip", "archive", "application/zip"),
    )
    for index, (conversation_id, filename, subject, media_type) in enumerate(records):
        turn_id = await _seed_listed_turn(
            workspace_id,
            conversation_id,
            agent_id,
            seq=index + 1,
            inbound="share",
            speaker_member_id=member_m if conversation_id == own_conversation else member_n,
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=workspace_id,
                    filename=filename,
                    subject=subject,
                    media_type=media_type,
                    size_bytes=3,
                    created_at=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=index),
                    updated_at=sa.func.now(),
                )
            )
    headers = {"cookie": f"{SESSION_COOKIE}={token_m}"}
    listed = (await client.get(ARTIFACTS_PATH, headers=headers)).json()["artifacts"]
    assert {entry["filename"] for entry in listed} == {
        "shared.png",
        "report.pdf",
        "data.csv",
        "archive.zip",
    }
    shared = next(entry for entry in listed if entry["filename"] == "shared.png")
    report = next(entry for entry in listed if entry["filename"] == "report.pdf")
    assert report["owner_email"] == "m@example.com"
    assert shared["origin"] == "Project"
    assert shared["conversation_id"] == str(shared_conversation)
    assert _names((await client.get(f"{ARTIFACTS_PATH}?q=report", headers=headers)).json()) == [
        "report.pdf"
    ]
    needle = (await client.get(f"{ARTIFACTS_PATH}?q=needle", headers=headers)).json()["artifacts"]
    assert {entry["filename"] for entry in needle} == {
        "shared.png",
        "report.pdf",
    }
    assert _names((await client.get(f"{ARTIFACTS_PATH}?media=image", headers=headers)).json()) == [
        "shared.png"
    ]
    assert _names((await client.get(f"{ARTIFACTS_PATH}?media=other", headers=headers)).json()) == [
        "archive.zip"
    ]
    assert (await client.get(f"{ARTIFACTS_PATH}?media=bad", headers=headers)).status_code == 400


async def test_workspace_usage_answers_a_member_their_own_and_an_admin_the_rollup(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A member's own burn is theirs to read, so the workspace usage view answers every member.
    A non-admin's payload carries only their own sums and their own member-scoped caps, naming no
    other member and no agent; an admin additionally receives the workspace rollup — every
    dimension, every member's burn, and every agent's — which this view is the only home for."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, member_m)
    await _seed_priced_turn(workspace_id, agent_id, member_n)
    async with workspace_tx() as connection:
        for subject, limit in ((member_m, 5_000_000), (member_n, 9_000_000)):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope="member",
                    subject_id=subject,
                    window_seconds=3_600,
                    limit_micro_usd=limit,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    path = "/surface/web/workspace/usage"
    mine = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert mine.status_code == 200
    payload = mine.json()
    assert payload["workspace"] is None
    assert payload["window_seconds"] == 86_400
    assert {line["dimension"] for line in payload["by_dimension"]} == {"egress", "tokens"}
    assert payload["total_micro_usd"] == 55_000
    assert [cap["limit_micro_usd"] for cap in payload["caps"]] == [5_000_000]
    body = mine.text
    assert "n@example.com" not in body
    assert "assistant" not in body
    theirs = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [cap["limit_micro_usd"] for cap in theirs["caps"]] == [9_000_000]
    rolled = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    workspace = rolled.json()["workspace"]
    assert rolled.json()["total_micro_usd"] == 0
    assert workspace["total_micro_usd"] == 110_000
    assert {entry["label"] for entry in workspace["by_member"]} == {
        "m@example.com",
        "n@example.com",
    }
    assert [entry["label"] for entry in workspace["by_agent"]] == ["assistant"]
    assert {line["dimension"] for line in workspace["by_dimension"]} == {"egress", "tokens"}
    windowed = (
        await client.get(
            f"{path}?window_seconds=3600", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
    ).json()
    assert windowed["window_seconds"] == 3_600
    for bad in ("abc", "-5", "0", str(web_surface.MAX_USAGE_WINDOW_SECONDS + 1)):
        refused = await client.get(
            f"{path}?window_seconds={bad}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
        assert refused.status_code == 400
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


ARTIFACTS_PATH = "/surface/web/workspace/artifacts"


async def _seed_artifacts(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    files: tuple[tuple[str, datetime], ...],
) -> None:
    """One turn of this member's sharing the given files at the given instants — the shape the
    share tool writes, several files to one turn included. Called again for the same member, the
    files ride a further turn of the web conversation an earlier call seeded."""
    async with workspace_tx() as connection:
        existing = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key.like(f"{agent_id}/{email}/%"),
                )
            )
        ).scalar_one_or_none()
    if existing is None:
        _conversation, turn_id = await _seed_web_turn(
            workspace_id, agent_id, member_id, email, TerminalFrame(status="done", text="ok")
        )
    else:
        turn_id = uuid4()
        async with workspace_tx() as connection:
            seq = (
                await connection.execute(
                    sa.select(sa.func.count()).where(tables.turn.c.conversation_id == existing)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=workspace_id,
                    conversation_id=existing,
                    agent_id=agent_id,
                    seq=seq + 1,
                    status="done",
                    inbound="ask",
                    speaker_member_id=member_id,
                    terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    for filename, shared_at in files:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=workspace_id,
                    filename=filename,
                    subject=None,
                    media_type="text/plain",
                    size_bytes=3,
                    created_at=shared_at,
                    updated_at=shared_at,
                )
            )


def _names(payload: dict) -> list[str]:
    return [entry["filename"] for entry in payload["artifacts"]]


WALK_PAGE_CEILING = 20


async def _walk_artifacts(client: AsyncClient, headers: dict[str, str]) -> list[str]:
    """Every file the Older control reaches, in the order the pages render them. The walk is
    bounded: a cursor that cannot advance repeats its page forever, and this states that as a
    failure rather than hanging the suite."""
    payload = (await client.get(ARTIFACTS_PATH, headers=headers)).json()
    walked = _names(payload)
    pages = 1
    while payload["older"]:
        assert pages < WALK_PAGE_CEILING, f"the walk never ended: {walked}"
        payload = (
            await client.get(f"{ARTIFACTS_PATH}?after={quote(payload['older'])}", headers=headers)
        ).json()
        walked.extend(_names(payload))
        pages += 1
    return walked


async def test_artifacts_listing_walks_pages_without_repeating_or_skipping(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keyset walk over shared files: Older reaches every file exactly once, Newer returns the
    page it came from, and each page's boundary cursors say which controls exist. The page size is
    patched small so the walk's arithmetic is what the assertions read — the real
    `ARTIFACT_LIST_LIMIT` is pinned by the fence-and-limit test."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        tuple((f"file-{index}.txt", base + timedelta(minutes=index)) for index in range(5)),
    )
    monkeypatch.setattr(web_surface, "ARTIFACT_LIST_LIMIT", 2)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}

    first = (await client.get(ARTIFACTS_PATH, headers=headers)).json()
    assert _names(first) == ["file-4.txt", "file-3.txt"]
    assert first["newer"] is None
    assert first["older"] is not None
    second = (
        await client.get(f"{ARTIFACTS_PATH}?after={quote(first['older'])}", headers=headers)
    ).json()
    assert _names(second) == ["file-2.txt", "file-1.txt"]
    assert second["newer"] is not None
    back = (
        await client.get(f"{ARTIFACTS_PATH}?after={quote(second['newer'])}", headers=headers)
    ).json()
    assert _names(back) == _names(first)
    walked = await _walk_artifacts(client, headers)
    assert walked == [f"file-{index}.txt" for index in (4, 3, 2, 1, 0)]
    assert len(walked) == len(set(walked))


async def test_artifacts_paging_breaks_a_shared_timestamp_at_the_boundary(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two files one turn shared in the same instant carry the same `created_at` and the same
    `turn_id`, so only the row id can break the tie: with the page boundary falling between them,
    the walk still reaches each exactly once."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    together = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        (
            ("first.txt", together),
            ("second.txt", together),
            ("third.txt", together - timedelta(minutes=1)),
        ),
    )
    monkeypatch.setattr(web_surface, "ARTIFACT_LIST_LIMIT", 1)
    walked = await _walk_artifacts(client, {"cookie": f"{SESSION_COOKIE}={token}"})
    assert sorted(walked) == ["first.txt", "second.txt", "third.txt"]
    assert len(walked) == len(set(walked))


async def test_artifacts_paging_is_stable_across_a_concurrent_share(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file shared while the member reads page one neither repeats nor hides a row of page two:
    the cursor names a position, not an offset."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        tuple((f"file-{index}.txt", base + timedelta(minutes=index)) for index in range(4)),
    )
    monkeypatch.setattr(web_surface, "ARTIFACT_LIST_LIMIT", 2)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    first = (await client.get(ARTIFACTS_PATH, headers=headers)).json()
    assert _names(first) == ["file-3.txt", "file-2.txt"]
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        (("arrived.txt", base + timedelta(minutes=9)),),
    )
    second = (
        await client.get(f"{ARTIFACTS_PATH}?after={quote(first['older'])}", headers=headers)
    ).json()
    assert _names(second) == ["file-1.txt", "file-0.txt"]


async def test_artifacts_view_refuses_a_cursor_it_never_minted(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A token this surface did not mint is the client's error: the view answers 400 rather than
    quietly serving the newest page, so a member on a stale link is told."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        (("file.txt", datetime(2026, 7, 1, tzinfo=UTC)),),
    )
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    for stale in ("nonsense", f"older|not-a-date|{uuid4()}", "sideways|2026-07-01T00:00:00|x"):
        refused = await client.get(f"{ARTIFACTS_PATH}?after={quote(stale)}", headers=headers)
        assert refused.status_code == 400, stale


async def test_artifacts_pages_hold_the_member_fence_throughout_the_walk(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another member's file is absent from every page of the walk, not merely the first — the
    fence rides the query the cursor pages, not the page it produced. Their files interleave with
    this member's by timestamp, so a leaked row would land mid-walk."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_m,
        "m@example.com",
        tuple((f"mine-{index}.txt", base + timedelta(minutes=index * 2)) for index in range(4)),
    )
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_n,
        "n@example.com",
        tuple(
            (f"theirs-{index}.txt", base + timedelta(minutes=index * 2 + 1)) for index in range(4)
        ),
    )
    monkeypatch.setattr(web_surface, "ARTIFACT_LIST_LIMIT", 2)
    walked = await _walk_artifacts(client, {"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert walked == [f"mine-{index}.txt" for index in (3, 2, 1, 0)]


async def test_artifacts_listing_caps_at_the_real_limit_with_more_behind_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One unpatched page carries at most `ARTIFACT_LIST_LIMIT` files and offers the Older control,
    so the bound the view ships with is the one a member meets."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        tuple(
            (f"file-{index}.txt", base + timedelta(seconds=index))
            for index in range(web_surface.ARTIFACT_LIST_LIMIT + 2)
        ),
    )
    page = (
        await client.get(ARTIFACTS_PATH, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    assert len(page["artifacts"]) == web_surface.ARTIFACT_LIST_LIMIT
    assert page["older"] is not None
    assert page["newer"] is None


async def test_site_index_answers_through_the_kinds_own_gate(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The generic index lists through the site kind's visibility gate: a shared site answers
    every member, a private one only its creator and an admin — the same rows chat's `object_list`
    answers, never a second ACL. Each row carries the kind's declared fields, and the page names
    the vocabulary it filters and orders on."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    conversation_id, _turn = await _seed_web_turn(
        workspace_id, agent_id, member_m, "m@example.com", TerminalFrame(status="done", text="ok")
    )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "landing",
            3000,
            member_m,
            "workspace",
            conversation_audience(member_m),
            True,
        )
        await sites.register(
            conversation_id,
            "draft",
            3001,
            member_m,
            "private",
            conversation_audience(member_m),
            True,
        )
    path = f"/surface/web/objects/site?agent={agent_id}"
    m_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})).json()
    assert sorted(row["name"].split("-")[0] for row in m_view["objects"]) == ["draft", "landing"]
    assert sorted(m_view["fields"]) == [
        "conversation",
        "created_at",
        "mine",
        "owner_email",
        "site_url",
        "visibility",
    ]
    assert "guidance" not in m_view and "description" not in m_view
    assert m_view["applies"] is False
    n_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [row["name"].split("-")[0] for row in n_view["objects"]] == ["landing"]
    admin_view = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert sorted(row["name"].split("-")[0] for row in admin_view["objects"]) == [
        "draft",
        "landing",
    ]
    by_name = {row["name"].split("-")[0]: row for row in m_view["objects"]}
    assert by_name["landing"]["visibility"] == "workspace"
    assert by_name["draft"]["visibility"] == "private"
    for row in m_view["objects"]:
        assert row["conversation"] == str(conversation_id)
        assert row["created_at"]
        assert row["owner_email"] == "m@example.com"


async def test_an_index_longer_than_a_page_walks_on_the_cursor_it_returns(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A kind with more rows than one page hands back a continuation token, the next read resumes
    exactly after the last row it served, and a token carried into a different order is refused
    rather than silently restarting the walk."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    conversation_id, _turn = await _seed_web_turn(
        workspace_id, agent_id, member_m, "m@example.com", TerminalFrame(status="done", text="ok")
    )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        for index in range(OBJECT_LIST_PAGE + 1):
            await sites.register(
                conversation_id,
                f"page-{index:03d}",
                3000 + index,
                member_m,
                "workspace",
                conversation_audience(member_m),
                True,
            )
    cookie = {"cookie": f"{SESSION_COOKIE}={token_m}"}
    base = f"/surface/web/objects/site?agent={agent_id}"
    first = (await client.get(base, headers=cookie)).json()
    assert len(first["objects"]) == OBJECT_LIST_PAGE
    assert first["next_cursor"]
    second = (await client.get(base + f"&cursor={first['next_cursor']}", headers=cookie)).json()
    assert len(second["objects"]) == 1
    assert second["next_cursor"] is None
    walked = [row["name"] for row in first["objects"]] + [row["name"] for row in second["objects"]]
    assert walked == sorted(walked)
    assert len(set(walked)) == OBJECT_LIST_PAGE + 1
    reordered = await client.get(
        base + f"&cursor={first['next_cursor']}&order=desc", headers=cookie
    )
    assert reordered.status_code == 400
    assert "cursor" in reordered.text


async def test_a_site_detail_carries_its_conversation_link_and_refuses_a_hidden_row(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The detail projection is the same gate one row at a time: the creator reads their private
    site with its `created_in` link, and another member gets a 404 naming the kind — a hidden row
    is indistinguishable from an absent one."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    conversation_id, _turn = await _seed_web_turn(
        workspace_id, agent_id, member_m, "m@example.com", TerminalFrame(status="done", text="ok")
    )
    with ws(workspace_id):
        await HostedSites(workspace_id, workspace_tx).register(
            conversation_id,
            "draft",
            3001,
            member_m,
            "private",
            conversation_audience(member_m),
            True,
        )
    name = site_object_name(conversation_id, "draft")
    path = f"/surface/web/objects/site/{name}?agent={agent_id}"
    read = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert read.status_code == 200
    detail = read.json()
    assert detail["spec"] == {"visibility": "private"}
    assert detail["status"]["visibility"] == "private"
    assert detail["links"] == [
        {
            "relation": "created_in",
            "kind": "conversation",
            "name": str(conversation_id),
            "opens": True,
        }
    ]
    assert detail["created_at"] is not None
    hidden = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    assert hidden.status_code == 404
    assert "site" in hidden.text


async def test_a_link_opens_only_where_its_own_row_answers_this_member(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A link is offered on the row it names, not on its kind: a workspace site every member reads
    was created in one member's own conversation, so its `created_in` opens for that member and is
    stated for everyone else — and the payload promises exactly what following it returns."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    conversation_id, _turn = await _seed_web_turn(
        workspace_id, agent_id, member_m, "m@example.com", TerminalFrame(status="done", text="ok")
    )
    with ws(workspace_id):
        await HostedSites(workspace_id, workspace_tx).register(
            conversation_id,
            "landing",
            3002,
            member_m,
            "workspace",
            conversation_audience(member_m),
            True,
        )
    path = (
        f"/surface/web/objects/site/{site_object_name(conversation_id, 'landing')}?agent={agent_id}"
    )
    conversation = f"/surface/web/objects/conversation/{conversation_id}?agent={agent_id}"
    for token, opens in ((token_m, True), (token_n, False)):
        cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
        [link] = (await client.get(path, headers=cookie)).json()["links"]
        assert link["opens"] is opens
        followed = await client.get(conversation, headers=cookie)
        assert (followed.status_code == 200) is opens


async def test_task_index_filters_and_orders_on_the_kinds_declared_fields(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The index applies the kind's own search, filter, and order vocabulary — `q` narrows to what
    it matches, `paused` narrows, and an order field reorders the page rather than leaving it in
    name order: the paused task is the one that sorts first by name, so ordering on `paused` puts
    it last, and ordering on `next_run_at` follows the moments the rows carry, desc being asc
    reversed. A field the kind never declared is refused, not silently ignored."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "creator@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    for name, schedule in (("alpha", "0 6 * * *"), ("zulu", "0 22 * * *")):
        created = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={
                "verb": "apply",
                "kind": "scheduled_task",
                "name": name,
                "spec": {"schedule": schedule, "prompt": f"run {name}"},
            },
            headers=cookie,
        )
        assert created.json()["applied"] is True
    paused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "apply", "kind": "scheduled_task", "name": "alpha", "spec": {"paused": True}},
        headers=cookie,
    )
    assert paused.json()["applied"] is True

    base = f"/surface/web/objects/scheduled_task?agent={agent_id}"
    listed = (await client.get(base, headers=cookie)).json()
    assert [row["name"] for row in listed["objects"]] == ["alpha", "zulu"]
    assert listed["applies"] is True
    assert listed["spec_schema"]["properties"]["schedule"]
    assert {row["name"]: row["paused"] for row in listed["objects"]} == {
        "alpha": True,
        "zulu": False,
    }
    assert {row["owner_email"] for row in listed["objects"]} == {"creator@example.com"}
    searched = (await client.get(base + "&q=alpha", headers=cookie)).json()
    assert [row["name"] for row in searched["objects"]] == ["alpha"]
    stopped = (await client.get(base + "&paused=true", headers=cookie)).json()
    assert [row["name"] for row in stopped["objects"]] == ["alpha"]
    by_state = (await client.get(base + "&order_by=paused", headers=cookie)).json()
    assert [row["name"] for row in by_state["objects"]] == ["zulu", "alpha"]
    by_state_desc = (await client.get(base + "&order_by=paused&order=desc", headers=cookie)).json()
    assert [row["name"] for row in by_state_desc["objects"]] == ["alpha", "zulu"]
    ascending = (await client.get(base + "&order_by=next_run_at", headers=cookie)).json()
    fires = [row["next_run_at"] for row in ascending["objects"]]
    assert len(set(fires)) == 2 and fires == sorted(fires)
    descending = (
        await client.get(base + "&order_by=next_run_at&order=desc", headers=cookie)
    ).json()
    assert [row["name"] for row in descending["objects"]] == [
        row["name"] for row in reversed(ascending["objects"])
    ]
    refused = await client.get(base + "&nonesuch=1", headers=cookie)
    assert refused.status_code == 400
    assert "nonesuch" in refused.text


async def test_a_task_detail_links_to_the_conversation_it_reports_into(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The navigation the object pages exist for: a task's detail carries its `reports_to` link,
    and that link's kind and name address the conversation's own detail, which answers with the
    conversation's spec. Another member reaches neither."""
    client, workspace_id, agent_id = web
    _creator_id, creator_token = await _seed_member(workspace_id, "creator@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    creator_cookie = {"cookie": f"{SESSION_COOKIE}={creator_token}"}
    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "daily-brief",
            "spec": {"schedule": "0 9 * * *", "prompt": "write the daily brief"},
        },
        headers=creator_cookie,
    )
    assert created.json()["applied"] is True

    task = (
        await client.get(
            f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_id}",
            headers=creator_cookie,
        )
    ).json()
    assert task["spec"]["prompt"] == "write the daily brief"
    assert task["status"]["paused"] is False
    assert task["status"]["owner_email"] == "creator@example.com"
    [link] = task["links"]
    assert link["relation"] == "reports_to"
    assert link["kind"] == "conversation"

    assert link["opens"] is True

    followed = await client.get(
        f"/surface/web/objects/{link['kind']}/{link['name']}?agent={agent_id}",
        headers=creator_cookie,
    )
    assert followed.status_code == 200
    [scoped] = followed.json()["links"]
    assert (scoped["relation"], scoped["kind"], scoped["opens"]) == ("scoped_to", "agent", False)
    walled = await client.get(
        f"/surface/web/objects/agent/{scoped['name']}?agent={agent_id}", headers=creator_cookie
    )
    assert walled.status_code == 404
    assert followed.json()["spec"]["surface"] == "web"
    assert followed.json()["name"] == link["name"]
    assert followed.json()["applies"] is False

    unseen = await client.get(
        f"/surface/web/objects/conversation/{link['name']}?agent={agent_id}",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert unseen.status_code == 404
    assert "conversation" in unseen.text


async def test_the_credential_index_lists_every_declared_slot_and_no_value(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A credential slot is a declaration, not a member's row: every member reads the same index
    and the same declaration, filled or empty, and no read carries a value. A slot no manifest
    declares is not-found for a plain member and for an admin alike."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    with ws(workspace_id):
        await CredentialStore(fernet=CREDENTIAL_FERNET).put(workspace_id, "acme_api_key", "s3cret")
    index = f"/surface/web/objects/credential?agent={agent_id}"
    for token_value in (token, admin_token):
        cookie = {"cookie": f"{SESSION_COOKIE}={token_value}"}
        listed = (await client.get(index, headers=cookie)).json()
        assert {row["name"]: row["filled"] for row in listed["objects"]} == {
            "acme-api-key": True,
            "acme-install-seal": False,
        }
        assert "s3cret" not in json.dumps(listed)
        read = await client.get(
            f"/surface/web/objects/credential/acme-api-key?agent={agent_id}", headers=cookie
        )
        assert read.status_code == 200
        assert read.json()["spec"]["description"] == "ACME API key"
        assert "s3cret" not in read.text
        missing = await client.get(
            f"/surface/web/objects/credential/nonesuch?agent={agent_id}", headers=cookie
        )
        assert missing.status_code == 404
        assert "credential" in missing.text


async def test_the_roster_answers_off_the_main_agent_and_narrows_to_self_off_another(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The `member` kind's own rule, one read at a time: off the main agent every member reads the
    whole roster, off a child agent the reader gets their own row alone and a colleague's row is
    not-found — for an admin too, since the roster belongs to the main agent, not to a role."""
    client, workspace_id, agent_id = web
    child_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=child_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    colleague_id, _colleague_token = await _seed_member(workspace_id, "n@example.com")
    admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    for email in ("m@example.com", "boss@example.com"):
        await _grant_web_access(workspace_id, child_agent, email)
    for token_value, reader in ((token, member_id), (admin_token, admin_id)):
        cookie = {"cookie": f"{SESSION_COOKIE}={token_value}"}
        roster = (
            await client.get(f"/surface/web/objects/member?agent={agent_id}", headers=cookie)
        ).json()
        assert {row["name"] for row in roster["objects"]} == {
            str(member_id),
            str(colleague_id),
            str(admin_id),
        }
        read = await client.get(
            f"/surface/web/objects/member/{colleague_id}?agent={agent_id}", headers=cookie
        )
        assert read.status_code == 200
        assert read.json()["spec"] == {"admin": False, "seated": False}

        narrowed = (
            await client.get(f"/surface/web/objects/member?agent={child_agent}", headers=cookie)
        ).json()
        assert [row["name"] for row in narrowed["objects"]] == [str(reader)]
        hidden = await client.get(
            f"/surface/web/objects/member/{colleague_id}?agent={child_agent}", headers=cookie
        )
        assert hidden.status_code == 404
        assert "member" in hidden.text


async def test_the_artifact_index_reads_only_the_members_own_and_shared_files(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A shared file answers on the subjects the reading member's own conversation carries: their
    own private conversations plus the workspace-shared. A file shared into a workspace-shared
    conversation answers every member; another member's private file is absent from the index and
    not-found by name — for an admin too, since audience is not a role."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    names: dict[str, str] = {}
    for member_id, email, filename in (
        (member_m, "m@example.com", "mine.txt"),
        (member_n, "n@example.com", "theirs.txt"),
    ):
        conversation_id, turn_id = await _seed_web_turn(
            workspace_id, agent_id, member_id, email, TerminalFrame(status="done", text="ok")
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=workspace_id,
                    filename=filename,
                    subject="the file",
                    media_type="text/plain",
                    size_bytes=3,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        names[filename] = f"{conversation_id.hex[:8]}-{filename.replace('.', '-')}"
    shared_conversation, shared_turn = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=shared_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                queue_key=f"{agent_id}/C1/{uuid4().hex}",
                member_id=None,
                audience=str(SHARED_AUDIENCE),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=shared_turn,
                workspace_id=workspace_id,
                conversation_id=shared_conversation,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="ask",
                speaker_member_id=member_n,
                terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=shared_turn,
                blob_key=f"artifacts/{uuid4()}/ours.txt",
                workspace_id=workspace_id,
                filename="ours.txt",
                subject="the shared file",
                media_type="text/plain",
                size_bytes=3,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    names["ours.txt"] = f"{shared_conversation.hex[:8]}-ours-txt"
    index = f"/surface/web/objects/artifact?agent={agent_id}"
    for token in (token_m, token_admin):
        cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
        listed = (await client.get(index, headers=cookie)).json()
        assert {row["filename"] for row in listed["objects"]} == (
            {"mine.txt", "ours.txt"} if token == token_m else {"ours.txt"}
        )
        shared_read = await client.get(
            f"/surface/web/objects/artifact/{names['ours.txt']}?agent={agent_id}", headers=cookie
        )
        assert shared_read.status_code == 200
        assert shared_read.json()["spec"]["subject"] == "the shared file"
        hidden = await client.get(
            f"/surface/web/objects/artifact/{names['theirs.txt']}?agent={agent_id}", headers=cookie
        )
        assert hidden.status_code == 404
        assert "artifact" in hidden.text
    own = await client.get(
        f"/surface/web/objects/artifact/{names['mine.txt']}?agent={agent_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert own.status_code == 200
    assert own.json()["spec"] == {
        "filename": "mine.txt",
        "media_type": "text/plain",
        "subject": "the file",
    }
    assert own.json()["status"]["filename"] == "mine.txt"
    [link] = own.json()["links"]
    assert (link["relation"], link["kind"], link["opens"]) == ("created_in", "conversation", True)


async def test_object_pages_refuse_an_unregistered_kind_an_unlisted_kind_and_a_walled_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every kind the portal cannot serve refuses by name: an unregistered kind, a kind that reads
    one row but does not list, and an agent outside the viewer's web audience."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    unknown = await client.get(f"/surface/web/objects/widget?agent={agent_id}", headers=cookie)
    assert unknown.status_code == 404
    assert "widget" in unknown.text
    unlisted = await client.get(
        f"/surface/web/objects/conversation?agent={agent_id}", headers=cookie
    )
    assert unlisted.status_code == 404
    assert "conversation does not list in the portal" in unlisted.text
    walled = await client.get(f"/surface/web/objects/site?agent={uuid4()}", headers=cookie)
    assert walled.status_code == 404
    assert walled.text == "no such agent"
    signed_out = await client.get(f"/surface/web/objects/site?agent={agent_id}")
    assert signed_out.status_code == 401


async def test_revoking_web_access_ends_streaming_too(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A revocation closes every portal route, including the tail of a turn admitted while the
    grant was live — the member owns the turn, but its agent left their audience, so the stream
    is not-found like chat and transcript."""
    client, workspace_id, _agent_id = web
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    member_id, token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, agent_id, "member@example.com")
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"{agent_id}/member@example.com/{uuid4().hex}",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="hello",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", text="hi").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    granted = await client.get(f"/surface/web/turns/{turn_id}/stream", headers=cookie)
    assert granted.status_code == 200
    with ws(workspace_id):
        await web_extension().store.delete(f"{AUDIENCE_PREFIX}{agent_id}/member@example.com")
    revoked = await client.get(f"/surface/web/turns/{turn_id}/stream", headers=cookie)
    assert revoked.status_code == 404


async def test_a_stale_cookie_does_not_block_a_fresh_token_post(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The fallback chain keys on the cookie failing to resolve, not being absent: a member whose
    cookie outlived its bearer recovers by posting a fresh token — the session reopens instead of
    401ing behind the stale cookie, and a GET behind that stale cookie sends them to the sign-in
    page rather than a bare rejection."""
    client, workspace_id, _agent_id = web
    stale = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=-1))
    fresh = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    page = await client.get("/surface/web", headers={"cookie": f"{SESSION_COOKIE}={stale}"})
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    opened = await client.post(
        "/surface/web",
        data={"token": fresh},
        headers={"cookie": f"{SESSION_COOKIE}={stale}"},
    )
    assert opened.status_code == 303
    assert opened.headers["set-cookie"].startswith(f"ufo_session={fresh}")


async def test_one_member_holds_a_conversation_per_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Two isolated agent areas (#624 acceptance): the same member's chats with two agents land in
    two conversations, each permanently bound to its agent."""
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    first = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new", content=b"hi", headers=cookie
    )
    await _consume(client, token, first.json()["turn_id"])
    STREAM_GATE.arm()
    second = await client.post(
        f"/surface/web/agents/{second_agent}/chat?conversation=new", content=b"hi", headers=cookie
    )
    await _consume(client, token, second.json()["turn_id"])
    async with workspace_tx() as connection:
        bound = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(bound) == {agent_id, second_agent}


async def test_a_first_message_opens_a_conversation_and_the_next_continues_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The chat POST is the conversation's opening act: no `conversation` parameter opens a new
    one titled from the message, the named parameter lands the next turn in the same one, and the
    two transcripts stay separate."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    opened = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"Plan the launch checklist",
        headers=cookie,
    )
    assert opened.status_code == 200
    first = opened.json()
    assert first["title"] == "Plan the launch checklist"
    await _consume(client, token, first["turn_id"])
    STREAM_GATE.arm()
    continued = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={first['conversation_id']}",
        content=b"add rollback steps",
        headers=cookie,
    )
    assert continued.status_code == 200
    assert continued.json()["conversation_id"] == first["conversation_id"]
    assert continued.json()["title"] == "Plan the launch checklist"
    await _consume(client, token, continued.json()["turn_id"])
    STREAM_GATE.arm()
    separate = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"Draft the QBR deck",
        headers=cookie,
    )
    assert separate.json()["conversation_id"] != first["conversation_id"]
    await _consume(client, token, separate.json()["turn_id"])
    joined = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={first['conversation_id']}",
        headers=cookie,
    )
    assert [m["text"] for m in joined.json()["messages"] if m["role"] == "user"] == [
        "Plan the launch checklist",
        "add rollback steps",
    ]
    apart = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation="
        + separate.json()["conversation_id"],
        headers=cookie,
    )
    assert [m["text"] for m in apart.json()["messages"] if m["role"] == "user"] == [
        "Draft the QBR deck"
    ]
    async with workspace_tx() as connection:
        keys = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.queue_key).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(keys) == 2
    assert all(key.startswith(f"{agent_id}/owner@example.com/") for key in keys)


async def test_the_rail_lists_own_conversations_newest_first_and_only_own(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`api/chats` is the member's own rail: their conversations across reachable agents, newest
    activity first, titled from their first message — and never another member's. A conversation
    caught between creation and its chat row's write is absent until the row lands."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _other_id, other_token = await _seed_member(workspace_id, "peer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    seeded_id, seeded_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="hi"),
        title="An earlier exchange",
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == seeded_turn)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"{agent_id}/owner@example.com/{uuid4().hex}",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    STREAM_GATE.arm()
    opened = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"Summarize the incident review",
        headers=cookie,
    )
    await _consume(client, token, opened.json()["turn_id"])
    STREAM_GATE.arm()
    theirs = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"peer message",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    await _consume(client, other_token, theirs.json()["turn_id"])
    rail = await client.get("/surface/web/api/chats", headers=cookie)
    assert rail.status_code == 200
    rows = rail.json()["chats"]
    assert [row["conversation_id"] for row in rows] == [
        opened.json()["conversation_id"],
        str(seeded_id),
    ]
    assert rows[0]["title"] == "Summarize the incident review"
    assert rows[0]["agent_name"] == "assistant"
    assert rows[0]["last_at"] is not None
    assert rows[1]["title"] == "An earlier exchange"
    peer_rail = await client.get(
        "/surface/web/api/chats", headers={"cookie": f"{SESSION_COOKIE}={other_token}"}
    )
    assert [row["conversation_id"] for row in peer_rail.json()["chats"]] == [
        theirs.json()["conversation_id"]
    ]


async def test_the_rail_lists_readable_slack_conversations_with_origin(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    slack_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="D1:1.0",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="slack",
        surface_label="Direct message",
    )
    await _seed_listed_turn(
        workspace_id, slack_id, agent_id, seq=1, inbound="Review this Slack message"
    )
    await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="room:C1",
        audience="room:slack:C1",
        member_id=None,
        surface="slack",
        surface_label="#general",
    )
    await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="prepared/member",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="web",
    )

    member_rail = await client.get(
        "/surface/web/api/chats", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_rail.status_code == 200
    rows = member_rail.json()["chats"]
    assert [row["conversation_id"] for row in rows] == [str(slack_id)]
    assert rows[0]["title"] == "Review this Slack message"
    assert rows[0]["origin"] == "Direct message"

    other_rail = await client.get(
        "/surface/web/api/chats", headers={"cookie": f"{SESSION_COOKIE}={other_token}"}
    )
    assert other_rail.json()["chats"] == []
    admin_rail = await client.get(
        "/surface/web/api/chats", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert admin_rail.json()["chats"] == []


async def test_the_rail_reads_every_surface_under_its_bound(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rail reads every surface under its bound: newer Slack traffic occupies a slot and uses
    its opening message, while a same-surface prepared-intent row without a chat record drops."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    peer_id, _peer_token = await _seed_member(workspace_id, "peer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    _mine_id, mine_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="hi"),
        title="Mine, older",
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == mine_turn)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
        )
        slack_id = uuid4()
        for conversation_id, queue_key, surface, owner in (
            (slack_id, "C42:1723.0", "slack", None),
            (uuid4(), f"intent/{agent_id}/owner@example.com", "web", member_id),
            (uuid4(), f"{agent_id}/peer@example.com/{uuid4().hex}", "web", peer_id),
        ):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=surface,
                    queue_key=queue_key,
                    member_id=owner,
                    audience="shared" if owner is None else f"member:{owner}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    await _seed_listed_turn(workspace_id, slack_id, agent_id, seq=1, inbound="new Slack traffic")
    monkeypatch.setattr(web_surface, "CONVERSATION_LIST_LIMIT", 2)
    rail = await client.get("/surface/web/api/chats", headers=cookie)
    rows = rail.json()["chats"]
    assert [row["conversation_id"] for row in rows] == [str(slack_id)]
    assert rows[0]["title"] == "new Slack traffic"
    assert rows[0]["origin"] == "slack"


async def test_an_answer_into_a_fresh_conversation_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An answer belongs to the conversation its question was asked in — pairing it with the
    `new` sentinel is refused before anything is opened: no conversation, no chat row, no turn."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"Now",
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "x-ufo-answer-turn": str(uuid4()),
            "x-ufo-answer-question": "0",
        },
    )
    assert refused.status_code == 400
    assert refused.text == "an answer names the conversation it was asked in"
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert conversations == 0
    assert turns == 0


async def test_a_parameterless_call_is_refused_before_anything_opens(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every chat call names its conversation — `new` or an id. A bare POST or transcript GET is
    refused before any conversation, chat row, or turn exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"hello", headers=cookie
    )
    assert refused.status_code == 400
    assert refused.text == "conversation is required"
    bare = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert bare.status_code == 400
    assert bare.text == "conversation is required"
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
    assert conversations == 0


async def test_a_conversation_past_the_rails_bound_still_resolves_by_id(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An emitted `#/c/<id>` link outlives the rail's bound: with the bound at one, the displaced
    older conversation still answers `api/chats?conversation=` with its one row, another member's
    id answers empty, and a malformed id answers empty."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    older_id, older_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="hi"),
        title="Displaced but linked",
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == older_turn)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
    STREAM_GATE.arm()
    newer = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"newer words",
        headers=cookie,
    )
    await _consume(client, token, newer.json()["turn_id"])
    monkeypatch.setattr(web_surface, "CONVERSATION_LIST_LIMIT", 1)
    rail = await client.get("/surface/web/api/chats", headers=cookie)
    assert [row["conversation_id"] for row in rail.json()["chats"]] == [
        newer.json()["conversation_id"]
    ]
    resolved = await client.get(f"/surface/web/api/chats?conversation={older_id}", headers=cookie)
    rows = resolved.json()["chats"]
    assert [row["conversation_id"] for row in rows] == [str(older_id)]
    assert rows[0]["title"] == "Displaced but linked"
    assert rows[0]["agent_name"] == "assistant"
    assert rows[0]["last_at"] is not None
    crossed = await client.get(
        f"/surface/web/api/chats?conversation={older_id}",
        headers={"cookie": f"{SESSION_COOKIE}={peer_token}"},
    )
    assert crossed.json() == {"chats": []}
    malformed = await client.get("/surface/web/api/chats?conversation=not-a-uuid", headers=cookie)
    assert malformed.json() == {"chats": []}


async def test_a_readable_slack_conversation_resolves_by_permalink(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1.0",
        audience="shared",
        member_id=None,
        surface="slack",
    )
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound="from Slack",
        speaker_member_id=member_id,
        context=TurnContext(sender="Robin Vale (owner@example.com)"),
    )

    resolved = await client.get(
        f"/surface/web/api/chats?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert resolved.status_code == 200
    target = resolved.json()["conversation"]
    assert resolved.json()["chats"] == []
    assert target == {
        "id": str(conversation_id),
        "agent": {"id": str(agent_id), "name": "assistant"},
        "surface": "slack",
        "surface_label": None,
        "audience": "shared",
        "member_email": None,
        "description": "from Slack",
        "speakers": ["Robin Vale (owner@example.com)"],
        "turn_count": 1,
        "created_at": target["created_at"],
        "last_turn_at": target["last_turn_at"],
        "readable": True,
        "disclosable": False,
    }


async def test_a_permalink_resolves_with_the_viewers_own_admin_flag(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `#/c/<id>` permalink reads the conversation the same way the panel lists it, admin flag
    included: the id an admin sees offered as "Open as admin" resolves here as unreadable and
    disclosable rather than as a conversation that does not exist, and an audience naming no member
    resolves unreadable and undisclosable — the permalink opens no room. For anyone else both stay
    absent."""
    client, workspace_id, agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    owner_id, _owner_token = await _seed_member(workspace_id, "owner@example.com")
    _peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com")
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="D1:1.0",
        audience=str(conversation_audience(owner_id)),
        member_id=owner_id,
        surface="slack",
    )
    room = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C9:1.0",
        audience="room:slack:C9",
        member_id=None,
        surface="slack",
    )
    for conversation_id in (theirs, room):
        await _seed_listed_turn(
            workspace_id, conversation_id, agent_id, seq=1, inbound="from Slack"
        )
    admin_cookie = {"cookie": f"{SESSION_COOKIE}={admin_token}"}
    peer_cookie = {"cookie": f"{SESSION_COOKIE}={peer_token}"}

    private = await client.get(
        f"/surface/web/api/chats?conversation={theirs}", headers=admin_cookie
    )
    walled = await client.get(f"/surface/web/api/chats?conversation={room}", headers=admin_cookie)

    assert private.json()["chats"] == []
    assert private.json()["conversation"]["id"] == str(theirs)
    assert private.json()["conversation"]["member_email"] == "owner@example.com"
    assert private.json()["conversation"]["readable"] is False
    assert private.json()["conversation"]["disclosable"] is True
    assert walled.json()["conversation"]["readable"] is False
    assert walled.json()["conversation"]["disclosable"] is False
    for conversation_id in (theirs, room):
        unprivileged = await client.get(
            f"/surface/web/api/chats?conversation={conversation_id}", headers=peer_cookie
        )
        assert unprivileged.json() == {"chats": []}, conversation_id


async def test_an_orphaned_chat_row_is_inert(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The chat row is written before its conversation exists, so the crash window leaves a row
    without a conversation — which no read carries: beside a landed conversation, the rail lists
    exactly that one and gives the orphan no slot."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    orphan = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.ext_store).values(
                workspace_id=workspace_id,
                extension="web",
                key=f"chat/{orphan}",
                value={
                    "agent_id": str(agent_id),
                    "email": "owner@example.com",
                    "title": "never landed",
                },
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    STREAM_GATE.arm()
    landed = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"a landed message",
        headers=cookie,
    )
    await _consume(client, token, landed.json()["turn_id"])
    rail = await client.get("/surface/web/api/chats", headers=cookie)
    assert [row["conversation_id"] for row in rail.json()["chats"]] == [
        landed.json()["conversation_id"]
    ]


async def test_a_malformed_chat_row_is_a_fault_not_a_missing_conversation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A chat row that fails validation raises — the chat POST and the rail both surface the
    fault instead of degrading to not-found or silently dropping the row."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, _turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="hi"),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.ext_store)
            .where(
                tables.ext_store.c.extension == "web",
                tables.ext_store.c.key == f"chat/{conversation_id}",
            )
            .values(value={"agent_id": "not-a-uuid", "title": 7})
        )
    with pytest.raises(ValidationError):
        await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
            content=b"hello",
            headers=cookie,
        )
    with pytest.raises(ValidationError):
        await client.get("/surface/web/api/chats", headers=cookie)


async def test_a_conversation_is_walled_to_its_member_and_its_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The conversation parameter opens nothing beyond the member's own chat with the named agent:
    another member's id, the wrong agent, and a malformed id are all not-found, for the POST and
    the transcript alike."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    peer_cookie = {"cookie": f"{SESSION_COOKIE}={peer_token}"}
    STREAM_GATE.arm()
    opened = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new", content=b"mine", headers=cookie
    )
    await _consume(client, token, opened.json()["turn_id"])
    conversation = opened.json()["conversation_id"]
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    crossed = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation}",
        content=b"not yours",
        headers=peer_cookie,
    )
    assert crossed.status_code == 404
    read_across = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation}",
        headers=peer_cookie,
    )
    assert read_across.status_code == 404
    wrong_agent = await client.post(
        f"/surface/web/agents/{second_agent}/chat?conversation={conversation}",
        content=b"wrong door",
        headers=cookie,
    )
    assert wrong_agent.status_code == 404
    malformed = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=not-a-uuid",
        content=b"hi",
        headers=cookie,
    )
    assert malformed.status_code == 404
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .join(
                    tables.conversation,
                    tables.turn.c.conversation_id == tables.conversation.c.id,
                )
                .where(tables.conversation.c.id == UUID(conversation))
            )
        ).scalar_one()
    assert turns == 1


async def test_web_stream_privately_opens_the_speakers_connect_handoff(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    flow = ConnectFlow(
        providers={"github": ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
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
        response = await client.get(
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    finally:
        install_connect_flow(None)
    assert response.status_code == 200
    lines = response.text.splitlines()
    connect_data = json.loads(lines[lines.index("event: connect") + 1].removeprefix("data: "))
    assert connect_data["url"].startswith("https://oauth.example.test/authorize")
    assert lines.index("event: connect") < lines.index("event: terminal")
    async with workspace_tx() as connection:
        memoized_url = (
            await connection.execute(
                sa.select(tables.turn.c.connect_authorization_url).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).scalar_one()
    assert memoized_url == connect_data["url"]


async def test_two_web_members_get_isolated_subjects_and_cannot_cross(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_a, token_a = await _seed_member(workspace_id, "a@example.com")
    member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    turn_a = (
        await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation=new",
            content=b"hi",
            headers={"cookie": f"{SESSION_COOKIE}={token_a}"},
        )
    ).json()["turn_id"]
    turn_b = (
        await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation=new",
            content=b"hi",
            headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
        )
    ).json()["turn_id"]
    await _consume(client, token_a, turn_a)
    await _consume(client, token_b, turn_b)
    async with workspace_tx() as connection:
        owners = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(owners) == {member_a, member_b}
    assert recall_subjects(conversation_audience(member_a)) & recall_subjects(
        conversation_audience(member_b)
    ) == frozenset({SHARED_SUBJECT})
    assert member_subject(member_a) not in recall_subjects(conversation_audience(member_b))
    crossed = await client.get(
        f"/surface/web/turns/{turn_a}/stream",
        headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
    )
    assert crossed.status_code == 403


async def _seed_priced_turn(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> None:
    """One finished turn with priced usage and an egress request, so the rollup has real sums to
    report against a known member and agent."""
    async with workspace_tx() as connection:
        conversation_id, turn_id = uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="x",
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000, output_tokens=2000),
        )
        await record_egress_request(connection, workspace_id, turn_id)


async def test_admin_view_reads_the_workspace_shape(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The administration read end to end: agents carry their policy, surface installations, and
    the exact web-audience grants written through the extension's store — the main agent carries
    none, and its `main` flag is what the portal renders as "every member"; members and seat
    state are `Seats.snapshot`'s answer; every spend cap arrives with its subject named for the
    reader; and the deploy reports its installed extensions and public-internet ceiling from the
    mounted manifest set."""
    client, workspace_id, _agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-sonnet-5",
                internet_access_allowed=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T42",
                agent_id=second_agent,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, _member_token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=5, included_seats=2)
            .where(tables.workspace.c.id == workspace_id)
        )
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )
        for scope, subject, window in (
            ("workspace", None, 86_400),
            ("agent", second_agent, 3_600),
            ("member", member_id, 86_400),
        ):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope=scope,
                    subject_id=subject,
                    window_seconds=window,
                    limit_micro_usd=5_000_000,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert view.status_code == 200
    payload = view.json()
    by_name = {agent["name"]: agent for agent in payload["agents"]}
    assert by_name["assistant"]["main"] and by_name["assistant"]["internet_access_allowed"]
    assert by_name["assistant"]["installations"] == []
    assert by_name["assistant"]["web_audience"] == []
    assert not by_name["ops"]["internet_access_allowed"]
    assert by_name["ops"]["installations"] == ["slack"]
    assert by_name["ops"]["web_audience"] == ["member@example.com"]
    assert {(m["email"], m["admin"], m["seated"]) for m in payload["members"]} == {
        ("admin@example.com", True, False),
        ("member@example.com", False, True),
    }
    assert payload["seats"] == {"limit": 5, "included": 2}
    assert [
        (
            cap["scope"],
            cap["subject"],
            cap["window_seconds"],
            cap["limit_micro_usd"],
            cap["on_breach"],
        )
        for cap in payload["caps"]
    ] == [
        ("agent", "ops", 3_600, 5_000_000, "park"),
        ("member", "member@example.com", 86_400, 5_000_000, "park"),
        ("workspace", None, 86_400, 5_000_000, "park"),
    ]
    assert payload["deploy"]["sandbox_internet"] is False
    assert [
        (entry["name"], entry["version"], entry["sandbox_internet"])
        for entry in payload["deploy"]["extensions"]
    ] == [
        ("scheduled_tasks", "0.1.0", False),
        ("sites", "0.1.0", False),
        ("stub", "0", False),
        ("todos", "0.1.0", False),
        ("web", "0.1.0", False),
    ]


async def test_admin_view_reports_ungated_seats(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The default deploy keeps both seat bounds NULL: auto-seating seats every member at
    creation, so the wire carries null bounds beside seated members — the page drops its seat
    column on this shape because the bounds, not per-member state, are what gate anything."""
    client, workspace_id, _agent_id = web
    admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, _member_token = await _seed_member(workspace_id, "member@example.com")
    async with workspace_tx() as connection:
        await Seats(workspace_id).auto_seat(connection, admin_id)
        await Seats(workspace_id).auto_seat(connection, member_id)
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert view.status_code == 200
    payload = view.json()
    assert payload["seats"] == {"limit": None, "included": None}
    assert {(m["email"], m["seated"]) for m in payload["members"]} == {
        ("admin@example.com", True),
        ("member@example.com", True),
    }


async def test_a_non_admin_is_not_found_on_the_admin_view(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    denied = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert denied.status_code == 404
    assert "admin@example.com" not in denied.text


def test_only_declared_asset_suffixes_are_served(tmp_path: Path) -> None:
    """This read runs on every boot, so nothing the build directory happens to hold may wedge it:
    a directory named for a declared suffix is the entry that reaches `read_bytes` when only the
    suffix is checked. Suffixes answer the other half — turning on `build.sourcemap` publishes
    nothing until someone declares `.map` here."""
    assets = tmp_path / "assets"
    (assets / "nested").mkdir(parents=True)
    (assets / "chunks.js").mkdir()
    (assets / "index-abc.js").write_text("boot()")
    (assets / "index-abc.css").write_text("body{}")
    (assets / "index-abc.js.map").write_text('{"sources":["main.tsx"]}')
    (assets / ".DS_Store").write_bytes(b"\x00")

    served = load_assets(assets)

    assert sorted(served) == ["assets/index-abc.css", "assets/index-abc.js"]
    assert served["assets/index-abc.js"] == (b"boot()", "text/javascript; charset=utf-8")


async def test_every_asset_the_portal_references_is_served_from_the_surface_itself(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The page names no origin but this surface. Splitting the stylesheet out of the page makes
    the absence of an external URL insufficient on its own — a page referencing a file nobody
    serves carries no external origin either — so every `src` and `href` it names must resolve
    under the surface's own static path and answer with the media type its element expects.
    Only the shell names these and the shell serves to a session, so an asset read carrying none is
    401; each revalidates by etag rather than transferring on every load."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    assert PORTAL_HTML is not None, f"portal app is not built — run `{PORTAL_BUILD}`"
    assert "<!doctype html>" in PORTAL_HTML
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in PORTAL_HTML

    referenced = set(re.findall(r'(?:src|href)="([^"]+)"', PORTAL_HTML))
    assets = {ref for ref in referenced if ref.startswith("/surface/web/static/")}
    assert any(ref.endswith(".js") for ref in assets)
    assert any(ref.endswith(".css") for ref in assets)
    for ref in referenced:
        assert ref.startswith("/surface/web"), ref

    for ref in sorted(assets):
        assert (await client.get(ref)).status_code == 401, ref

        signed_in = await client.get(ref, headers=cookie)
        assert signed_in.status_code == 200
        expected = "text/javascript" if ref.endswith(".js") else "text/css"
        assert signed_in.headers["content-type"].startswith(expected)
        assert signed_in.text.strip()

        etag = signed_in.headers["etag"]
        unchanged = await client.get(ref, headers={**cookie, "if-none-match": etag})
        assert unchanged.status_code == 304

    missing = await client.get("/surface/web/static/assets/nothing.css", headers=cookie)
    assert missing.status_code == 404
    traversal = await client.get("/surface/web/static/assets/..%2F..%2Fsurface.py", headers=cookie)
    assert traversal.status_code == 404


async def test_a_sessionless_arrival_is_sent_to_sign_in_and_the_posted_token_opens_one(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The portal offers no second way in: a GET with no session redirects to the deploy's one
    sign-in page, serving nothing of the shell. The bearer never rides a URL — the one POST that
    opens a session lands the form token as the host-only session cookie — HttpOnly, Secure, and
    `lax`, because arrival is a cross-site navigation from the gateway's signed-in card — then
    redirects into the portal."""
    client, workspace_id, _agent_id = web
    page = await client.get("/surface/web")
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    token = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    opened = await client.post("/surface/web", data={"token": token})
    assert opened.status_code == 303
    cookie = opened.headers["set-cookie"]
    assert cookie.startswith(f"ufo_session={token}")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    shell = await client.get("/surface/web", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert shell.status_code == 200
    assert shell.text == PORTAL_FILE.read_text()
    client.cookies.clear()
    tokenless = await client.post("/surface/web", data={})
    assert tokenless.status_code == 401
    assert "Domain" not in cookie


async def test_a_clicked_conversation_survives_the_sign_in_it_lands_in(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `view on web` click by a signed-out member keeps its target: the conversation names itself
    in the query (a fragment would never reach the server), so it rides the redirect to the
    sign-in page and the POST the card makes there redirects onto the same conversation. The
    target is re-parsed as a UUID, so a mixed-case id normalizes and anything else is dropped
    rather than reflected into the redirect."""
    client, workspace_id, _agent_id = web
    conversation_id = uuid4()
    page = await client.get(f"/surface/web?c={conversation_id}")
    assert page.status_code == 303
    assert page.headers["location"] == f"/login?c={conversation_id}"
    cased = await client.get(f"/surface/web?c={str(conversation_id).upper()}")
    assert cased.headers["location"] == f"/login?c={conversation_id}"
    junk = await client.get("/surface/web?c=../../login")
    assert junk.headers["location"] == "/login"
    token = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    opened = await client.post(f"/surface/web?c={conversation_id}", data={"token": token})
    assert opened.status_code == 303
    assert opened.headers["location"].endswith(f"/surface/web?c={conversation_id}")


@pytest.mark.parametrize("pasted", ["tok\rnl", "tok\x00x", "tok日", "tok x"])
async def test_a_token_outside_the_bearer_alphabet_answers_400(
    web: tuple[AsyncClient, UUID, UUID], pasted: str
) -> None:
    """A pasted value outside the bearer alphabet is refused before a Set-Cookie header is built —
    nothing outside it can be a bearer, and the control-character and non-latin-1 cases would
    raise inside the cookie writer (a 500 without the shape check; a space would merely land
    quoted). The live cookie is what routes the request to the handler (a form-only garbage token
    dies at identify with 401)."""
    client, workspace_id, _agent_id = web
    session = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    refused = await client.post(
        "/surface/web",
        data={"token": pasted},
        headers={"cookie": f"{SESSION_COOKIE}={session}"},
    )
    assert refused.status_code == 400
    assert "set-cookie" not in refused.headers


async def test_an_unverified_bearer_authenticates_nobody(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A POST behind a cookie that still resolves lands its form token unread, so the session cookie
    can carry a string nothing verified. That is inert: every request after it verifies the cookie
    again, and one that verifies against nothing reaches no member."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    landed = await client.post(
        "/surface/web",
        data={"token": "not-a-signed-bearer"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert landed.status_code == 303
    assert "ufo_session=not-a-signed-bearer" in landed.headers["set-cookie"]
    refused = await client.get(
        "/surface/web/api/agents",
        headers={"cookie": f"{SESSION_COOKIE}=not-a-signed-bearer"},
    )
    assert refused.status_code == 401


async def test_a_bearer_whose_email_holds_no_member_row_names_that_fault(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The two refusals behind a 401 are told apart: a live bearer whose email holds no member row
    in this workspace names that and carries the session-fault header, so the page states the cause
    rather than advising a sign-in that cannot change it. Every other refusal carries no header,
    and signing in again is the remedy it already offers."""
    client, workspace_id, _agent_id = web
    stranger_token = mint_token(
        TOKEN_SECRET, str(workspace_id), "stranger@example.com", timedelta(hours=1)
    )
    stranger = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={stranger_token}"}
    )
    assert stranger.status_code == 401
    assert stranger.text == "no member with this email in this workspace"
    assert stranger.headers[SESSION_FAULT_HEADER] == NO_MEMBER_FAULT
    sessionless = await client.get("/surface/web/api/agents")
    assert sessionless.status_code == 401
    assert SESSION_FAULT_HEADER not in sessionless.headers


async def _seed_web_turn(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    terminal: TerminalFrame,
    title: str = "a seeded conversation",
) -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"{agent_id}/{email}/{uuid4().hex}",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ext_store).values(
                workspace_id=workspace_id,
                extension="web",
                key=f"chat/{conversation_id}",
                value={"agent_id": str(agent_id), "email": email, "title": title},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="ask",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _seed_subagent(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    parent_turn_id: UUID,
    terminal_text: str = "{}",
    profile: str = "general_purpose",
) -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=SUBAGENT_SURFACE,
                queue_key=str(turn_id),
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="{}",
                terminal=TerminalFrame(status="done", text=terminal_text).model_dump(mode="json"),
                parent_turn_id=parent_turn_id,
                subagent_profile=profile,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _collect_events(
    client: AsyncClient, token: str, turn_id: UUID
) -> list[tuple[str, dict[str, object]]]:
    collected: list[tuple[str, dict[str, object]]] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET",
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        ) as stream:
            assert stream.status_code == 200
            event = "message"
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    collected.append((event, json.loads(line.split(":", 1)[1].strip())))
                    if event == "terminal":
                        return collected
                elif not line:
                    event = "message"
    raise AssertionError("stream ended without a terminal frame")


QUESTION = AskUserInput(
    title="Pick a deploy window",
    questions=(
        AskQuestion(
            question="When should the deploy run?",
            options=(
                QuestionOption(label="Now"),
                QuestionOption(label="Tonight", description="after 22:00 UTC"),
            ),
        ),
        AskQuestion(
            question="Page the on-call?",
            options=(
                QuestionOption(label="Yes"),
                QuestionOption(label="No"),
            ),
        ),
    ),
)


async def test_question_affordance_admits_the_first_answer_only(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A turn that ended by asking renders its options on reload, and an answer click admits the
    answer as the conversation's next turn under a per-question idempotency key — a double click
    or a second tab joins the turn the first answer won, and the response names the landed body so
    only the winning click renders as the answer."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, asked_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="one question", question=QUESTION),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert loaded.json()["question"]["turn_id"] == str(asked_turn)
    assert loaded.json()["question"]["title"] == "Pick a deploy window"
    answer_headers = {
        **cookie,
        "x-ufo-answer-turn": str(asked_turn),
        "x-ufo-answer-question": "0",
    }
    STREAM_GATE.arm()
    first = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content="Now · When should the deploy run?".encode(),
        headers=answer_headers,
    )
    assert first.status_code == 200
    assert first.json()["body"] == "Now · When should the deploy run?"
    assert first.json()["conversation_id"] == str(conversation_id)
    await _consume(client, token, first.json()["turn_id"])
    second = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content="Tonight · When should the deploy run?".encode(),
        headers=answer_headers,
    )
    assert second.status_code == 200
    assert second.json()["turn_id"] == first.json()["turn_id"]
    assert second.json()["body"] == "Now · When should the deploy run?"
    STREAM_GATE.arm()
    sibling = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content="Yes · Page the on-call?".encode(),
        headers={**answer_headers, "x-ufo-answer-question": "1"},
    )
    assert sibling.status_code == 200
    assert sibling.json()["turn_id"] != first.json()["turn_id"]
    assert sibling.json()["body"] == "Yes · Page the on-call?"
    await _consume(client, token, sibling.json()["turn_id"])
    async with workspace_tx() as connection:
        answers = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.idempotency_key)
                .where(
                    tables.turn.c.idempotency_key.is_not(None),
                    tables.turn.c.workspace_id == workspace_id,
                )
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert [row.inbound for row in answers] == [
        "Now · When should the deploy run?",
        "Yes · Page the on-call?",
    ]
    assert answers[0].idempotency_key.endswith(":answer:0")
    assert answers[1].idempotency_key.endswith(":answer:1")


async def test_credential_prompts_stream_pending_and_fulfill_privately(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The web leg of the private credential handoff: the stream names only the prompts still
    awaiting values, a posted value lands through the sealed fulfillment without admitting a turn
    or touching a transcript, and the seal's member gate refuses anyone but the requester."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    sealed = seal_credential_request(
        CREDENTIAL_FERNET,
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("api_key", "signing_key")
        ),
    )
    request = CredentialRequest(
        reason="the acme connector needs its keys",
        prompts=(
            CredentialPrompt(slot="api_key", prompt="Acme API key"),
            CredentialPrompt(slot="signing_key", prompt="Acme signing key"),
        ),
        sealed=sealed,
    )
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="keys please", credential_request=request),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    events = dict(await _collect_events(client, token, turn_id))
    assert [p["slot"] for p in events["credentials"]["prompts"]] == ["api_key", "signing_key"]
    _member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    hijack = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "stolen"},
        headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
    )
    assert hijack.status_code == 403
    stored = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "s3cr3t"},
        headers=cookie,
    )
    assert stored.status_code == 200
    assert stored.json() == {"stored": "api_key"}
    assert await CredentialStore(fernet=CREDENTIAL_FERNET).get(workspace_id, "api_key") == "s3cr3t"
    events = dict(await _collect_events(client, token, turn_id))
    assert [p["slot"] for p in events["credentials"]["prompts"]] == ["signing_key"]
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert [p["slot"] for p in loaded.json()["credentials"]["prompts"]] == ["signing_key"]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert turns == 1


async def test_terminal_stream_carries_the_turns_child_work_and_its_own_children(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The stream states a run the same way a reload does — one projection — so the bubble a live
    turn leaves behind and the bubble the transcript draws hold the same tree."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Done."),
    )
    child_conversation, child_turn = await _seed_subagent(
        workspace_id,
        agent_id,
        member_id,
        turn_id,
        terminal_text='{"result": "It shipped Tuesday."}',
    )
    await Transcript(blob=blob, conversation_id=child_conversation).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="{}"),
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(id="call-1", name="fetch_url", input={"url": "https://x/y"}),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="call-1", content="…", activity=True),),
                ),
            ),
        )
    )
    grandchild_conversation, _grandchild_turn = await _seed_subagent(
        workspace_id, agent_id, member_id, child_turn, profile="deep_research"
    )

    events = dict(await _collect_events(client, token, turn_id))

    assert events["subagent"] == {
        "profile": "general_purpose",
        "conversation_id": str(child_conversation),
        "events": [
            {
                "kind": "tool",
                "name": "fetch_url",
                "preview": '{"url":"https://x/y"}',
                "description": "",
            }
        ],
        "output": "It shipped Tuesday.",
        "subagents": [
            {
                "profile": "deep_research",
                "conversation_id": str(grandchild_conversation),
                "events": [],
                "output": "",
                "subagents": [],
            }
        ],
    }


async def test_shared_files_stream_and_reload_as_download_links(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="here is the report"),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/report.pdf",
                workspace_id=workspace_id,
                filename="report.pdf",
                subject="the report",
                media_type="application/pdf",
                size_bytes=3,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    events = dict(await _collect_events(client, token, turn_id))
    (file,) = events["files"]["files"]
    assert file["filename"] == "report.pdf"
    assert file["size_bytes"] == 3
    assert file["url"].startswith("https://web/artifacts/")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert loaded.json()["files"][0]["url"].startswith("https://web/artifacts/")
    assert loaded.json()["files"][0]["size_bytes"] == 3


async def test_composer_files_land_in_the_workspace_before_the_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A multipart composer submit streams each attached file into the conversation's
    `web-inbox/` and admits one message naming the saved paths — colliding names get distinct
    files, and the turn's sandbox mounts them because the write precedes admission."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "read these"},
        files=[
            ("file", ("notes.txt", b"hello", "text/plain")),
            ("file", ("notes.txt", b"again", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.conversation_id).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).one()
    assert row.inbound.startswith("read these")
    assert "web-inbox/notes.txt" in row.inbound
    assert "web-inbox/notes-1.txt" in row.inbound
    inbox = sandboxes.workspace_root / str(row.conversation_id) / "web-inbox"
    assert (inbox / "notes.txt").read_bytes() == b"hello"
    assert (inbox / "notes-1.txt").read_bytes() == b"again"


async def test_an_oversize_request_is_refused_at_the_door(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal lands before the multipart parse runs, and the body the parser would choke on
    is what proves it: each request below carries a boundary its body never uses, so a parse that
    ran would surface as a 400 instead of the door's own status. A chunked body is refused whatever
    it declares — the server frames by the chunks, so a Content-Length beside them bounds
    nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    unparseable = {**cookie, "content-type": "multipart/form-data; boundary=never-used"}
    monkeypatch.setattr(web_surface, "MAX_REQUEST_BYTES", 4)
    oversize = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers=unparseable,
    )
    assert oversize.status_code == 413

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    chunked = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=_streamed(),
        headers=unparseable,
    )
    assert chunked.status_code == 411
    lying = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers={**unparseable, "transfer-encoding": "chunked"},
    )
    assert lying.status_code == 411
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_plain_body_is_bounded_by_what_it_consumes(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary composer submit and every answer click take the plain-body path, whose bound is
    the bytes actually read — so an oversize body is refused on both framings, the honest length
    and the chunked one that declares none."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    monkeypatch.setattr(web_surface, "MAX_INBOUND_BYTES", 16)
    declared = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"x" * 64, headers=cookie
    )
    assert declared.status_code == 413

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"x" * 64

    chunked = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=_streamed(), headers=cookie
    )
    assert chunked.status_code == 413
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_malformed_multipart_body_is_the_clients_400(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A multipart body its own boundary never appears in is the client's malformed request —
    refused like every other malformed shape, never a fault — and admits nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "content-type": "multipart/form-data; boundary=never-used",
        },
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_body_that_is_not_utf8_is_refused_not_rewritten(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A plain body that does not decode as UTF-8 is refused whole — the member's bytes are never
    silently substituted into the admitted turn."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"caf\xe9 in latin-1",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_multipart_message_part_lands_as_the_parsers_decode(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The multipart twin of the strict plain-path refusal, pinning where the guarantee ends: a
    `message` part arrives already decoded by starlette's form parser (UTF-8, falling back to
    latin-1), so the same bytes the plain path refuses admit here as that parser's reading —
    stated in `_parse_inbound`'s docstring and pinned so the paths' divergence is the parser's
    decode, never a silent drop."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    body = (
        b"--frame\r\n"
        b'Content-Disposition: form-data; name="message"\r\n\r\n'
        b"caf\xe9 in latin-1\r\n"
        b"--frame--\r\n"
    )
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=body,
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "content-type": "multipart/form-data; boundary=frame",
        },
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert inbound == b"caf\xe9 in latin-1".decode("latin-1")


async def test_a_urlencoded_chat_body_is_unsupported(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The composer posts plain text or multipart, never urlencoded — the odd shape is refused,
    not parsed."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "hi"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 415
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_the_token_form_reads_are_framed_at_both_doors(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Both places a token form is parsed — the identify fallback (no resolving cookie) and
    `open_session` behind one — refuse a chunked body with 411 and an over-limit declared length
    with 413 before any parse runs. Order is what the last two legs discriminate: each carries a
    boundary its body never uses, so a door that fired after the parse would answer the parse's
    own 400 instead of 411 — and `open_session`'s malformed multipart is the parse refusal
    itself, proving `_form`'s arm at this site."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    urlencoded = {"content-type": "application/x-www-form-urlencoded"}
    cookie = {"cookie": f"{SESSION_COOKIE}={token}", **urlencoded}

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"token=x"

    oversized = b"x" * (web_surface.MAX_FORM_BYTES + 1)
    anonymous_chunked = await client.post("/surface/web", content=_streamed(), headers=urlencoded)
    assert anonymous_chunked.status_code == 411
    anonymous_oversize = await client.post("/surface/web", content=oversized, headers=urlencoded)
    assert anonymous_oversize.status_code == 413
    session_chunked = await client.post("/surface/web", content=_streamed(), headers=cookie)
    assert session_chunked.status_code == 411
    session_oversize = await client.post("/surface/web", content=oversized, headers=cookie)
    assert session_oversize.status_code == 413
    unparseable = {
        "cookie": f"{SESSION_COOKIE}={token}",
        "content-type": "multipart/form-data; boundary=never-used",
    }

    async def _streamed_junk() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    door_before_parse = await client.post(
        "/surface/web", content=_streamed_junk(), headers=unparseable
    )
    assert door_before_parse.status_code == 411
    parse_refusal = await client.post(
        "/surface/web", content=b"not a multipart body at all", headers=unparseable
    )
    assert parse_refusal.status_code == 400


async def test_a_multibyte_message_at_the_char_bound_admits(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The byte door exists to bound the read, not to shrink the message bound: a message of
    exactly `MAX_INBOUND_CHARS` characters admits even when every character is four bytes — the
    widest UTF-8 makes — pinning the full 4-byte relationship at the real constants, no
    stand-ins."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    text = "\U0001d11e" * web_surface.MAX_INBOUND_CHARS
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=text.encode(),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert inbound == text


async def test_a_client_chosen_filename_is_never_a_path(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The content-disposition leaf is untrusted: path components drop, everything outside the
    safe charset collapses, and an absurd length caps on the stem so the suffix a read routes on
    survives it — the note names exactly what landed."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "renamed"},
        files=[
            ("file", ("../../we ird&name!!.txt", b"safe", "text/plain")),
            ("file", ("x" * 300 + ".txt", b"capped", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.conversation_id).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).one()
    assert "web-inbox/we-ird-name--.txt" in row.inbound
    assert row.inbound.endswith("web-inbox/" + "x" * 76 + ".txt]")
    inbox = sandboxes.workspace_root / str(row.conversation_id) / "web-inbox"
    assert (inbox / "we-ird-name--.txt").read_bytes() == b"safe"
    assert (inbox / ("x" * 76 + ".txt")).read_bytes() == b"capped"


async def test_a_files_note_cannot_blow_the_inbound_bound(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound applies to the admitted body — text plus the attached-files note — and a refusal
    lands nothing in the member's existing conversation: no new turn, no workspace file."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    opened = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new", content=b"hi", headers=cookie
    )
    await _consume(client, token, opened.json()["turn_id"])
    monkeypatch.setattr(web_surface, "MAX_INBOUND_CHARS", 64)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "short"},
        files=[("file", ("long-name-that-pads-the-note.txt", b"x", "text/plain"))],
        headers=cookie,
    )
    assert refused.status_code == 413
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
        conversation_id = (
            await connection.execute(sa.select(tables.conversation.c.id))
        ).scalar_one()
    assert turns == 1
    stray = (
        sandboxes.workspace_root
        / str(conversation_id)
        / "web-inbox"
        / "long-name-that-pads-the-note.txt"
    )
    assert not stray.exists()


async def test_malformed_answer_headers_are_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    bad_turn = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"Now",
        headers={**cookie, "x-ufo-answer-turn": "not-a-uuid"},
    )
    assert bad_turn.status_code == 400
    bad_index = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"Now",
        headers={**cookie, "x-ufo-answer-turn": str(uuid4()), "x-ufo-answer-question": "one"},
    )
    assert bad_index.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_identify_never_parses_a_multipart_body(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The identify token fallback reads only a urlencoded body — a multipart request never
    carries the session token, so an unauthenticated multipart POST is refused without its parse
    (and its disk spool) ever running, and a token smuggled as a multipart field authenticates
    nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    unparseable = {"content-type": "multipart/form-data; boundary=never-used"}
    anonymous = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers=unparseable,
    )
    assert anonymous.status_code == 401
    smuggled = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=f"token={token}".encode(),
        headers=unparseable,
    )
    assert smuggled.status_code == 401
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_message_part_that_is_not_text_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `message` part carrying a filename parses as a file, not text — refused loud instead of
    silently dropping the member's words from the admitted turn."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        files=[
            ("message", ("message.txt", b"typed words", "text/plain")),
            ("file", ("notes.txt", b"hello", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_credential_fulfillment_refusals(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every declared refusal produces: no session 401s, a chunked body 411s (a chunked malformed
    multipart too — the door fires before any parse could answer its 400), an over-limit declared
    length 413s, a malformed multipart body 400s (`_form`'s arm at this site), an empty value
    400s, a non-text field 400s, an over-cap value 413s, a slot the seal never named 403s, and a
    garbage seal 403s — none of them stores a byte, and the four bodies the portal splices into
    its member sentence are pinned to the lowercase unpunctuated wire register."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    sealed = seal_credential_request(
        CREDENTIAL_FERNET,
        CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("api_key",)),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    sessionless = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "value", "token": token},
    )
    assert sessionless.status_code == 401

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"sealed=x"

    chunked = await client.post(
        "/surface/web/credentials",
        content=_streamed(),
        headers={**cookie, "content-type": "application/x-www-form-urlencoded"},
    )
    assert chunked.status_code == 411

    async def _streamed_junk() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    unparseable = {**cookie, "content-type": "multipart/form-data; boundary=never-used"}
    door_before_parse = await client.post(
        "/surface/web/credentials", content=_streamed_junk(), headers=unparseable
    )
    assert door_before_parse.status_code == 411
    malformed = await client.post(
        "/surface/web/credentials", content=b"not a multipart body at all", headers=unparseable
    )
    assert malformed.status_code == 400
    assert malformed.text == "malformed form body"
    over_limit = await client.post(
        "/surface/web/credentials",
        content=b"x" * (web_surface.MAX_FORM_BYTES + 1),
        headers={**cookie, "content-type": "application/x-www-form-urlencoded"},
    )
    assert over_limit.status_code == 413
    assert over_limit.text == "request too large"
    not_text = await client.post(
        "/surface/web/credentials",
        data={"slot": "api_key", "value": "value"},
        files=[("sealed", ("sealed.bin", sealed.encode(), "application/octet-stream"))],
        headers=cookie,
    )
    assert not_text.status_code == 400
    empty = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "  "},
        headers=cookie,
    )
    assert empty.status_code == 400
    oversize = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "x" * 4_097},
        headers=cookie,
    )
    assert oversize.status_code == 413
    assert oversize.text == "value too large"
    off_seal = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "unnamed", "value": "value"},
        headers=cookie,
    )
    assert off_seal.status_code == 403
    assert off_seal.text.startswith("not stored: ")
    garbage = await client.post(
        "/surface/web/credentials",
        data={"sealed": "garbage", "slot": "api_key", "value": "value"},
        headers=cookie,
    )
    assert garbage.status_code == 403
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.credential))
        ).scalar_one()
    assert stored == 0


@pytest.mark.parametrize("pasted", ["tok\rnl", "tok\x00x", "tok日", "tok x"])
async def test_a_token_that_cannot_ride_a_cookie_answers_400(
    web: tuple[AsyncClient, UUID, UUID], pasted: str
) -> None:
    """A pasted value outside the bearer alphabet is refused before a Set-Cookie header is built —
    control characters and non-latin-1 raise inside the cookie writer, so without the shape check
    this exact request was a 500. The live cookie is what routes the request to the handler (a
    form-only garbage token dies at identify with 401)."""
    client, workspace_id, _agent_id = web
    session = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    refused = await client.post(
        "/surface/web",
        data={"token": pasted},
        headers={"cookie": f"{SESSION_COOKIE}={session}"},
    )
    assert refused.status_code == 400
    assert "set-cookie" not in refused.headers


INTENT_BODY = {
    "verb": "apply",
    "kind": "agent",
    "name": "assistant",
    "spec": {"model": "claude-sonnet-5", "internet_access_allowed": False, "reasoning": "medium"},
}


async def _agent_row(agent_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.agent.c.model,
                    tables.agent.c.internet_access_allowed,
                    tables.agent.c.reasoning,
                ).where(tables.agent.c.id == agent_id)
            )
        ).one()


async def _task_row(workspace_id: UUID, name: str) -> sa.Row | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.scheduled_task).where(
                    tables.scheduled_task.c.workspace_id == workspace_id,
                    tables.scheduled_task.c.name == name,
                )
            )
        ).one_or_none()


async def test_a_task_intent_creates_pauses_resumes_and_deletes(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The tasks panel's whole mutation surface through the one intent lane: a create lands the
    exact spec as a durable row, `paused: true` stops the schedule from claiming without losing
    the task, `paused: false` resumes it from the next cron fire, and a delete removes the row —
    each mutation a turn, each outcome the kind's own."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "creator@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "daily-brief",
            "spec": {"schedule": "0 9 * * *", "prompt": "write the daily brief"},
        },
        headers=cookie,
    )
    assert created.status_code == 200
    assert created.json()["applied"] is True
    row = await _task_row(workspace_id, "daily-brief")
    assert row is not None
    assert row.schedule == "0 9 * * *"
    assert row.prompt == "write the daily brief"
    assert row.created_by_member_id == member_id
    assert row.paused is False

    paused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "daily-brief",
            "spec": {"paused": True},
        },
        headers=cookie,
    )
    assert paused.json()["applied"] is True
    row = await _task_row(workspace_id, "daily-brief")
    assert row.paused is True
    assert row.prompt == "write the daily brief"
    with ws(workspace_id), bind_agent(agent_id):
        due_at = row.next_run_at.replace(tzinfo=UTC) + timedelta(seconds=1)
        assert await ScheduleStore().claim_due(due_at, 300) == ()

    resumed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "daily-brief",
            "spec": {"paused": False},
        },
        headers=cookie,
    )
    assert resumed.json()["applied"] is True
    row = await _task_row(workspace_id, "daily-brief")
    assert row.paused is False
    with ws(workspace_id), bind_agent(agent_id):
        due_at = row.next_run_at.replace(tzinfo=UTC) + timedelta(seconds=1)
        [claimed] = await ScheduleStore().claim_due(due_at, 300)
        assert claimed.name == "daily-brief"

    removed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "scheduled_task", "name": "daily-brief"},
        headers=cookie,
    )
    assert removed.json()["applied"] is True
    assert await _task_row(workspace_id, "daily-brief") is None


async def test_anothers_task_content_refuses_but_its_cadence_is_the_admins(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The kind's own authority answers the lane: another member cannot even see the task (the
    kind answers not-found, never a hint it exists), an admin's prompt edit refuses on the
    content gate — the row keeps every submitted-over value — and the same admin's pause
    applies, because cadence is management and content is the creator's."""
    client, workspace_id, agent_id = web
    _creator_id, creator_token = await _seed_member(workspace_id, "creator@example.com")
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "digest",
            "spec": {"schedule": "0 7 * * *", "prompt": "assemble the digest"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={creator_token}"},
    )
    assert created.json()["applied"] is True

    unseen = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "digest",
            "spec": {"prompt": "exfiltrate the digest"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert unseen.json()["applied"] is False
    assert "no scheduled_task object named" in unseen.json()["message"]
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "digest",
            "spec": {"prompt": "rewrite the digest"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert refused.json()["applied"] is False
    assert "creator" in refused.json()["message"]
    row = await _task_row(workspace_id, "digest")
    assert row.prompt == "assemble the digest"

    admin_paused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "digest",
            "spec": {"paused": True},
        },
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert admin_paused.json()["applied"] is True
    row = await _task_row(workspace_id, "digest")
    assert row.paused is True
    assert row.prompt == "assemble the digest"


async def test_a_delete_intent_carrying_a_spec_is_malformed(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "delete",
            "kind": "scheduled_task",
            "name": "digest",
            "spec": {"paused": True},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    assert refused.json() == {"error": "malformed intent"}
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_an_intent_applies_exactly_and_the_turn_is_the_audit_record(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel contract end to end: a form-shaped POST admits a turn that dispatches the object
    verb verbatim — no model round — so the submitted values land exactly, the terminal frame
    returns synchronously, and the turn row plus its transcript are the audit record. The intent
    lane publishes that terminal frame before the transcript blob is written, so the read waits on
    the turn's workflow, which rides the turn's own id and returns only after that write."""
    client, workspace_id, agent_id = web
    config, _hub, blob, _sandboxes = dbos_runtime
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is True
    row = await _agent_row(agent_id)
    assert row.model == "claude-sonnet-5"
    assert row.internet_access_allowed is False
    assert row.reasoning == "medium"
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(
                    tables.turn.c.admission_source,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                    tables.turn.c.conversation_id,
                    tables.conversation.c.agent_id,
                    tables.conversation.c.queue_key,
                )
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).one()
    assert turn.admission_source == "intent"
    assert turn.speaker_member_id == admin_id
    assert turn.status == "done"
    assert turn.agent_id == agent_id
    assert turn.queue_key == f"intent/{agent_id}/admin@example.com"
    assert outcome["message"] == "Saved."
    assert "claude-sonnet-5" in turn.inbound
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    try:
        handle = await dbos_client.retrieve_workflow_async(outcome["turn_id"])
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            await handle.get_result(polling_interval_sec=0.05)
    finally:
        dbos_client.destroy()
    recorded = await Transcript(blob=blob, conversation_id=turn.conversation_id).read()
    assert recorded is not None
    assert len(recorded.messages) == 2
    assert recorded.messages[-1].role == "assistant"


def _held_tail(
    frames: tuple[LiveFrame, ...], teardown_seconds: float
) -> Callable[..., AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]]:
    """A tail that yields `frames`, holds, and takes `teardown_seconds` to close — the real tail's
    two costs (frames that may never arrive, a close that cancels the pump and poll and waits for
    both) as exact timings, so what the intent deadline covers is decided rather than raced."""

    def tail(
        self: hub_tail.HubTailer, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]:
        async def held() -> AsyncGenerator[tuple[str, LiveFrame]]:
            try:
                for cursor, frame in enumerate(frames, start=1):
                    yield str(cursor), frame
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(teardown_seconds)

        return aclosing(held())

    return tail


async def test_an_intent_the_deadline_outruns_answers_that_it_is_still_applying(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deadline branch: a terminal frame that does not arrive in time answers 504 naming the
    turn to check back on, because the turn keeps running after the connection is done with it."""
    client, workspace_id, agent_id = web
    monkeypatch.setattr(web_panels, "INTENT_RESULT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(hub_tail.HubTailer, "tail", _held_tail((), 0.0))
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 504
    outcome = submitted.json()
    assert outcome["applied"] is False
    assert outcome["message"] == "The change is still being applied — check back."
    assert UUID(outcome["turn_id"])


async def test_a_deadline_spanning_the_tails_close_keeps_the_outcome_it_already_read(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the tail takes time, and the answer is already decided by then: the deadline bounds
    reading frames, never the close that follows, so a slow close answers with the outcome the
    terminal frame carried instead of the 504 that means the change had not landed yet."""
    client, workspace_id, agent_id = web
    monkeypatch.setattr(web_panels, "INTENT_RESULT_TIMEOUT_SECONDS", 0.05)
    applied = Terminal(frame=TerminalFrame(status="done", text="applied"))
    monkeypatch.setattr(hub_tail.HubTailer, "tail", _held_tail((applied,), 0.2))
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is True
    assert outcome["message"] == "Saved."


async def test_an_answered_intent_leaves_no_tail_running(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intent answers on the turn's first terminal frame, returning out of the middle of the
    tail — which owns a hub subscription and the pump and poll feeding it. Every tail the route
    opens is held for the length of the test, so what the route itself released is what this
    asserts: an answer costs the turn's live leg nothing beyond the answer."""
    client, workspace_id, agent_id = web
    opened: list[AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]] = []
    tail = hub_tail.HubTailer.tail

    def recorded_tail(
        self: hub_tail.HubTailer, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]:
        opened.append(tail(self, turn_id, since))
        return opened[-1]

    monkeypatch.setattr(hub_tail.HubTailer, "tail", recorded_tail)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.json()["applied"] is True
    assert opened, "the intent answered without tailing its turn"
    running = [
        task
        for task in asyncio.all_tasks()
        if task.get_coro().__qualname__ in ("_pump", "_poll_status")
    ]
    assert not running


SKILL_MD = "---\nname: release-notes\ndescription: How release notes read.\n---\nWrite tersely.\n"


OTHER_SKILL_MD = "---\nname: other\ndescription: Else.\n---\nBody.\n"
LEADERBOARD = (
    'a:["$","div",null,{"skills":['
    '{"source":"acme/kit","skillId":"release-notes","name":"release-notes","installs":12},'
    '{"source":"acme/kit","skillId":"other","name":"other","installs":40},'
    '{"source":"acme/kit","skillId":"release-notes","name":"release-notes","installs":12},'
    '{"source":"open.example.com","skillId":"hosted","name":"hosted","installs":900},'
    '{"source":"acme/kit","skillId":"undocumented","name":"undocumented","installs":7}'
    "]}]"
)
DOCUMENTS = {"release-notes": SKILL_MD, "other": OTHER_SKILL_MD}


def _directory(calls: list[str]) -> Callable[[httpx.Request], Response]:
    def handler(request: httpx.Request) -> Response:
        assert request.url.host == "skills.sh"
        calls.append(request.url.path)
        if request.url.path == "/":
            assert request.headers["RSC"] == "1"
            return Response(200, text=LEADERBOARD)
        if request.url.path == "/api/search":
            assert request.url.params["q"] == "release"
            assert request.url.params["limit"] == "24"
            return Response(
                200,
                json={
                    "skills": [
                        {"skillId": "release-notes", "source": "acme/kit", "installs": 12},
                        {"skillId": "other", "source": "acme/kit", "installs": 40},
                    ]
                },
            )
        owner, repo, skill = request.url.path.removeprefix("/api/download/").split("/")
        assert (owner, repo) == ("acme", "kit")
        if skill not in DOCUMENTS:
            return Response(200, json={"files": [{"path": "README.md", "contents": "not a skill"}]})
        return Response(200, json={"files": [{"path": "SKILL.md", "contents": DOCUMENTS[skill]}]})

    return handler


async def test_community_skills_list_and_fetch(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no query the community listing is the directory's leaderboard, deduplicated and ranked
    by installs; with one it is the directory's search. Either way a listed skill states the name,
    the source and the install count the directory publishes, a listing and a fetched document are
    each read once and held, and an upstream failure answers 502 with the sentence the member
    reads, never an empty list."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    calls: list[str] = []
    monkeypatch.setattr(
        web_surface,
        "COMMUNITY",
        web_community.CommunitySkills(transport=MockTransport(_directory(calls))),
    )

    short = await client.get(f"/surface/web/agents/{agent_id}/skills/community?q=x", headers=cookie)
    assert short.status_code == 400

    popular = await client.get(f"/surface/web/agents/{agent_id}/skills/community", headers=cookie)
    assert popular.status_code == 200
    assert popular.json()["skills"] == [
        {"name": "other", "source": "acme/kit", "installs": 40},
        {"name": "release-notes", "source": "acme/kit", "installs": 12},
        {"name": "undocumented", "source": "acme/kit", "installs": 7},
    ]
    assert calls == ["/"]

    calls.clear()
    again = await client.get(f"/surface/web/agents/{agent_id}/skills/community", headers=cookie)
    assert again.json() == popular.json()
    assert calls == []

    found = await client.get(
        f"/surface/web/agents/{agent_id}/skills/community?q=release", headers=cookie
    )
    assert found.status_code == 200
    assert [skill["name"] for skill in found.json()["skills"]] == ["other", "release-notes"]
    assert calls == ["/api/search"]

    calls.clear()
    fetched = await client.get(
        f"/surface/web/agents/{agent_id}/skills/community/acme/kit/release-notes",
        headers=cookie,
    )
    assert fetched.status_code == 200
    assert fetched.json() == {
        "name": "release-notes",
        "description": "How release notes read.",
        "instructions": "Write tersely.",
        "document": SKILL_MD,
    }

    assert calls == ["/api/download/acme/kit/release-notes"]

    calls.clear()
    reopened = await client.get(
        f"/surface/web/agents/{agent_id}/skills/community/acme/kit/release-notes",
        headers=cookie,
    )
    assert reopened.json() == fetched.json()
    assert calls == []

    absent = await client.get(
        f"/surface/web/agents/{agent_id}/skills/community/acme/kit/undocumented", headers=cookie
    )
    assert absent.status_code == 404

    monkeypatch.setattr(
        web_surface,
        "COMMUNITY",
        web_community.CommunitySkills(
            transport=MockTransport(lambda _request: Response(429, text="rate_limit_exceeded"))
        ),
    )
    limited = await client.get(
        f"/surface/web/agents/{agent_id}/skills/community/acme/kit/release-notes", headers=cookie
    )
    assert limited.status_code == 502
    assert "60 an hour" in limited.text

    monkeypatch.setattr(
        web_surface,
        "COMMUNITY",
        web_community.CommunitySkills(
            transport=MockTransport(lambda _request: Response(500, text="down"))
        ),
    )
    failed = await client.get(f"/surface/web/agents/{agent_id}/skills/community", headers=cookie)
    assert failed.status_code == 502
    assert failed.text == "The skill directory answered 500."


async def test_a_skill_intent_creates_replaces_and_deletes(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The skill kind rides the intent lane end to end for any member: apply creates a skill the
    skills projection lists as member-authored, a second apply on the same name replaces it,
    delete tears it out whole — the projection forgets it and the store holds no rows — and a
    walled agent's lane stays not-found for the same body."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    async def listed() -> dict[str, dict[str, str]]:
        answer = await client.get(f"/surface/web/agents/{agent_id}/skills", headers=cookie)
        assert answer.status_code == 200
        return {skill["name"]: skill for skill in answer.json()["skills"]}

    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "release-notes",
            "spec": {"files": {"SKILL.md": SKILL_MD}},
        },
        headers=cookie,
    )
    assert created.status_code == 200
    assert created.json()["applied"] is True, created.json()
    saved = (await listed())["release-notes"]
    assert saved["description"] == "How release notes read."
    assert saved["origin"] == "member"
    assert saved["instructions"] == "Write tersely."
    replaced = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "release-notes",
            "spec": {
                "files": {
                    "SKILL.md": SKILL_MD.replace("How release notes read.", "Terse and dated.")
                }
            },
        },
        headers=cookie,
    )
    assert replaced.json()["applied"] is True
    assert (await listed())["release-notes"]["description"] == "Terse and dated."
    deleted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "skill", "name": "release-notes"},
        headers=cookie,
    )
    assert deleted.json()["applied"] is True
    assert "release-notes" not in await listed()
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(user_skill))
        ).scalar_one()
    assert rows == 0
    walled = await client.post(
        f"/surface/web/agents/{uuid4()}/intents",
        json={"verb": "delete", "kind": "skill", "name": "release-notes"},
        headers=cookie,
    )
    assert walled.status_code == 404


async def test_a_refused_skill_intent_surfaces_the_kinds_error_and_writes_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A SKILL.md whose frontmatter name does not match the object name is the kind's own
    refusal: the outcome carries it, no skill lands, and no store row exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "other-name",
            "spec": {"files": {"SKILL.md": SKILL_MD}},
        },
        headers=cookie,
    )
    assert refused.status_code == 200
    outcome = refused.json()
    assert outcome["applied"] is False
    assert outcome["message"]
    skills = await client.get(f"/surface/web/agents/{agent_id}/skills", headers=cookie)
    assert skills.json()["skills"] == []
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(user_skill))
        ).scalar_one()
    assert rows == 0


async def test_an_agent_delete_intent_surfaces_the_kinds_refusal(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The widened verb Literal admits `delete` for the agent kind too, and the kind itself is
    the gate: agents are undeletable through objects, so the outcome is the refusal and the row
    survives — the same answer chat gives."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "agent", "name": "assistant"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    row = await _agent_row(agent_id)
    assert row.model == "claude-opus-4-8"


async def test_a_refused_intent_surfaces_the_refusal_and_applies_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The kind's own admin gate answers the panel: the refusal text returns, the turn commits
    failed, and the submitted values never land — no partial application."""
    client, workspace_id, agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    before = await _agent_row(agent_id)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is False
    assert "admin" in outcome["message"]
    assert await _agent_row(agent_id) == before
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).scalar_one()
    assert status == "failed"


async def test_an_out_of_audience_agent_takes_no_intent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A walled agent's intent lane is not-found like every portal route, writing nothing — while
    the main agent, which every member reaches, admits the turn and answers with the object
    verb's own refusal: the panel mutates exactly what chat would."""
    client, workspace_id, agent_id = web
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _outsider, token = await _seed_member(workspace_id, "outsider@example.com")
    denied = await client.post(
        f"/surface/web/agents/{walled_agent}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert denied.status_code == 404
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    outcome = refused.json()
    assert outcome["applied"] is False
    assert "admin" in outcome["message"]


async def test_a_source_intent_reaches_the_source_kind(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel's own envelope, reconstructed from the projection the panel reads, reaching the
    real `source` kind: Resync pulls the binding's next sync to now through the kind's registrar
    gate, and Remove takes the rows out. The envelope is built from `workspace/sources` exactly as
    `renderSources` builds it, so the projection's binding fields and the kind's `SourceSpec`
    cannot drift apart without this failing — and a source apply carries no model, so the
    agent-spec registry precheck must not swallow it."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="asana",
                config={"account": "acct-7", "stream": "workspaces"},
                subject=SHARED_SUBJECT,
                owner_member_id=admin_id,
                next_sync_at=datetime.now(UTC) + timedelta(hours=6),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    listed = await client.get("/surface/web/workspace/sources", headers=cookie)
    [projected] = listed.json()["sources"]
    spec = {
        "provider": projected["backend"],
        "streams": [projected["stream"]],
        "account_id": projected["account_id"],
        "base_url": projected["base_url"],
        "shared": projected["shared"],
    }

    resynced = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "source",
            "name": projected["name"],
            "spec": {**spec, "resync": True},
        },
        headers=cookie,
    )

    assert resynced.status_code == 200
    outcome = resynced.json()
    assert outcome["applied"] is True, outcome["message"]
    assert "No model named" not in outcome["message"]
    async with workspace_tx() as connection:
        due = (
            await connection.execute(
                sa.select(tables.source.c.next_sync_at).where(tables.source.c.id == source_id)
            )
        ).scalar_one()
    assert due <= datetime.now(UTC).replace(tzinfo=due.tzinfo)  # the kind pulled it forward

    removed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "source", "name": projected["name"]},
        headers=cookie,
    )

    assert removed.status_code == 200
    assert removed.json()["applied"] is True, removed.json()["message"]
    async with workspace_tx() as connection:
        live = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.source)
                .where(tables.source.c.id == source_id, tables.source.c.removed_at.is_(None))
            )
        ).scalar_one()
    assert live == 0
    async with workspace_tx() as connection:
        inbound = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).order_by(tables.turn.c.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert [json.loads(entry)["tool"] for entry in inbound] == ["object_apply", "object_delete"]
    assert projected["name"] in json.loads(inbound[1])["input"]["name"]


async def test_grant_intents_flip_and_revoke_under_the_owner_gate(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The connections panel's two mutations ride the intent lane against the `connector_grant`
    kind, named by the grant's stable object name: a non-owner member's flip surfaces the kind's
    own refusal and changes nothing, the owner's flip lands exactly, and the owner's delete
    revokes the grant row — each outcome synchronous and audited as a turn."""
    client, workspace_id, agent_id = web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    await _seed_connection(workspace_id, agent_id, owner_id, "github", shared=True)
    name = account_object_name("github", "github-account")
    flip = {
        "verb": "apply",
        "kind": "connector_grant",
        "name": name,
        "spec": {"provider": "github", "account_id": "github-account", "shared": False},
    }
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=flip,
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert "owner" in refused.json()["message"]
    async with workspace_tx() as connection:
        still_shared = (
            await connection.execute(sa.select(tables.connector_grant.c.shared))
        ).scalar_one()
    assert still_shared is True
    flipped = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=flip,
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert flipped.status_code == 200
    assert flipped.json()["applied"] is True
    async with workspace_tx() as connection:
        shared_now = (
            await connection.execute(sa.select(tables.connector_grant.c.shared))
        ).scalar_one()
    assert shared_now is False
    revoked = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "connector_grant", "name": name},
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["applied"] is True
    async with workspace_tx() as connection:
        grants_left = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert grants_left == 0


async def test_a_connect_intent_leaves_the_private_handoff_on_the_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel's connect rides the same private OAuth handoff as chat's `connect_account`: the
    intent's terminal carries the connect request (never a URL), the member's stream mints their
    private authorization URL from it, and an unknown provider is the tool's own refusal."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    flow = ConnectFlow(
        providers={"github": ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    try:
        submitted = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "connect", "kind": "connection", "name": "github"},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert submitted.status_code == 200
        outcome = submitted.json()
        assert outcome["applied"] is True
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(
                        tables.turn.c.id == UUID(outcome["turn_id"])
                    )
                )
            ).scalar_one()
        frame = TerminalFrame.model_validate(terminal)
        assert frame.connect_request is not None
        assert frame.connect_request.provider == "github"
        assert frame.connect_request.requester_member_id == member_id
        assert "oauth.example.test" not in json.dumps(terminal)
        streamed = await client.get(
            f"/surface/web/turns/{outcome['turn_id']}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        lines = streamed.text.splitlines()
        connect_data = json.loads(lines[lines.index("event: connect") + 1].removeprefix("data: "))
        assert connect_data["url"].startswith("https://oauth.example.test/authorize")
        unknown = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "connect", "kind": "connection", "name": "nonesuch"},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert unknown.status_code == 200
        assert unknown.json()["applied"] is False
    finally:
        install_connect_flow(None)


async def test_connect_pairs_with_the_connection_kind_exactly(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`connect` is the one verb no kind gates, so the model pins its pair both ways: `connect`
    with any other kind, and any other verb with the `connection` kind, are malformed intents —
    400 before any turn exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    for body in (
        {"verb": "connect", "kind": "connector_grant", "name": "github"},
        {"verb": "connect", "kind": "agent", "name": "assistant"},
        {"verb": "apply", "kind": "connection", "name": "github"},
        {"verb": "delete", "kind": "connection", "name": "github"},
    ):
        refused = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json=body,
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_an_intent_naming_another_kind_is_refused_at_validation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """ApplyIntent.kind's Literal is the whole gate keeping this route from becoming a general
    object_apply endpoint: the panels mutate agents and members today, and an intent naming any
    other kind must die at validation, before a turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    for kind in ("connection", "conversation", "artifact"):
        refused = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "apply", "kind": kind, "name": "x", "spec": {"admin": True}},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_credential_set_intent_mints_a_prompt_and_the_seal_stores_the_value(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Set/replace end to end with no pending chat request: the panel's `request` intent dispatches
    `request_credentials` verbatim, the turn's terminal frame carries the server-minted seal and
    its prompts, and the value crosses only in the sealed fulfillment — the intent outcome, the
    audit turn, and every response body stay secret-free. A deploy-written slot is not a slot the
    panel can name."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    minted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-api-key"},
        headers=cookie,
    )
    assert minted.status_code == 200
    outcome = minted.json()
    assert outcome["applied"] is True
    request = outcome["credentials"]
    assert [prompt["slot"] for prompt in request["prompts"]] == ["acme_api_key"]
    assert [prompt["prompt"] for prompt in request["prompts"]] == ["ACME API key"]
    assert request["sealed"]
    stored = await client.post(
        "/surface/web/credentials",
        data={"sealed": request["sealed"], "slot": "acme_api_key", "value": "s3cr3t-value"},
        headers=cookie,
    )
    assert stored.status_code == 200
    assert "s3cr3t-value" not in stored.text
    assert (
        await CredentialStore(fernet=CREDENTIAL_FERNET).get(workspace_id, "acme_api_key")
        == "s3cr3t-value"
    )
    async with workspace_tx() as connection:
        audit = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.inbound, tables.turn.c.terminal)
            )
        ).one()
    assert audit.status == "done"
    assert "s3cr3t-value" not in audit.inbound
    assert "s3cr3t-value" not in str(audit.terminal)
    machinery = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-install-seal"},
        headers=cookie,
    )
    refused = machinery.json()
    assert refused["applied"] is False
    assert "No credential slot named" in refused["message"]


async def test_a_credential_clear_intent_empties_the_slot_and_gates_on_admin(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Clear rides the intent lane to the credential kind's delete verb: an admin's clear empties
    the stored value and the slot lists as empty again; a non-admin gets the kind's own admin
    refusal — for clear and for minting a prompt alike — and the value stands."""
    client, workspace_id, agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    store = CredentialStore(fernet=CREDENTIAL_FERNET)
    await store.put(workspace_id, "acme_api_key", "live-value")
    member_cookie = {"cookie": f"{SESSION_COOKIE}={member_token}"}
    held = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "credential", "name": "acme-api-key"},
        headers=member_cookie,
    )
    refusal = held.json()
    assert refusal["applied"] is False
    assert "admin" in refusal["message"]
    assert await store.get(workspace_id, "acme_api_key") == "live-value"
    unminted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-api-key"},
        headers=member_cookie,
    )
    assert unminted.json()["applied"] is False
    assert "admin" in unminted.json()["message"]
    cleared = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "credential", "name": "acme-api-key"},
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert cleared.json()["applied"] is True
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, "acme_api_key")
    listed = await client.get(
        "/surface/web/workspace/credentials",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    slots = {entry["slot"]: entry for entry in listed.json()["slots"]}
    assert slots["acme_api_key"]["filled"] is False
    assert slots["acme_api_key"]["name"] == "acme-api-key"


async def test_a_credential_intent_never_carries_a_spec(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The kind pairing is the gate: a credential slot's value is set through its private prompt,
    so an apply naming the kind — the shape that would carry a secret in an intent body — is
    malformed before any turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    crossed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "credential",
            "name": "acme-api-key",
            "spec": {"value": "s3cr3t"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert crossed.status_code == 400
    assert "s3cr3t" not in crossed.text
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_malformed_intent_answers_400_before_any_turn(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "rename", "kind": "agent", "name": "assistant"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_overview_projects_spec_schema_ceiling_and_admin_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The settings panel's read: the agent row beside its prompt digest and bound surfaces, the
    deploy internet capability as the ceiling, the writable spec's own schema, and — admins only —
    the web audience this extension grants (empty for the main agent, which no grant ever holds).
    The read answers the agent's whole web audience — every member on the main agent — while
    the audience list itself stays the admin's."""
    client, workspace_id, agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface="slack",
                installation_id="T123",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    seen = await client.get(
        f"/surface/web/agents/{agent_id}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert seen.status_code == 200
    data = seen.json()
    assert data["agent"]["name"] == "assistant"
    assert data["agent"]["surfaces"] == ["slack"]
    assert data["agent"]["prompt"] == "be brief"
    assert len(data["agent"]["prompt_digest"]) > 8
    assert data["agent"]["updated_at"].endswith("+00:00")
    assert data["deploy"]["sandbox_internet"] is False
    assert data["models"] == ["auto", "claude-opus-4-8", "claude-sonnet-5"]
    assert data["spec"] == {
        "model": "claude-opus-4-8",
        "internet_access_allowed": True,
        "reasoning": "high",
    }
    assert set(data["spec_schema"]["properties"]) == {
        "model",
        "internet_access_allowed",
        "reasoning",
    }
    assert data["audience"] == []
    granted_view = await client.get(
        f"/surface/web/agents/{second_agent}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert granted_view.json()["audience"] == ["member@example.com"]
    member_view = await client.get(
        f"/surface/web/agents/{second_agent}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert member_view.status_code == 200
    assert member_view.json()["audience"] is None
    ungranted_main = await client.get(
        f"/surface/web/agents/{agent_id}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert ungranted_main.status_code == 200
    assert ungranted_main.json()["agent"]["name"] == "assistant"
    assert ungranted_main.json()["audience"] is None
    stranger = await client.get(
        f"/surface/web/agents/{uuid4()}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert stranger.status_code == 404


async def test_concurrent_intents_serialize_on_the_members_intent_conversation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Intent admission never folds into a live turn: two racing submits land as two whole turns
    on the member's one intent conversation with the agent, and the per-conversation partition
    runs them in order — both answer with their own turn's outcome."""
    client, workspace_id, agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    token = mint_token(TOKEN_SECRET, str(workspace_id), "admin@example.com", timedelta(hours=1))
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    first, second = await asyncio.gather(
        client.post(f"/surface/web/agents/{agent_id}/intents", json=INTENT_BODY, headers=cookie),
        client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={
                "verb": "apply",
                "kind": "agent",
                "name": "assistant",
                "spec": {"model": "auto", "internet_access_allowed": True, "reasoning": "auto"},
            },
            headers=cookie,
        ),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["applied"] is True and second.json()["applied"] is True
    assert first.json()["turn_id"] != second.json()["turn_id"]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.seq, tables.conversation.c.queue_key)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.conversation.c.queue_key.like("intent/%"))
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert [turn.seq for turn in turns] == [1, 2]
    assert {turn.queue_key for turn in turns} == {f"intent/{agent_id}/admin@example.com"}


async def test_a_capped_members_intent_parks_and_the_panel_reads_the_reason(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Spend enforcement runs at admission for intents: a breached park cap holds the turn and the
    submit answers with the park message instead of applying."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, admin_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="park",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    before = await _agent_row(agent_id)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is False
    assert outcome["message"]
    assert await _agent_row(agent_id) == before
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).scalar_one()
    assert status == "parked"


async def test_a_model_outside_the_registry_refuses_before_any_turn(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A stored unknown model wedges the agent's every later turn at setup, so the submit refuses
    it before a turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "assistant",
            "spec": {"model": "claude-sonnet-5-typo", "internet_access_allowed": False},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json() == {
        "applied": False,
        "message": "No model named 'claude-sonnet-5-typo'.",
    }
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert (await _agent_row(agent_id)).model == "claude-opus-4-8"


async def test_an_oversized_intent_answers_413(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    body = dict(INTENT_BODY, spec={"model": "x" * 20_000, "internet_access_allowed": False})
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=body,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413


async def test_overview_reports_the_deploy_internet_ceiling_when_granted(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The True direction of the deploy ceiling: a manifest declaring sandbox_internet makes the
    overview report the capability the agent setting narrows."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (web_manifest(), Manifest(name="net", version="0.0.1", sandbox_internet=True)),
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        seen = await client.get(
            f"/surface/web/agents/{agent_id}/overview",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    dbos_client.destroy()
    assert seen.status_code == 200
    assert seen.json()["deploy"]["sandbox_internet"] is True


def test_outcome_strips_the_error_class_and_names_a_bare_status() -> None:
    """The three outcome transforms members read: a dispatch refusal loses its exception-class
    prefix but keeps the kind's reason, a frame with neither message nor text reads as
    `Not applied (<status>).` instead of one bare word, and success is the fixed word, never the
    tool's JSON."""
    turn_id = uuid4()
    refused = json.loads(
        _outcome(
            TerminalFrame(
                status="failed",
                error_class="IntentRefused",
                error_message="AdminRequired: editing an agent requires a workspace admin",
            ),
            turn_id,
        ).body
    )
    assert refused == {
        "applied": False,
        "message": "editing an agent requires a workspace admin",
        "turn_id": str(turn_id),
    }
    bare = json.loads(_outcome(TerminalFrame(status="cancelled"), turn_id).body)
    assert bare["message"] == "Not applied (cancelled)."
    saved = json.loads(
        _outcome(TerminalFrame(status="done", text='{"result": "updated"}'), turn_id).body
    )
    assert saved == {"applied": True, "message": "Saved.", "turn_id": str(turn_id)}


async def test_an_admin_creates_an_agent_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The administration view's create rides the same lane as every panel mutation: an admin's
    create intent on the main agent's lane lands a fresh non-main row with exactly the submitted
    configuration; without a prompt, under a taken name, or from a non-admin the kind's refusal
    returns — and nothing is created."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    envelope = {
        "verb": "apply",
        "kind": "agent",
        "name": "research",
        "spec": {
            "model": "claude-sonnet-5",
            "internet_access_allowed": False,
            "reasoning": "medium",
            "prompt": "be curious",
        },
    }
    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert created.status_code == 200
    assert created.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.agent).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == "research",
                )
            )
        ).one()
    assert (row.prompt, row.model, row.internet_access_allowed, row.reasoning, row.is_main) == (
        "be curious",
        "claude-sonnet-5",
        False,
        "medium",
        False,
    )
    listed = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert "research" in [agent["name"] for agent in listed.json()["agents"]]
    duplicate = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert duplicate.json()["applied"] is False
    assert "proposal path" in duplicate.json()["message"]
    promptless = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "second",
            "spec": {
                "model": "claude-sonnet-5",
                "internet_access_allowed": True,
                "reasoning": "auto",
            },
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert promptless.json()["applied"] is False
    assert "requires a prompt" in promptless.json()["message"]
    outsider = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={**envelope, "name": "third"},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.json()["applied"] is False
    assert "admin" in outsider.json()["message"]
    async with workspace_tx() as connection:
        names = (
            (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(names) == ["assistant", "research"]


async def test_member_seat_and_role_ride_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The members table's controls are member-kind applies on the main agent's lane: role and
    seat changes land exactly, and the kind's own guards answer — the last admin cannot be
    demoted, the last seated admin cannot be unseated, and a non-admin mutates nobody."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now())
            .where(tables.member.c.id.in_((admin_id, member_id)))
        )

    def envelope(member: UUID, *, admin: bool, seated: bool) -> dict:
        return {
            "verb": "apply",
            "kind": "member",
            "name": str(member),
            "spec": {"admin": admin, "seated": seated},
        }

    promoted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(member_id, admin=True, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert promoted.json()["applied"] is True
    unseated = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(member_id, admin=True, seated=False),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert unseated.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.member.c.is_admin, tables.member.c.seated_at).where(
                    tables.member.c.id == member_id
                )
            )
        ).one()
    assert row.is_admin is True
    assert row.seated_at is None
    demote_last_seated = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=False, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert demote_last_seated.json()["applied"] is False
    assert "seated admin" in demote_last_seated.json()["message"]
    unseat_last = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=True, seated=False),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert unseat_last.json()["applied"] is False
    assert "last seated" in unseat_last.json()["message"]
    outsider = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=False, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.json()["applied"] is False
    assert "admin" in outsider.json()["message"]


async def test_audience_intents_write_the_grant_store(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The administration view's audience controls ride the target agent's own intent lane and
    land in the same store the chat verbs write: a grant makes the agent appear in the member's
    portal, a revoke removes it, and a non-admin changes nothing."""
    client, workspace_id, _agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    granted = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "grant_web_access", "email": "member@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert granted.status_code == 200
    assert granted.json()["applied"] is True, granted.json()
    listed = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" in [agent["name"] for agent in listed.json()["agents"]]
    revoked = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "revoke_web_access", "email": "member@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert revoked.json()["applied"] is True
    relisted = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" not in [agent["name"] for agent in relisted.json()["agents"]]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    outsider = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "grant_web_access", "email": "other@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.status_code == 404
    with ws(workspace_id):
        assert await web_extension().store.list(AUDIENCE_PREFIX) == ()


async def test_admin_payload_names_the_ids_its_controls_address(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The administration view's controls need targets: every agent row carries its id (the intent
    lane is per-agent) and every member row carries the stable member id the member kind applies
    to."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    payload = view.json()
    assert [agent["id"] for agent in payload["agents"]] == [str(agent_id)]
    assert [entry["id"] for entry in payload["members"]] == [str(admin_id)]


async def _seed_agent_conversation(
    workspace_id: UUID,
    agent_id: UUID,
    *,
    queue_key: str,
    audience: str,
    member_id: UUID | None,
    surface: str = "web",
    sandbox_conversation_id: UUID | None = None,
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
                surface_label=surface_label,
                queue_key=queue_key,
                member_id=member_id,
                audience=audience,
                sandbox_conversation_id=sandbox_conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _seed_listed_turn(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    *,
    seq: int,
    inbound: str,
    parent_turn_id: UUID | None = None,
    subagent_profile: str | None = None,
    speaker_member_id: UUID | None = None,
    context: TurnContext | None = None,
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
                admission_source="internal" if subagent_profile else "member",
                terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                parent_turn_id=parent_turn_id,
                subagent_profile=subagent_profile,
                speaker_member_id=speaker_member_id,
                context=None if context is None else context.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_a_conversation_describes_itself_and_names_who_spoke(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every conversation the panel lists says what it is about and who is in it. A Slack one has
    no chat row of this surface's, so its description is the same cut the rail takes, from the
    member's own words — never the ambient digest Slack renders around them, which is the
    bystanders' traffic and not what the member asked. Speakers read as the display line Slack
    reported, in order of first appearance and once each; a member the surface named no line for
    reads as their address."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    peer_id, _peer_token = await _seed_member(workspace_id, "peer@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C7:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    marker = mint_marker()
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound=fence_member_message(
            marker,
            "<ambient_1>\nbystander: the salary spreadsheet went to the wrong channel\n"
            "</ambient_1>\n",
            "can you take a look at the failing deploy",
            "",
        ),
        speaker_member_id=member_id,
        context=TurnContext(sender="Mel Okafor (m@example.com)"),
    )
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound="on it",
        speaker_member_id=peer_id,
    )
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=3,
        inbound="thanks",
        speaker_member_id=member_id,
    )

    listed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert listed.status_code == 200
    row = listed.json()["conversations"][0]
    assert row["description"] == "can you take a look at the failing deploy"
    assert "salary spreadsheet" not in row["description"]
    assert row["speakers"] == ["Mel Okafor (m@example.com)", "peer@example.com"]


async def test_a_web_conversation_is_described_by_the_title_the_rail_shows(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One conversation is named one way wherever the member meets it. A chat this surface opened
    already carries the rail's title in its own store row, so the panel states that string rather
    than re-cutting the first message and drifting from the row beside it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    conversation_id, _turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="hi"),
        title="Rename the deploy job",
    )

    listed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    rows = listed.json()["conversations"]
    assert [row["id"] for row in rows] == [str(conversation_id)]
    assert rows[0]["description"] == "Rename the deploy job"
    assert rows[0]["speakers"] == ["m@example.com"]


async def test_an_unreadable_conversation_states_no_words_and_no_speakers(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A row an admin may list but not read stays administration metadata: whose it is and how
    busy, never a word of it and never who else is in it. Opening it is the acknowledgement's act
    and that act is what gets audited, so a listing that quoted the first message would hand over
    the content the acknowledgement exists to record."""
    client, workspace_id, agent_id = web
    owner_id, _owner_token = await _seed_member(workspace_id, "owner@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="D1:1.0",
        audience=str(conversation_audience(owner_id)),
        member_id=owner_id,
        surface="slack",
    )
    await _seed_listed_turn(
        workspace_id,
        theirs,
        agent_id,
        seq=1,
        inbound="the salary review spreadsheet",
        speaker_member_id=owner_id,
        context=TurnContext(sender="Robin Vale (owner@example.com)"),
    )

    listed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )

    row = next(entry for entry in listed.json()["conversations"] if entry["id"] == str(theirs))
    assert row["readable"] is False
    assert row["disclosable"] is True
    assert row["member_email"] == "owner@example.com"
    assert row["description"] == ""
    assert row["speakers"] == []


async def test_conversations_list_by_audience_and_the_agent_wall(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The conversations view, read by a member who holds no grant and reaches the main agent
    by default: they list their own conversations plus the workspace-shared ones, never another
    member's private one or a room's. An admin lists every one of the agent's
    as administration metadata and reads only the workspace-shared one — every member-private
    conversation and every room is listed unreadable, the same shape a private task's content takes
    for an admin. Another agent's conversation is absent, and an out-of-audience agent is
    not-found."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    mine = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    shared = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1.0",
        audience="shared",
        member_id=None,
        surface="slack",
    )
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
    )
    room = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C9:1.0",
        audience="room:slack:C9",
        member_id=None,
        surface="slack",
    )
    elsewhere = await _seed_agent_conversation(
        workspace_id, walled_agent, queue_key="other", audience="shared", member_id=None
    )
    path = f"/surface/web/agents/{agent_id}/conversations"

    listed = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert listed.status_code == 200
    rows = listed.json()["conversations"]
    assert {entry["id"] for entry in rows} == {str(mine), str(shared)}
    assert all(entry["readable"] for entry in rows)
    by_id = {entry["id"]: entry for entry in rows}
    assert by_id[str(mine)]["member_email"] == "m@example.com"
    assert by_id[str(shared)]["surface"] == "slack"
    assert "queue_key" not in by_id[str(shared)]
    assert by_id[str(shared)]["member_email"] is None

    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    admin_rows = admin_view.json()["conversations"]
    assert {entry["id"] for entry in admin_rows} == {
        str(mine),
        str(shared),
        str(theirs),
        str(room),
    }
    assert {entry["id"] for entry in admin_rows if entry["readable"]} == {str(shared)}
    assert {entry["id"] for entry in admin_rows if not entry["readable"]} == {
        str(mine),
        str(theirs),
        str(room),
    }
    assert str(elsewhere) not in {entry["id"] for entry in admin_rows}

    walled = await client.get(
        f"/surface/web/agents/{walled_agent}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert walled.status_code == 404
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_conversation_transcript_reads_as_chat_and_fails_closed(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A readable conversation answers the same messages the chat draws, each reply naming the
    children it spawned — a subagent runs in its own conversation carrying this one's audience —
    while another member's private conversation, another agent's, a room's, and a guessed id are
    all not-found, and stay not-found for an admin who has recorded no disclosure against them."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    mine = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
    )
    room = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C9:1.0",
        audience="room:slack:C9",
        member_id=None,
        surface="slack",
    )
    elsewhere = await _seed_agent_conversation(
        workspace_id, walled_agent, queue_key="other", audience="shared", member_id=None
    )
    parent = await _seed_listed_turn(workspace_id, mine, agent_id, seq=1, inbound="research")
    child_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=str(parent),
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
        surface="subagent",
    )
    await _seed_listed_turn(
        workspace_id,
        child_conversation,
        agent_id,
        seq=1,
        inbound="search",
        parent_turn_id=parent,
        subagent_profile="deep_research",
    )
    await Transcript(blob=blob, conversation_id=mine).write(
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {parent}\n</context>\nresearch",
                ),
                Message(role="assistant", content="found it"),
            ),
        )
    )

    read = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{mine}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert read.status_code == 200
    assert read.json()["messages"] == [
        {"role": "user", "text": "research"},
        {
            "role": "assistant",
            "text": "found it",
            "subagents": [
                {
                    "profile": "deep_research",
                    "conversation_id": str(child_conversation),
                    "events": [],
                    "output": "ok",
                    "subagents": [],
                }
            ],
        },
    ]

    for conversation_id in (theirs, room, uuid4()):
        for token in (token_m, token_admin):
            denied = await client.get(
                f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
                headers={"cookie": f"{SESSION_COOKIE}={token}"},
            )
            assert denied.status_code == 404
    crossed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{elsewhere}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token_admin}"},
    )
    assert crossed.status_code == 404
    malformed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/not-a-uuid/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert malformed.status_code == 404
    anonymous = await client.get(f"/surface/web/agents/{agent_id}/conversations/{mine}/transcript")
    assert anonymous.status_code == 401


async def _acknowledge(
    client: AsyncClient, agent_id: UUID, conversation_id: UUID, token: str
) -> Response:
    return await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
        json={
            "verb": "read",
            "kind": "transcript",
            "conversation_id": str(conversation_id),
        },
    )


async def test_an_acknowledgement_opens_a_private_transcript_and_records_the_read(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_m, _token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=f"member:{member_m}",
        member_id=member_m,
    )
    parent_turn = await _seed_listed_turn(
        workspace_id, theirs, agent_id, seq=1, inbound="private question"
    )
    child_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=str(parent_turn),
        audience=f"member:{member_m}",
        member_id=member_m,
        surface=SUBAGENT_SURFACE,
        sandbox_conversation_id=theirs,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation_change).values(
                workspace_id=workspace_id,
                conversation_id=theirs,
                scan=WorkspaceChanges(
                    changes=(
                        WorkspaceChange(
                            path="repo/private.py", patch="+private child change\n", truncated=False
                        ),
                    ),
                    truncated=False,
                ).model_dump(mode="json"),
            )
        )
    await _seed_listed_turn(
        workspace_id,
        child_conversation,
        agent_id,
        seq=1,
        inbound="private child task",
        parent_turn_id=parent_turn,
        subagent_profile="deep_research",
    )
    await Transcript(blob=blob, conversation_id=theirs).write(
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {parent_turn}\n</context>\nprivate question",
                ),
                Message(role="assistant", content="private answer"),
            ),
        )
    )
    await Transcript(blob=blob, conversation_id=child_conversation).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="private child task"),
                Message(
                    role="assistant",
                    content=(
                        TextBlock(text="Reading the private file."),
                        ToolUseBlock(id="private-call", name="bash", input={"command": "ls"}),
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(tool_use_id="private-call", content="done", activity=True),
                    ),
                ),
            ),
        )
    )
    unrelated = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="unrelated",
        audience="shared",
        member_id=None,
    )
    transcript_path = f"/surface/web/agents/{agent_id}/conversations/{theirs}/transcript"
    child_run = f"/surface/web/subagents/deep_research/conversations/{child_conversation}"
    admin_cookie = {"cookie": f"{SESSION_COOKIE}={token_admin}"}

    blocked = await client.get(transcript_path, headers=admin_cookie)
    assert blocked.status_code == 404
    assert (await client.get(f"{child_run}?root={theirs}", headers=admin_cookie)).status_code == 404
    child_changes = (
        f"/surface/web/agents/{agent_id}/conversations/{child_conversation}/slots/changes"
    )
    assert (await client.get(child_changes, headers=admin_cookie)).status_code == 404

    refused = await _acknowledge(client, agent_id, theirs, token_n)
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert (
        await client.get(transcript_path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    ).status_code == 404
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.transcript_access)
            )
        ).scalar_one() == 0

    recorded = await _acknowledge(client, agent_id, theirs, token_admin)
    assert recorded.status_code == 200
    assert recorded.json()["applied"] is True

    opened = await client.get(transcript_path, headers=admin_cookie)
    assert opened.status_code == 200
    assert opened.json()["messages"] == [
        {"role": "user", "text": "private question"},
        {
            "role": "assistant",
            "text": "private answer",
            "subagents": [
                {
                    "profile": "deep_research",
                    "conversation_id": str(child_conversation),
                    "events": [
                        {"kind": "note", "text": "Reading the private file."},
                        {
                            "kind": "tool",
                            "name": "bash",
                            "preview": '{"command":"ls"}',
                            "description": "",
                        },
                    ],
                    "output": "ok",
                    "subagents": [],
                }
            ],
        },
    ]
    assert (await client.get(child_run, headers=admin_cookie)).status_code == 404
    opened_child = await client.get(f"{child_run}?root={theirs}", headers=admin_cookie)
    assert opened_child.status_code == 200
    assert opened_child.json()["run"]["id"] == str(child_conversation)
    assert opened_child.json()["messages"] == [
        {
            "role": "assistant",
            "text": "Reading the private file.",
            "events": [
                {
                    "kind": "tool",
                    "name": "bash",
                    "preview": '{"command":"ls"}',
                    "description": "",
                }
            ],
        }
    ]
    assert (
        await client.get(f"{child_run}?root={unrelated}", headers=admin_cookie)
    ).status_code == 404
    assert (await client.get(child_changes, headers=admin_cookie)).status_code == 404
    rooted_changes = await client.get(f"{child_changes}?root={theirs}", headers=admin_cookie)
    assert rooted_changes.status_code == 200
    assert rooted_changes.json()["changes"][0]["patch"] == "+private child change\n"
    assert (
        await client.get(f"{child_changes}?root={unrelated}", headers=admin_cookie)
    ).status_code == 404

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.transcript_access.c.reader_member_id,
                    tables.transcript_access.c.subject_member_id,
                )
            )
        ).all()
    assert [(row.reader_member_id, row.subject_member_id) for row in rows] == [
        (_admin_id, member_m)
    ]


async def test_losing_admin_closes_an_open_disclosure_window(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The content route answers on the reader's role now, not the role they held when the row was
    written: an admin acknowledges, reads, is demoted, and the same window that was open a moment
    ago is not-found — the row alone opens nothing."""
    client, workspace_id, agent_id = web
    member_m, _token_m = await _seed_member(workspace_id, "m@example.com")
    admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=f"member:{member_m}",
        member_id=member_m,
    )
    await _seed_listed_turn(workspace_id, theirs, agent_id, seq=1, inbound="private question")
    transcript_path = f"/surface/web/agents/{agent_id}/conversations/{theirs}/transcript"
    admin_cookie = {"cookie": f"{SESSION_COOKIE}={token_admin}"}
    assert (await _acknowledge(client, agent_id, theirs, token_admin)).json()["applied"] is True
    assert (await client.get(transcript_path, headers=admin_cookie)).status_code == 200

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == admin_id).values(is_admin=False)
        )

    assert (await client.get(transcript_path, headers=admin_cookie)).status_code == 404
    assert (
        await client.get(
            f"/surface/web/agents/{agent_id}/conversations/{theirs}/slots/changes",
            headers=admin_cookie,
        )
    ).status_code == 404


async def test_the_conversations_route_serializes_who_may_be_disclosed(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The wire carries the disclosure predicate as the seam computes it: another member's private
    conversation is disclosable to an admin, a room is not, and the admin's own is not — one
    acknowledgement changes none of those flags, since each is a property of the conversation
    rather than of what has already been read."""
    client, workspace_id, agent_id = web
    member_m, _token_m = await _seed_member(workspace_id, "m@example.com")
    admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=f"member:{member_m}",
        member_id=member_m,
    )
    room = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience="room:slack:C7",
        member_id=None,
        surface="slack",
        surface_label="#ops",
    )
    own = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="own",
        audience=f"member:{admin_id}",
        member_id=admin_id,
    )
    assert (await _acknowledge(client, agent_id, theirs, token_admin)).json()["applied"] is True

    listed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={token_admin}"},
    )
    rows = {entry["id"]: entry for entry in listed.json()["conversations"]}
    assert rows[str(theirs)]["disclosable"] is True
    assert rows[str(room)]["disclosable"] is False
    assert rows[str(own)]["disclosable"] is False
    assert rows[str(theirs)]["audience"] == f"member:{member_m}"
    assert rows[str(room)]["audience"] == "room:slack:C7"
    assert rows[str(room)]["surface_label"] == "#ops"
    assert rows[str(own)]["surface_label"] is None


async def test_conversation_changes_answer_from_the_one_shared_workspace(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A subagent runs in the conversation that spawned it, so its edits land in the parent's
    workspace and the parent is who the member asks about them. One scan answers both screens."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "member@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="coding-request",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    parent_turn_id = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound="update the application",
    )
    worker_conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=str(parent_turn_id),
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface=SUBAGENT_SURFACE,
        sandbox_conversation_id=conversation_id,
    )
    await _seed_listed_turn(
        workspace_id,
        worker_conversation_id,
        agent_id,
        seq=1,
        inbound="edit the files",
        parent_turn_id=parent_turn_id,
        subagent_profile="coding",
    )
    scanned = WorkspaceChanges(
        changes=(WorkspaceChange(path="repo/app.py", patch="-old\n+new\n", truncated=False),),
        truncated=False,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation_change).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                scan=scanned.model_dump(mode="json"),
            )
        )

    for read in (conversation_id, worker_conversation_id):
        response = await client.get(
            f"/surface/web/agents/{agent_id}/conversations/{read}/slots/changes",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "type": "changes",
            "changes": [{"path": "repo/app.py", "patch": "-old\n+new\n", "truncated": False}],
            "truncated": False,
        }


async def test_durable_shared_files_fill_the_typed_artifacts_slot(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "artifacts@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="artifacts",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    turn_id = await _seed_listed_turn(
        workspace_id, conversation_id, agent_id, seq=1, inbound="share the report"
    )
    shared_at = datetime(2026, 8, 6, 12, tzinfo=UTC)
    chart_png = _png()
    chart_key = f"artifacts/{uuid4()}/chart.png"
    await blob.put(chart_key, chart_png)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=chart_key,
                workspace_id=workspace_id,
                filename="chart.png",
                subject="Quarterly chart",
                media_type="image/png",
                size_bytes=len(chart_png),
                created_at=shared_at,
                updated_at=shared_at,
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/poster.png",
                workspace_id=workspace_id,
                filename="poster.png",
                subject="Large poster",
                media_type="image/png",
                size_bytes=IMAGE_PREVIEW_MAX_BYTES + 1,
                created_at=shared_at,
                updated_at=shared_at,
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/diagram.svg",
                workspace_id=workspace_id,
                filename="diagram.svg",
                subject="Vector diagram",
                media_type="image/svg+xml",
                size_bytes=84,
                created_at=shared_at,
                updated_at=shared_at,
            )
        )

    base = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}"
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    inventory = await client.get(f"{base}/slots", headers=headers)
    response = await client.get(f"{base}/slots/artifacts", headers=headers)

    assert inventory.status_code == 200
    assert next(slot for slot in inventory.json()["slots"] if slot["id"] == "artifacts") == {
        "id": "artifacts",
        "label": "Artifacts",
        "icon": "artifact",
        "kind": "artifacts",
        "count": 3,
    }
    assert response.status_code == 200
    assert response.json()["type"] == "artifacts"
    assert response.json()["truncated"] is False
    artifacts = {entry["filename"]: entry for entry in response.json()["artifacts"]}
    artifact = artifacts["chart.png"]
    assert {key: artifact[key] for key in ("filename", "subject", "media_type", "size_bytes")} == {
        "filename": "chart.png",
        "subject": "Quarterly chart",
        "media_type": "image/png",
        "size_bytes": len(chart_png),
    }
    assert artifact["created_at"].startswith("2026-08-06T12:00:00")
    assert artifact["url"].startswith("https://web/")
    assert artifact["preview"]["type"] == "image"
    assert artifact["preview"]["media_type"] == "image/png"
    assert artifact["preview"]["url"].startswith("/artifacts/")
    assert "&preview=" in artifact["preview"]["url"]
    assert "preview=" not in urlsplit(artifact["url"]).query
    preview = await client.get(artifact["preview"]["url"])
    assert preview.status_code == 200
    assert preview.content == chart_png
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert artifacts["diagram.svg"]["preview"] is None
    assert artifacts["poster.png"]["preview"] is None


async def test_durable_todo_board_fills_the_typed_tasks_slot(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "tasks@example.com")
    _other_member_id, other_token = await _seed_member(workspace_id, "other-tasks@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="tasks",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    producer = cast(
        ToolContext,
        SimpleNamespace(
            ext=context_for(todos.NAME, frozenset()),
            turn=SimpleNamespace(conversation_id=conversation_id),
        ),
    )
    with ws(workspace_id):
        await todos.update_todo_list(
            producer,
            todos.UpdateTodoListInput(
                title="Ship slots",
                tasks=(
                    todos.TodoTask(description="Define the payload", status="completed"),
                    todos.TodoTask(description="Render the board", status="in_progress"),
                    todos.TodoTask(description="Verify the flow", status="pending"),
                ),
                user_description="Track the slot rollout",
            ),
        )
        assert await ScopedStore(extension=todos.NAME).get(
            f"{todos.TODO_KEY_PREFIX}{conversation_id}"
        ) == {
            "title": "Ship slots",
            "tasks": [
                {"description": "Define the payload", "status": "completed"},
                {"description": "Render the board", "status": "in_progress"},
                {"description": "Verify the flow", "status": "pending"},
            ],
        }

    base = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}"
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    inventory = await client.get(f"{base}/slots", headers=headers)
    response = await client.get(f"{base}/slots/tasks", headers=headers)
    denied = await client.get(
        f"{base}/slots/tasks",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )

    assert inventory.status_code == 200
    assert next(slot for slot in inventory.json()["slots"] if slot["id"] == "tasks") == {
        "id": "tasks",
        "label": "Tasks",
        "icon": "task",
        "kind": "tasks",
        "count": 3,
    }
    assert response.status_code == 200
    assert denied.status_code == 404
    assert response.json() == {
        "type": "tasks",
        "title": "Ship slots",
        "tasks": [
            {"description": "Define the payload", "status": "completed"},
            {"description": "Render the board", "status": "in_progress"},
            {"description": "Verify the flow", "status": "pending"},
        ],
        "total_count": 3,
        "completed_count": 1,
        "truncated": False,
    }


async def test_sites_slot_preserves_private_site_visibility_on_a_shared_conversation(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, workspace_id, agent_id = web
    creator_id, creator_token = await _seed_member(workspace_id, "creator@example.com")
    hidden_creator_id, _hidden_token = await _seed_member(workspace_id, "hidden@example.com")
    _viewer_id, viewer_token = await _seed_member(workspace_id, "viewer@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="sites-slot",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
    )
    hidden_at = datetime(2026, 8, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(hosted_site),
            [
                {
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "name": f"aaa-hidden-{index:03d}",
                    "port": 10_000 + index,
                    "visibility": "private",
                    "creator_member_id": hidden_creator_id,
                    "created_at": hidden_at,
                    "updated_at": hidden_at,
                }
                for index in range(101)
            ],
        )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        for name, port, visibility in (
            ("private-dashboard", 8000, "private"),
            ("workspace-dashboard", 8001, "workspace"),
            ("public-dashboard", 8002, "public"),
        ):
            await sites.register(
                conversation_id,
                name,
                port,
                creator_id,
                visibility,
                SHARED_AUDIENCE,
                True,
            )

    async def refuse_workspace_scan(_sites: HostedSites) -> tuple[()]:
        raise AssertionError("sites slot scanned the workspace")

    monkeypatch.setattr(HostedSites, "all", refuse_workspace_scan)

    path = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/slots/sites"
    creator = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={creator_token}"})
    viewer = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"})
    admin = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"})
    inventory = await client.get(
        path.rsplit("/", 1)[0], headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"}
    )

    assert creator.status_code == 200
    assert {site["name"] for site in creator.json()["sites"]} == {
        "private-dashboard",
        "public-dashboard",
        "workspace-dashboard",
    }
    assert viewer.status_code == 200
    assert {site["name"] for site in viewer.json()["sites"]} == {
        "public-dashboard",
        "workspace-dashboard",
    }
    assert admin.json()["sites"] == viewer.json()["sites"]
    assert next(slot for slot in inventory.json()["slots"] if slot["id"] == "sites")["count"] == 2


async def test_automations_slot_follows_the_conversation_audience(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slot answers the kind's own gate, so a task is as visible beside a thread as it is in
    the index: in a shared conversation every member reads all three whole, because the fires
    already post there for all of them, and one member's own conversation is not a slot anybody
    else opens. Display fields stay bounded and one batched inspection serves each read."""
    client, workspace_id, agent_id = web
    creator_id, creator_token = await _seed_member(workspace_id, "task-owner@example.com")
    _viewer_id, viewer_token = await _seed_member(workspace_id, "task-viewer@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "task-admin@example.com", admin=True)
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="automations-slot",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
    )
    long_description = "d" * 2_001
    long_schedule = ",".join(str(minute) for minute in range(60)) + " 9 * * *"
    long_response = "r" * 401
    with ws(workspace_id), bind_agent(agent_id):
        task = await ScheduleStore().create(
            conversation_id=conversation_id,
            name="daily-brief",
            schedule="0 9 * * *",
            prompt="Read private sources and send the brief.",
            description="Send the morning brief.",
            next_run_at=datetime(2026, 8, 8, 9, tzinfo=UTC),
            created_by_member_id=creator_id,
        )
        ownerless = await ScheduleStore().create(
            conversation_id=conversation_id,
            name="system-cleanup",
            schedule="0 3 * * 0",
            prompt="Remove expired system records.",
            description="Clean expired system records.",
            next_run_at=datetime(2026, 8, 9, 3, tzinfo=UTC),
            created_by_member_id=None,
        )
        bounded = await ScheduleStore().create(
            conversation_id=conversation_id,
            name="bounded-output",
            schedule=long_schedule,
            prompt="Produce a large result.",
            description=long_description,
            next_run_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
            created_by_member_id=creator_id,
        )
    last_turn_id = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound="Run the daily brief.",
    )
    bounded_turn_id = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound="Run the bounded output.",
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.scheduled_task)
            .where(tables.scheduled_task.c.id.in_((task.id, ownerless.id)))
            .values(
                last_run_at=datetime(2026, 8, 7, 9, tzinfo=UTC),
                last_turn_id=last_turn_id,
            )
        )
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == bounded_turn_id)
            .values(
                terminal=TerminalFrame(status="done", text=long_response).model_dump(mode="json")
            )
        )
        await connection.execute(
            sa.update(tables.scheduled_task)
            .where(tables.scheduled_task.c.id == bounded.id)
            .values(
                last_run_at=datetime(2026, 8, 7, 10, tzinfo=UTC),
                last_turn_id=bounded_turn_id,
            )
        )

    base = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/slots"

    async def refuse_single_inspection(_store: ScheduleStore, _task: ScheduledTask) -> None:
        raise AssertionError("automations slot performed a per-task inspection")

    real_inspect_many = ScheduleStore.inspect_many
    batch_calls = 0

    async def count_batch(
        store: ScheduleStore, tasks: tuple[ScheduledTask, ...]
    ) -> dict[UUID, TaskInspection]:
        nonlocal batch_calls
        batch_calls += 1
        return await real_inspect_many(store, tasks)

    monkeypatch.setattr(ScheduleStore, "inspect", refuse_single_inspection)
    monkeypatch.setattr(ScheduleStore, "inspect_many", count_batch)
    creator_inventory = await client.get(
        base, headers={"cookie": f"{SESSION_COOKIE}={creator_token}"}
    )
    assert (
        next(slot for slot in creator_inventory.json()["slots"] if slot["id"] == "automations")[
            "count"
        ]
        == 3
    )
    assert batch_calls == 0
    creator = await client.get(
        f"{base}/automations",
        headers={"cookie": f"{SESSION_COOKIE}={creator_token}"},
    )
    viewer = await client.get(
        f"{base}/automations", headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"}
    )
    viewer_inventory = await client.get(
        base, headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"}
    )
    admin = await client.get(
        f"{base}/automations", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert batch_calls == 3

    assert creator.status_code == 200
    creator_tasks = {task["name"]: task for task in creator.json()["automations"]}
    assert set(creator_tasks) == {"bounded-output", "daily-brief", "system-cleanup"}
    creator_task = creator_tasks["daily-brief"]
    assert creator_task["description"] == "Send the morning brief."
    assert creator_task["schedule"] == "0 9 * * *"
    assert creator_task["paused"] is False
    assert creator_task["next_run_at"].startswith("2026-08-08T09:00:00")
    assert creator_task["last_run_at"].startswith("2026-08-07T09:00:00")
    assert creator_task["latest_status"] == "done"
    assert creator_task["latest_response"] == "ok"
    assert "prompt" not in creator_task
    bounded_task = creator_tasks["bounded-output"]
    assert bounded_task["description"] == long_description[:2_000]
    assert bounded_task["schedule"] == long_schedule[:100]
    assert bounded_task["latest_response"] == long_response[:400]
    assert creator.json()["truncated"] is True
    assert viewer.status_code == 200
    viewer_tasks = {task["name"]: task for task in viewer.json()["automations"]}
    assert set(viewer_tasks) == {"bounded-output", "daily-brief", "system-cleanup"}
    assert viewer_tasks["daily-brief"]["description"] == "Send the morning brief."
    assert viewer_tasks["daily-brief"]["latest_response"] == "ok"
    assert "prompt" not in viewer_tasks["daily-brief"]
    assert (
        next(slot for slot in viewer_inventory.json()["slots"] if slot["id"] == "automations")[
            "count"
        ]
        == 3
    )
    assert admin.status_code == 200
    admin_tasks = {task["name"]: task for task in admin.json()["automations"]}
    assert set(admin_tasks) == {"bounded-output", "daily-brief", "system-cleanup"}
    admin_task = admin_tasks["daily-brief"]
    assert admin_task["name"] == "daily-brief"
    assert admin_task["description"] == "Send the morning brief."
    assert admin_task["schedule"] == "0 9 * * *"
    assert admin_task["paused"] is False
    assert admin_task["latest_status"] == "done"
    assert admin_task["latest_response"] == "ok"
    assert "prompt" not in admin_task
    ownerless_task = admin_tasks["system-cleanup"]
    assert ownerless_task["description"] == "Clean expired system records."
    assert ownerless_task["latest_response"] == "ok"
    assert "prompt" not in ownerless_task
    admin_bounded = admin_tasks["bounded-output"]
    assert admin_bounded["description"] == long_description[:2_000]
    assert admin_bounded["latest_response"] == long_response[:400]
    assert admin_bounded["schedule"] == long_schedule[:100]

    private_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="automations-private",
        audience=str(conversation_audience(creator_id)),
        member_id=creator_id,
    )
    with ws(workspace_id), bind_agent(agent_id):
        await ScheduleStore().create(
            conversation_id=private_conversation,
            name="private-cadence",
            schedule="0 6 * * *",
            prompt="Read private sources.",
            description="The creator's own cadence.",
            next_run_at=datetime(2026, 8, 11, 6, tzinfo=UTC),
            created_by_member_id=creator_id,
        )
    private_base = (
        f"/surface/web/agents/{agent_id}/conversations/{private_conversation}/slots/automations"
    )
    walled = await client.get(private_base, headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"})
    assert walled.status_code == 404


async def test_conversation_reads_ride_the_same_gate(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _config, _hub, _blob, _sandboxes = dbos_runtime
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    mine = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
    )
    empty = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="empty",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    scanned = WorkspaceChanges(
        changes=(WorkspaceChange(path="repo/file-0.py", patch="-old\n+new\n", truncated=False),),
        truncated=False,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation_change).values(
                workspace_id=workspace_id,
                conversation_id=mine,
                scan=scanned.model_dump(mode="json"),
            )
        )

    changes = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{mine}/slots/changes",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert changes.status_code == 200
    assert changes.json() == {
        "type": "changes",
        "changes": [{"path": "repo/file-0.py", "patch": "-old\n+new\n", "truncated": False}],
        "truncated": False,
    }
    slots = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{mine}/slots",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert slots.status_code == 200
    assert slots.json() == {
        "slots": [
            {
                "id": "changes",
                "label": "Changes",
                "icon": "diff",
                "kind": "changes",
                "count": 1,
            }
        ]
    }
    unscanned = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{empty}/slots/changes",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert unscanned.status_code == 200
    assert unscanned.json() == {"type": "changes", "changes": [], "truncated": False}
    empty_slots = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{empty}/slots",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert empty_slots.status_code == 200
    assert empty_slots.json() == {"slots": []}
    for token in (token_m, token_admin):
        for route in ("transcript", "slots/changes"):
            denied = await client.get(
                f"/surface/web/agents/{agent_id}/conversations/{theirs}/{route}",
                headers={"cookie": f"{SESSION_COOKIE}={token}"},
            )
            assert denied.status_code == 404

    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    elsewhere = await _seed_agent_conversation(
        workspace_id, second_agent, queue_key="elsewhere", audience="shared", member_id=None
    )
    for route in ("transcript", "slots/changes"):
        crossed = await client.get(
            f"/surface/web/agents/{agent_id}/conversations/{elsewhere}/{route}",
            headers={"cookie": f"{SESSION_COOKIE}={token_admin}"},
        )
        assert crossed.status_code == 404


async def test_team_view_lists_the_roster_for_every_member(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace team view: every member reads the whole roster with each member's role and
    seat — the same rows the `member` kind lists to a member asking the main agent — while
    `can_add` opens the add form for an admin alone. An unauthenticated read is refused."""
    client, workspace_id, _agent_id = web
    _member_id, token_m = await _seed_member(workspace_id, "m@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    path = "/surface/web/workspace/team"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == _member_id)
            .values(seated_at=sa.func.now())
        )
    member_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    body = member_view.json()
    assert [(entry["email"], entry["admin"], entry["seated"]) for entry in body["members"]] == [
        ("boss@example.com", True, False),
        ("m@example.com", False, True),
    ]
    assert "id" not in body["members"][0]
    assert body["can_add"] is False

    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert admin_view.json()["can_add"] is True
    assert [entry["email"] for entry in admin_view.json()["members"]] == [
        entry["email"] for entry in body["members"]
    ]

    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_the_team_panel_adds_a_member_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel's own envelope reaching the real `add_member` verb: an admin's submit mints the
    member row at the workspace's domain with the admin flag the form carried, and the new member
    appears in the roster the panel re-reads. A non-admin's identical submit is refused by the
    verb with no row written — the panel's hidden form is not the gate."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "plain@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    added = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "add_member", "email": "New.Hire@example.com", "admin": True},
        headers=cookie,
    )
    assert added.status_code == 200
    outcome = added.json()
    assert outcome["applied"] is True, outcome["message"]
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.member.c.is_admin, tables.member.c.seated_at).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "new.hire@example.com",
                )
            )
        ).one()
    assert row.is_admin is True
    assert row.seated_at is not None
    roster = await client.get("/surface/web/workspace/team", headers=cookie)
    assert "new.hire@example.com" in [entry["email"] for entry in roster.json()["members"]]

    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "add_member", "email": "sneak@example.com", "admin": True},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert refused.json()["message"] == ADD_MEMBER_GATE
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "sneak@example.com",
                )
            )
        ).one_or_none() is None


async def test_the_team_panel_adds_a_member_at_another_domain(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """An address outside the workspace's own domain lands as a member: an admin staffing the
    workspace with a contractor or an advisor types the address they have, and the panel writes the
    row the verb writes."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    added = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "add_member", "email": "contractor@other.test", "admin": False},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert added.status_code == 200
    assert added.json()["applied"] is True, added.json()["message"]
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "contractor@other.test",
                )
            )
        ).one_or_none() is not None


async def test_subagent_page_reads_the_profile_and_refuses_an_unknown_name(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A profile's own page: its instructions, the model it pins, its round cap, and whether its
    answer is walled as untrusted. The roster is the whole set that exists, so a name outside it is
    a 404 — and a profile holding no `load_skill` lists no skills rather than the deploy's."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    overview = await client.get("/surface/web/subagents/deep_research/overview", headers=cookie)
    assert overview.status_code == 200
    detail = overview.json()["subagent"]
    assert {key: value for key, value in detail.items() if key != "prompt"} == {
        "name": "deep_research",
        "model": "claude-opus-4-8",
        "max_rounds": 40,
        "untrusted_output": False,
        "loads_skills": False,
    }
    assert detail["prompt"] == subagent_system_prompt(
        _profile("deep_research", "claude-opus-4-8", 40), skills=EMPTY_SKILL_REGISTRY.index()
    )
    assert detail["prompt"].startswith("be focused")
    assert FINISH_CONTRACT in detail["prompt"]

    inherits = await client.get("/surface/web/subagents/general_purpose/overview", headers=cookie)
    assert inherits.json()["subagent"]["model"] is None

    assert (
        await client.get("/surface/web/subagents/nonexistent/overview", headers=cookie)
    ).status_code == 404

    skills_read = await client.get("/surface/web/subagents/general_purpose/skills", headers=cookie)
    assert skills_read.json() == {"loads_skills": False, "skills": []}


async def test_subagent_conversations_follow_the_spawning_conversation_audience(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A spawn copies the spawning conversation's audience onto the child, so a profile's page
    lists the children of this member's own requests and of the workspace-shared ones, never
    another member's — and an admin lists every one. Another profile's children never appear on
    this page, and the transcript read fails closed on the same answer the listing shows. That read
    carries the run itself, identical to the listing row, so a permalink opened cold titles its
    page without the listing in hand."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    admin_id, token_admin = await _seed_member(workspace_id, "admin@example.com", admin=True)
    mine = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
        surface=SUBAGENT_SURFACE,
    )
    theirs = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
        surface=SUBAGENT_SURFACE,
    )
    other_profile = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="other",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
        surface=SUBAGENT_SURFACE,
    )
    await _seed_listed_turn(
        workspace_id, mine, agent_id, seq=1, inbound="find it", subagent_profile="deep_research"
    )
    await _seed_listed_turn(
        workspace_id, mine, agent_id, seq=2, inbound="and again", subagent_profile="deep_research"
    )
    await _seed_listed_turn(
        workspace_id, theirs, agent_id, seq=1, inbound="theirs", subagent_profile="deep_research"
    )
    await _seed_listed_turn(
        workspace_id,
        other_profile,
        agent_id,
        seq=1,
        inbound="general work",
        subagent_profile="general_purpose",
    )
    await Transcript(blob=blob, conversation_id=mine).write(
        Conversation(seq=1, messages=(Message(role="assistant", content="found it"),))
    )
    path = "/surface/web/subagents/deep_research/conversations"

    listed = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert listed.status_code == 200
    rows = listed.json()["conversations"]
    assert len(rows) == 1
    assert rows[0] | {"last_turn_at": None, "created_at": None} == {
        "id": str(mine),
        "agent": {"id": str(agent_id), "name": "assistant"},
        "surface": SUBAGENT_SURFACE,
        "member_email": "m@example.com",
        "surface_label": None,
        "audience": rows[0]["audience"],
        "description": "find it",
        "speakers": [],
        "turn_count": 2,
        "created_at": None,
        "last_turn_at": None,
        "readable": True,
        "disclosable": False,
    }
    assert rows[0]["audience"].startswith("member:")
    assert datetime.fromisoformat(rows[0]["last_turn_at"]).tzinfo is not None

    admin_rows = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()["conversations"]
    assert {entry["id"] for entry in admin_rows} == {str(mine), str(theirs)}
    assert {entry["readable"] for entry in admin_rows} == {False}

    readable = await client.get(f"{path}/{mine}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert readable.status_code == 200
    assert readable.json()["messages"] == [{"role": "assistant", "text": "found it"}]
    assert readable.json()["run"] == rows[0]

    for blocked in (theirs, other_profile):
        refused = await client.get(
            f"{path}/{blocked}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
        assert refused.status_code == 404
    admin_refused = await client.get(
        f"{path}/{theirs}", headers={"cookie": f"{SESSION_COOKIE}={token_admin}"}
    )
    assert admin_refused.status_code == 404
    with ws(workspace_id):
        assert await record_transcript_access(workspace_id, theirs, agent_id, admin_id) is not None
    still_refused = await client.get(
        f"{path}/{theirs}", headers={"cookie": f"{SESSION_COOKIE}={token_admin}"}
    )
    assert still_refused.status_code == 404


async def test_a_run_page_states_the_prose_a_run_wrote_not_the_payload_it_rode_in(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A run answers by calling finish, and `persist_transcript` closes its conversation with that
    payload — so its own page would read back the JSON its output schema carried. The page states
    the fields the run wrote, exactly as the tree under the reply that spawned it does."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    run = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="run",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface=SUBAGENT_SURFACE,
    )
    await _seed_listed_turn(
        workspace_id, run, agent_id, seq=1, inbound="find it", subagent_profile="deep_research"
    )
    await Transcript(blob=blob, conversation_id=run).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content='{"task": "find the deadline"}'),
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(id="c1", name="fetch_url", input={"url": "https://x/y"}),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="c1", content="…", activity=True),),
                ),
                Message(role="assistant", content='{"result": "The deadline is March 31."}'),
            ),
        )
    )

    read = await client.get(
        f"/surface/web/subagents/deep_research/conversations/{run}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert read.status_code == 200
    assert read.json()["messages"] == [
        {
            "role": "assistant",
            "text": "The deadline is March 31.",
            "events": [
                {
                    "kind": "tool",
                    "name": "fetch_url",
                    "preview": '{"url":"https://x/y"}',
                    "description": "",
                }
            ],
        }
    ]


CODE_REVIEW_FINDING = CodeReviewFinding(
    path="core/x.py",
    line=42,
    title="Wedged turn",
    trigger="A cancel lands mid-dispatch",
    failure="The turn never commits a terminal",
    impact="production outage, deadlock, or permanently unfinished work",
)


async def test_a_run_page_states_an_answer_that_wrote_no_prose(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A profile whose output is findings rather than sentences carries no prose at the top of its
    payload. Its page is the record, so it states those findings — reading them off the only names
    they have — rather than the empty bubble a prose-only reading leaves behind."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    run = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="run",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface=SUBAGENT_SURFACE,
    )
    await _seed_listed_turn(
        workspace_id, run, agent_id, seq=1, inbound="review it", subagent_profile="deep_research"
    )
    await Transcript(blob=blob, conversation_id=run).write(
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content='{"comparison": "pr-1409"}'),
                Message(
                    role="assistant",
                    content=CodeReviewOutput(findings=(CODE_REVIEW_FINDING,)).model_dump_json(),
                ),
            ),
        )
    )

    read = await client.get(
        f"/surface/web/subagents/deep_research/conversations/{run}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert read.status_code == 200
    assert read.json()["messages"] == [
        {
            "role": "assistant",
            "text": (
                "**Findings**\n"
                "**Path** — core/x.py\n"
                "**Line** — 42\n"
                "**Title** — Wedged turn\n"
                "**Trigger** — A cancel lands mid-dispatch\n"
                "**Failure** — The turn never commits a terminal\n"
                "**Impact** — production outage, deadlock, or permanently unfinished work"
            ),
        }
    ]


def test_an_answer_arrives_whole_however_long_and_never_blank() -> None:
    """One answer, stated once wherever it is read: the surface sends every character it has, and
    how much of it stands on a screen is the fold's decision at the other end. Never blank — a
    review that found nothing answered, and a page saying nothing would state it never ran."""
    long_answer = json.dumps({"result": "word " * 900})
    findings = CodeReviewOutput(findings=(CODE_REVIEW_FINDING,)).model_dump_json()

    assert _run_answer(long_answer) == ("word " * 900).strip()
    assert len(_run_answer(long_answer)) > len(long_answer) - 40

    assert _run_answer(findings).startswith("**Findings**\n**Path** — core/x.py")
    assert _run_answer(CodeReviewOutput().model_dump_json()) == "**Findings** — none"
    assert _run_answer("{}") == ""


def test_a_note_arrives_whole_however_long() -> None:
    """A line an agent wrote between its calls is prose, and the surface cuts none of it."""
    written = "The changelog is long. " * 400
    events = _subagent_activity(
        (
            Message(role="user", content="{}"),
            Message(role="assistant", content=(TextBlock(text=written),)),
        )
    )

    assert events == [{"kind": "note", "text": written.strip()}]


async def test_an_agents_own_reply_is_never_read_as_a_payload(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A main agent answering with JSON wrote that JSON — reading it the way a run's answer is read
    would strip it to one field and drop the rest. Only a conversation whose turns ran a profile is
    a run, so this one is rendered verbatim."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    reply = '{"result": "not a subagent", "rows": [1, 2]}'
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text=reply),
    )
    await Transcript(blob=blob, conversation_id=conversation_id).write(
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nGive me the json.",
                ),
                Message(role="assistant", content=reply),
            ),
        )
    )

    read = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert read.status_code == 200
    assert read.json()["messages"] == [
        {"role": "user", "text": "Give me the json."},
        {"role": "assistant", "text": reply},
    ]


async def test_subagent_work_stays_behind_the_agent_wall(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The page is deploy shape but the work it lists is an agent's, so an agent outside the
    viewer's web audience is absent from the listing and not-found on the transcript — the same
    answer every other portal route gives for it. A grant on that agent, or being an admin, shows
    the row."""
    client, workspace_id, _agent_id = web
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "admin@example.com", admin=True)
    walled = await _seed_agent_conversation(
        workspace_id,
        walled_agent,
        queue_key="walled",
        audience="shared",
        member_id=None,
        surface=SUBAGENT_SURFACE,
    )
    await _seed_listed_turn(
        workspace_id,
        walled,
        walled_agent,
        seq=1,
        inbound="ops work",
        subagent_profile="deep_research",
    )
    path = "/surface/web/subagents/deep_research/conversations"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    assert (await client.get(path, headers=cookie)).json()["conversations"] == []
    assert (await client.get(f"{path}/{walled}", headers=cookie)).status_code == 404

    admin_rows = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()["conversations"]
    assert [entry["id"] for entry in admin_rows] == [str(walled)]

    await _grant_web_access(workspace_id, walled_agent, "member@example.com")
    granted = await client.get(path, headers=cookie)
    assert [entry["id"] for entry in granted.json()["conversations"]] == [str(walled)]
    assert (await client.get(f"{path}/{walled}", headers=cookie)).status_code == 200


def test_projection_draws_no_member_bubble_for_a_delivered_subagent_result() -> None:
    """A background child hands its output back as a turn on the parent's conversation, and its
    prompt is the wire's element around the child's answer. Drawn as a member bubble it reads as
    words the member typed; the reply that answers it is what the member actually gets."""
    delivered = "11111111-1111-1111-1111-111111111111"
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {delivered}\n</context>\n"
                '<subagent_result profile="general_purpose" subagent_id="c7" status="done">\n'
                '{"result":"a joke"}\n</subagent_result>',
            ),
            Message(role="assistant", content="Here is the joke: a joke"),
        ),
        None,
        frozenset({delivered}),
        frozenset({delivered}),
    )
    assert rendered == [{"role": "assistant", "text": "Here is the joke: a joke"}]


def test_projection_draws_no_member_bubble_for_a_scheduled_firing() -> None:
    """The same rule reaches the cron envelope: a firing carries `<scheduled_task>` in front of the
    instruction the member stored once, and no member spoke it into this conversation."""
    fired = "22222222-2222-2222-2222-222222222222"
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {fired}\n</context>\n"
                "<scheduled_task>\nscheduled_fire: 2026-08-11T09:00:00Z\n</scheduled_task>\n"
                "Digest the investor email.",
            ),
            Message(role="assistant", content="Nothing new since yesterday."),
        ),
        None,
        frozenset({fired}),
        frozenset({fired}),
    )
    assert rendered == [{"role": "assistant", "text": "Nothing new since yesterday."}]


def test_projection_keeps_the_bubble_for_what_a_member_spoke() -> None:
    """The set names only what no member spoke, so a ref it does not carry keeps the bubble it
    always had — a projection that misses one shows the message rather than hiding it."""
    spoken = "33333333-3333-3333-3333-333333333333"
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {spoken}\n</context>\nask a subagent for a joke",
            ),
            Message(role="assistant", content="Handing that off."),
        ),
        None,
        frozenset({spoken}),
        frozenset(),
    )
    assert rendered == [
        {"role": "user", "text": "ask a subagent for a joke"},
        {"role": "assistant", "text": "Handing that off."},
    ]


def test_a_members_bubble_carries_no_recalled_memory() -> None:
    """`user_prompt_submit` appends what memory recall retrieved to the message the model reads.
    The member never typed it, so their bubble must not carry it — rendered as their own words it
    reads as though they pasted their own stored memories into the chat, and a recall about someone
    else's work would appear over their name."""
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=(
                    "<context>\nmessage_ref: 11111111-1111-1111-1111-111111111111\n</context>\n"
                    "hi\n\n<injected_context>\nRelevant memory:\n- a template Marshall stored\n"
                    "</injected_context>"
                ),
            ),
            Message(role="assistant", content="Hello."),
        ),
    )
    assert rendered == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "Hello."},
    ]
