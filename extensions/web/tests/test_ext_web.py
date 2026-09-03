import asyncio
import base64
import json
import re
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, aclosing, contextmanager
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast, get_args
from urllib.parse import quote, quote_plus
from uuid import UUID, uuid4

import httpx
import lz4.frame
import pytest
import sqlalchemy as sa
import ufo_ext_todos as todos
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from openfeature import api
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from PIL import Image
from pydantic import BaseModel, ValidationError
from ufo_ext_app_chat.manifest import manifest as app_chat_manifest
from ufo_ext_app_radar.manifest import manifest as app_radar_manifest
from ufo_ext_connectors.manifest import manifest as connectors_manifest
from ufo_ext_imessage.manifest import manifest as imessage_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import recall_subjects
from ufo_ext_report_digest.manifest import manifest as report_digest_manifest
from ufo_ext_report_digest.writer import report_digest_entry
from ufo_ext_scheduled_tasks.conversation_slot import AUTOMATIONS_SLOT
from ufo_ext_scheduled_tasks.manifest import NAME as SCHEDULED_TASKS_NAME
from ufo_ext_scheduled_tasks.schedules import (
    ScheduledTask,
    ScheduleStore,
    TaskInspection,
    scheduled_task,
)
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_OBJECT
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import site_object_name
from ufo_ext_sites.store import HostedSites, hosted_site
from ufo_ext_sites.surface import site_address
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import user_skill
from ufo_ext_slack.manifest import manifest as slack_manifest
from ufo_ext_slack.surface import (
    SLACK_CLIENT_ID_ENV,
    SLACK_CLIENT_SECRET_ENV,
    SLACK_OAUTH_AUTHORIZE_URL,
)
from ufo_ext_sources.manifest import manifest as sources_manifest
from ufo_ext_sources.tools import SOURCE_TRIGGER_OBJECT
from ufo_ext_web import panels as web_panels
from ufo_ext_web import surface as web_surface
from ufo_ext_web.anthropic_login import (
    AUTHORIZE_URL as ANTHROPIC_AUTHORIZE_URL,
)
from ufo_ext_web.anthropic_login import (
    CODE_FIELD as ANTHROPIC_CODE_FIELD,
)
from ufo_ext_web.anthropic_login import (
    CODE_REFUSED as ANTHROPIC_CODE_REFUSED,
)
from ufo_ext_web.anthropic_login import (
    STATE_COOKIE as ANTHROPIC_STATE_COOKIE,
)
from ufo_ext_web.anthropic_login import (
    PendingAuthorization as AnthropicPending,
)
from ufo_ext_web.audience import AUDIENCE_PREFIX, EXTENSION_WEB, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.openai_login import (
    DEVICE_COOKIE,
    DEVICE_UNAVAILABLE,
    DeviceAuthorization,
    DeviceClaim,
)
from ufo_ext_web.panels import (
    FRAME_HEADER,
    NO_FRAME_ACCESS,
    _action_intent,
    _outcome,
)
from ufo_ext_web.starters import SLATE_DIGEST, RankedUnlock, Slate, starters_key
from ufo_ext_web.surface import (
    DEFAULT_APP_SETUP_ASK,
    LANES_SHELL_FLAG,
    MAX_INBOUND_FILES,
    NO_MEMBER_FAULT,
    NO_SEAT_FAULT,
    SESSION_COOKIE,
    SESSION_FAULT_HEADER,
    SIDEBAR_FILE,
    SUBAGENT_EVENT_LIMIT,
    SubagentNode,
    _answer_key,
    _rendered_messages,
    _run_answer,
    _sse,
    _subagent_activity,
    load_assets,
    portal_shell,
    rum_config,
)
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_SURFACE_MODEL,
    no_member_skills,
)

import ufo.db
import ufo.host.kinds.conversations as conversations_kind
from ufo.blob import FilesystemBlobStore, FleetBlobStore, WorkspaceBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.flags import SERVED_FALSE, SERVED_TRUE, init_flags
from ufo.harness.auth.bearer import mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import (
    ANTHROPIC_KEY_SLOT,
    CORE_MODEL_SPECS,
    CORE_PRICING,
    OPENAI_KEY_SLOT,
)
from ufo.harness.models.grant import (
    ANTHROPIC_CLIENT_ID_ENV,
    Grant,
    read_grant,
)
from ufo.harness.models.interface import (
    Message,
    ModelEvent,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.conversation import (
    SANDBOX_IMAGE_REF,
    WORKSPACE_WRITE_MAX_BYTES,
    ConversationSandbox,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import (
    member_object_registry,
    member_skill_listing,
    skill_registry,
)
from ufo.host.kinds.members import ADD_MEMBER_GATE
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import (
    CatalogEntry,
    CatalogPage,
    ConnectorEntry,
    ConnectorRegistry,
)
from ufo.runtime.access.credentials import (
    CREDENTIAL_REQUEST_TTL_SECONDS,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    member_slot,
    seal_credential_request,
)
from ufo.runtime.access.grants import (
    ConnectFlow,
    GrantStore,
    OAuthAccount,
    account_object_name,
    install_connect_flow,
)
from ufo.runtime.agent_scope import agent as bind_agent
from ufo.runtime.billing.accounting import record_egress_request, record_turn_usage
from ufo.runtime.engine import FINISH_PROMPT
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.surface import (
    AMBIENT_CONTEXT_ELEMENT,
    CONVERSATION_TITLE_CHARS,
    PreviewRender,
    SurfaceContext,
    fence_member_message,
    member_message_text,
    mint_marker,
)
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    ArtifactsChanged,
    CostTick,
    InProcessHub,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.objects import OBJECT_LIST_PAGE
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.surfaces.artifacts import router as artifacts_router
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
)
from ufo.runtime.turns.workspace_changes import WorkspaceChange, WorkspaceChanges
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    MEMBER_ADMISSION,
    SPAWN_RESULT_KEY_PREFIX,
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
from ufo.sdk.jobs import unseeded_agent_workspaces
from ufo.sdk.manifest import (
    SCHEDULE_KIND,
    AgentProvision,
    AgentSetup,
    AgentSpec,
    CredentialSlot,
    Manifest,
    SetupCadence,
    SetupCredential,
    SetupSchedule,
    SubagentProfile,
)
from ufo.serve import _mount_shared_surfaces

SECRET = "artifact-signing-secret"


@dataclass(frozen=True)
class CatalogResolver:
    @property
    def transfer_hosts(self) -> tuple[str, ...]:
        return ()

    async def claims(self, provider: str) -> bool:
        return provider in {"notion", "salesforce"}

    def entry(self, provider: str) -> ConnectorEntry:
        raise AssertionError(provider)

    async def catalog(self, query: str, limit: int, after: str | None) -> CatalogPage:
        rows = (
            CatalogEntry(provider="notion", label="Notion"),
            CatalogEntry(provider="salesforce", label="Salesforce"),
        )
        matched = tuple(row for row in rows if query.lower() in row.label.lower())
        if after == "catalog-page-two":
            return CatalogPage(entries=matched[1:limit], after=None)
        return CatalogPage(
            entries=matched[:1],
            after="catalog-page-two" if len(matched) > 1 else None,
        )


def _schedule_store() -> ScheduleStore:
    """The relocated store, which now reads its workspace and object agent off the context the
    extension's own callers hand it."""
    return ScheduleStore(context_for(SCHEDULED_TASKS_NAME, frozenset()))


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
SOURCE_TRIGGER_KIND_ONLY = Manifest(
    name="sources",
    version="0.1.0",
    objects=(SOURCE_TRIGGER_OBJECT,),
)
SLOTTED = Manifest(
    name="stub",
    version="0",
    credentials=(
        CredentialSlot(name="acme_api_key", description="ACME API key"),
        CredentialSlot(name="acme_signing_key", description="ACME signing key"),
        CredentialSlot(name="acme_install_seal", description="ACME install binding"),
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
SLACK_THREAD_PERMALINK = (
    "https://acme.slack.com/archives/C1/p1700000000000100?thread_ts=1700000000.000100&cid=C1"
)
STREAM_TIMEOUT_SECONDS = 30
STREAM_GATE = StreamGate()
CREDENTIAL_FERNET = Fernet(Fernet.generate_key())


def test_sse_tags_activity_frames() -> None:
    activity = _sse("7", Activity(text="Checking the workspace."))
    assert activity.startswith(b"id: 7\nevent: activity\ndata: ")
    assert json.loads(activity.split(b"data: ", 1)[1]) == {"text": "Checking the workspace."}


def test_sse_names_every_live_frame_kind_and_refuses_an_unmapped_one() -> None:
    """The browser subscribes by event name, and an unnamed event lands in `onmessage` as reply
    text — so a frame kind this projection does not name must raise here, never reach a member's
    transcript as `undefined`."""
    frames: dict[type, LiveFrame] = {
        TextDelta: TextDelta(text="t"),
        Terminal: Terminal(frame=TerminalFrame(status="done", text="t")),
        Parked: Parked(message="m"),
        CostTick: CostTick(cost_micro_usd=1, tokens=2),
        Activity: Activity(text="Checking the workspace."),
        Absorbed: Absorbed(arrivals=()),
        Resumed: Resumed(attempt="attempt-one"),
        Reply: Reply(id=uuid4(), text="sent"),
        SubagentActivity: SubagentActivity(
            turn_id=uuid4(),
            parent_turn_id=uuid4(),
            conversation_id=uuid4(),
            profile="general_purpose",
        ),
    }
    assert set(frames) | {ArtifactsChanged} == set(get_args(LiveFrame))
    named = {
        Terminal: b"event: terminal\n",
        Parked: b"event: parked\n",
        CostTick: b"event: cost\n",
        Activity: b"event: activity\n",
        Absorbed: b"event: absorbed\n",
        Resumed: b"event: resumed\n",
        Reply: b"event: reply\n",
        SubagentActivity: b"event: subagent_activity\n",
    }
    for kind, frame in frames.items():
        event = _sse("7", frame)
        assert event.startswith(b"id: 7\n")
        if kind is TextDelta:
            assert b"event:" not in event
        else:
            assert named[kind] in event
    assert b"event: comment\n" in _sse("8", Reply(id=uuid4(), text="commented", is_comment=True))

    class Unmapped(BaseModel):
        pass

    with pytest.raises(ValueError, match="unmapped live frame Unmapped"):
        _sse("", cast(LiveFrame, Unmapped()))


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
                            "requested_by": "internal-message-ref",
                        },
                    ),
                    ToolUseBlock(id="call-2", name="load_skill", input={"name": "coding"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="1 passed",
                        activity=True,
                        activity_text="Running the focused tests.",
                    ),
                    ToolResultBlock(
                        tool_use_id="call-2",
                        content="mounted",
                        activity=True,
                        activity_text="Loading coding guidance.",
                    ),
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
                {"kind": "note", "text": "Let me check."},
                {"kind": "activity", "text": "Running the focused tests."},
                {"kind": "activity", "text": "Loading coding guidance."},
            ],
        },
    ]


def test_transcript_projection_omits_an_empty_generated_activity() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nInspect it."),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="call-1", name="bash", input={"command": "ls"}),),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="README.md",
                        activity=True,
                        activity_text="",
                    ),
                ),
            ),
            Message(role="assistant", content="Done."),
        )
    )

    assert rendered == [
        {"role": "user", "text": "Inspect it."},
        {"role": "assistant", "text": "Done."},
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
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="…",
                        activity=True,
                        activity_text="Reading the changelog.",
                    ),
                ),
            ),
        )
    )

    assert events == [
        {"kind": "note", "text": "Reading the changelog first."},
        {"kind": "activity", "text": "Reading the changelog."},
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
        content=(
            ToolResultBlock(
                tool_use_id="call-1",
                content="…",
                activity=True,
                activity_text="Fetching deadline sources.",
            ),
        ),
    ),
    Message(role="assistant", content=FORCE_FINISHED_PROSE),
    Message(role="user", content=FINISH_PROMPT),
    Message(role="assistant", content=FORCE_FINISHED_PAYLOAD),
)


def test_a_run_answer_reads_the_fields_it_wrote_never_the_json_carrying_them() -> None:
    assert _run_answer('{"result": "It shipped Tuesday."}') == "It shipped Tuesday."
    assert _run_answer('{"result": "It shipped Tuesday.", "confidence": 3}') == (
        "**Result** — It shipped Tuesday.\n**Confidence** — 3"
    )
    assert _run_answer("ran out of rounds") == "ran out of rounds"
    assert _run_answer("") == ""


def test_transcript_projection_keeps_mid_turn_narration_as_steps_of_the_reply() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nWhat shipped?"),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Reading the changelog first.\n"),
                    ToolUseBlock(id="call-1", name="read", input={"file_path": "CHANGELOG.md"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="…",
                        activity=True,
                        activity_text="Reading the changelog.",
                    ),
                ),
            ),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Checking the tags now."),
                    ToolUseBlock(id="call-2", name="bash", input={"command": "git tag"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="call-2",
                        content="v1",
                        activity=True,
                        activity_text="Checking the release tags.",
                    ),
                ),
            ),
            Message(role="assistant", content=(TextBlock(text="It shipped Tuesday."),)),
        )
    )

    assert rendered == [
        {"role": "user", "text": "What shipped?"},
        {
            "role": "assistant",
            "text": "It shipped Tuesday.",
            "events": [
                {"kind": "note", "text": "Reading the changelog first."},
                {"kind": "activity", "text": "Reading the changelog."},
                {"kind": "note", "text": "Checking the tags now."},
                {"kind": "activity", "text": "Checking the release tags."},
            ],
        },
    ]


def test_transcript_projection_bounds_the_narration_it_keeps() -> None:
    rounds = SUBAGENT_EVENT_LIMIT + 5
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            *(
                Message(role="assistant", content=(TextBlock(text=f"Step {number}."),))
                for number in range(rounds)
            ),
            Message(role="assistant", content=(TextBlock(text="Done."),)),
        )
    )

    reply = rendered[-1]
    assert reply["text"] == "Done."
    events = cast(list[dict[str, str]], reply["events"])
    assert len(events) == SUBAGENT_EVENT_LIMIT
    assert events[0] == {"kind": "note", "text": "Step 0."}
    assert events[-1] == {"kind": "note", "text": f"Step {SUBAGENT_EVENT_LIMIT - 1}."}


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
                content=(
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="",
                        activity=True,
                        activity_text="Running the requested command.",
                    ),
                ),
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
            "events": [{"kind": "activity", "text": "Running the requested command."}],
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
                    ToolResultBlock(
                        tool_use_id="accepted",
                        content="done",
                        activity=True,
                        activity_text="Running the requested command.",
                    ),
                    ToolResultBlock(tool_use_id="rejected", content="ValueError", is_error=True),
                ),
            ),
        )
    )

    assert rendered == [
        {"role": "user", "text": "Run it."},
        {
            "role": "assistant",
            "text": "",
            "events": [{"kind": "activity", "text": "Running the requested command."}],
        },
    ]


def test_transcript_projection_keeps_generated_activity_labels() -> None:
    rendered = _rendered_messages(
        (
            Message(role="user", content="<context>source: web</context>\nRun it."),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="skill", name="load_skill", input={"name": 7}),
                    ToolUseBlock(id="tool", name="bash", input={}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="skill",
                        content="done",
                        activity=True,
                        activity_text="Loading the requested guidance.",
                    ),
                    ToolResultBlock(
                        tool_use_id="tool",
                        content="done",
                        activity=True,
                        activity_text="Running the requested command.",
                    ),
                ),
            ),
            Message(role="assistant", content="Done."),
        )
    )

    assert rendered[-1] == {
        "role": "assistant",
        "text": "Done.",
        "events": [
            {"kind": "activity", "text": "Loading the requested guidance."},
            {"kind": "activity", "text": "Running the requested command."},
        ],
    }


def _node(profile: str, conversation_id: UUID) -> SubagentNode:
    return SubagentNode(
        profile=profile,
        name="",
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
                icon="compass",
                is_main=True,
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _seed_member(
    workspace_id: UUID, email: str, *, admin: bool = False, openai_key: str | None = "sk-seeded"
) -> tuple[UUID, str]:
    """Seed a member and mint the signed bearer the gateway or `ufoctl init` would — the value the
    `ufo_session` cookie carries; the web surface resolves the workspace and the member email from
    it. An admin reaches every agent; anyone else reaches the main agent plus the non-main agents
    the web audience grants. The member arrives holding the OpenAI key sign-in collects, which is
    what the portal opens for; `openai_key=None` seeds the member who has not signed in yet."""
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
    if openai_key is not None:
        await CredentialStore(fernet=CREDENTIAL_FERNET).put(
            workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), openai_key
        )
    token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
    return member_id, token


async def _grant_web_access(workspace_id: UUID, agent_id: UUID, email: str) -> None:
    with ws(workspace_id):
        await web_extension().store.put(
            f"{AUDIENCE_PREFIX}{agent_id}/{email}", {"granted_by": str(uuid4())}
        )


async def _write_transcript(
    blob: WorkspaceBlobStore, conversation_id: UUID, conversation: Conversation
) -> None:
    async with workspace_tx() as connection:
        workspace_id = (
            await connection.execute(
                sa.select(tables.conversation.c.workspace_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    with ws(workspace_id):
        await Transcript(blob=blob, conversation_id=conversation_id).write(conversation)


async def _write_compaction(
    blob: WorkspaceBlobStore,
    conversation_id: UUID,
    index: int,
    before: tuple[Message, ...],
    after: tuple[Message, ...],
) -> None:
    async with workspace_tx() as connection:
        workspace_id = (
            await connection.execute(
                sa.select(tables.conversation.c.workspace_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    summary = CompactionSummary(intent="", current_work="", next_step="")
    with ws(workspace_id):
        await blob.put(
            compaction_key(conversation_id, index, "before"),
            lz4.frame.compress(CompactionWindow(messages=before).model_dump_json().encode()),
        )
        await blob.put(
            compaction_key(conversation_id, index, "after"),
            lz4.frame.compress(CompactionWindow(messages=after).model_dump_json().encode()),
        )
        await blob.put(
            compaction_key(conversation_id, index, "summary"),
            lz4.frame.compress(summary.model_dump_json().encode()),
        )


PORTAL_MANIFESTS = (
    web_manifest(),
    connectors_manifest(),
    imessage_manifest(),
    SCHEDULED_TASK_KIND_ONLY,
    skill_create_manifest(),
    slack_manifest(),
    sources_manifest(),
    todos.manifest(),
    SLOTTED,
)


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, GatingHub, WorkspaceBlobStore, ConversationSandbox]]:
    config = dbos_launched
    hub = GatingHub(InProcessHub(), STREAM_GATE)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=config.blob.root))
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
            run_tokens=RunTokenCodec(b"web-test-run-token-secret"),
            dbos=dbos_client,
            invoker_for=invoker_factory(dbos_client),
            subagents=PORTAL_SUBAGENTS,
            subagent_grants={},
            manifests=PORTAL_MANIFESTS,
            environment=HostEnvironment(
                manifests=PORTAL_MANIFESTS,
                credentials=CredentialStore(fernet=CREDENTIAL_FERNET),
                index=DefaultIndex(transaction=workspace_tx),
                embed=StubEmbed(),
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


SURFACE_MODEL: list[object] = [UNREACHED_SURFACE_MODEL]
"""What a mounted surface hands its routes as `ctx.model`. Every test holds the loud stub, so an
unintended generation fails rather than passing quietly; the one test that wants a route to reach a
model swaps it and puts it back."""


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
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.state.instance_id = uuid4()
    app.include_router(artifacts_router)
    _mount_shared_surfaces(
        app,
        (
            web_manifest(),
            imessage_manifest(),
            todos.manifest(),
            SCHEDULED_TASK_KIND_ONLY,
            SOURCE_TRIGGER_KIND_ONLY,
            SLOTTED,
            sites_manifest(),
            report_digest_manifest(),
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
        connectors=ConnectorRegistry(entries={}, resolver=CatalogResolver()),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        surface_model=lambda _name: SURFACE_MODEL[0],
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=lambda: member_skill_listing(
            (skill_create_manifest(),),
            CredentialStore(fernet=CREDENTIAL_FERNET),
            DefaultIndex(transaction=workspace_tx),
            StubEmbed(),
        ),
        objects=member_object_registry(
            (
                web_manifest(),
                imessage_manifest(),
                slack_manifest(),
                SCHEDULED_TASK_KIND_ONLY,
                SOURCE_TRIGGER_KIND_ONLY,
                SLOTTED,
                sites_manifest(),
                skill_create_manifest(),
                report_digest_manifest(),
            ),
            CredentialStore(fernet=CREDENTIAL_FERNET),
            public_base_url="https://web",
            artifact_token_secret=SECRET,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_reported_timezone_lands_on_the_turn_and_an_unknown_one_drops(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    turns: dict[str, str] = {}
    for zone in ("America/New_York", "Mars/Olympus_Mons"):
        STREAM_GATE.arm()
        admitted = await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation=new",
            content=b"hello",
            headers={"cookie": f"{SESSION_COOKIE}={token}", "x-ufo-timezone": zone},
        )
        assert admitted.status_code == 200
        turns[zone] = admitted.json()["turn_id"]
        await _consume(client, token, turns[zone])
    async with workspace_tx() as connection:
        contexts = {
            str(row.id): row.context
            for row in await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.context).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        }
    assert contexts[turns["America/New_York"]]["timezone"] == "America/New_York"
    assert contexts[turns["Mars/Olympus_Mons"]]["timezone"] is None


def test_title_excerpt_needs_an_assistant_reply_and_bounds_both_sides() -> None:
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


def test_title_excerpt_reads_the_member_s_own_words_out_of_the_inbound() -> None:
    """A channel surface's inbound is the ambient digest of the room, the member's own words fenced
    inside it, and the engine's context tag over the whole. A title written from all of that names
    the room and the wire rather than the conversation, so the excerpt is the fenced words alone."""
    marker = mint_marker()
    fenced = fence_member_message(
        marker,
        f"<{AMBIENT_CONTEXT_ELEMENT}_{marker}>\nOthers said: restock the depot\n"
        f"</{AMBIENT_CONTEXT_ELEMENT}_{marker}>\n",
        "Order more pallets before Friday",
        "",
    )
    inbound = Message(
        role="user",
        content=f"<context>\nmessage_ref: {uuid4()}\n</context>\n{fenced}",
    )
    reply = Message(role="assistant", content=(TextBlock(text="Ordered them."),))
    assert web_surface._title_excerpt((inbound, reply)) == (
        "Order more pallets before Friday\n\nOrdered them."
    )


async def _seed_running_turn(
    workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID, seq: int
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
                status="running",
                inbound="Review PR 1268.",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _turn_status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_shared_commenter_cannot_stop_another_members_turn(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    speaker_id, _speaker_token = await _seed_member(workspace_id, "speaker@example.com")
    _viewer_id, viewer_token = await _seed_member(workspace_id, "viewer@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    running = await _seed_running_turn(workspace_id, conversation_id, agent_id, speaker_id, 1)

    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        headers={
            "cookie": f"{SESSION_COOKIE}={viewer_token}",
            "x-ufo-stop-turn": str(running),
        },
    )

    assert refused.status_code == 403
    assert refused.headers[web_surface.REFUSAL_HEADER] == "1"
    assert await _turn_status(running) == "running"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_stop_refused_by_its_own_shape_touches_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every refusal of the stop lane lands before the turn is reached: a header that is no turn id,
    a stop carrying a message, and a stop paired with the `new` sentinel — which names no turn to
    end, so it opens no conversation on the way to finding that out."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )
    running = await _seed_running_turn(workspace_id, conversation_id, agent_id, member_id, 2)

    malformed = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        headers={**cookie, "x-ufo-stop-turn": "not-a-turn"},
    )
    assert malformed.status_code == 400
    assert malformed.text == "x-ufo-stop-turn must be a turn id"

    spoken = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"stop and also read this",
        headers={**cookie, "x-ufo-stop-turn": str(running)},
    )
    assert spoken.status_code == 400
    assert spoken.text == "a stop admits no message"

    fresh = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        headers={**cookie, "x-ufo-stop-turn": str(running)},
    )
    assert fresh.status_code == 400
    assert fresh.text == "a stop names the conversation its turn runs in"

    assert await _turn_status(running) == "running"
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert (conversations, turns) == (1, 2)


async def _seed_arrival(
    workspace_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
    *,
    seq: int,
    body: str,
    admission_source: str,
    speaker_member_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> UUID:
    arrival_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=arrival_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=seq,
                body=body,
                admission_source=admission_source,
                speaker_member_id=speaker_member_id,
                idempotency_key=idempotency_key,
                admitted_turn_id=turn_id,
                created_at=sa.func.now(),
            )
        )
    return arrival_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_agent_origin_arrival_states_its_prompt_and_claims_no_wait_of_the_members(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An undrained row is not always the member's: an extension folding a job prompt into a live
    turn queues one the same way. Its prose is the turn's to answer, so it draws the bubble the
    prompt that founds a turn draws — but the wait under a bubble says the member's own message is
    waiting to be picked up, and the row's admission source is what decides that."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )
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
    mine = await _seed_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=1,
        body="Also check the tests.",
        admission_source="member",
        speaker_member_id=member_id,
    )
    await _seed_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=2,
        body="[seat approval request] ask an admin to decide.",
        admission_source="internal",
    )

    reloaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert reloaded.status_code == 200
    assert reloaded.json() == {
        "messages": [
            {"role": "user", "text": "Review PR 1268."},
            {"role": "user", "text": "Also check the tests.", "arrival_id": str(mine)},
            {"role": "user", "text": "[seat approval request] ask an admin to decide."},
        ],
        "turn": str(running),
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_pending_subagent_result_is_no_member_bubble(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A background child's result folds onto the running turn's queue like any arrival, so between
    the fold and the drain it is pending beside the member's own message. It is a machine envelope,
    not words a member said: the projection skips it by its queue-row id — the id space
    `agent_origin_refs` answers for a drained arrival answers for an undrained one too — and renders
    the member's row alone."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )
    running = uuid4()
    delivered = uuid4()
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
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=delivered,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="<subagent_result>the child answered</subagent_result>",
                admission_source="internal",
                idempotency_key=f"{SPAWN_RESULT_KEY_PREFIX}{uuid4()}",
                admitted_turn_id=running,
                created_at=sa.func.now(),
            )
        )

    folded = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"Also check the tests.",
        headers=cookie,
    )
    assert folded.status_code == 200
    arrival_id = folded.json()["arrival_id"]

    reloaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert reloaded.status_code == 200
    assert reloaded.json() == {
        "messages": [
            {"role": "user", "text": "Review PR 1268."},
            {
                "role": "user",
                "text": "Also check the tests.",
                "arrival_id": arrival_id,
            },
        ],
        "turn": str(running),
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        "member": {
            "email": "outsider@example.com",
            "admin": False,
            "workspace_id": str(workspace_id),
        },
        "surfaces": dict.fromkeys(web_surface.PORTAL_SURFACES, True) | {"team": False},
        "archived": [],
        "agents": [
            {
                "id": str(agent_id),
                "name": "assistant",
                "main": True,
                "model": "claude-opus-4-8",
                "icon": "compass",
                "purpose": None,
                "app": None,
                "mine": False,
                "hidden": False,
                "homepage": {"state": "none"},
                "setup_due": False,
                "stands_on_setup": False,
            }
        ],
    }
    assert await _rail_rows(client, cookie) == []
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
                icon="telescope",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(owner_member_id=member_id)
            .where(tables.agent.c.id == second_agent)
        )
    admin_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert admin_view.json()["member"] == {
        "email": "admin@example.com",
        "admin": True,
        "workspace_id": str(workspace_id),
    }
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
            "icon": "compass",
            "purpose": None,
            "app": None,
            "mine": False,
            "hidden": False,
            "homepage": {"state": "none"},
            "setup_due": False,
            "stands_on_setup": False,
            "web_audience": [],
        },
        {
            "id": str(second_agent),
            "name": "ops",
            "main": False,
            "model": "claude-sonnet-5",
            "icon": "telescope",
            "purpose": None,
            "app": None,
            "mine": False,
            "hidden": False,
            "homepage": {"state": "none"},
            "setup_due": False,
            "stands_on_setup": False,
            "web_audience": ["member@example.com"],
        },
    ]
    member_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_view.json()["member"] == {
        "email": "member@example.com",
        "admin": False,
        "workspace_id": str(workspace_id),
    }
    assert [(a["id"], a["mine"]) for a in member_view.json()["agents"]] == [
        (str(agent_id), False),
        (str(second_agent), True),
    ]
    assert all("web_audience" not in agent for agent in member_view.json()["agents"])
    reachable = await client.get(
        f"/surface/web/agents/{agent_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert reachable.status_code == 400
    assert reachable.text == "conversation is required"


@pytest.fixture
def unbound_flags() -> Iterator[None]:
    """The boot read asks the deploy's flag backend what to offer, so a backend one case binds must
    not answer the next — and the fact that one was bound at all is what a case here drives."""
    yield
    api.clear_providers()
    init_flags(None)


async def _seed_shipped_app(workspace_id: UUID, slug: str) -> UUID:
    """One shipped app as its extension provisions it: the slug the portal reads its flag by comes
    off `provisioned_by`, not off the row's member-visible name."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=slug,
                prompt=f"be the {slug}",
                model="claude-sonnet-5",
                icon="book",
                provisioned_by=f"app_{slug}",
                provisioned_name=slug,
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _app_visibility(
    client: AsyncClient, token: str
) -> tuple[dict[str | None, bool], dict[str, bool]]:
    boot = (
        await client.get("/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    return {agent["app"]: agent["hidden"] for agent in boot["agents"]}, boot["surfaces"]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize("bound", [False, True])
async def test_a_flag_service_that_answers_nothing_leaves_a_member_what_they_had(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None, bound: bool
) -> None:
    """The two silences are one state to a member: a deploy that selected no flag backend (a
    development run, an eval stack, a self-hosted deploy) and one whose service holds none of these
    keys — which is every deploy the moment this lands, and every deploy again while Flagship is
    unreachable. Neither takes one of the portal's own screens away, and neither lists a shipped
    app: an app is offered where somebody turned its flag on, so silence draws it in no list while
    the workspace goes on holding it. The Team tab is answered by the reader's admin standing rather
    than by a flag, so no silence reaches it either way."""
    client, workspace_id, _agent_id = web
    init_flags(InMemoryProvider({}) if bound else None)
    for slug in web_surface.APP_FLAGS:
        await _seed_shipped_app(workspace_id, slug)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    visibility, surfaces = await _app_visibility(client, token)
    assert surfaces == dict.fromkeys(web_surface.PORTAL_SURFACES, True) | {"team": True}
    assert visibility == {None: False, **dict.fromkeys(web_surface.APP_FLAGS, True)}


STATUS_PATH = "/surface/web/api/agents/status"


async def _seed_status_agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be operational",
                model="claude-sonnet-5",
                icon="telescope",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _seed_status_turn(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    seq: int,
    status: str,
    at: datetime,
) -> UUID:
    turn_id = uuid4()
    terminal = (
        None
        if status in ("queued", "running", "parked")
        else TerminalFrame(status=status, text="ended").model_dump(mode="json")
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="asked",
                terminal=terminal,
                created_at=at,
                updated_at=at,
            )
        )
    return turn_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_agents_status_marks_the_latest_terminal_failure_until_a_later_run_clears_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation = await _seed_agent_conversation(
        workspace_id, agent_id, queue_key="web/runs", audience=str(SHARED_AUDIENCE), member_id=None
    )
    base = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    await _seed_status_turn(workspace_id, agent_id, conversation, seq=1, status="failed", at=base)
    failed = (await client.get(STATUS_PATH, headers=headers)).json()["statuses"][0]
    assert failed["last_failed"] is True
    assert failed["turn"] is None
    assert failed["last_active_at"] == base.isoformat()
    await _seed_status_turn(
        workspace_id, agent_id, conversation, seq=2, status="done", at=base + timedelta(hours=1)
    )
    cleared = (await client.get(STATUS_PATH, headers=headers)).json()["statuses"][0]
    assert cleared["last_failed"] is False
    assert cleared["last_active_at"] == (base + timedelta(hours=1)).isoformat()


@contextmanager
def _executed_statements() -> Iterator[list[str]]:
    """Every statement this loop's pool sends to the database while the block runs, read off the
    engine rather than any stand-in, so a test can observe a read that was skipped as well as one
    whose answer was thrown away."""
    loop = asyncio.get_running_loop()
    engines = [engine for (held, _url), engine in ufo.db._APP.engines.items() if held is loop]
    assert engines
    executed: list[str] = []

    def record(
        connection: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        executed.append(statement)

    for engine in engines:
        sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield executed
    finally:
        for engine in engines:
            sa.event.remove(engine.sync_engine, "before_cursor_execute", record)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_agents_status_never_reads_the_task_store(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every open tab polls this for as long as it is open, so the read costs one turn aggregate
    and nothing per agent in the object registry: an agent standing still beside an unpaused
    scheduled task sends the database no `scheduled_task` statement, and neither does the same
    agent once a turn is running on it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    conversation = await _seed_agent_conversation(
        workspace_id, agent_id, queue_key="web/tasks", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_radar_task(
        workspace_id,
        agent_id,
        conversation,
        name="nightly",
        created_by_member_id=member_id,
        next_run_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
    )
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}

    with _executed_statements() as idle:
        resting = (await client.get(STATUS_PATH, headers=headers)).json()

    row = next(entry for entry in resting["statuses"] if entry["agent_id"] == str(agent_id))
    assert row["turn"] is None
    assert "next_run_at" not in row
    assert [statement for statement in idle if "scheduled_task" in statement] == []

    await _seed_status_turn(
        workspace_id,
        agent_id,
        conversation,
        seq=1,
        status="running",
        at=datetime(2026, 8, 19, 6, 0, tzinfo=UTC),
    )
    with _executed_statements() as busy:
        working = (await client.get(STATUS_PATH, headers=headers)).json()

    row = next(entry for entry in working["statuses"] if entry["agent_id"] == str(agent_id))
    assert row["turn"] == "running"
    assert [statement for statement in busy if "scheduled_task" in statement] == []


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_agents_status_answers_only_the_member_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    private_agent = await _seed_status_agent(workspace_id, "ops")
    _member, member_token = await _seed_member(workspace_id, "m@example.com")
    _admin, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    conversation = await _seed_agent_conversation(
        workspace_id,
        private_agent,
        queue_key="web/private",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
    )
    await _seed_status_turn(
        workspace_id,
        private_agent,
        conversation,
        seq=1,
        status="running",
        at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
    )
    member_view = (
        await client.get(STATUS_PATH, headers={"cookie": f"{SESSION_COOKIE}={member_token}"})
    ).json()
    assert [row["agent_id"] for row in member_view["statuses"]] == [str(agent_id)]
    admin_view = (
        await client.get(STATUS_PATH, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"})
    ).json()
    assert {row["agent_id"] for row in admin_view["statuses"]} == {
        str(agent_id),
        str(private_agent),
    }
    private_row = next(
        row for row in admin_view["statuses"] if row["agent_id"] == str(private_agent)
    )
    assert private_row["turn"] == "running"
    anonymous = await client.get(STATUS_PATH)
    assert anonymous.status_code == 401


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
                shared=shared,
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
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connections_panel_holds_the_member_gate_and_the_wall(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """#624 acceptance, read-side: inside one agent, member M's private connector never appears in
    member N's panel while shared ones appear to both — a shared connection naming its owner to
    every member who can reach it; another agent's grants are absent; an out-of-audience agent is
    not-found; a workspace admin sees every edge."""
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connection_pool_names_no_agent_outside_the_web_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="legal-review",
                prompt="review",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_connection(workspace_id, walled_agent, member_m, "github", shared=True)
    pool = await client.get(
        "/surface/web/connections", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert [(c["provider"], c["agents"]) for c in pool.json()["connections"]] == [("github", [])]
    admin_pool = await client.get(
        "/surface/web/connections", headers={"cookie": f"{SESSION_COOKIE}={token_admin}"}
    )
    assert [a["name"] for c in admin_pool.json()["connections"] for a in c["agents"]] == [
        "legal-review"
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connection_pool_hides_another_members_private_connection_from_an_admin(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The connectors page lists a private account to its owner alone. A workspace admin reads
    their own private account and every shared one, and never another member's private account:
    admin authority governs acts on a connection, not the sight of one."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    admin_member, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_connection(workspace_id, agent_id, member_m, "github", shared=False)
    await _seed_connection(workspace_id, agent_id, member_m, "slack", shared=True)
    await _seed_connection(workspace_id, agent_id, admin_member, "asana", shared=False)
    admin_pool = await client.get(
        "/surface/web/connections", headers={"cookie": f"{SESSION_COOKIE}={token_admin}"}
    )
    assert [
        (c["provider"], c["owner_email"], c["own"], c["shared"])
        for c in admin_pool.json()["connections"]
    ] == [
        ("asana", "boss@example.com", True, False),
        ("slack", "m@example.com", True, True),
    ]
    member_pool = await client.get(
        "/surface/web/connections", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert [(c["provider"], c["own"]) for c in member_pool.json()["connections"]] == [
        ("github", True),
        ("slack", True),
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credentials_view_reports_slots_and_never_values(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace credentials view: every declared slot with its fill state — the slots are the
    deploy's, shared across every agent, and a value never renders. An unauthenticated read is
    refused before a byte of it."""
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
        },
        {
            "slot": "acme_install_seal",
            "name": "acme-install-seal",
            "extension": "stub",
            "description": "ACME install binding",
            "filled": False,
        },
        {
            "slot": "acme_signing_key",
            "name": "acme-signing-key",
            "extension": "stub",
            "description": "ACME signing key",
            "filled": False,
        },
    ]
    assert "sealed" not in listed.text


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_workspace_surfaces_names_each_installed_surface_inside_the_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace surfaces view behind the agents topology graph: every surface installation
    with the agent its conversations land on, narrowed to the reader's web audience — an agent no
    grant admits names neither its id nor its bound surface, an admin reads every binding — and
    refused without a session."""
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
        for surface, installation, holder in (
            ("slack", "team:T42", agent_id),
            ("teams", "tenant:T99", second_agent),
        ):
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    routes_ingress=True,
                    workspace_id=workspace_id,
                    surface=surface,
                    installation_id=installation,
                    agent_id=holder,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    path = "/surface/web/workspace/surfaces"
    view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert view.status_code == 200
    assert view.json()["installations"] == [{"surface": "slack", "agent_id": str(agent_id)}]
    assert str(second_agent) not in view.text
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"})
    assert admin_view.json()["installations"] == [
        {"surface": "slack", "agent_id": str(agent_id)},
        {"surface": "teams", "agent_id": str(second_agent)},
    ]
    await _grant_web_access(workspace_id, second_agent, "m@example.com")
    widened = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert widened.json()["installations"] == admin_view.json()["installations"]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_workspace_surfaces_states_each_chat_surface_for_the_reader(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three chat surfaces in one fixed order, each with whether the deploy offers it and
    whether the reader reaches it: Slack by the workspace's installation, iMessage by the reader's
    own proved address — a reservation nobody proved and another member's address count for
    nothing — and the terminal by the identity its first authenticated request links, beside the
    install line built from the deploy's public base. iMessage is offered where the deploy holds
    the provider pair; this fixture's https base is no dev deploy, so without the pair it is not."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    other_id, other_token = await _seed_member(workspace_id, "n@example.com")
    for name in ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)
    path = "/surface/web/workspace/surfaces"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    bare = await client.get(path, headers=cookie)
    assert bare.status_code == 200
    assert set(bare.json()) == {"installations", "surfaces"}
    assert bare.json()["surfaces"] == [
        {
            "name": "slack",
            "label": "Slack",
            "offered": True,
            "connected": False,
            "install_command": None,
        },
        {
            "name": "imessage",
            "label": "iMessage",
            "offered": False,
            "connected": False,
            "install_command": None,
        },
        {
            "name": "ufo",
            "label": "Terminal",
            "offered": True,
            "connected": False,
            "install_command": "curl -fsSL https://web/ufo | sh",
        },
    ]
    monkeypatch.setenv("UFO_SPECTRUM_PROJECT_ID", "project")
    monkeypatch.setenv("UFO_SPECTRUM_PROJECT_SECRET", "secret")
    live = datetime.now(UTC) + timedelta(minutes=10)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                routes_ingress=True,
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T42",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for address, holder, proved_by in (
            ("+15550000001", member_id, "msg-1"),
            ("+15550000002", other_id, None),
        ):
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface="imessage",
                    address=address,
                    workspace_id=workspace_id,
                    member_id=holder,
                    claim_expires_at=None if proved_by else live,
                    proved_by=proved_by,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface="ufo",
                external_id="m@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    connected = await client.get(path, headers=cookie)
    assert [
        (row["name"], row["offered"], row["connected"]) for row in connected.json()["surfaces"]
    ] == [("slack", True, True), ("imessage", True, True), ("ufo", True, True)]
    other = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={other_token}"})
    assert [(row["name"], row["connected"]) for row in other.json()["surfaces"]] == [
        ("slack", True),
        ("imessage", False),
        ("ufo", False),
    ]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_first_run_states_the_tiles_and_the_connectors_real_state(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The first run's projection: the tiles a member picks what their team uses from — Slack and
    GitHub among them, because a team that uses them says so like any other tool — and the one of
    those tiles the page installs itself, beside whether the workspace holds it. Slack's state is
    the leg its own Connect act writes, the surface installation, and it is the whole workspace's,
    so the step reads installed for every member. GitHub is a broker connection like Gmail or
    Notion — the member connects it in chat, so it is a tile and never a step, and a `github`
    connection changes no step. Refused without a session."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    other_id, other_token = await _seed_member(workspace_id, "n@example.com")
    path = "/surface/web/workspace/first-run"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    bare = await client.get(path, headers=cookie)
    assert bare.status_code == 200
    payload = bare.json()
    assert payload["model_key_held"] is True
    assert {"gmail", "notion", "linear", "slack", "github"} <= {
        tile["name"] for tile in payload["providers"]
    }
    assert payload["connectors"] == [{"name": "slack", "label": "Slack", "installed": False}]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                routes_ingress=True,
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T42",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _seed_connection(workspace_id, agent_id, other_id, "github", shared=False)
    held = await client.get(path, headers=cookie)
    assert held.json()["connectors"] == [{"name": "slack", "label": "Slack", "installed": True}]
    owner = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={other_token}"})
    assert owner.json()["connectors"] == held.json()["connectors"]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401
    assert set(payload) == {
        "providers",
        "connectors",
        "actions",
        "model_key_held",
    }
    assert [view["name"] for view in payload["actions"]["member"]] == ["add_member"]
    assert payload["actions"]["enrichment_profile"] == []


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_artifact_fanout_does_not_discover_an_admin_only_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The shelf's unnamed read uses the member's ordinary agent audience, so administration does
    not expose a private agent's files. Naming that agent keeps the admin's direct access."""
    client, workspace_id, _main_agent = web
    creator_id, _creator_token = await _seed_member(workspace_id, "creator@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    private_agent = await _seed_status_agent(workspace_id, "private-files")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        private_agent,
        queue_key="slack/private-agent",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    turn_id = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        private_agent,
        seq=1,
        inbound="share",
        speaker_member_id=creator_id,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/private-agent.txt",
                workspace_id=workspace_id,
                filename="private-agent.txt",
                subject="private agent output",
                media_type="text/plain",
                size_bytes=3,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    headers = {"cookie": f"{SESSION_COOKIE}={admin_token}"}

    shelf = (await client.get(OBJECT_ARTIFACTS_PATH, headers=headers)).json()
    direct = (
        await client.get(
            f"{OBJECT_ARTIFACTS_PATH}?agent={private_agent}",
            headers=headers,
        )
    ).json()

    assert shelf["objects"] == []
    assert _names(direct) == ["private-agent.txt"]


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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert [entry["label"] for entry in workspace["by_origin"]] == ["web"]
    assert {line["dimension"] for line in workspace["by_dimension"]} == {"egress", "tokens"}
    windowed = (
        await client.get(
            f"{path}?window_seconds=3600", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
    ).json()
    assert windowed["window_seconds"] == 3_600
    assert web_surface.USAGE_RANGES["1d"] == 86_400
    for name, seconds in web_surface.USAGE_RANGES.items():
        ranged = await client.get(
            f"{path}?range={name}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
        assert ranged.status_code == 200
        assert ranged.json()["window_seconds"] == seconds
    unnamed = await client.get(
        f"{path}?range=2d", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert unnamed.status_code == 400
    for bad in ("abc", "-5", "0", str(web_surface.MAX_USAGE_WINDOW_SECONDS + 1)):
        refused = await client.get(
            f"{path}?window_seconds={bad}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
        assert refused.status_code == 400
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


OBJECT_ARTIFACTS_PATH = "/surface/web/objects/artifact"


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
    return [entry["filename"] for entry in payload["objects"]]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_artifact_pages_walk_the_object_cursor_behind_the_member_fence(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The agent-scoped object cursor pages every one of the member's files exactly once, another
    member's files are absent from every page of the walk, and a cursor this surface never minted
    is a 400 rather than the newest page."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, _token_n = await _seed_member(workspace_id, "n@example.com")
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_m,
        "m@example.com",
        tuple(
            (f"mine-{index:02d}.txt", base + timedelta(minutes=index * 2))
            for index in range(OBJECT_LIST_PAGE + 2)
        ),
    )
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_n,
        "n@example.com",
        tuple(
            (f"theirs-{index}.txt", base + timedelta(minutes=index * 2 + 1)) for index in range(3)
        ),
    )
    headers = {"cookie": f"{SESSION_COOKIE}={token_m}"}
    walk = f"{OBJECT_ARTIFACTS_PATH}?agent={agent_id}&order_by=shared_at&order=desc"
    first = (await client.get(walk, headers=headers)).json()
    assert len(first["objects"]) == OBJECT_LIST_PAGE
    assert first["next_cursor"]
    second = (
        await client.get(f"{walk}&cursor={quote(first['next_cursor'])}", headers=headers)
    ).json()
    assert second["next_cursor"] is None
    walked = _names(first) + _names(second)
    assert len(walked) == OBJECT_LIST_PAGE + 2
    assert len(walked) == len(set(walked))
    assert all(name.startswith("mine-") for name in walked)
    refused = await client.get(f"{walk}&cursor=nonsense", headers=headers)
    assert refused.status_code == 400


async def _seed_radar_task(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    name: str,
    created_by_member_id: UUID,
    next_run_at: datetime = datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
    paused: bool = False,
) -> UUID:
    task_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(scheduled_task).values(
                id=task_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                name=name,
                created_by_member_id=created_by_member_id,
                schedule="0 9 * * *",
                prompt="check the queue",
                description="the queue check",
                next_run_at=next_run_at,
                paused=paused,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return task_id


async def _seed_scheduled_run(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    *,
    seq: int = 1,
    key: str | None = None,
    status: str = "done",
    text: str,
    fired: datetime,
    artifact: tuple[str, str] | None = None,
    context: TurnContext | None = None,
) -> UUID:
    run_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=run_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound="fired",
                admission_source="scheduled",
                idempotency_key=key,
                context=None if context is None else context.model_dump(mode="json"),
                terminal=TerminalFrame(status=status, text=text).model_dump(mode="json"),
                created_at=fired,
                updated_at=fired,
            )
        )
        if artifact is not None:
            filename, media_type = artifact
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=run_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=workspace_id,
                    filename=filename,
                    subject="the file",
                    media_type=media_type,
                    size_bytes=3,
                    created_at=fired,
                    updated_at=fired,
                )
            )
    return run_id


async def _seed_digest_entry(
    workspace_id: UUID,
    turn_id: UUID,
    *,
    title: str,
    summary: str,
    points: tuple[tuple[str, str], ...] = (),
) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(report_digest_entry).values(
                workspace_id=workspace_id,
                turn_id=turn_id,
                title=title,
                summary=summary,
                points=[{"text": text, "actor": actor} for text, actor in points],
                reader="the queue owner",
                model="model-under-test",
                written_at=datetime(2026, 8, 14, 9, 5, tzinfo=UTC),
            )
        )


REPORTS_PATH = "/surface/web/objects/report"
REPORTS_NEWEST = f"{REPORTS_PATH}?order_by=fired_at&order=desc"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            manifest=None,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            manifest=None,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert {
            row["name"]: row["filled"]
            for row in listed["objects"]
            if row["name"].startswith("acme-")
        } == {
            "acme-api-key": True,
            "acme-install-seal": False,
            "acme-signing-key": False,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert read.json()["spec"] == {"admin": False, "seated": True}

        narrowed = (
            await client.get(f"/surface/web/objects/member?agent={child_agent}", headers=cookie)
        ).json()
        assert [row["name"] for row in narrowed["objects"]] == [str(reader)]
        hidden = await client.get(
            f"/surface/web/objects/member/{colleague_id}?agent={child_agent}", headers=cookie
        )
        assert hidden.status_code == 404
        assert "member" in hidden.text


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_artifact_index_reads_only_the_members_own_and_shared_files(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A shared file answers on the subjects the reading member's own conversation carries: their
    own private conversations plus the workspace-shared. A file shared into a workspace-shared
    conversation answers every member; another member's private file is absent from the index and
    not-found by name. An admin is fenced the same way — audience is not a role, so a file shared
    in a conversation an admin cannot read is absent for them too."""
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
                admission_source=MEMBER_ADMISSION,
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
    for token, reach in (
        (token_m, {"mine.txt", "ours.txt"}),
        (token_admin, {"ours.txt"}),
    ):
        cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
        listed = (await client.get(index, headers=cookie)).json()
        assert {row["filename"] for row in listed["objects"]} == reach
        shared_read = await client.get(
            f"/surface/web/objects/artifact/{names['ours.txt']}?agent={agent_id}", headers=cookie
        )
        assert shared_read.status_code == 200
        assert shared_read.json()["spec"]["subject"] == "the shared file"
        hidden = await client.get(
            f"/surface/web/objects/artifact/{names['theirs.txt']}?agent={agent_id}", headers=cookie
        )
        assert hidden.status_code == 404
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_object_pages_refuse_an_unregistered_kind_an_unlisted_kind_and_a_walled_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every kind the portal cannot serve refuses by name: an unregistered kind, a kind that does
    not list, and an agent outside the viewer's web audience."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    unknown = await client.get(f"/surface/web/objects/widget?agent={agent_id}", headers=cookie)
    assert unknown.status_code == 404
    assert "widget" in unknown.text
    unlisted = await client.get(f"/surface/web/objects/agent?agent={agent_id}", headers=cookie)
    assert unlisted.status_code == 404
    assert "agent does not list in the portal" in unlisted.text
    walled = await client.get(f"/surface/web/objects/site?agent={uuid4()}", headers=cookie)
    assert walled.status_code == 404
    assert walled.text == "no such agent"
    signed_out = await client.get(f"/surface/web/objects/site?agent={agent_id}")
    assert signed_out.status_code == 401


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    rows = await _rail_rows(client, cookie)
    assert [row["name"] for row in rows] == [
        opened.json()["conversation_id"],
        str(seeded_id),
    ]
    assert rows[0]["title"] == "Summarize the incident review"
    assert rows[0]["agent_name"] == "assistant"
    assert rows[0]["last_at"] is not None
    assert rows[1]["title"] == "An earlier exchange"
    peer_rows = await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={other_token}"})
    assert [row["name"] for row in peer_rows] == [theirs.json()["conversation_id"]]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_can_read_and_reply_in_a_private_extension_conversation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _main_agent = web
    member_id, _token = await _seed_member(workspace_id, "Member@Example.com")
    token = mint_token(
        TOKEN_SECRET,
        str(workspace_id),
        "member@example.com",
        timedelta(hours=1),
    )
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    review_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=review_agent,
                workspace_id=workspace_id,
                name="code-review",
                prompt="Answer briefly.",
                model="claude-opus-4-8",
                is_main=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        review_agent,
        queue_key=f"code-review:{member_id}:42",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="extension:coding",
    )
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        review_agent,
        seq=1,
        inbound="Review pull request 42.",
    )
    shared_id = await _seed_agent_conversation(
        workspace_id,
        review_agent,
        queue_key="C1:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    await _seed_listed_turn(
        workspace_id,
        shared_id,
        review_agent,
        seq=1,
        inbound="Shared review room.",
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    reached = await client.get(
        f"/surface/web/api/chats?conversation={conversation_id}", headers=cookie
    )
    assert [row["conversation_id"] for row in reached.json()["chats"]] == [str(conversation_id)]
    index = await client.get("/surface/web/api/agents", headers=cookie)
    assert str(review_agent) not in {agent["id"] for agent in index.json()["agents"]}
    settings = await client.get(f"/surface/web/agents/{review_agent}/settings", headers=cookie)
    assert settings.status_code == 404
    new_chat = await client.post(
        f"/surface/web/agents/{review_agent}/chat?conversation=new",
        content=b"Open another chat.",
        headers=cookie,
    )
    assert new_chat.status_code == 404
    transcript = await client.get(
        f"/surface/web/agents/{review_agent}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert transcript.status_code == 200
    reply = await client.post(
        f"/surface/web/agents/{review_agent}/chat?conversation={conversation_id}",
        content=b"Apply the first review suggestion.",
        headers=cookie,
    )
    assert reply.status_code == 200
    async with workspace_tx() as connection:
        speaker = (
            await connection.execute(
                sa.select(tables.turn.c.speaker_member_id).where(
                    tables.turn.c.id == UUID(reply.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert speaker == member_id
    foreign = await client.get(
        f"/surface/web/agents/{review_agent}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert foreign.status_code == 404
    shared_transcript = await client.get(
        f"/surface/web/agents/{review_agent}/transcript?conversation={shared_id}",
        headers=cookie,
    )
    shared_reply = await client.post(
        f"/surface/web/agents/{review_agent}/chat?conversation={shared_id}",
        content=b"Cross the private agent boundary.",
        headers=cookie,
    )
    shared_permalink = await client.get(
        f"/surface/web/api/chats?conversation={shared_id}",
        headers=cookie,
    )
    assert shared_transcript.status_code == 404
    assert shared_reply.status_code == 404
    assert shared_permalink.json() == {"chats": []}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_rail_reads_every_surface_under_its_bound(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rail reads every surface under its bound: newer Slack traffic this member opened occupies
    a slot and uses its opening message, while a same-surface prepared-intent row without a chat
    record drops."""
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
    await _seed_listed_turn(
        workspace_id,
        slack_id,
        agent_id,
        seq=1,
        inbound="new Slack traffic",
        speaker_member_id=member_id,
    )
    monkeypatch.setattr(conversations_kind, "CONVERSATION_MINE_LIMIT", 1)
    rows = await _rail_rows(client, cookie)
    assert [row["name"] for row in rows] == [str(slack_id)]
    assert rows[0]["title"] == "new Slack traffic"
    assert rows[0]["surface"] == "slack"
    assert rows[0]["surface_label"] is None


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_rail_groups_the_members_own_conversations_and_everyone_elses(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A workspace-shared conversation is readable by everyone, so the rail asks who is in it and
    says so on the row. The member's own thread and the one they answered in are `mine`; the peer's
    is theirs, named by the peer who spoke it; a run an extension triggered with no member turn at
    all is nobody's and stands in neither group. Each member reads the same two threads from the
    opposite sides."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    mine_id, theirs_id, triggered_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for conversation_id, surface, queue_key in (
            (mine_id, "slack", "C42:1723.0"),
            (theirs_id, "slack", "C42:1723.1"),
            (triggered_id, "coding", "review/9"),
        ):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=surface,
                    queue_key=queue_key,
                    member_id=None,
                    audience="shared",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    await _seed_listed_turn(
        workspace_id, mine_id, agent_id, seq=1, inbound="mine", speaker_member_id=member_id
    )
    await _seed_listed_turn(
        workspace_id, theirs_id, agent_id, seq=1, inbound="theirs", speaker_member_id=peer_id
    )
    await _seed_listed_turn(
        workspace_id, theirs_id, agent_id, seq=2, inbound="i answered", speaker_member_id=member_id
    )
    await _seed_listed_turn(workspace_id, triggered_id, agent_id, seq=1, inbound="review run")
    rows = {row["name"]: row for row in await _rail_rows(client, cookie)}
    assert set(rows) == {str(mine_id), str(theirs_id)}
    assert rows[str(mine_id)]["mine"] is True
    assert rows[str(theirs_id)]["mine"] is True
    assert rows[str(mine_id)]["speaker"] is None

    peer_rows = {
        row["name"]: row
        for row in await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={peer_token}"})
    }
    assert set(peer_rows) == {str(mine_id), str(theirs_id)}
    assert peer_rows[str(theirs_id)]["mine"] is True
    assert peer_rows[str(mine_id)]["mine"] is False
    assert peer_rows[str(mine_id)]["speaker"] == "owner@example.com"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_conversation_the_member_cannot_name_is_not_railed(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A conversation this member cannot name is not a row. `title` is their own opening words, so
    one with none was opened by something other than a member speaking — a probe, a provisioning
    run — and holds nothing to read: railed, it is a blank line leading to a screen that says only
    that it is empty."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    named_id, _ = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="ok"),
        title="Ship the release",
    )
    nameless_id, _ = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text=""),
        title="",
    )

    assert [row["name"] for row in await _rail_rows(client, cookie)] == [str(named_id)]

    # The row is hidden, never destroyed: its permalink still resolves the conversation it names.
    reached = await client.get(f"/surface/web/api/chats?conversation={nameless_id}", headers=cookie)
    assert reached.status_code == 200


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_bare_chats_read_names_no_conversation_and_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The rail's listing is the `conversation` kind's member listing; `api/chats` keeps only the
    permalink resolve, so a read naming no conversation is a stated refusal rather than a page."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    bare = await client.get(
        "/surface/web/api/chats", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert bare.status_code == 400
    assert bare.text == "name a conversation to resolve"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    monkeypatch.setattr(conversations_kind, "CONVERSATION_MINE_LIMIT", 1)
    assert [row["name"] for row in await _rail_rows(client, cookie)] == [
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert private.json()["conversation"]["commentable"] is False
    assert walled.json()["conversation"]["readable"] is False
    assert walled.json()["conversation"]["disclosable"] is False
    assert walled.json()["conversation"]["commentable"] is False
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={theirs}",
        content=b"not mine",
        headers=admin_cookie,
    )
    assert refused.status_code == 404
    for conversation_id in (theirs, room):
        unprivileged = await client.get(
            f"/surface/web/api/chats?conversation={conversation_id}", headers=peer_cookie
        )
        assert unprivileged.json() == {"chats": []}, conversation_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_malformed_chat_row_is_a_fault_not_a_missing_conversation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A chat row that fails validation raises — the chat POST and the permalink resolve both
    surface the fault instead of degrading to not-found or silently dropping the row."""
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
            .values(value={"agent_id": "not-a-uuid", "email": 7})
        )
    with pytest.raises(ValidationError):
        await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
            content=b"hello",
            headers=cookie,
        )
    with pytest.raises(ValidationError):
        await client.get(f"/surface/web/api/chats?conversation={conversation_id}", headers=cookie)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_shared_slack_turn_streams_to_a_member_who_did_not_speak_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    speaker_id, _speaker_token = await _seed_member(workspace_id, "speaker@example.com")
    _viewer_id, viewer_token = await _seed_member(workspace_id, "viewer@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    turn_id = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound="from Slack",
        speaker_member_id=speaker_id,
    )

    streamed = await client.get(
        f"/surface/web/turns/{turn_id}/stream",
        headers={"cookie": f"{SESSION_COOKIE}={viewer_token}"},
    )

    assert streamed.status_code == 200
    assert "event: terminal" in streamed.text


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


def _write_apps_tree(root: Path) -> None:
    for path, body in (
        ("radar/index.html", "<script src=/assets/radar-A1.js>"),
        ("wiki/index.html", "<script src=/assets/wiki-B2.js>"),
        ("assets/radar-A1.js", "r()"),
        ("assets/wiki-B2.js", "w()"),
        ("assets/pages-C3.css", ":root{}"),
        ("assets/Inter-D4.woff2", "font"),
    ):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body)


def test_an_apps_tree_carries_only_what_the_build_emitted(tmp_path: Path) -> None:
    """A file browser dropping `.DS_Store` into the built tree must not join it. It has no suffix to
    type and no page names it, but it would change the digest — republishing every file of the tree
    under a fresh prefix and moving the `deploy_generation` every open app page remounts on."""
    _write_apps_tree(tmp_path)
    clean = web_surface.load_apps(tmp_path)
    assert clean is not None

    (tmp_path / ".DS_Store").write_bytes(b"\x00finder")
    (tmp_path / "assets/.DS_Store").write_bytes(b"\x00finder")
    (tmp_path / ".vite").mkdir()
    (tmp_path / ".vite/manifest.json").write_text("{}")

    littered = web_surface.load_apps(tmp_path)
    assert littered is not None
    assert littered.files == clean.files
    assert littered.digest == clean.digest


RUM_DEPLOY = {
    "UFO_WEB_RUM_APPLICATION_ID": "1ea7beef-0000-4000-8000-000000000001",
    "UFO_WEB_RUM_CLIENT_TOKEN": "pubdeadbeef",
    "UFO_WEB_RUM_SITE": "us5.datadoghq.com",
    "UFO_WEB_RUM_ENV": "testing",
    "UFO_WEB_RUM_VERSION": "abc12345",
}
RUM_SLOT = '<script type="application/json" id="rum">null</script>'


def test_a_half_configured_recording_is_refused_by_the_variables_it_left_unset() -> None:
    """Recording against an application the deploy half-names reaches the wrong application or
    none, and nobody looks for the sessions that never arrived — so the page is never served."""
    named = dict(RUM_DEPLOY)
    del named["UFO_WEB_RUM_CLIENT_TOKEN"]
    named["UFO_WEB_RUM_ENV"] = "  "

    with pytest.raises(RuntimeError, match="UFO_WEB_RUM_CLIENT_TOKEN, UFO_WEB_RUM_ENV"):
        rum_config(named)


def test_a_configured_value_cannot_close_the_block_it_is_written_into() -> None:
    shell = portal_shell(f"<head>{RUM_SLOT}</head>", {"env": "</script><script>alert(1)"})

    assert "<script>alert(1)" not in shell
    assert "\\u003c/script>\\u003cscript>alert(1)" in shell
    assert shell.count("</script>") == 1


def test_a_build_that_declares_no_rum_block_is_refused_rather_than_served() -> None:
    """A page with no block reads no configuration and records nothing, whatever the deploy sets."""
    with pytest.raises(RuntimeError, match="declares no rum block"):
        portal_shell("<head></head>", rum_config(RUM_DEPLOY))


async def test_the_portal_serves_the_sidebar_shell_until_the_lanes_flag_answers(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None
) -> None:
    """Which shell a member sees is the deploy's call, read per workspace at the page: a flag
    service holding no `enable-lanes-shell` key serves the sidebar shell — the closed state every
    silence falls to — and the lanes shell reaches a member only where somebody turned it on."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    init_flags(InMemoryProvider({}))
    silent = await client.get("/surface/web", headers=cookie)
    assert silent.status_code == 200
    assert "assets/sidebar-" in silent.text
    assert "assets/index-" not in silent.text

    init_flags(
        InMemoryProvider(
            {
                LANES_SHELL_FLAG: InMemoryFlag(
                    default_variant="on", variants={"on": SERVED_TRUE, "off": SERVED_FALSE}
                )
            }
        )
    )
    lanes = await client.get("/surface/web", headers=cookie)
    assert lanes.status_code == 200
    assert "assets/index-" in lanes.text
    assert "assets/sidebar-" not in lanes.text


def _stub_openai(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authorization: DeviceAuthorization | None,
    claims: tuple[DeviceClaim, ...] = (),
) -> None:
    """OpenAI's auth server, scripted: what the ask is granted and what each poll answers in turn.
    The surface constructs its own login object, so the stand-in is a class over this call's script
    rather than an instance."""
    pending = list(claims)

    @dataclass(frozen=True)
    class StubDeviceLogin:
        client_id: str

        async def request_code(self) -> DeviceAuthorization | None:
            return authorization

        async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim:
            assert (device_auth_id, user_code) == ("auth-1", "HY0H-0FOKK")
            return pending.pop(0)

    monkeypatch.setattr(web_surface, "OpenAiDeviceLogin", StubDeviceLogin)


def _authorization() -> DeviceAuthorization:
    return DeviceAuthorization(
        device_auth_id="auth-1",
        user_code="HY0H-0FOKK",
        verification_uri="https://auth.test/codex/device",
        interval=1,
    )


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_portal_opens_for_a_member_who_connected_no_provider(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connecting a provider is an onboarding step, not a toll gate: a member who skipped it reaches
    the portal and everything in it that does not need their key. What skipping costs them is the
    coding subagent, which is asserted where that gate lives — not here."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "skipper@example.com", openai_key=None)
    _stub_openai(monkeypatch, authorization=_authorization())

    opened = await client.get("/surface/web", headers={"cookie": f"{SESSION_COOKIE}={token}"})

    assert opened.status_code == 200


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_accounts_read_states_which_coding_account_the_member_holds(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One row per provider, answered for the member who asked — the workspace's own key is not
    theirs, and neither is another member's. The first run and the credentials screen both draw
    these rows, so what a member is offered and what they are told they hold cannot disagree."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "rows@example.com", openai_key=None)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    store = CredentialStore(fernet=CREDENTIAL_FERNET)
    await store.put(workspace_id, OPENAI_KEY_SLOT, "sk-workspace")

    bare = await client.get("/surface/web/workspace/accounts", headers=cookie)

    assert bare.status_code == 200
    assert bare.json() == {
        "accounts": [
            {"provider": "openai", "label": "ChatGPT", "connected": False},
            {"provider": "anthropic", "label": "Claude", "connected": False},
        ]
    }

    await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant-member")
    held = await client.get("/surface/web/workspace/accounts", headers=cookie)
    assert held.json()["accounts"][1] == {
        "provider": "anthropic",
        "label": "Claude",
        "connected": True,
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_disconnecting_drops_the_members_own_row_and_nothing_else(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A member who rotated or revoked an account comes back to replace it, so dropping theirs is
    an act they hold. It reaches their row only: the workspace key an admin set stands."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "rotator@example.com", openai_key=None)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    store = CredentialStore(fernet=CREDENTIAL_FERNET)
    await store.put(workspace_id, OPENAI_KEY_SLOT, "sk-workspace")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), "sk-member")

    dropped = await client.post("/surface/web/accounts/openai/disconnect", headers=cookie)

    assert dropped.status_code == 200
    assert dropped.json() == {"status": "disconnected"}
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id))
    assert await store.get(workspace_id, OPENAI_KEY_SLOT) == "sk-workspace"

    stray = await client.post("/surface/web/accounts/gemini/disconnect", headers=cookie)
    assert stray.status_code == 404


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_anthropic_ask_opens_an_authorization_the_member_pastes_back(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The member is sent to Anthropic carrying a PKCE challenge, and the verifier stays in an
    HttpOnly cookie — the code they paste back is only spendable with it."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "grant@example.com", openai_key=None)
    monkeypatch.setenv(ANTHROPIC_CLIENT_ID_ENV, "client-1")

    opened = await client.post(
        "/surface/web/anthropic/authorize", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )

    assert opened.status_code == 200
    url = opened.json()["url"]
    assert url.startswith(ANTHROPIC_AUTHORIZE_URL)
    assert quote_plus("client-1") in url
    assert "code_challenge_method=S256" in url
    state_cookie = opened.headers["set-cookie"]
    assert state_cookie.startswith(f"{ANTHROPIC_STATE_COOKIE}=")
    assert "HttpOnly" in state_cookie


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_pasted_anthropic_code_lands_the_token_under_the_members_own_slot(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pasted code buys an access token, which is checked against Anthropic before it is
    stored — and stored under the member who pasted it, never the workspace row."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "paster@example.com", openai_key=None)
    monkeypatch.setenv(ANTHROPIC_CLIENT_ID_ENV, "client-1")
    store = CredentialStore(fernet=CREDENTIAL_FERNET)

    @dataclass(frozen=True)
    class StubLogin:
        client_id: str

        def authorize(self) -> AnthropicPending:
            return AnthropicPending(url="https://claude.test/authorize", cookie="state-1.verify-1")

        async def claim(self, pasted: str, cookie: str) -> Grant | None:
            assert cookie == "state-1.verify-1"
            if pasted != "code-1#state-1":
                return None
            return Grant(
                access="sk-ant-oat01-granted", refresh="r-1", expires_at=time.time() + 3600
            )

    async def anthropic_answers(credential: str) -> bool:
        return credential == "sk-ant-oat01-granted"

    monkeypatch.setattr(web_surface, "AnthropicCodeLogin", StubLogin)
    monkeypatch.setattr(web_surface, "anthropic_verified_key", anthropic_answers)
    waiting = {"cookie": f"{SESSION_COOKIE}={token}; {ANTHROPIC_STATE_COOKIE}=state-1.verify-1"}

    refused = await client.post(
        "/surface/web/anthropic/code", data={ANTHROPIC_CODE_FIELD: "wrong"}, headers=waiting
    )
    assert refused.status_code == 200
    assert refused.json() == {"status": "refused", "message": ANTHROPIC_CODE_REFUSED}
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id))

    taken = await client.post(
        "/surface/web/anthropic/code",
        data={ANTHROPIC_CODE_FIELD: "code-1#state-1"},
        headers=waiting,
    )
    assert taken.json() == {"status": "connected"}
    stored = read_grant(await store.get(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id)))
    assert stored is not None
    assert (stored.access, stored.refresh) == ("sk-ant-oat01-granted", "r-1")
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, ANTHROPIC_KEY_SLOT)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_device_ask_hands_back_the_code_and_keeps_the_grant_in_a_cookie(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The member types the short user code at OpenAI, so that crosses. The device auth id is the
    handle that claims the grant, so it rides an HttpOnly cookie and never reaches the page."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "walker@example.com", openai_key=None)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    _stub_openai(monkeypatch, authorization=_authorization())

    opened = await client.post("/surface/web/openai/device", headers=cookie)

    assert opened.status_code == 200
    assert opened.json() == {
        "user_code": "HY0H-0FOKK",
        "verification_uri": "https://auth.test/codex/device",
        "interval": 1,
    }
    assert "auth-1" not in opened.text
    device_cookie = opened.headers["set-cookie"]
    assert device_cookie.startswith(f"{DEVICE_COOKIE}=auth-1.HY0H-0FOKK")
    assert "HttpOnly" in device_cookie


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_account_the_grant_is_refused_for_is_told_so_rather_than_left_waiting(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account with device code authorization switched off gets no grant. The ask says so, and
    sets no cookie — a poll behind a grant that was never opened would never end."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "blocked@example.com", openai_key=None)
    _stub_openai(monkeypatch, authorization=None)

    refused = await client.post(
        "/surface/web/openai/device", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )

    assert refused.status_code == 200
    assert refused.json() == {"error": DEVICE_UNAVAILABLE}
    assert "set-cookie" not in refused.headers


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_poll_lands_the_key_when_the_member_approves_the_grant(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each poll answers pending while the member is still at OpenAI, and connected once they
    approved. The key lands under that member's own slot, never the workspace row."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "device@example.com", openai_key=None)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    _stub_openai(
        monkeypatch,
        authorization=_authorization(),
        claims=(DeviceClaim(status="pending"), DeviceClaim(status="granted", key="sk-device")),
    )
    await client.post("/surface/web/openai/device", headers=cookie)
    waiting = {"cookie": f"{SESSION_COOKIE}={token}; {DEVICE_COOKIE}=auth-1.HY0H-0FOKK"}
    store = CredentialStore(fernet=CREDENTIAL_FERNET)

    pending = await client.get("/surface/web/openai/device/poll", headers=waiting)
    assert pending.json() == {"status": "pending"}
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id))

    granted = await client.get("/surface/web/openai/device/poll", headers=waiting)
    assert granted.json() == {"status": "connected"}
    assert await store.get(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id)) == "sk-device"
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, OPENAI_KEY_SLOT)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_poll_with_no_grant_behind_it_is_refused_rather_than_left_pending(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A poll only follows an ask. One arriving without the cookie that ask set has no grant to
    claim, so it says so instead of holding the screen in a loop that cannot end."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "stray@example.com", openai_key=None)

    stray = await client.get(
        "/surface/web/openai/device/poll", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )

    assert stray.json() == {"status": "refused", "message": DEVICE_UNAVAILABLE}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_first_run_states_whether_the_reading_member_holds_a_model_key(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The step that offers the doors reads the member, not the workspace: turns run on the key
    stored under the member's own id, so an admin's workspace row leaves them holding none, and
    either provider's slot answers for both doors."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "keyless@example.com", openai_key=None)
    _other_id, other_token = await _seed_member(workspace_id, "keyed@example.com")
    path = "/surface/web/workspace/first-run"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    store = CredentialStore(fernet=CREDENTIAL_FERNET)
    await store.put(workspace_id, OPENAI_KEY_SLOT, "sk-workspace")

    bare = await client.get(path, headers=cookie)
    assert bare.status_code == 200
    assert bare.json()["model_key_held"] is False

    await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant-member")
    held = await client.get(path, headers=cookie)
    assert held.json()["model_key_held"] is True

    seeded = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={other_token}"})
    assert seeded.json()["model_key_held"] is True


INLINE_ASSET = "data:"
URL_REFERENCE = re.compile(r"""url\((?:"([^"]*)"|'([^']*)'|([^)]*))\)""")


async def test_a_sessionless_arrival_is_sent_to_sign_in_and_the_posted_token_opens_one(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The portal offers no second way in: a GET with no session redirects to the deploy's one
    sign-in page, serving nothing of the shell. The bearer never rides a URL — the one POST that
    opens a session lands the form token as the host-only session cookie — HttpOnly, Secure, and
    `lax`, because arrival is a cross-site navigation from gateway sign-in — then
    redirects into the portal. The shell it serves is `no-store`: it names the build to load, and a
    cached copy would go on naming assets a later deploy no longer holds."""
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
    assert shell.text == SIDEBAR_FILE.read_text()
    assert shell.headers["cache-control"] == "no-store"
    client.cookies.clear()
    tokenless = await client.post("/surface/web", data={})
    assert tokenless.status_code == 401
    assert "Domain" not in cookie


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_failed_asset_publish_fails_the_page_and_the_next_page_retries(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The publish is loud: a store that refuses the write fails the page that awaited it, and the
    next page runs the publish again — a pod never serves a reference nothing can answer."""
    client, workspace_id, _agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    monkeypatch.setattr(web_surface, "_ASSET_PUBLISH", None)
    token = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    real_put = FilesystemBlobStore.put
    real_exists = FilesystemBlobStore.exists

    async def refuse(self: FilesystemBlobStore, key: str, data: bytes) -> None:
        raise RuntimeError("store refused the write")

    async def absent(self: FilesystemBlobStore, key: str) -> bool:
        return False

    monkeypatch.setattr(FilesystemBlobStore, "put", refuse)
    monkeypatch.setattr(FilesystemBlobStore, "exists", absent)
    with pytest.raises(RuntimeError, match="store refused the write"):
        await client.get("/surface/web", headers=headers)

    monkeypatch.setattr(FilesystemBlobStore, "put", real_put)
    monkeypatch.setattr(FilesystemBlobStore, "exists", real_exists)
    page = await client.get("/surface/web", headers=headers)
    assert page.status_code == 200
    for name in web_surface.STATIC_ASSETS:
        assert await FleetBlobStore(backend=blob.backend).exists("static/web/" + name)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_clicked_conversation_survives_the_sign_in_it_lands_in(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `chat on web` click by a signed-out member keeps its target: the conversation names itself
    in the query (a fragment would never reach the server), so it rides the redirect to the
    sign-in page and its automatic POST redirects onto the same conversation. The
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_unseated_members_live_session_cannot_read_the_portal(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "removed@example.com", admin=True)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == member_id)
            .values(seated_at=None, updated_at=sa.func.now())
        )

    refused = await client.get(
        "/surface/web/api/agents",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert refused.status_code == 403
    assert refused.text == "workspace access was removed"
    assert refused.headers[SESSION_FAULT_HEADER] == NO_SEAT_FAULT


async def _seed_web_turn(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    terminal: TerminalFrame,
    title: str = "a seeded conversation",
    context: TurnContext | None = None,
    audience: str | None = None,
    surface: str = "web",
) -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=f"{agent_id}/{email}/{uuid4().hex}",
                member_id=None if audience is not None else member_id,
                title=title,
                **({"audience": audience} if audience is not None else {}),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ext_store).values(
                workspace_id=workspace_id,
                extension="web",
                key=f"chat/{conversation_id}",
                value={"agent_id": str(agent_id), "email": email},
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
                admission_source="member",
                speaker_member_id=member_id,
                context=None if context is None else context.model_dump(mode="json"),
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _rail_rows(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, object]]:
    listing = await client.get(
        "/surface/web/objects/conversation?order_by=last_at&order=desc", headers=headers
    )
    assert listing.status_code == 200
    rows = listing.json()["objects"]
    assert isinstance(rows, list)
    return rows


async def _seed_subagent(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    parent_turn_id: UUID,
    terminal_text: str = "{}",
    profile: str = "general_purpose",
    name: str = "",
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
                subagent_name=name or None,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert answers[0].idempotency_key == _answer_key(conversation_id, asked_turn, 0)
    assert answers[1].idempotency_key == _answer_key(conversation_id, asked_turn, 1)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_prompts_stream_pending_and_fulfill_privately(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The web leg of the private credential handoff: the stream names only the prompts still
    awaiting values, a posted value lands through the sealed fulfillment without admitting a turn
    or touching a transcript, and the seal's member gate refuses anyone but the requester."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    state = CredentialRequestState(
        workspace_id=workspace_id,
        member_id=member_id,
        slots=("acme_api_key", "acme_signing_key"),
    )
    sealed = CREDENTIAL_FERNET.encrypt_at_time(
        state.model_dump_json().encode(),
        int(datetime.now(UTC).timestamp()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
    ).decode()
    request = CredentialRequest(
        reason="the acme connector needs its keys",
        prompts=(
            CredentialPrompt(slot="acme_api_key", prompt="Acme API key"),
            CredentialPrompt(slot="acme_signing_key", prompt="Acme signing key"),
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
    assert [p["slot"] for p in events["credentials"]["prompts"]] == [
        "acme_api_key",
        "acme_signing_key",
    ]
    renewed = events["credentials"]["sealed"]
    assert renewed != sealed
    _member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    hijack = await client.post(
        "/surface/web/credentials",
        data={"sealed": renewed, "slot": "acme_api_key", "value": "stolen"},
        headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
    )
    assert hijack.status_code == 403
    stored = await client.post(
        "/surface/web/credentials",
        data={"sealed": renewed, "slot": "acme_api_key", "value": "s3cr3t"},
        headers=cookie,
    )
    assert stored.status_code == 200
    assert stored.json() == {"stored": "acme_api_key"}
    assert (
        await CredentialStore(fernet=CREDENTIAL_FERNET).get(workspace_id, "acme_api_key")
        == "s3cr3t"
    )
    events = dict(await _collect_events(client, token, turn_id))
    assert [p["slot"] for p in events["credentials"]["prompts"]] == ["acme_signing_key"]
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert [p["slot"] for p in loaded.json()["credentials"]["prompts"]] == ["acme_signing_key"]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert turns == 1


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_transcript_reload_draws_the_standing_connect_without_minting(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The reload draws the same connect control the live stream drew, at the same memoized URL —
    the reply told the member to press it, and the consent window's own focus round-trip is what
    re-reads the transcript. A deploy without a connect flow serves the transcript without it."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    flow = ConnectFlow(
        providers={"github": ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(
            status="done",
            text="Use the connection control.",
            connect_request=ConnectRequest(provider="github", requester_member_id=member_id),
        ),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nconnect github",
                ),
                Message(role="assistant", content="Use the connection control."),
            ),
        ),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    try:
        streamed = dict(await _collect_events(client, token, turn_id))
        loaded = await client.get(
            f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
            headers=cookie,
        )
    finally:
        install_connect_flow(None)
    assert loaded.status_code == 200
    control = _drawn_connect(loaded)
    assert control == {"provider": "github", "label": "GitHub", "turn": str(turn_id)}
    # The stream and the reload draw one control, and neither mints: a read answers a read.
    assert streamed["connect"] == control
    assert "oauth.example.test" not in loaded.text
    unbrokered = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    assert unbrokered.status_code == 200
    assert _drawn_connect(unbrokered) is None


def _drawn_connect(response: Response) -> dict[str, object] | None:
    """The connect control the transcript draws, off the reply that asked for it."""
    for message in response.json()["messages"]:
        if message["role"] == "assistant" and "connect" in message:
            return message["connect"]
    return None


FRAMED_PAGE_ORIGIN = "https://siwnfzm3trn3jahsxigamfhuh56n74adgc6q.sites.example/"
"""The origin an app page is framed on: a site label of its own, never the app host. A picture link
is read from here in `test_chat_pictures_resolve_from_a_page_framed_on_its_own_origin`."""


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_card_is_drawn_only_for_an_application_the_reader_may_open(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """An application is private by default, and a conversation another member may read can name
    one they may not open. The card is gated by the reader's own audience, so it never states the
    name, model or mark of an application the portal would refuse to show them."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    owner_id, _owner_token = await _seed_member(workspace_id, "owner@example.com", admin=False)
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com", admin=False)
    private_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=private_id,
                workspace_id=workspace_id,
                name="payroll-inbox",
                prompt="work the payroll inbox",
                model="claude-sonnet-5",
                icon="receipt",
                visibility="private",
                owner_member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id, _turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        owner_id,
        "owner@example.com",
        TerminalFrame(
            status="done",
            text="payroll-inbox is set up.",
            created=(ObjectRef(kind="agent", name="payroll-inbox"),),
        ),
        audience=str(SHARED_AUDIENCE),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="build me one"),
                Message(role="assistant", content="payroll-inbox is set up."),
            ),
        ),
    )
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert loaded.status_code == 200
    assert str(private_id) not in loaded.text
    assert all("apps" not in message for message in loaded.json()["messages"])


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_transcript_reply_keeps_its_files_after_a_later_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """Files ride the reply that shared them, so a follow-up turn takes nothing off an earlier
    reply and the payload carries no conversation-level file list."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, first_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="here is the portrait"),
    )
    second_turn = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=second_turn,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=2,
                status="done",
                inbound="thanks",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", text="anything else?").model_dump(
                    mode="json"
                ),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=first_turn,
                blob_key=f"artifacts/{uuid4()}/portrait.jpg",
                workspace_id=workspace_id,
                filename="portrait.jpg",
                subject="the portrait",
                media_type="image/jpeg",
                size_bytes=5,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=2,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {first_turn}\n</context>\nfind a headshot",
                ),
                Message(role="assistant", content="here is the portrait"),
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {second_turn}\n</context>\nthanks",
                ),
                Message(role="assistant", content="anything else?"),
            ),
        ),
    )
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    payload = loaded.json()
    assert "files" not in payload
    replies = [message for message in payload["messages"] if message["role"] == "assistant"]
    (file,) = replies[0]["files"]
    assert file["filename"] == "portrait.jpg"
    assert file["url"].startswith("https://web/artifacts/")
    assert file["preview_url"].startswith("https://web/artifacts/")
    assert "files" not in replies[1]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


def test_a_members_bubble_carries_what_they_attached_rather_than_the_note() -> None:
    """The note admission writes at the foot of a member's words says where their files landed; the
    bubble states the files themselves, so the words read as the words they typed. A raster carries
    the link the attachment route serves its picture from and every other type carries none."""
    agent_id, conversation_id = uuid4(), uuid4()
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=(
                    "<context>source: web</context>\nwhat are these\n\n"
                    "[Attached files, saved in the workspace: web-inbox/lights.gif, "
                    "web-inbox/paper.pdf]"
                ),
            ),
            Message(role="assistant", content="a lamp and a paper"),
        ),
        attach=lambda path: web_surface._attachment_preview(
            "https://web", agent_id, conversation_id, path
        ),
    )
    assert rendered[0] == {
        "role": "user",
        "text": "what are these",
        "files": [
            {
                "filename": "lights.gif",
                "url": None,
                "media_type": "image/gif",
                "preview_url": (
                    f"https://web/surface/web/agents/{agent_id}/conversations/{conversation_id}"
                    "/attachments/web-inbox/lights.gif"
                ),
            },
            {
                "filename": "paper.pdf",
                "url": None,
                "media_type": "application/pdf",
                "preview_url": None,
            },
        ],
    }
    assert rendered[1] == {"role": "assistant", "text": "a lamp and a paper"}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_attached_picture_is_drawn_from_the_conversations_own_workspace(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The member's own bubble names the attachment route for the picture it draws, and that route
    serves the workspace bytes inline once they prove to be the raster the filename declares — never
    as a download, and never for a path outside the inbox the composer wrote to."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    picture = _png()
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "what are these"},
        files=[
            ("file", ("lights.png", picture, "image/png")),
            ("file", ("paper.pdf", b"%PDF-1.7 not really", "application/pdf")),
        ],
        headers=cookie,
    )
    assert admitted.status_code == 200
    conversation_id = admitted.json()["conversation_id"]
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    (said,) = [row for row in loaded.json()["messages"] if row["role"] == "user"]
    assert said["text"] == "what are these"
    drawn, carded = said["files"]
    assert carded == {
        "filename": "paper.pdf",
        "url": None,
        "media_type": "application/pdf",
        "preview_url": None,
    }
    assert drawn["filename"] == "lights.png"
    served = await client.get(str(drawn["preview_url"]), headers=cookie)
    assert served.status_code == 200
    assert served.content == picture
    assert served.headers["content-type"] == "image/png"
    assert "content-disposition" not in served.headers
    assert served.headers["x-content-type-options"] == "nosniff"
    base = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/attachments"
    for refused in (
        f"{base}/web-inbox/paper.pdf",
        f"{base}/web-inbox/nothing.png",
        f"{base}/notes/lights.png",
        f"{base}/web-inbox/deeper/lights.png",
    ):
        assert (await client.get(refused, headers=cookie)).status_code == 404
    await _consume(client, token, admitted.json()["turn_id"])


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_attachment_answers_no_other_member(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A picture answers exactly where the message naming it answers: another member's chat is not
    theirs to read, so the attachment route refuses them as the transcript does."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "guest@example.com")
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "mine"},
        files=[("file", ("lights.png", _png(), "image/png"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    conversation_id = admitted.json()["conversation_id"]
    refused = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}"
        "/attachments/web-inbox/lights.png",
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert refused.status_code == 404
    await _consume(client, token, admitted.json()["turn_id"])


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_preview_answers_a_batch_of_pages_from_the_page_asked_for(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composer draws a document as several pages, so the route renders a batch at a time and
    says how long the whole file is — the member asks for the next batch by naming the page it
    starts at."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    asked: dict[str, object] = {}

    async def render(
        self: SurfaceContext, kind: str, data: bytes, start_page: int = 1, pages: int = 1
    ) -> PreviewRender:
        asked.update(kind=kind, start_page=start_page, pages=pages)
        return PreviewRender(
            pages=(b"\x89PNG-nine", b"\x89PNG-ten"), start_page=start_page, page_count=10
        )

    monkeypatch.setattr(SurfaceContext, "render_preview", render)
    answered = await client.post(
        "/surface/web/preview",
        data={"start_page": "9"},
        files=[("file", ("report.pdf", b"%PDF-1.7", "application/pdf"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["start_page"] == 9
    assert body["page_count"] == 10
    assert [base64.b64decode(page) for page in body["pages"]] == [
        b"\x89PNG-nine",
        b"\x89PNG-ten",
    ]
    assert asked == {"kind": "pdf", "start_page": 9, "pages": web_surface.PREVIEW_PAGE_BATCH}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_preview_renders_the_pages_the_caller_draws_within_the_batch(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composer's card draws one cover page and asks for one, so a picked file costs one
    rasterize rather than a batch it drops. The count is the caller's to name and the route's to
    bound: a form asking past the batch is held to it, and one naming nothing gets the batch."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    asked: list[int] = []

    async def render(
        self: SurfaceContext, kind: str, data: bytes, start_page: int = 1, pages: int = 1
    ) -> PreviewRender:
        asked.append(pages)
        return PreviewRender(pages=(b"\x89PNG-one",), start_page=start_page, page_count=12)

    monkeypatch.setattr(SurfaceContext, "render_preview", render)
    for named in ("1", str(web_surface.PREVIEW_PAGE_BATCH + 40), "0", "all of them"):
        answered = await client.post(
            "/surface/web/preview",
            data={"pages": named},
            files=[("file", ("report.pdf", b"%PDF-1.7", "application/pdf"))],
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert answered.status_code == 200
        assert [base64.b64decode(page) for page in answered.json()["pages"]] == [b"\x89PNG-one"]
    batch = web_surface.PREVIEW_PAGE_BATCH
    assert asked == [1, batch, 1, batch]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_preview_refuses_an_unpreviewable_type(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    refused = await client.post(
        "/surface/web/preview",
        files=[("file", ("archive.zip", b"PK\x03\x04", "application/zip"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 415


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_preview_without_a_session_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, _workspace_id, _agent_id = web
    unauth = await client.post(
        "/surface/web/preview",
        files=[("file", ("report.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    assert unauth.status_code in (401, 403, 404)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_upload_start_on_a_filesystem_store_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A dev deploy runs the filesystem blob store, which mints no presigned URLs — dev
    attachments stream through the composer body under its own framings, so the route says so
    rather than pretending to serve what it cannot."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    refused = await client.post(
        "/surface/web/uploads",
        json={"name": "big.bin", "size_bytes": 1024, "sha256": "x" * 44},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 409


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_upload_start_refuses_a_malformed_body_and_no_session(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    malformed = await client.post(
        "/surface/web/uploads",
        json={"size_bytes": "not a number", "sha256": "x" * 44},
        headers=cookie,
    )
    assert malformed.status_code == 400
    unmeasured = await client.post(
        "/surface/web/uploads",
        json={"name": "big.bin", "size_bytes": 1024},
        headers=cookie,
    )
    assert unmeasured.status_code == 400
    unauth = await client.post(
        "/surface/web/uploads", json={"name": "f", "size_bytes": 1, "sha256": "x" * 44}
    )
    assert unauth.status_code in (401, 403, 404)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_upload_start_refuses_a_file_the_workspace_write_cannot_take(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The send writes the stored bytes into the conversation's workspace, and that write is capped.
    A URL minted past the cap would take the member's whole upload and then refuse the message it
    was for, leaving the object orphaned — so the size is refused before anything is signed."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    refused = await client.post(
        "/surface/web/uploads",
        json={
            "name": "huge.bin",
            "size_bytes": WORKSPACE_WRITE_MAX_BYTES + 1,
            "sha256": "x" * 44,
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_send_refuses_an_uploaded_key_outside_the_upload_prefix(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The send names which stored object it attaches, so a key that escapes the upload namespace —
    a conversation's transcript, an artifact the member's audience hides — is refused whatever the
    store holds under it, and the turn is never admitted."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    for key in (
        f"conversations/{uuid4()}/messages.json.lz4",
        "web-inbox-uploads/../conversations/elsewhere/messages.json.lz4",
        "web-inbox/file.bin",
    ):
        refused = await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation=new",
            data={"message": "read this", "uploaded_key": key},
            files=[("file", ("notes.txt", b"hello", "text/plain"))],
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert refused.status_code == 403


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_send_naming_an_upload_the_store_never_took_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A key of the right shape whose bytes never landed carries nothing to deliver, so the send
    says so instead of opening a turn the delivery would fail behind."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "read this", "uploaded_key": f"web-inbox-uploads/{uuid4()}/absent.bin"},
        files=[("file", ("notes.txt", b"hello", "text/plain"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 404


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_send_carrying_more_attachments_than_the_cap_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A key costs the body nothing, so one request could name stored objects without end and read
    the store into a single workspace. The count is what bounds a send now that the framing bounds
    the text alone, and inline files and keys count together against it."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    keys = [f"web-inbox-uploads/{uuid4()}/report.pdf" for _ in range(MAX_INBOUND_FILES)]
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "read these", "uploaded_key": keys},
        files=[("file", ("notes.txt", b"hello", "text/plain"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_presigned_files_land_in_the_workspace_beside_the_inline_ones(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, WorkspaceBlobStore, ConversationSandbox],
) -> None:
    """An attachment the browser PUT to the blob store travels as its key: the send streams those
    bytes into the conversation's `web-inbox/` under the member's own filename, beside any file the
    body still carried inline, and the admitted message names both paths."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    key = f"web-inbox-uploads/{uuid4()}/report.pdf"
    with ws(workspace_id):
        await blob.put(key, b"%PDF-1.7 stored")
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        data={"message": "read these", "uploaded_key": key},
        files=[("file", ("notes.txt", b"hello", "text/plain"))],
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
    assert "web-inbox/notes.txt" in row.inbound
    assert "web-inbox/report.pdf" in row.inbound
    inbox = sandboxes.workspace_root / str(row.conversation_id) / "web-inbox"
    assert (inbox / "notes.txt").read_bytes() == b"hello"
    assert (inbox / "report.pdf").read_bytes() == b"%PDF-1.7 stored"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.credential)
                .where(tables.credential.c.slot == "api_key")
            )
        ).scalar_one()
    assert stored == 0


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
                sa.select(scheduled_task).where(
                    scheduled_task.c.workspace_id == workspace_id,
                    scheduled_task.c.name == name,
                )
            )
        ).one_or_none()


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert await _schedule_store().claim_due(due_at, 300) == ()

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
        [claimed] = await _schedule_store().claim_due(due_at, 300)
        assert claimed.name == "daily-brief"

    removed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "scheduled_task", "name": "daily-brief"},
        headers=cookie,
    )
    assert removed.json()["applied"] is True
    assert await _task_row(workspace_id, "daily-brief") is None


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_oversized_intent_returns_a_portal_refusal(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        content="é".encode() * (web_panels.INTENT_MAX_BYTES // 2 + 1),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413
    assert refused.json() == {
        "applied": False,
        "message": f"Intent exceeds {web_panels.INTENT_MAX_BYTES} bytes.",
    }
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    detail = await client.get(
        f"/surface/web/objects/skill/release-notes?agent={agent_id}", headers=cookie
    )
    assert detail.status_code == 200
    generation = detail.json()["generation"]
    assert generation
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
            "generation": generation,
        },
        headers=cookie,
    )
    assert replaced.json()["applied"] is True
    assert (await listed())["release-notes"]["description"] == "Terse and dated."
    stale = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "release-notes",
            "spec": {
                "files": {"SKILL.md": SKILL_MD.replace("How release notes read.", "Lost race.")}
            },
            "generation": generation,
        },
        headers=cookie,
    )
    assert stale.json()["applied"] is False
    assert "changed after it was read" in stale.json()["message"]
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_read_answers_none_without_a_binding(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "home-none@example.com")
    read = await client.get(
        f"/surface/web/agents/{agent_id}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert read.status_code == 200
    assert read.json() == {"state": "none"}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_read_carries_the_bound_site(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "home@example.com")
    conversation_id = uuid4()
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "home",
            8000,
            member_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert await sites.set_homepage(agent_id, conversation_id, "home") is not None
    read = await client.get(
        f"/surface/web/agents/{agent_id}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert read.status_code == 200
    payload = read.json()
    assert payload["state"] == "set"
    assert payload["url"].startswith("https://web/surface/sites/")
    assert set(payload) == {"state", "url", "deploy_generation"}
    assert payload["deploy_generation"] > 0
    address = site_address(payload["url"].rpartition("/")[2])
    assert address is not None and address.portal_embed


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_read_hands_over_a_bound_page_whatever_the_agent_is_doing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A homepage that exists is served while its agent works. The read cannot see a rebuild — one
    asked for in a chat runs in that chat and registers its own site row — so treating a running
    turn as a rebuild would blank the page for every unrelated thing the member says to the app."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "home-bound@example.com")
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=EXTENSION_WEB,
                queue_key=conversation_id.hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "home",
            8000,
            member_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert await sites.set_homepage(agent_id, conversation_id, "home") is not None
        ctx = context_for(EXTENSION_WEB, frozenset(), member_context_read=True)
        await ctx.store.put(f"{web_surface.HOMEPAGE_SEED_PREFIX}{agent_id}", str(member_id))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="what is your name?",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    read = await client.get(
        f"/surface/web/agents/{agent_id}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert read.status_code == 200
    assert read.json()["state"] == "set"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_read_answers_none_for_an_agent_with_no_page(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An agent with no forked page and no shipped bundle has no homepage: the read answers absent,
    and the pane draws the app's conversation until a page arrives on its own."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "home-none@example.com")
    read = await client.get(
        f"/surface/web/agents/{agent_id}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert read.status_code == 200
    assert read.json() == {"state": "none"}


async def test_homepage_read_follows_the_agents_visibility(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A workspace-visible agent's homepage answers every member even while the bound row's own
    column says private; a private agent's answers its owner and an admin, and a member the agent
    reaches only by grant gets the absent state — the same set the frame would admit."""
    client, workspace_id, agent_id = web
    creator_id, creator_token = await _seed_member(workspace_id, "home-creator@example.com")
    _member_id, member_token = await _seed_member(workspace_id, "home-member@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "home-admin@example.com", admin=True)
    owner_id, owner_token = await _seed_member(workspace_id, "home-owner@example.com")
    conversation_id = uuid4()
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "own",
            8000,
            creator_id,
            "private",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert await sites.set_homepage(agent_id, conversation_id, "own") is not None
    for token in (creator_token, member_token, admin_token):
        opened = await client.get(
            f"/surface/web/agents/{agent_id}/homepage",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert opened.json()["state"] == "set"

    private_agent = uuid4()
    _granted_id, granted_token = await _seed_member(workspace_id, "home-granted@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=private_agent,
                workspace_id=workspace_id,
                name="private-host",
                prompt="be narrow",
                model="claude-opus-4-8",
                owner_member_id=owner_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _grant_web_access(workspace_id, private_agent, "home-granted@example.com")
    private_conversation = uuid4()
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            private_conversation,
            "own",
            8001,
            owner_id,
            "private",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert await sites.set_homepage(private_agent, private_conversation, "own") is not None
    path = f"/surface/web/agents/{private_agent}/homepage"
    assert (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={owner_token}"})).json()[
        "state"
    ] == "set"
    assert (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"})).json()[
        "state"
    ] == "set"
    assert (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={granted_token}"})
    ).json() == {"state": "none"}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_read_is_not_found_out_of_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="walled",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _outsider, token = await _seed_member(workspace_id, "home-outsider@example.com")
    denied = await client.get(
        f"/surface/web/agents/{walled_agent}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert denied.status_code == 404


@dataclass
class _SeedDbos:
    enqueued: list[str] = dataclass_field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _seed_app_agent(workspace_id: UUID, slug: str) -> UUID:
    """One app agent as an `app_<slug>` provision creates it — ownerless, workspace-visible, the
    full provenance the agent check requires."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=slug,
                prompt="the app",
                model="claude-opus-4-8",
                visibility="workspace",
                provisioned_by=f"app_{slug}",
                provisioned_name=slug,
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_seed_marks_an_app_agent_shipped_without_a_turn(db: None) -> None:
    """A shipped app agent needs no seed turn — its homepage is the deploy-wide bundle. The sweep
    marks it settled rather than skipping it unmarked, so the candidate query stops returning the
    workspace (an unmarked agent it never builds would keep it due forever), and no seed
    conversation is opened for it. The generic seed path still runs for the main agent."""
    workspace_id, main_agent = await _seed_workspace()
    await _seed_member(workspace_id, "seed-app-admin@example.com", admin=True)
    app_agent = await _seed_app_agent(workspace_id, "radar")
    dbos = _SeedDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    candidates = unseeded_agent_workspaces(EXTENSION_WEB, web_surface.HOMEPAGE_SEED_PREFIX)
    assert workspace_id in await candidates()
    with ws(workspace_id):
        ctx = context_for(EXTENSION_WEB, frozenset(), invoker=invoker, member_context_read=True)
        await web_surface.seed_homepages(ctx)
        markers = dict(await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX))
    assert markers[f"{web_surface.HOMEPAGE_SEED_PREFIX}{app_agent}"] == "shipped"
    assert workspace_id not in await candidates()
    async with workspace_tx() as connection:
        seeded = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.queue_key.startswith("homepage/")
                )
            )
        ).scalars()
    assert list(seeded) == [main_agent]


APP_NOTES = Manifest(
    name="app_notes",
    version="0.1.0",
    agents=(
        AgentProvision(
            name="notes",
            spec=AgentSpec(
                prompt="You are the Notes app for this workspace.",
                purpose="Keeps the workspace's notes.",
                model="auto",
                reasoning="medium",
                internet_access_allowed=False,
                visibility="workspace",
            ),
            icon="notes",
        ),
    ),
)
"""A sixth app extension the deploy installs and the frontend build knows nothing about — no
`apps/notes/` page is built for it. It is what tells the two faults of a missing page apart: this
one is installed, so its agent's homepage read is a broken deploy, while `app_wiki` is left out of
this mount even though the build ships a `wiki/` page, so its agent's read is a tear-out."""


@pytest.fixture
async def web_apps(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID, UUID, FleetBlobStore]]:
    """The web surface mounted with the app extensions in its deploy tier and one app agent seeded,
    so the homepage read resolves a row-less shipped page and the portal publish lands the built
    apps tree."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setattr(web_surface, "_ASSET_PUBLISH", None)
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id, _main = await _seed_workspace()
    app_agent = await _seed_app_agent(workspace_id, "radar")
    manifests = (
        web_manifest(),
        sites_manifest(),
        report_digest_manifest(),
        app_radar_manifest(),
        app_chat_manifest(),
        APP_NOTES,
    )
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.include_router(artifacts_router)
    _mount_shared_surfaces(
        app,
        manifests,
        CredentialStore(fernet=CREDENTIAL_FERNET),
        blob,
        sandboxes,
        hub,
        dbos_client,
        SECRET,
        "https://web",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        connectors=ConnectorRegistry(entries={}, resolver=CatalogResolver()),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        surface_model=lambda _name: SURFACE_MODEL[0],
        skills=skill_registry(manifests),
        member_skill_listing=lambda: member_skill_listing(
            (skill_create_manifest(),),
            CredentialStore(fernet=CREDENTIAL_FERNET),
            DefaultIndex(transaction=workspace_tx),
            StubEmbed(),
        ),
        objects=member_object_registry(
            manifests, public_base_url="https://web", artifact_token_secret=SECRET
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield client, workspace_id, app_agent, FleetBlobStore(backend=blob.backend)
    dbos_client.destroy()


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_private_apps_shipped_page_reaches_the_member_it_was_granted_to(
    web_apps: tuple[AsyncClient, UUID, UUID, "FleetBlobStore"],
) -> None:
    """A private app agent (the wiki app is one) reaches its owner, the workspace's admins, and the
    members a grant put in its web audience. The page is the app, so the read has to answer that
    same audience: a granted member reads `set` from the boot index and from the granular route.
    The shipped bundle is the deploy's own code and the frame gates it on nothing, so the read is
    handing out a link the frame opens. A member with no grant never reaches the route at all."""
    client, workspace_id, app_agent, _fleet = web_apps
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == app_agent)
            .values(visibility="private")
        )
    _member_id, token = await _seed_member(workspace_id, "granted-app@example.com")
    path = f"/surface/web/agents/{app_agent}/homepage"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    assert (await client.get(path, headers=cookie)).status_code == 404

    await _grant_web_access(workspace_id, app_agent, "granted-app@example.com")
    opened = await client.get(path, headers=cookie)
    assert opened.status_code == 200
    assert opened.json()["state"] == "set"
    boot = (await client.get("/surface/web/api/agents", headers=cookie)).json()
    homepages = {agent["app"]: agent["homepage"]["state"] for agent in boot["agents"]}
    assert homepages["radar"] == "set"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_seed_leaves_a_refused_agent_unmarked_and_retries(db: None) -> None:
    workspace_id, main_agent = await _seed_workspace()
    unseated = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=unseated,
                workspace_id=workspace_id,
                email="seed-unseated@example.com",
                is_admin=False,
                seated_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == main_agent)
            .values(owner_member_id=unseated)
        )
    dbos = _SeedDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        ctx = context_for(EXTENSION_WEB, frozenset(), invoker=invoker, member_context_read=True)
        await web_surface.seed_homepages(ctx)
        assert await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX) == ()
        async with workspace_tx() as connection:
            statuses = (await connection.execute(sa.select(tables.turn.c.status))).scalars()
            assert list(statuses) == ["cancelled"]
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.id == unseated)
                .values(seated_at=sa.func.now())
            )
        await web_surface.seed_homepages(ctx, bucket="retry")
        markers = await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX)
    assert [key for key, _ in markers] == [f"{web_surface.HOMEPAGE_SEED_PREFIX}{main_agent}"]
    async with workspace_tx() as connection:
        statuses = (await connection.execute(sa.select(tables.turn.c.status))).scalars()
        assert sorted(statuses) == ["cancelled", "queued"]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_homepage_seed_waits_for_an_admin_for_ownerless_agents(db: None) -> None:
    workspace_id, _agent_id = await _seed_workspace()
    await _seed_member(workspace_id, "seed-plain@example.com")
    dbos = _SeedDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        ctx = context_for(EXTENSION_WEB, frozenset(), invoker=invoker, member_context_read=True)
        await web_surface.seed_homepages(ctx)
        assert await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX) == ()
    assert dbos.enqueued == []
    candidates = unseeded_agent_workspaces(EXTENSION_WEB, web_surface.HOMEPAGE_SEED_PREFIX)
    assert workspace_id in await candidates()


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            await connection.execute(sa.select(tables.connection.c.shared))
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
        shared_now = (await connection.execute(sa.select(tables.connection.c.shared))).scalar_one()
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_connection_intent_disconnects_one_account_under_the_owner_gate(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The connect screen's per-account remove rides the intent lane against the `connection` kind,
    named by the account's stable object name: a member who neither owns the account nor
    administers the workspace is refused — the kind holds a connection private to its owner however
    the connection itself is shared, so the account is not even theirs to name — and the owner's
    remove ends that one connection and its grant while every other connected account stays."""
    client, workspace_id, agent_id = web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    await _seed_connection(workspace_id, agent_id, owner_id, "github", shared=True)
    await _seed_connection(workspace_id, agent_id, owner_id, "notion", shared=True)
    remove = {
        "verb": "delete",
        "kind": "connection",
        "name": account_object_name("github", "github-account"),
    }
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=remove,
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert "no connection object named" in refused.json()["message"]
    async with workspace_tx() as connection:
        held = (await connection.execute(sa.select(tables.connection.c.provider))).scalars().all()
    assert sorted(held) == ["github", "notion"]
    removed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=remove,
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert removed.status_code == 200
    assert removed.json()["applied"] is True
    async with workspace_tx() as connection:
        left = (await connection.execute(sa.select(tables.connection.c.provider))).scalars().all()
        grants_left = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert list(left) == ["notion"]
    assert grants_left == 1


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert connect_data == {
            "provider": "github",
            "label": "GitHub",
            "turn": outcome["turn_id"],
        }
        assert "oauth.example.test" not in streamed.text
        unknown = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "connect", "kind": "connection", "name": "nonesuch"},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert unknown.status_code == 200
        assert unknown.json()["applied"] is False
    finally:
        install_connect_flow(None)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_slack_step_mints_an_install_link_for_an_admin_and_no_one_else(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first run's Slack step end to end: the intent dispatches the `slack_connect` action on
    `surface/slack` on the member's own lane, and that action's own admin gate is the whole gate —
    a member is told who installs it and gets no link, the admin gets the deploy's Add to Slack URL
    sealed to this workspace. No message is spoken and no chat conversation exists to speak it in:
    the link comes back on the submit."""
    client, workspace_id, agent_id = web
    config, _hub, _blob, _sandboxes = dbos_runtime
    monkeypatch.setenv(SLACK_CLIENT_ID_ENV, "slack-client")
    monkeypatch.setenv(SLACK_CLIENT_SECRET_ENV, "slack-secret")
    monkeypatch.setattr(config.connect, "public_base_url", "https://web")
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    path = f"/surface/web/agents/{agent_id}/actions/surface/slack/slack_connect"
    connect: dict[str, object] = {}
    refused = await client.post(path, json=connect, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert refused.status_code == 200
    assert refused.json().get("url") is None, refused.json()
    assert "url" in refused.json(), refused.json()
    assert "admin" in refused.json()["message"]
    minted = await client.post(
        path, json=connect, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert minted.status_code == 200
    outcome = minted.json()
    assert outcome["applied"] is True
    assert outcome["message"] == ""
    assert outcome["url"].startswith(SLACK_OAUTH_AUTHORIZE_URL)
    assert "client_id=slack-client" in outcome["url"]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_delete_only_kind_admits_a_delete_and_refuses_an_apply(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A source trigger is created from its owning conversation, and this lane runs on the
    member's own intent conversation, so the portal can only ever end one. The lane refuses apply
    at validation, before a turn exists, and the object read the screen is drawn from carries that
    same answer — `deletes` without `applies` — so no control is offered that the lane would
    refuse."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "apply", "kind": "source_trigger", "name": "x", "spec": {"source": "y"}},
        headers=cookie,
    )
    assert refused.status_code == 400
    index = await client.get(
        f"/surface/web/objects/source_trigger?agent={agent_id}", headers=cookie
    )
    assert index.status_code == 200
    assert index.json()["applies"] is False
    assert index.json()["deletes"] is True


def _credential_request(slot: str, prompt: str) -> dict[str, object]:
    """The body the credentials listing authors for its Set/Replace act from the slot it read: the
    `request_credentials` action's own input, and nothing about where it lands — the route's path
    names the credential collection and the action."""
    return {
        "reason": "This value is stored encrypted and never shown again.",
        "prompts": [{"slot": slot, "prompt": prompt}],
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_credential_set_intent_mints_a_prompt_and_the_seal_stores_the_value(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Set/replace end to end with no pending chat request: the panel submits the credential
    collection's `request_credentials` action with the prompt it authored from the slot it listed,
    the turn's terminal frame carries the server-minted seal and its prompts, and the value crosses
    only in the sealed fulfillment — the intent outcome, the audit turn, and every response body
    stay secret-free."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    minted = await client.post(
        f"/surface/web/agents/{agent_id}/actions/credential/request_credentials",
        json=_credential_request("acme_api_key", "ACME API key"),
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        f"/surface/web/agents/{agent_id}/actions/credential/request_credentials",
        json=_credential_request("acme_api_key", "ACME API key"),
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_settings_projects_spec_schema_ceiling_and_admin_audience(
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
                routes_ingress=True,
                workspace_id=workspace_id,
                surface="slack",
                installation_id="T123",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    seen = await client.get(
        f"/surface/web/agents/{agent_id}/settings",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert seen.status_code == 200
    data = seen.json()
    assert data["agent"]["name"] == "assistant"
    assert data["agent"]["surfaces"] == ["slack"]
    assert data["agent"]["prompt"] == "be brief"
    assert len(data["agent"]["prompt_digest"]) > 8
    assert data["agent"]["updated_at"].endswith("+00:00")
    assert "setup" not in data["agent"]
    assert data["deploy"]["sandbox_internet"] is False
    assert data["models"] == ["auto", "claude-opus-4-8", "claude-sonnet-5"]
    assert data["spec"] == {
        "model": "claude-opus-4-8",
        "internet_access_allowed": True,
        "use_workspace_skills": True,
        "reasoning": "high",
        "visibility": "workspace",
        "icon": "compass",
        "input_schema": None,
        "output_schema": None,
    }
    assert set(data["spec_schema"]["properties"]) == {
        "model",
        "internet_access_allowed",
        "use_workspace_skills",
        "reasoning",
        "visibility",
    }
    skills_field = data["spec_schema"]["properties"]["use_workspace_skills"]
    assert skills_field["title"] == "Use workspace skills"
    assert data["audience"] == []
    granted_view = await client.get(
        f"/surface/web/agents/{second_agent}/settings",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert granted_view.json()["audience"] == ["member@example.com"]
    member_view = await client.get(
        f"/surface/web/agents/{second_agent}/settings",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert member_view.status_code == 200
    assert member_view.json()["audience"] is None
    ungranted_main = await client.get(
        f"/surface/web/agents/{agent_id}/settings",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert ungranted_main.status_code == 200
    assert ungranted_main.json()["agent"]["name"] == "assistant"
    assert ungranted_main.json()["audience"] is None
    stranger = await client.get(
        f"/surface/web/agents/{uuid4()}/settings",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert stranger.status_code == 404


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_admin_updates_agent_prompt_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(sandbox_size="large")
            .where(tables.agent.c.id == agent_id)
        )
    prompt = "検" * 11_000
    intent = {
        "verb": "apply",
        "kind": "agent",
        "name": "assistant",
        "spec": {"prompt": prompt},
    }
    changed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=intent,
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.internet_access_allowed,
                    tables.agent.c.reasoning,
                    tables.agent.c.sandbox_size,
                ).where(tables.agent.c.id == agent_id)
            )
        ).one()
        proposal_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.proposal))
        ).scalar_one()
        turn = (
            await connection.execute(
                sa.select(tables.turn.c.admission_source, tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(changed.json()["turn_id"])
                )
            )
        ).one()
    assert tuple(row) == (
        prompt,
        "claude-opus-4-8",
        True,
        "high",
        "large",
    )
    assert proposal_count == 0
    assert turn.admission_source == "intent"
    assert json.loads(turn.inbound)["tool"] == "object_apply"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_repicks_the_agent_icon_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The icon picker submits the icon alone. The fields `AgentSpec` requires are merged in from
    the agent, so the pick applies and every setting the member was not shown survives it."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(sandbox_size="large", icon="compass", prompt="be useful")
            .where(tables.agent.c.id == agent_id)
        )
    picked = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "assistant",
            "spec": {"icon": "chart-line"},
        },
        headers=headers,
    )
    assert picked.status_code == 200
    assert picked.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.agent.c.icon,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.internet_access_allowed,
                    tables.agent.c.reasoning,
                    tables.agent.c.sandbox_size,
                ).where(tables.agent.c.id == agent_id)
            )
        ).one()
    assert tuple(row) == ("chart-line", "be useful", "claude-opus-4-8", True, "high", "large")


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_icon_no_mark_could_be_named_by_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "assistant",
            "spec": {"icon": "Unicorn"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    async with workspace_tx() as connection:
        icon = (
            await connection.execute(
                sa.select(tables.agent.c.icon).where(tables.agent.c.id == agent_id)
            )
        ).scalar_one()
    assert icon != "unicorn"


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_sandbox_size_refuses_where_the_deploy_offers_none(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The field is hidden from every form on a single-shape deploy, so a spec naming a size here
    is a hand-built intent — refused before a turn exists, the same fence the model check holds."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "assistant",
            "spec": {
                "model": "claude-opus-4-8",
                "internet_access_allowed": True,
                "reasoning": "high",
                "sandbox_size": "large",
            },
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json() == {
        "applied": False,
        "message": "This deploy does not offer sandbox sizes.",
    }
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


ARMED_CRON = "0 9 * * 1-5"

SHIPPED_SETUP = AgentSetup(
    connectors=("acme",),
    credentials=(
        SetupCredential(label="ACME install", slots=("acme_install_seal", "acme_api_key")),
    ),
    standing=(SCHEDULE_KIND,),
    schedule=SetupSchedule(
        name="acme-sweep",
        prompt="Sweep what arrived and report it.",
        cadences=(SetupCadence(), SetupCadence(hour=9, weekdays=(1, 2, 3, 4, 5))),
    ),
    instructions="Connect the ACME account.",
)
"""One shipped app's whole declaration: an account its member grants, a credential an admin fills
once for the workspace, and the standing order that gives it an occasion to run."""


async def _seed_account(
    workspace_id: UUID,
    agent_id: UUID,
    owner_member_id: UUID,
    provider: str,
    shared: bool = True,
) -> UUID:
    """One connection of a provider, held by its owner and granted to nobody. `shared` is whether
    the workspace may work from it or only the member who made it."""
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
                account_id=f"{provider}-{connection_id.hex[:8]}",
                host="api.example.test",
                owner_member_id=owner_member_id,
                conversation_id=conversation_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return connection_id


async def _grant_account(workspace_id: UUID, agent_id: UUID, connection_id: UUID) -> None:
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.connection.c.conversation_id).where(
                    tables.connection.c.id == connection_id
                )
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _fill_slot(workspace_id: UUID, slot: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=slot,
                ciphertext=CREDENTIAL_FERNET.encrypt(b"filled"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _declare_setup(agent_id: UUID, setup: AgentSetup = SHIPPED_SETUP) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == agent_id)
            .values(setup=setup.model_dump(mode="json"))
        )


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_credential_an_app_cannot_work_without_says_so(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An install is optional by default because an admin settles it and the app answers a member
    thinner in the meantime. An app that cannot read anything without one declares it, and the read
    carries that so the screen marks the row rather than deciding the rule for itself."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "reader@example.com")
    await _declare_setup(
        agent_id,
        AgentSetup(
            credentials=(
                SetupCredential(label="ACME key", slots=("acme_api_key",), required=True),
                SetupCredential(label="ACME install", slots=("acme_install_seal",)),
            )
        ),
    )
    state = (
        await client.get(
            f"/surface/web/agents/{agent_id}/setup",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    ).json()
    assert [(row["label"], row["required"]) for row in state["credentials"]] == [
        ("ACME key", True),
        ("ACME install", False),
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_private_account_is_connected_only_for_the_member_who_made_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A grant over a private connection is usable by its owner and by nobody else. A read that
    counted every grant told the second member their app was connected and then refused every call
    it made — the worst of both, and with nothing on the screen to press about it."""
    client, workspace_id, agent_id = web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    await _declare_setup(agent_id)
    private_id = await _seed_account(workspace_id, agent_id, owner_id, "acme", shared=False)
    await _grant_account(workspace_id, agent_id, private_id)

    async def connectors(token: str) -> list[dict[str, object]]:
        read = await client.get(
            f"/surface/web/agents/{agent_id}/setup",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        return read.json()["connectors"]

    assert await connectors(owner_token) == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": True, "required": False}
    ]
    assert await connectors(other_token) == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": False, "required": False}
    ]

    # Shared is the workspace's own: every member reads the one account, because every member's
    # turns work from it.
    shared_id = await _seed_account(workspace_id, agent_id, owner_id, "acme", shared=True)
    await _grant_account(workspace_id, agent_id, shared_id)
    assert await connectors(other_token) == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": True, "required": False}
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_credential_is_filled_by_any_slot_that_answers_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A credential often has more than one way in — an app installation and a fine-grained token
    reach the same API — so the declaration names a set and any one of them settles it. An app
    naming only the first would report itself unready for a workspace that chose the second."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    assert (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()[
        "credentials"
    ] == [{"label": "ACME install", "filled": False, "required": False}]

    await _fill_slot(workspace_id, "acme_api_key")
    assert (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()[
        "credentials"
    ] == [{"label": "ACME install", "filled": True, "required": False}]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_credential_the_deploy_supplies_reads_as_filled(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot the deploy fills from its own environment is filled. `WorkspaceScope.credential` falls
    back to it where the workspace stored none, so the agent obtains the secret and works — and a
    read that asked the credential table alone answered a different question, reporting a working
    app unready for ever with a row stating a need the member had no act to settle."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    read = await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)
    assert [row["filled"] for row in read.json()["credentials"]] == [False]

    monkeypatch.setenv("UFO_ACME_API_KEY", "from-the-deploy")
    supplied = await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)
    assert [row["filled"] for row in supplied.json()["credentials"]] == [True]


async def _crowd_the_listing(agent_id: UUID, held: str, count: int) -> None:
    """`count` more tasks on the same agent, every name sorting ahead of the one it holds."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(scheduled_task).where(
                    scheduled_task.c.agent_id == agent_id, scheduled_task.c.name == held
                )
            )
        ).one()
        await connection.execute(
            sa.insert(scheduled_task),
            [
                {
                    **{
                        column: getattr(row, column)
                        for column in ("workspace_id", "conversation_id", "agent_id", "schedule")
                    },
                    "id": uuid4(),
                    "name": f"aaa-{index:03d}",
                    "prompt": row.prompt,
                    "description": row.description,
                    "next_run_at": row.next_run_at,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for index in range(count)
            ],
        )


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_setup_read_says_whether_this_workspace_has_its_own_page(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The band a page draws over an unwired app is a task list, and a task list that stays after
    the tasks are done is what makes a member read their own app as a setup screen. So a page the
    workspace has forked states a missing account in one line instead — which it can only do if the
    read says whose page this is, read the same way the homepage read reads it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    path = f"/surface/web/agents/{agent_id}/setup"
    assert (await client.get(path, headers=cookie)).json()["own_page"] is False

    conversation_id = await _seed_agent_conversation(
        workspace_id, agent_id, queue_key="fork", audience=str(SHARED_AUDIENCE), member_id=None
    )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "meetings-home",
            8100,
            member_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert await sites.set_homepage(agent_id, conversation_id, "meetings-home") is not None
    assert (await client.get(path, headers=cookie)).json()["own_page"] is True


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_only_required_setup_blocks_readiness(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Optional setup is an offer. Required setup blocks readiness."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    quiet = await _seed_app_agent(workspace_id, "radar")
    wired = await _seed_app_agent(workspace_id, "meetings")
    optional = await _seed_app_agent(workspace_id, "issues")
    blocked = await _seed_app_agent(workspace_id, "metrics")
    await _declare_setup(quiet, AgentSetup())
    await _declare_setup(wired, AgentSetup(connectors=("acme",), instructions="Connect ACME."))
    await _declare_setup(
        optional,
        AgentSetup(credentials=(SetupCredential(label="ACME key", slots=("acme_api_key",)),)),
    )
    await _declare_setup(
        blocked,
        AgentSetup(
            credentials=(
                SetupCredential(label="Metrics key", slots=("metrics_api_key",), required=True),
            )
        ),
    )

    async def due() -> dict[str, bool]:
        read = await client.get("/surface/web/api/agents", headers=cookie)
        return {agent["name"]: agent["setup_due"] for agent in read.json()["agents"]}

    assert await due() == {
        "assistant": False,
        "radar": False,
        "meetings": False,
        "issues": False,
        "metrics": True,
    }

    account = await _seed_account(workspace_id, wired, member_id, "acme")
    await _grant_account(workspace_id, wired, account)
    await _fill_slot(workspace_id, "metrics_api_key")
    assert await due() == {
        "assistant": False,
        "radar": False,
        "meetings": False,
        "issues": False,
        "metrics": False,
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_starters_offer_an_unconfigured_app_until_one_setup_offer_is_accepted(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    code = await _seed_app_agent(workspace_id, "code")
    await _declare_setup(code, AgentSetup(connectors=("github",)))
    account = await _seed_account(workspace_id, code, member_id, "github")
    slate = Slate(
        generated_at=datetime.now(UTC),
        prompt=SLATE_DIGEST,
        ranked=(
            RankedUnlock(
                unlock="pr-babysitter",
                title="PR watch",
                line="Keeps pull requests moving.",
                ask="Watch my pull requests.",
            ),
        ),
    )
    with ws(workspace_id):
        await web_extension().store.put(starters_key(member_id), slate.model_dump(mode="json"))

    offered = await client.get("/surface/web/workspace/starters", headers=cookie)
    assert offered.json()["starters"] == [
        {
            "kind": "app",
            "mark": "gnomon",
            "line": "Keeps pull requests moving.",
            "ask": DEFAULT_APP_SETUP_ASK,
            "agent_id": str(code),
            "providers": [],
        }
    ]

    await _grant_account(workspace_id, code, account)
    configured = await client.get("/surface/web/workspace/starters", headers=cookie)
    assert configured.json()["starters"] == []


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_intents_lane_refuses_a_verb_it_does_not_name(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The lane's own fence, under the bridge's. A verb no intent model names is malformed at the
    door rather than dispatched to whatever tool its name resembles."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "prober@example.com")
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "build_the_homepage"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    assert refused.json() == {"error": "malformed intent"}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_prepared_intent_lane_takes_no_member_message(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The one portal room a member may not speak in. An intent turn dispatches its one tool call
    and runs no model round, so it claims no arrivals — a message folded onto a live one is a
    message no round ever reads, and the member waits for a reply that is not coming. The lane is
    read like any other room; it is the chat POST that is refused."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "submitter@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    applied = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "note-taking",
            "spec": {"body": "Write it down.", "description": "How to write things down."},
        },
        headers=cookie,
    )
    assert applied.status_code == 200, applied.text
    async with workspace_tx() as connection:
        room = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.queue_key.like("intent/%"),
                )
            )
        ).scalar_one()
    read = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{room}/transcript", headers=cookie
    )
    assert read.status_code == 200, read.text
    spoke = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={room}",
        content=b"Actually, make it weekly.",
        headers=cookie,
    )
    assert spoke.status_code == 404


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_reads_the_room_the_sweep_opened_for_them(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A room carries no chat row — that row is the (agent, member) binding a chat is founded with,
    and the homepage room is opened by the sweep instead, on behalf of one member. So the durable
    audience is what says whose it is, and without reading it the member the page was built for
    opened their own room and met a 404 on it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "builder@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=f"homepage/{agent_id}/{member_id}",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    read = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers=cookie,
    )
    assert read.status_code == 200, read.text
    # And speaks in it: the room is where the app answers, so a member who wants a different page
    # says so where the build was asked for rather than opening a second conversation about it.
    spoke = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"Put the open pull requests at the top.",
        headers=cookie,
    )
    assert spoke.status_code == 200, spoke.text

    # Another member's room is still another member's, whatever its surface.
    _other_id, other_token = await _seed_member(workspace_id, "onlooker@example.com")
    other = {"cookie": f"{SESSION_COOKIE}={other_token}"}
    onlooking = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers=other,
    )
    assert onlooking.status_code == 404
    intruding = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"Mine now.",
        headers=other,
    )
    assert intruding.status_code == 404


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_sizes_offering_deploy_draws_the_sandbox_size_setting(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deploy whose carrier declares sizes offers the setting on the agent's own form: the update
    schema carries `sandbox_size` as its enum, the spec states the stored value, and the field stays
    optional — an agent born without one takes the default."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (web_manifest(),),
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
        surface_model=lambda _name: SURFACE_MODEL[0],
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
        sandbox_sizes=("small", "medium", "large"),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        settings = await client.get(
            f"/surface/web/agents/{agent_id}/settings",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    dbos_client.destroy()
    assert settings.status_code == 200
    data = settings.json()
    assert data["agent"]["archivable"] is False
    assert data["spec"]["sandbox_size"] == "small"
    assert data["spec_schema"]["properties"]["sandbox_size"]["enum"] == [
        "small",
        "medium",
        "large",
    ]
    assert data["spec_schema"]["properties"]["sandbox_size"]["title"] == "Sandbox Size"
    assert "sandbox_size" not in data["spec_schema"]["required"]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_oversized_intent_answers_413(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    body = dict(
        INTENT_BODY,
        spec={"model": "x" * (web_panels.INTENT_MAX_BYTES + 1), "internet_access_allowed": False},
    )
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=body,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_settings_reports_the_deploy_internet_ceiling_when_granted(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The True direction of the deploy ceiling: a manifest declaring sandbox_internet makes the
    settings read report the capability the agent setting narrows."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = replay_safe_client(config.database.system_url)
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
        surface_model=lambda _name: SURFACE_MODEL[0],
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        seen = await client.get(
            f"/surface/web/agents/{agent_id}/settings",
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_admin_creates_an_agent_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The administration view's create rides the same lane as every panel mutation: a create
    intent lands a fresh non-main row with exactly the submitted configuration, stamped with the
    submitting member as owner. A taken name refuses without changing its row; without a prompt
    the kind refuses and nothing is created."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    envelope = {
        "verb": "apply",
        "create_only": True,
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
        json={
            **envelope,
            "spec": {**envelope["spec"], "prompt": "replace the existing prompt"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert duplicate.json() == {
        "applied": False,
        "message": "an agent named 'research' already exists",
        "turn_id": duplicate.json()["turn_id"],
    }
    async with workspace_tx() as connection:
        prompt = (
            await connection.execute(
                sa.select(tables.agent.c.prompt).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == "research",
                )
            )
        ).scalar_one()
    assert prompt == "be curious"
    promptless = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "create_only": True,
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
    member_created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={**envelope, "name": "third"},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert member_created.json()["applied"] is True
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.owner_member_id).where(
                    tables.agent.c.workspace_id == workspace_id
                )
            )
        ).all()
    owners = {row.name: row.owner_member_id for row in rows}
    assert sorted(owners) == ["assistant", "research", "third"]
    assert owners["third"] == member_id
    owner_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "third" in [agent["name"] for agent in owner_view.json()["agents"]]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_member_seat_and_role_ride_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The roster's row acts are member-kind applies on the main agent's lane: role and access
    changes land exactly, and the kind's own guards answer — the last admin cannot be demoted, the
    last seated admin cannot be unseated, and a non-admin mutates nobody."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, _member_token = await _seed_member(workspace_id, "member@example.com")
    plain_id, plain_token = await _seed_member(workspace_id, "plain@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now())
            .where(tables.member.c.id.in_((admin_id, member_id, plain_id)))
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
        headers={"cookie": f"{SESSION_COOKIE}={plain_token}"},
    )
    assert outsider.json()["applied"] is False
    assert "admin" in outsider.json()["message"]
    roster = await client.get(
        "/surface/web/workspace/team", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert {
        (entry["email"], entry["admin"], entry["seated"]) for entry in roster.json()["members"]
    } == {
        ("admin@example.com", True, True),
        ("member@example.com", True, False),
        ("plain@example.com", False, True),
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_audience_intents_write_the_grant_store(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The member object's web-audience acts ride the target agent's own intent lane and land in
    the same store the chat verbs write: a grant makes the agent appear in the member's portal, a
    revoke removes it, and a non-admin changes nothing."""
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
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    granted = await client.post(
        f"/surface/web/agents/{second_agent}/actions/member/{member_id}/grant_web_access",
        json={},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert granted.status_code == 200
    assert granted.json()["applied"] is True, granted.json()
    listed = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" in [agent["name"] for agent in listed.json()["agents"]]
    revoked = await client.post(
        f"/surface/web/agents/{second_agent}/actions/member/{member_id}/revoke_web_access",
        json={},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert revoked.json()["applied"] is True
    relisted = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" not in [agent["name"] for agent in relisted.json()["agents"]]
    other_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    outsider = await client.post(
        f"/surface/web/agents/{second_agent}/actions/member/{other_id}/grant_web_access",
        json={},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.status_code == 404
    with ws(workspace_id):
        assert await web_extension().store.list(AUDIENCE_PREFIX) == ()


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
    idempotency_key: str | None = None,
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
                idempotency_key=idempotency_key,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.conversation)
            .where(
                tables.conversation.c.id == conversation_id,
                tables.conversation.c.title.is_(None),
            )
            .values(title=member_message_text(inbound).strip()[:CONVERSATION_TITLE_CHARS])
        )
    return turn_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_conversations_search_narrows_the_read_not_the_page(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`q` reaches the query the bound is applied to, so a conversation the member searches for is
    found by what it is called whether or not it would have stood on the unsearched page."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    wanted, _turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="hi"),
        title="Rename the deploy job",
    )
    await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="hi"),
        title="Order more coffee",
    )
    path = f"/surface/web/agents/{agent_id}/conversations"

    everything = await client.get(path, headers=cookie)
    matched = await client.get(path + "?q=DEPLOY", headers=cookie)
    unmatched = await client.get(path + "?q=nothing%20here", headers=cookie)

    assert len({row["id"] for row in everything.json()["conversations"]}) == 2
    assert [row["id"] for row in matched.json()["conversations"]] == [str(wanted)]
    assert unmatched.json()["conversations"] == []


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_conversations_read_says_it_stopped_at_its_bound(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read carries no cursor, so the screen drawing its rows can only say the rest are there
    if this answer says so. It is counted rather than guessed from a full page: a set exactly the
    size of the bound says nothing stands behind it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    for title in ("Rename the deploy job", "Order more coffee"):
        await _seed_web_turn(
            workspace_id,
            agent_id,
            member_id,
            "m@example.com",
            TerminalFrame(status="done", text="hi"),
            title=title,
        )
    path = f"/surface/web/agents/{agent_id}/conversations"

    monkeypatch.setattr(web_surface, "CONVERSATION_LIST_LIMIT", 1)
    bounded = (await client.get(path, headers=cookie)).json()
    monkeypatch.setattr(web_surface, "CONVERSATION_LIST_LIMIT", 2)
    exact = (await client.get(path, headers=cookie)).json()

    assert len(bounded["conversations"]) == 1
    assert bounded["more"] is True
    assert len(exact["conversations"]) == 2
    assert exact["more"] is False


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_unreadable_conversation_states_no_words_and_no_speakers(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A row an admin may list but not read stays administration metadata: whose it is and how
    busy, never a word of it, never where it was said, and never who else is in it. Opening it is
    the acknowledgement's act and that act is what gets audited, so a listing that quoted the first
    message — or handed over the link that opens it in Slack — would give away the content the
    acknowledgement exists to record."""
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
        context=TurnContext(sender="Robin Vale (owner@example.com)", source=SLACK_THREAD_PERMALINK),
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
    assert row["source"] is None
    assert row["speakers"] == []


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    await _write_transcript(
        blob,
        mine,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {parent}\n</context>\nresearch",
                ),
                Message(role="assistant", content="found it"),
            ),
        ),
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
                    "name": "",
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
            for page in ("", "/1"):
                denied = await client.get(
                    f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript"
                    + page,
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_earlier_messages_are_bounded_and_cursor_complete(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A large compaction window crosses in bounded newest-first pages whose cursors reconstruct
    the complete history once, whether the message-count or encoded-byte ceiling cuts a page."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    conversation_id, _turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="done"),
    )
    texts = [f"short {at}" for at in range(170)] + [f"long {at} " + "x" * 2_000 for at in range(80)]
    history = tuple(
        Message(role="user", content=f"<context>\nmessage_ref: bulk-{at}\n</context>\n{text}")
        for at, text in enumerate(texts)
    )
    summary = Message(role="user", content="Compacted context:\nthe bulk history")
    await _write_compaction(blob, conversation_id, 1, before=history, after=(summary,))
    await _write_transcript(blob, conversation_id, Conversation(seq=1, messages=(summary,)))

    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    path = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript"
    tail = await client.get(path, headers=headers)
    cursor = tail.json()["earlier_cursor"]
    pages: list[list[dict[str, object]]] = []
    page_lengths: list[int] = []
    seen: set[str] = set()
    while cursor:
        assert cursor not in seen
        seen.add(cursor)
        page = await client.get(path, headers=headers, params={"cursor": cursor})
        assert page.status_code == 200
        payload = page.json()
        assert len(payload["messages"]) <= web_surface.HISTORY_PAGE_MESSAGE_LIMIT
        assert len(page.content) <= web_surface.HISTORY_PAGE_BYTE_LIMIT
        pages.insert(0, payload["messages"])
        page_lengths.append(len(payload["messages"]))
        cursor = payload.get("earlier_cursor")

    assert [message for page in pages for message in page] == [
        {"role": "user", "text": text} for text in texts
    ]
    assert web_surface.HISTORY_PAGE_MESSAGE_LIMIT in page_lengths
    assert any(length < web_surface.HISTORY_PAGE_MESSAGE_LIMIT for length in page_lengths)


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_earlier_names_only_records_the_transcript_reflects(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A compaction record can exist without ever reaching the transcript: a turn that compacted
    and then ended non-done keeps the pre-compaction transcript, and the next compaction
    summarizes from that fuller window, shadowing the orphaned record. The tail then already
    holds everything such a record replaced, so the cursor names only the newest record the
    transcript opens with — nothing while the transcript is unreflective, and never a shadowed
    record from the page above it."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="first reply"),
    )
    second = await _seed_listed_turn(
        workspace_id, conversation_id, agent_id, seq=2, inbound="second ask"
    )
    history = (
        Message(role="user", content=f"<context>\nmessage_ref: {first}\n</context>\nfirst ask"),
        Message(role="assistant", content="first reply"),
        Message(role="user", content=f"<context>\nmessage_ref: {second}\n</context>\nsecond ask"),
        Message(role="assistant", content="second reply"),
    )
    orphaned = Message(role="user", content="Compacted context:\nnever landed")
    await _write_compaction(
        blob, conversation_id, 1, before=history[:2], after=(orphaned, *history[1:2])
    )
    await _write_transcript(blob, conversation_id, Conversation(seq=2, messages=history))

    path = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript"
    repaired = await client.get(path, headers=headers)
    assert repaired.status_code == 200
    assert "earlier_cursor" not in repaired.json()

    third = await _seed_listed_turn(
        workspace_id, conversation_id, agent_id, seq=3, inbound="third ask"
    )
    late = (
        Message(role="user", content=f"<context>\nmessage_ref: {third}\n</context>\nthird ask"),
        Message(role="assistant", content="third reply"),
    )
    summary = Message(role="user", content="Compacted context:\nthe fuller window, summarized")
    await _write_compaction(
        blob, conversation_id, 2, before=(*history, *late), after=(summary, *late)
    )
    await _write_transcript(blob, conversation_id, Conversation(seq=3, messages=(summary, *late)))

    compacted = await client.get(path, headers=headers)
    assert compacted.status_code == 200
    compacted_payload = compacted.json()
    assert compacted_payload["messages"] == [
        {"role": "user", "text": "third ask"},
        {"role": "assistant", "text": "third reply"},
    ]
    page = await client.get(
        path, headers=headers, params={"cursor": compacted_payload["earlier_cursor"]}
    )
    assert page.status_code == 200
    assert page.json() == {
        "messages": [
            {"role": "user", "text": "first ask"},
            {"role": "assistant", "text": "first reply"},
            {"role": "user", "text": "second ask"},
            {"role": "assistant", "text": "second reply"},
        ]
    }


async def _acknowledge(
    client: AsyncClient, agent_id: UUID, conversation_id: UUID, token: str
) -> Response:
    return await client.post(
        f"/surface/web/agents/{agent_id}/actions/conversation/{conversation_id}"
        "/read_private_transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
        json={},
    )


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    await _write_transcript(
        blob,
        theirs,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {parent_turn}\n</context>\nprivate question",
                ),
                Message(role="assistant", content="private answer"),
            ),
        ),
    )
    await _write_transcript(
        blob,
        child_conversation,
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
                        ToolResultBlock(
                            tool_use_id="private-call",
                            content="done",
                            activity=True,
                            activity_text="Listing the private files.",
                        ),
                    ),
                ),
            ),
        ),
    )
    unrelated = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="unrelated",
        audience="shared",
        member_id=None,
    )
    transcript_path = f"/surface/web/agents/{agent_id}/conversations/{theirs}/transcript"
    admin_cookie = {"cookie": f"{SESSION_COOKIE}={token_admin}"}

    blocked = await client.get(transcript_path, headers=admin_cookie)
    assert blocked.status_code == 404
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
                    "name": "",
                    "conversation_id": str(child_conversation),
                    "events": [
                        {"kind": "note", "text": "Reading the private file."},
                        {"kind": "activity", "text": "Listing the private files."},
                    ],
                    "output": "ok",
                    "subagents": [],
                }
            ],
        },
    ]
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
                manifest=None,
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
    assert admin.status_code == 200
    assert len(admin.json()["sites"]) == 100
    assert admin.json()["truncated"] is True
    assert all(site["name"].startswith("aaa-hidden-") for site in admin.json()["sites"])
    assert next(slot for slot in inventory.json()["slots"] if slot["id"] == "sites")["count"] == 2


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        task = await _schedule_store().create(
            conversation_id=conversation_id,
            name="daily-brief",
            schedule="0 9 * * *",
            prompt="Read private sources and send the brief.",
            description="Send the morning brief.",
            next_run_at=datetime(2026, 8, 8, 9, tzinfo=UTC),
            created_by_member_id=creator_id,
        )
        ownerless = await _schedule_store().create(
            conversation_id=conversation_id,
            name="system-cleanup",
            schedule="0 3 * * 0",
            prompt="Remove expired system records.",
            description="Clean expired system records.",
            next_run_at=datetime(2026, 8, 9, 3, tzinfo=UTC),
            created_by_member_id=None,
        )
        bounded = await _schedule_store().create(
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
            sa.update(scheduled_task)
            .where(scheduled_task.c.id.in_((task.id, ownerless.id)))
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
            sa.update(scheduled_task)
            .where(scheduled_task.c.id == bounded.id)
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
        await _schedule_store().create(
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_team_view_lists_the_roster_for_every_member(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace team view: every member reads the whole roster with each member's role, their
    access state, and the stable id the panel's acts apply to — the same rows the `member` kind
    lists to a member asking the main agent — while `can_manage` opens the add and the row acts for
    an admin alone. An unauthenticated read is refused."""
    client, workspace_id, _agent_id = web
    member_id, token_m = await _seed_member(workspace_id, "m@example.com")
    admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    unseated_id, _unseated_token = await _seed_member(workspace_id, "gone@example.com")
    path = "/surface/web/workspace/team"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == unseated_id).values(seated_at=None)
        )
    member_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    body = member_view.json()
    assert [(entry["email"], entry["admin"], entry["seated"]) for entry in body["members"]] == [
        ("boss@example.com", True, True),
        ("gone@example.com", False, False),
        ("m@example.com", False, True),
    ]
    assert [entry["id"] for entry in body["members"]] == [
        str(admin_id),
        str(unseated_id),
        str(member_id),
    ]
    assert body["can_manage"] is False

    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert admin_view.json()["can_manage"] is True
    assert [entry["email"] for entry in admin_view.json()["members"]] == [
        entry["email"] for entry in body["members"]
    ]

    anonymous = await client.get(path)
    assert anonymous.status_code == 401


def _add_member(email: str, *, admin: bool) -> dict[str, object]:
    """The team panel's add as the member collection's projected `add_member` view submits it: the
    action's own input; the route's path names the collection and the action."""
    return {"email": email, "admin": admin}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json=_add_member("New.Hire@example.com", admin=True),
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
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json=_add_member("sneak@example.com", admin=True),
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


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json=_add_member("contractor@other.test", admin=False),
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


RUN_FINDING = {
    "path": "core/x.py",
    "line": 42,
    "title": "Wedged turn",
    "trigger": "A cancel lands mid-dispatch",
    "failure": "The turn never commits a terminal",
    "impact": "production outage, deadlock, or permanently unfinished work",
}


def test_an_answer_arrives_whole_however_long_and_never_blank() -> None:
    """One answer, stated once wherever it is read: the surface sends every character it has, and
    how much of it stands on a screen is the fold's decision at the other end. Never blank — a
    review that found nothing answered, and a page saying nothing would state it never ran."""
    long_answer = json.dumps({"result": "word " * 900})
    findings = json.dumps({"findings": [RUN_FINDING]})

    assert _run_answer(long_answer) == ("word " * 900).strip()
    assert len(_run_answer(long_answer)) > len(long_answer) - 40

    assert _run_answer(findings).startswith("**Findings**\n**Path** — core/x.py")
    assert _run_answer(json.dumps({"findings": []})) == "**Findings** — none"
    assert _run_answer("{}") == ""


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nGive me the json.",
                ),
                Message(role="assistant", content=reply),
            ),
        ),
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


def test_a_bubble_reads_as_the_members_words_out_of_the_surface_fence() -> None:
    """A channel surface fences the member's words between the ambient digest and what their
    attachments delivered, and that fence is the prompt's wire: a bubble carrying it shows the
    reader bystanders' traffic and markup they never typed."""
    spoken = "44444444-4444-4444-4444-444444444444"
    marker = mint_marker()
    ambient = (
        f"<{AMBIENT_CONTEXT_ELEMENT}_{marker}>\n"
        "[18:09] @Robin Vale: Draft announcement tweets\n"
        f"</{AMBIENT_CONTEXT_ELEMENT}_{marker}>\n"
    )
    fenced = fence_member_message(
        marker, ambient, "give me three variants on this tweet storm", "poster.png: delivered"
    )
    rendered = _rendered_messages(
        (
            Message(role="user", content=f"<context>\nmessage_ref: {spoken}\n</context>\n{fenced}"),
            Message(role="assistant", content="Here are three."),
        ),
        None,
        frozenset({spoken}),
        frozenset(),
    )
    assert rendered == [
        {"role": "user", "text": "give me three variants on this tweet storm"},
        {"role": "assistant", "text": "Here are three."},
    ]


def test_a_bubble_names_its_speaker_exactly_where_the_read_names_one() -> None:
    """The read hands the projection who spoke each turn; a turn it names no speaker for keeps the
    bare bubble, so a viewer's own words carry no label and everyone else's carry theirs."""
    theirs = "55555555-5555-5555-5555-555555555555"
    mine = "66666666-6666-6666-6666-666666666666"
    rendered = _rendered_messages(
        (
            Message(role="user", content=f"<context>\nmessage_ref: {theirs}\n</context>\nship it"),
            Message(role="assistant", content="Shipping."),
            Message(role="user", content=f"<context>\nmessage_ref: {mine}\n</context>\nhold on"),
            Message(role="assistant", content="Holding."),
        ),
        None,
        frozenset({theirs, mine}),
        frozenset(),
        {theirs: "Mel Okafor (m@example.com)"},
    )
    assert rendered == [
        {"role": "user", "text": "ship it", "speaker": "Mel Okafor (m@example.com)"},
        {"role": "assistant", "text": "Shipping."},
        {"role": "user", "text": "hold on"},
        {"role": "assistant", "text": "Holding."},
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_slack_conversation_reads_as_words_and_names_the_other_speakers(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A workspace-shared Slack conversation read in the portal states each message as the words
    its member typed — never the fence the surface wrote around them — and names every speaker but
    the viewer, whose own bubbles the pane already accounts for. The rule reaches the rows the
    written transcript does not hold yet: a running turn's prompt and a queued arrival are named
    the same way."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C7:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    marker = mint_marker()
    ambient = (
        f"<{AMBIENT_CONTEXT_ELEMENT}_{marker}>\n"
        "[18:09] @Robin Vale: Draft announcement tweets\n"
        f"</{AMBIENT_CONTEXT_ELEMENT}_{marker}>\n"
    )
    first = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound=fence_member_message(marker, ambient, "draft the tweets", ""),
        speaker_member_id=peer_id,
        context=TurnContext(sender="Sam Frost (peer@example.com)"),
    )
    second = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound="thanks",
        speaker_member_id=member_id,
        context=TurnContext(sender="Mel Okafor (m@example.com)"),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=2,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {first}\n</context>\n"
                    + fence_member_message(marker, ambient, "draft the tweets", ""),
                ),
                Message(role="assistant", content="Drafted."),
                Message(
                    role="user", content=f"<context>\nmessage_ref: {second}\n</context>\nthanks"
                ),
                Message(role="assistant", content="Any time."),
            ),
        ),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    settled = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers=cookie,
    )

    assert settled.status_code == 200
    assert settled.json()["messages"] == [
        {"role": "user", "text": "draft the tweets", "speaker": "Sam Frost (peer@example.com)"},
        {"role": "assistant", "text": "Drafted."},
        {"role": "user", "text": "thanks"},
        {"role": "assistant", "text": "Any time."},
    ]

    running = uuid4()
    folded = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=running,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=3,
                status="running",
                inbound=fence_member_message(marker, ambient, "now the launch email", ""),
                admission_source="member",
                speaker_member_id=peer_id,
                context=TurnContext(
                    sender="Sam Frost (peer@example.com)", question="Announce where first?"
                ).model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=folded,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body=fence_member_message(marker, ambient, "and a blog post", ""),
                admission_source="member",
                speaker_member_id=member_id,
                context=TurnContext(
                    sender="Mel Okafor (m@example.com)", question="A blog post too?"
                ).model_dump(mode="json"),
                admitted_turn_id=running,
                consumed_turn_id=None,
                created_at=sa.func.now(),
            )
        )

    mid = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers=cookie,
    )

    assert mid.status_code == 200
    tail = mid.json()["messages"][-2:]
    assert tail[0] == {
        "role": "user",
        "text": "now the launch email",
        "speaker": "Sam Frost (peer@example.com)",
        "asked": "Announce where first?",
    }
    assert tail[1]["text"] == "and a blog post"
    assert tail[1]["asked"] == "A blog post too?"
    assert "speaker" not in tail[1]

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == running)
            .values(
                status="done",
                terminal=TerminalFrame(status="done", text="Sent.").model_dump(mode="json"),
            )
        )
        await connection.execute(
            sa.update(tables.inbound_message)
            .where(tables.inbound_message.c.id == folded)
            .values(consumed_turn_id=running)
        )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=3,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {running}\n</context>\n"
                    + fence_member_message(marker, ambient, "now the launch email", ""),
                ),
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {folded}\n</context>\n"
                    + fence_member_message(marker, ambient, "and a blog post", ""),
                ),
                Message(role="assistant", content="Sent."),
            ),
        ),
    )

    written = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={peer_token}"},
    )

    assert written.status_code == 200
    settled = written.json()["messages"][-3:]
    assert settled[0] == {
        "role": "user",
        "text": "now the launch email",
        "asked": "Announce where first?",
    }
    assert settled[1] == {
        "role": "user",
        "text": "and a blog post",
        "speaker": "Mel Okafor (m@example.com)",
        "asked": "A blog post too?",
    }
    assert settled[2] == {"role": "assistant", "text": "Sent."}

    own = await client.get(
        f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript",
        headers=cookie,
    )

    assert own.json()["messages"][-2] == {
        "role": "user",
        "text": "and a blog post",
        "asked": "A blog post too?",
    }


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_admin_archives_a_shipped_app_from_settings_and_restores_it(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    app_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=app_id,
                workspace_id=workspace_id,
                name="invoice-intake",
                prompt="read the invoices",
                model="claude-opus-4-8",
                reasoning="high",
                icon="aten",
                visibility="workspace",
                provisioned_by="app_invoice",
                provisioned_name="invoice-intake",
                provisioned_version="1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    settings = await client.get(f"/surface/web/agents/{app_id}/settings", headers=cookie)
    assert settings.status_code == 200
    assert settings.json()["agent"]["archivable"] is True
    archived = await client.post(
        f"/surface/web/agents/{app_id}/intents",
        json={"verb": "delete", "kind": "agent", "name": "invoice-intake"},
        headers=cookie,
    )
    assert archived.status_code == 200
    assert archived.json()["applied"] is True, archived.json()["message"]
    index = await client.get("/surface/web/api/agents", headers=cookie)
    assert [app["name"] for app in index.json()["archived"]] == ["invoice-intake"]
    assert [agent["id"] for agent in index.json()["agents"]] == [str(agent_id)]

    assert index.json()["archived"][0]["object"] == f"~archived-{app_id}"
    projected = await client.get(f"/surface/web/actions/agent/~archived-{app_id}", headers=cookie)
    assert projected.status_code == 200, projected.text
    [restore] = [v for v in projected.json()["actions"] if v["name"] == "restore_application"]
    assert restore["label"]
    assert restore["call"] == {
        "kind": "agent",
        "action": "restore_application",
        "name": f"~archived-{app_id}",
        "input": {},
    }
    restored = await client.post(
        f"/surface/web/agents/{agent_id}/actions/agent/~archived-{app_id}/restore_application",
        json={"new_name": "invoice-intake-2"},
        headers=cookie,
    )
    assert restored.status_code == 200
    assert restored.json()["applied"] is True, restored.json()["message"]
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(restored.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert (
        inbound
        == _action_intent(
            "agent", f"~archived-{app_id}", "restore_application", {"new_name": "invoice-intake-2"}
        ).model_dump_json()
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.agent.c.name, tables.agent.c.archived_at).where(
                    tables.agent.c.id == app_id
                )
            )
        ).one()
    assert (row.name, row.archived_at) == ("invoice-intake-2", None)
    back = await client.get("/surface/web/api/agents", headers=cookie)
    assert back.json()["archived"] == []
    assert sorted(agent["name"] for agent in back.json()["agents"]) == [
        "assistant",
        "invoice-intake-2",
    ]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_homepage_sweep_settles_an_archived_app_without_a_turn(db: None) -> None:
    workspace_id, main_agent = await _seed_workspace()
    await _seed_member(workspace_id, "seed-admin@example.com", admin=True)
    archived_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=archived_agent,
                workspace_id=workspace_id,
                name=f"~archived-{archived_agent}",
                archived_name="invoice-intake",
                prompt="read the invoices",
                model="claude-opus-4-8",
                archived_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = _SeedDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        ctx = context_for(EXTENSION_WEB, frozenset(), invoker=invoker, member_context_read=True)
        await web_surface.seed_homepages(ctx)
        markers = dict(await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX))
    assert sorted(markers) == sorted(
        f"{web_surface.HOMEPAGE_SEED_PREFIX}{agent_id}" for agent_id in (main_agent, archived_agent)
    )
    assert markers[f"{web_surface.HOMEPAGE_SEED_PREFIX}{archived_agent}"] == "archived"
    async with workspace_tx() as connection:
        seeded = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert seeded == [main_agent]


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_lane_refuses_an_action_the_portal_never_presented(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The panel-authority fence: an action without a presentation has no portal control, and the
    route refuses to prepare it before a turn exists — the same rule the projection draws by, so a
    body a page authored by hand cannot reach past what the portal offers."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/actions/surface/slack/slack_channels",
        json={},
        headers=cookie,
    )
    assert refused.status_code == 200
    assert refused.json() == {
        "applied": False,
        "message": "surface/slack has no portal action named 'slack_channels'.",
    }
    addressed = await client.post(
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json={"email": "x@example.com", "kind": "agent", "name": "other"},
        headers=cookie,
    )
    assert addressed.json() == {
        "applied": False,
        "message": "The body names kind, name; the route binds the target.",
    }
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


FRAMED_CONNECT = {
    "verb": "connect",
    "kind": "connection",
    "name": "github",
    "spec": {"shared": False},
}


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_frames_post_reaches_only_the_acts_declared_for_a_page(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The shell marks every call it forwards for an embedded page, and both lanes hold a marked
    post to the acts whose declaration says `frame`: a presented action without the mark answers the
    typed refusal before any turn exists, and `connect_account`, which carries it, is admitted
    exactly as the same post is without the mark."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    framed = {**cookie, FRAME_HEADER: "1"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json={"email": "x@example.com"},
        headers=framed,
    )
    assert refused.status_code == 200
    assert refused.json() == {"applied": False, "message": NO_FRAME_ACCESS}
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0

    lane = f"/surface/web/agents/{agent_id}/intents"
    admitted = await client.post(lane, json=FRAMED_CONNECT, headers=framed)
    unmarked = await client.post(lane, json=FRAMED_CONNECT, headers=cookie)
    assert admitted.status_code == unmarked.status_code == 200
    assert admitted.json()["message"] != NO_FRAME_ACCESS
    assert admitted.json()["message"] == unmarked.json()["message"]


@pytest.fixture
def frameless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ufo.serve.frame_admissible", lambda manifests, registry: frozenset())


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_deploy_whose_pack_presents_nothing_to_a_page_refuses_every_framed_post(
    frameless: None,
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The set the lane checks is the one boot computed from the pack: with nothing in it, the mark
    alone refuses `connect_account` and every presented action on both lanes, and the same posts
    without the mark are admitted as before."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    framed = {**cookie, FRAME_HEADER: "1"}
    connect = await client.post(
        f"/surface/web/agents/{agent_id}/intents", json=FRAMED_CONNECT, headers=framed
    )
    assert connect.json() == {"applied": False, "message": NO_FRAME_ACCESS}
    action = await client.post(
        f"/surface/web/agents/{agent_id}/actions/member/add_member",
        json={"email": "x@example.com"},
        headers=framed,
    )
    assert action.json() == {"applied": False, "message": NO_FRAME_ACCESS}
    unmarked = await client.post(
        f"/surface/web/agents/{agent_id}/intents", json=FRAMED_CONNECT, headers=cookie
    )
    assert unmarked.json()["message"] != NO_FRAME_ACCESS


async def test_an_app_is_listed_where_the_flag_service_answers_for_it_and_no_other_with_it(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None
) -> None:
    """The other half: closed is the state before an answer, not a state no answer can leave —
    flipping one environment's `enable-code-app` to true offers that app there, and offers nothing
    else with it."""
    client, workspace_id, _agent_id = web
    init_flags(
        InMemoryProvider(
            {
                "enable-code-app": InMemoryFlag(
                    default_variant="on", variants={"on": SERVED_TRUE, "off": SERVED_FALSE}
                )
            }
        )
    )
    for slug in web_surface.APP_FLAGS:
        await _seed_shipped_app(workspace_id, slug)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    visibility, _surfaces = await _app_visibility(client, token)
    assert visibility == {
        None: False,
        **{slug: slug != "code" for slug in web_surface.APP_FLAGS},
    }


async def test_a_flag_answered_false_is_the_one_thing_that_takes_a_screen_away(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None
) -> None:
    """What an operator running `ufoctl flags sync --off` produces, read back through the boot the
    portal paints from. A withheld app stays in the payload — the workspace holds it, and the
    address still opens it — carrying the mark that keeps it out of every list, while a flag the
    same service answers true leaves its screen exactly where it was."""
    client, workspace_id, _agent_id = web
    variants = {"on": SERVED_TRUE, "off": SERVED_FALSE}
    init_flags(
        InMemoryProvider(
            {
                "enable-community-skills": InMemoryFlag(default_variant="off", variants=variants),
                "enable-installed-skills": InMemoryFlag(default_variant="off", variants=variants),
                "enable-memory-tab": InMemoryFlag(default_variant="on", variants=variants),
                "enable-wiki-app": InMemoryFlag(default_variant="off", variants=variants),
            }
        )
    )
    await _seed_shipped_app(workspace_id, "wiki")
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    boot = (
        await client.get("/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    assert boot["surfaces"] == {
        "memory": True,
        "community-skills": False,
        "installed-skills": False,
        "team": True,
    }
    assert [(agent["app"], agent["hidden"]) for agent in boot["agents"]] == [
        (None, False),
        ("wiki", True),
    ]


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


async def test_a_shared_file_streams_before_the_turn_ends(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    _config, hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Ready."),
    )
    running = uuid4()
    artifact_key = f"artifacts/{uuid4()}/world-clock-wireframe.svg"
    preview_key = f"artifacts/{uuid4()}/world-clock-wireframe.png"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=running,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=2,
                status="running",
                inbound="build a world clock app",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    STREAM_GATE.arm()
    tailing = asyncio.ensure_future(_collect_events(client, token, running))
    await hub.publish(running, TextDelta(text="Drawing the design."))
    with ws(workspace_id):
        await blob.put(artifact_key, b"<svg></svg>")
        await blob.put(preview_key, b"\x89PNG preview")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=running,
                blob_key=artifact_key,
                workspace_id=workspace_id,
                filename="world-clock-wireframe.svg",
                subject="Application wireframe",
                media_type="image/svg+xml",
                size_bytes=11,
                preview_blob_key=preview_key,
                preview_media_type="image/png",
                preview_size_bytes=12,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await hub.publish(running, ArtifactsChanged())
    await hub.publish(
        running,
        Terminal(frame=TerminalFrame(status="done", text="Review this design.")),
    )

    events = await tailing

    names = [name for name, _payload in events]
    assert names.count("files") == 1
    assert names.index("files") < names.index("terminal")
    files = dict(events)["files"]["files"]
    assert [file["filename"] for file in files] == ["world-clock-wireframe.svg"]
    assert files[0]["preview_url"].startswith("https://web/artifacts/")
    assert "&preview=" in files[0]["preview_url"]
