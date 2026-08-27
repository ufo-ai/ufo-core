import asyncio
import json
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, aclosing, contextmanager
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast, get_args
from urllib.parse import quote, urljoin, urlsplit
from uuid import UUID, uuid4

import httpx
import lz4.frame
import pytest
import sqlalchemy as sa
import ufo_ext_todos as todos
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from openfeature import api
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from PIL import Image
from pydantic import BaseModel, ValidationError
from ufo_ext_app_chat.manifest import manifest as app_chat_manifest
from ufo_ext_app_radar.manifest import manifest as app_radar_manifest
from ufo_ext_composio.client import BANNED
from ufo_ext_connectors.manifest import manifest as connectors_manifest
from ufo_ext_imessage.manifest import manifest as imessage_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MEMORY_BODY_MAX_CHARS, recall_subjects
from ufo_ext_pipedream.client import CONNECTORS as PIPEDREAM_CONNECTORS
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
from ufo_ext_sites.surface import shipped_address, site_address
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
from ufo_ext_web import community as web_community
from ufo_ext_web import panels as web_panels
from ufo_ext_web import surface as web_surface
from ufo_ext_web.audience import AUDIENCE_PREFIX, EXTENSION_WEB, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.panels import (
    FIRST_RUN_PROVIDER_NAMES,
    ApplyIntent,
    ConnectGitHubIntent,
    ConnectImessageIntent,
    ConnectSlackIntent,
    PanelIntent,
    _connect_outcome,
    _imessage_outcome,
    _outcome,
    _rebuild_outcome,
    _tool_intent,
)
from ufo_ext_web.surface import (
    ASSET_MEDIA_TYPES,
    NO_MEMBER_FAULT,
    PORTAL_BUILD,
    PORTAL_FILE,
    PORTAL_HTML,
    SESSION_COOKIE,
    SESSION_FAULT_HEADER,
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
import ufo.kinds.conversations as conversations_kind
import ufo.objects as objects_module
from ufo.access.connectors import CatalogEntry, CatalogPage, ConnectorEntry, ConnectorRegistry
from ufo.access.credentials import (
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    seal_credential_request,
)
from ufo.access.grants import (
    ConnectFlow,
    GrantStore,
    OAuthAccount,
    account_object_name,
    install_connect_flow,
)
from ufo.agent_scope import agent as bind_agent
from ufo.auth.bearer import mint_token
from ufo.billing.accounting import record_egress_request, record_turn_usage
from ufo.blob import FilesystemBlobStore, FleetBlobStore, WorkspaceBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.durability import replay_safe_client
from ufo.ext.context import ScopedStore, context_for
from ufo.ext.loader import member_object_registry, member_skill_listing, skill_registry
from ufo.ext.surface import (
    AMBIENT_CONTEXT_ELEMENT,
    CONVERSATION_TITLE_CHARS,
    SurfaceContext,
    fence_member_message,
    member_message_text,
    mint_marker,
)
from ufo.flags import init_flags
from ufo.hub import (
    Absorbed,
    Activity,
    CostTick,
    InProcessHub,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SubagentActivity,
    Terminal,
)
from ufo.kinds.members import ADD_MEMBER_GATE
from ufo.loop import queue as loop_queue
from ufo.loop.engine import FINISH_PROMPT
from ufo.loop.subagents import SubagentRegistry
from ufo.loop.transcript import Transcript
from ufo.media.image_previews import IMAGE_PREVIEW_MAX_BYTES
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
from ufo.object_name import ObjectRef
from ufo.objects import OBJECT_LIST_PAGE
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import (
    MEMBER_ADMISSION,
    SPAWN_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    SURFACE_COMMENT_ROUND_INDEX,
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
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience, room_audience
from ufo.sdk.jobs import unseeded_agent_workspaces, untitled_conversation_workspaces
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
from ufo.sdk.seats import Seats
from ufo.serve import _mount_shared_surfaces
from ufo.surfaces import hub_tail
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.tools.context import ToolContext
from ufo.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
    decode,
    encode,
)
from ufo.turns.untrusted import wall
from ufo.turns.workspace_changes import WorkspaceChange, WorkspaceChanges
from ufo.workspace import ws

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
    assert set(frames) == set(get_args(LiveFrame))
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


def test_transcript_projection_reads_stored_activity_after_a_rewrite() -> None:
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="<context>source: web</context>\nInspect it."),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="call-1",
                        name="bash",
                        input={"command": "ls", "user_description": "Listing the workspace."},
                    ),
                    ToolUseBlock(id="call-2", name="load_skill", input={"name": "coding"}),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="call-1", content="README.md", activity=True),
                    ToolResultBlock(tool_use_id="call-2", content="mounted", activity=True),
                ),
            ),
            Message(role="assistant", content="Done."),
        ),
    )
    rewritten = decode(encode(conversation))

    rendered = _rendered_messages(rewritten.messages)

    assert rendered == [
        {"role": "user", "text": "Inspect it."},
        {
            "role": "assistant",
            "text": "Done.",
            "events": [
                {"kind": "activity", "text": "Listing the workspace."},
                {"kind": "activity", "text": "Loading skill · coding"},
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


def test_subagent_activity_reads_a_stored_tool_description_after_a_rewrite() -> None:
    conversation = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="{}"),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="call-1",
                        name="fetch_url",
                        input={"url": "https://x/y", "user_description": "Reading the source."},
                    ),
                ),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id="call-1", content="…", activity=True),),
            ),
        ),
    )
    rewritten = decode(encode(conversation))

    events = _subagent_activity(rewritten.messages)

    assert events == [{"kind": "activity", "text": "Reading the source."}]


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


def test_a_force_finished_run_states_its_prose_once() -> None:
    """A child that stops on prose is force-finished over it, so the engine appends that prose to
    the transcript and `persist_transcript` closes with the finish payload — the same words land
    durably twice. Work is read from blocks and both are string content, so the tree states the
    call it made and the answer states the prose, each exactly once."""
    events = _subagent_activity(FORCE_FINISHED_RUN)
    output = _run_answer(FORCE_FINISHED_PAYLOAD)

    assert events == [{"kind": "activity", "text": "Fetching deadline sources."}]
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
                        tool_use_id="call-1",
                        content="exit 1",
                        is_error=True,
                        activity=True,
                        activity_text="Checking the command outcome.",
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
            "events": [{"kind": "activity", "text": "Checking the command outcome."}],
        },
        {"role": "user", "text": "Try something else."},
        {"role": "assistant", "text": "Done."},
    ]


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


def test_transcript_projection_states_a_drained_round_as_the_steps_it_wrote() -> None:
    """A drain cuts a round that narrated and then dispatched work, so that round answered nothing:
    the reply behind the folded message states its thought and its call as steps and no words — the
    shape the live view settles into, which a reload has to draw the same way."""
    turn_id = "55555555-5555-5555-5555-555555555555"
    drained = "66666666-6666-6666-6666-666666666666"
    rendered = _rendered_messages(
        (
            Message(
                role="user",
                content=f"<context>\nmessage_ref: {turn_id}\n</context>\nWhat shipped?",
            ),
            Message(
                role="assistant",
                content=(
                    TextBlock(text="Reading the changelog first."),
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
                role="user",
                content=f"<context>\nmessage_ref: {drained}\n</context>\nAny news?",
            ),
            Message(role="assistant", content=(TextBlock(text="It shipped Tuesday."),)),
        ),
        None,
        frozenset({turn_id}),
    )

    assert rendered == [
        {"role": "user", "text": "What shipped?"},
        {
            "role": "assistant",
            "text": "",
            "events": [
                {"kind": "note", "text": "Reading the changelog first."},
                {"kind": "activity", "text": "Reading the changelog."},
            ],
        },
        {"role": "user", "text": "Any news?"},
        {"role": "assistant", "text": "It shipped Tuesday."},
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
            manifests=(
                web_manifest(),
                connectors_manifest(),
                imessage_manifest(),
                SCHEDULED_TASK_KIND_ONLY,
                skill_create_manifest(),
                slack_manifest(),
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
                SCHEDULED_TASK_KIND_ONLY,
                SOURCE_TRIGGER_KIND_ONLY,
                SLOTTED,
                sites_manifest(),
                skill_create_manifest(),
                report_digest_manifest(),
            ),
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


async def test_web_turn_round_trip_admits_streams_and_links_identity(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation=new",
        content=b"hello",
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "x-ufo-timezone": "America/New_York",
        },
    )
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    opened = admitted.json()["conversation_id"]
    assert admitted.json()["title"] == "hello"
    assert admitted.json()["opened_run"] is True
    assert "arrival_id" not in admitted.json()
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
        timezone = (
            await connection.execute(
                sa.select(tables.member.c.timezone).where(tables.member.c.id == member_id)
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
        "timezone": "America/New_York",
        "question": None,
        "source": (f"https://web/surface/web#/c/{opened} (owner@example.com)"),
    }
    assert timezone == "America/New_York"
    transcript = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={opened}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert transcript.status_code == 200
    assert transcript.json()["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "echo:1"},
    ]


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

    candidates = untitled_conversation_workspaces()
    assert workspace_id in await candidates()

    ctx = context_for(
        EXTENSION_WEB,
        frozenset(),
        blob=blob,
        model_resolver=STANDIN_REGISTRY,
        model_job=f"{EXTENSION_WEB}:{web_surface.TITLE_JOB_NAME}",
    )
    assert ctx.corpus is not None
    with ws(workspace_id):
        async with asyncio.timeout(5):
            while True:
                read = await ctx.corpus.conversations((UUID(opened),))
                if read and web_surface._title_excerpt(read[0].messages):
                    break
                await asyncio.sleep(0.01)
        await web_surface.summarize_chat_titles(ctx)
        assert await ctx.conversations_awaiting_title(web_surface.TITLE_BATCH) == ()

    (row,) = await _rail_rows(client, headers)
    assert row["name"] == opened
    assert row["title"] == "echo:1"
    assert workspace_id not in await candidates()

    with ws(workspace_id):
        await web_surface.summarize_chat_titles(context_for(EXTENSION_WEB, frozenset()))


async def test_chat_titles_name_every_surface_s_conversations_and_summarize_each_once(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The rail lists a Slack thread and a terminal session beside a portal chat, so the job names
    all three: a conversation open before the job ever ran is a candidate like a new one, and the
    surface that holds it is not a filter. Every candidate a tick takes is recorded, so the backlog
    behind it drains: a transcript that states no reply keeps the name its opening words gave it,
    costs no model call, and is not read again. The one conversation read again is the one whose
    transcript has not landed yet — its exchange is coming, so it is named on a later tick rather
    than from nothing."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "surfaces@example.com")
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
        workspace_id, slack_id, agent_id, seq=1, inbound="Order more pallets before Friday"
    )
    cli_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="tty:1",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="ufo",
    )
    await _seed_listed_turn(workspace_id, cli_id, agent_id, seq=1, inbound="Deploy the branch")
    unanswered_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="tty:2",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="ufo",
    )
    await _seed_listed_turn(workspace_id, unanswered_id, agent_id, seq=1, inbound="Still failed")
    landing_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="tty:3",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="ufo",
    )
    await _seed_listed_turn(workspace_id, landing_id, agent_id, seq=1, inbound="Just answered")
    errand_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="errand/1",
        audience="shared",
        member_id=None,
        surface="extension:sample",
    )
    await _seed_listed_turn(
        workspace_id,
        errand_id,
        agent_id,
        seq=1,
        inbound="{}",
        subagent_profile="general_purpose",
    )
    for named in (slack_id, cli_id):
        await _write_transcript(
            blob,
            named,
            Conversation(
                seq=1,
                messages=(
                    Message(role="user", content="what the member said"),
                    Message(role="assistant", content=(TextBlock(text="what the agent did"),)),
                ),
            ),
        )
    await _write_transcript(
        blob,
        unanswered_id,
        Conversation(seq=1, messages=(Message(role="user", content="Still failed"),)),
    )

    ctx = context_for(
        EXTENSION_WEB,
        frozenset(),
        blob=blob,
        model_resolver=STANDIN_REGISTRY,
        model_job=f"{EXTENSION_WEB}:{web_surface.TITLE_JOB_NAME}",
    )
    with ws(workspace_id):
        assert set(await ctx.conversations_awaiting_title(web_surface.TITLE_BATCH)) == {
            slack_id,
            cli_id,
            unanswered_id,
            landing_id,
        }
        await web_surface.summarize_chat_titles(ctx)
        assert await ctx.conversations_awaiting_title(web_surface.TITLE_BATCH) == (landing_id,)

    listed = {
        row["name"]: row["title"]
        for row in await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={token}"})
    }
    assert listed[str(slack_id)] == "echo:1"
    assert listed[str(cli_id)] == "echo:1"
    assert listed[str(unanswered_id)] == "Still failed"
    assert listed[str(landing_id)] == "Just answered"

    await _write_transcript(
        blob,
        landing_id,
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="Just answered"),
                Message(role="assistant", content=(TextBlock(text="here it is"),)),
            ),
        ),
    )
    with ws(workspace_id):
        await web_surface.summarize_chat_titles(ctx)
        assert await ctx.conversations_awaiting_title(web_surface.TITLE_BATCH) == ()
    named = await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={token}"})
    assert {row["name"]: row["title"] for row in named}[str(landing_id)] == "echo:1"


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
    await _write_transcript(
        blob,
        conversation_id,
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
                        ToolResultBlock(
                            tool_use_id="call-1",
                            content="/workspace",
                            activity=True,
                            activity_text="Checking the current directory.",
                        ),
                    ),
                ),
                Message(role="assistant", content="Done."),
            ),
        ),
    )
    child_conversation, child_turn = await _seed_subagent(
        workspace_id,
        agent_id,
        member_id,
        turn_id,
        terminal_text='{"result": "Nothing is stale."}',
        name="Lockfile check",
    )
    await _write_transcript(
        blob,
        child_conversation,
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
                    content=(
                        ToolResultBlock(
                            tool_use_id="call-2",
                            content="…",
                            activity=True,
                            activity_text="Reading the lockfile.",
                        ),
                    ),
                ),
            ),
        ),
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
            "events": [{"kind": "activity", "text": "Checking the current directory."}],
            "subagents": [
                {
                    "profile": "general_purpose",
                    "name": "Lockfile check",
                    "conversation_id": str(child_conversation),
                    "events": [
                        {"kind": "note", "text": "Checking the lockfile."},
                        {"kind": "activity", "text": "Reading the lockfile."},
                    ],
                    "output": "Nothing is stale.",
                    "subagents": [
                        {
                            "profile": "deep_research",
                            "name": "",
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
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(role="user", content="<context>source: web</context>\nFirst ask."),
                Message(role="assistant", content="Looked."),
            ),
        ),
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


async def test_a_message_sent_while_a_turn_runs_joins_it_and_the_reload_still_shows_it(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The composer may send while a turn streams: the message joins the running turn rather than
    founding one, and the route says so twice over — `opened_run` false, because admission opened no
    run for this delivery, and the arrival the turn's `absorbed` event will carry. The page reads
    the first to leave the tail it holds alone, and holds its wait against the second. Until the
    turn ends the message is in no written transcript, so the read projects it as the member bubble
    it is — still waiting before the drain, and after it as a bubble with nothing left to wait on,
    since the turn that took it up has yet to write it."""
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

    folded = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"Also check the tests.",
        headers=cookie,
    )

    assert folded.status_code == 200
    body = folded.json()
    assert body["turn_id"] == str(running)
    assert body["opened_run"] is False
    arrival_id = UUID(body["arrival_id"])
    async with workspace_tx() as connection:
        queued = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.body,
                    tables.inbound_message.c.admitted_turn_id,
                ).where(tables.inbound_message.c.id == arrival_id)
            )
        ).one()
    assert (queued.body, queued.admitted_turn_id) == ("Also check the tests.", running)

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
                "arrival_id": str(arrival_id),
            },
        ],
        "turn": str(running),
    }

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.inbound_message)
            .values(consumed_turn_id=running)
            .where(tables.inbound_message.c.id == arrival_id)
        )
    drained = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert drained.status_code == 200
    assert drained.json() == {
        "messages": [
            {"role": "user", "text": "Review PR 1268."},
            {"role": "user", "text": "Also check the tests."},
        ],
        "turn": str(running),
    }


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


async def test_a_stop_ends_the_running_turn_and_admits_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The stop press is not a message: the header names the turn, the body is empty, and the route
    admits nothing — so the transcript never mentions the press. The turn's row reads cancelled and
    the tail the member is holding ends on the cancelled terminal the stop published, which is how
    the page learns the turn is over."""
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

    stopped = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        headers={**cookie, "x-ufo-stop-turn": str(running)},
    )

    assert stopped.status_code == 200
    assert stopped.json() == {"stopped": True}
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == running
                )
            )
        ).one()
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
        arrivals = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.inbound_message))
        ).scalar_one()
    assert row.status == "cancelled"
    assert row.terminal["status"] == "cancelled"
    assert (turns, arrivals) == (2, 0)
    streamed, terminal = await _consume(client, token, str(running))
    assert (streamed, terminal["status"]) == ("", "cancelled")


async def test_a_stop_naming_another_conversations_turn_is_not_found(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A stop reaches only the conversation the route authorized: a turn of the member's other
    conversation is not this one's to end, and it goes on running."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    done = TerminalFrame(status="done", text="Looked.")
    mine, _first = await _seed_web_turn(
        workspace_id, agent_id, member_id, "owner@example.com", done
    )
    theirs, _second = await _seed_web_turn(
        workspace_id, agent_id, member_id, "owner@example.com", done
    )
    running = await _seed_running_turn(workspace_id, theirs, agent_id, member_id, 2)

    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={mine}",
        headers={**cookie, "x-ufo-stop-turn": str(running)},
    )

    assert refused.status_code == 404
    assert refused.text == "no such turn in this conversation"
    assert await _turn_status(running) == "running"


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


async def test_a_stop_of_a_settled_turn_reports_that_it_ended_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A press racing the turn's own last frame, and a second press behind the first, reach a turn
    that is already terminal: the route answers that it stopped nothing and leaves the committed
    outcome alone."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, settled = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )

    stopped = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}", "x-ufo-stop-turn": str(settled)},
    )

    assert stopped.status_code == 200
    assert stopped.json() == {"stopped": False}
    assert await _turn_status(settled) == "done"


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


async def test_an_undrained_row_under_a_settled_turn_waits_on_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A wait names a turn working on the message. A row admitted after the last drain of a turn
    that then committed is still pending, and the turn that will take it up is not admitted yet — so
    the read states the message and no wait, rather than a pulse pointing at a turn that ended."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, settled = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Looked."),
    )
    await _seed_arrival(
        workspace_id,
        conversation_id,
        settled,
        seq=1,
        body="Also check the tests.",
        admission_source="member",
        speaker_member_id=member_id,
    )

    reloaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert reloaded.status_code == 200
    assert reloaded.json()["messages"] == [{"role": "user", "text": "Also check the tests."}]


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
        "surfaces": dict.fromkeys(web_surface.PORTAL_SURFACES, True),
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
            "icon": "compass",
            "purpose": None,
            "app": None,
            "mine": False,
            "hidden": False,
            "homepage": {"state": "none"},
            "setup_due": False,
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
            "web_audience": ["member@example.com"],
        },
    ]
    member_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_view.json()["member"] == {"email": "member@example.com", "admin": False}
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


async def test_every_agent_read_carries_its_icon_and_no_form_asks_for_one(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The portal draws an agent by its icon wherever it names one, so the icon rides all three
    reads a screen draws an agent from: the boot index, the administration table, and the settings
    spec that states the current value the picker opens on. The form offers no field for it — the
    settings page picks from a grid of drawn marks that a schema enum cannot describe."""
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
                icon="telescope",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    boot = (await client.get("/surface/web/api/agents", headers=headers)).json()
    assert {agent["name"]: agent["icon"] for agent in boot["agents"]} == {
        "assistant": "compass",
        "ops": "telescope",
    }
    administration = (await client.get("/surface/web/api/admin", headers=headers)).json()
    assert {agent["name"]: agent["icon"] for agent in administration["agents"]} == {
        "assistant": "compass",
        "ops": "telescope",
    }
    settings = (
        await client.get(f"/surface/web/agents/{second_agent}/settings", headers=headers)
    ).json()
    assert settings["spec"]["icon"] == "telescope"
    assert "icon" not in settings["spec_schema"]["properties"]


@pytest.fixture
def unbound_flags() -> Iterator[None]:
    """The boot read asks the deploy's flag backend what to offer, so a backend one case binds must
    not answer the next — and the fact that one was bound at all is what a case here drives."""
    yield
    api.clear_providers()
    init_flags(None)


async def _seed_wiki_app(workspace_id: UUID) -> UUID:
    """The wiki app as its extension provisions it: the slug the portal reads its flag by comes off
    `provisioned_by`, not off the row's member-visible name."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="wiki",
                prompt="be the wiki",
                model="claude-sonnet-5",
                icon="book",
                provisioned_by="app_wiki",
                provisioned_name="wiki",
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


@pytest.mark.parametrize("bound", [False, True])
async def test_a_flag_service_that_answers_nothing_leaves_a_member_what_they_had(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None, bound: bool
) -> None:
    """The two silences are one state to a member: a deploy that selected no flag backend (a
    development run, an eval stack, a self-hosted deploy) and one whose service holds none of these
    keys — which is every deploy the moment this lands, and every deploy again while Flagship is
    unreachable. Neither takes a shipped screen away, and neither is what finally offers the wiki
    app, which has never been offered and so is the one flag read closed."""
    client, workspace_id, _agent_id = web
    init_flags(InMemoryProvider({}) if bound else None)
    await _seed_wiki_app(workspace_id)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    boot = (
        await client.get("/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    assert boot["surfaces"] == dict.fromkeys(web_surface.PORTAL_SURFACES, True)
    assert [(agent["app"], agent["hidden"]) for agent in boot["agents"]] == [
        (None, False),
        ("wiki", True),
    ]


async def test_the_wiki_app_is_listed_where_the_service_answers_for_it(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None
) -> None:
    """The other half of the exception: closed is the state before an answer, not a state no answer
    can leave — flipping this environment's `enable-wiki-app` to true offers the app."""
    client, workspace_id, _agent_id = web
    init_flags(
        InMemoryProvider(
            {
                "enable-wiki-app": InMemoryFlag(
                    default_variant="on", variants={"on": True, "off": False}
                )
            }
        )
    )
    await _seed_wiki_app(workspace_id)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    boot = (
        await client.get("/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    assert [(agent["app"], agent["hidden"]) for agent in boot["agents"]] == [
        (None, False),
        ("wiki", False),
    ]


async def test_a_flag_answered_false_is_the_one_thing_that_takes_a_screen_away(
    web: tuple[AsyncClient, UUID, UUID], unbound_flags: None
) -> None:
    """What an operator running `ufoctl flags sync --off` produces, read back through the boot the
    portal paints from. A withheld app stays in the payload — the workspace holds it, and the
    address still opens it — carrying the mark that keeps it out of every list, while a flag the
    same service answers true leaves its screen exactly where it was."""
    client, workspace_id, _agent_id = web
    variants = {"on": True, "off": False}
    init_flags(
        InMemoryProvider(
            {
                "enable-admin-settings": InMemoryFlag(default_variant="off", variants=variants),
                "enable-community-skills": InMemoryFlag(default_variant="off", variants=variants),
                "enable-installed-skills": InMemoryFlag(default_variant="off", variants=variants),
                "enable-memory-tab": InMemoryFlag(default_variant="on", variants=variants),
                "enable-wiki-app": InMemoryFlag(default_variant="off", variants=variants),
            }
        )
    )
    await _seed_wiki_app(workspace_id)
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    boot = (
        await client.get("/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    assert boot["surfaces"] == {
        "admin": False,
        "memory": True,
        "community-skills": False,
        "installed-skills": False,
    }
    assert [(agent["app"], agent["hidden"]) for agent in boot["agents"]] == [
        (None, False),
        ("wiki", True),
    ]


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


async def test_agents_status_reports_the_liveest_turn_and_last_activity(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The status read answers every visible agent: the liveest non-terminal turn wins in
    running > queued > parked order however the turns are aged — a newer queued turn does not
    displace a running one, nor a newer parked turn a queued one — `last_active_at` is the newest
    turn's `updated_at` whichever turn that is, and an agent holding no turns reads all nulls."""
    client, workspace_id, agent_id = web
    ops = await _seed_status_agent(workspace_id, "ops")
    scout = await _seed_status_agent(workspace_id, "scout")
    still = await _seed_status_agent(workspace_id, "still")
    _admin, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    base = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    main_conversation = await _seed_agent_conversation(
        workspace_id, agent_id, queue_key="web/main", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_status_turn(
        workspace_id, agent_id, main_conversation, seq=1, status="running", at=base
    )
    await _seed_status_turn(
        workspace_id,
        agent_id,
        main_conversation,
        seq=2,
        status="queued",
        at=base + timedelta(minutes=5),
    )
    ops_conversation = await _seed_agent_conversation(
        workspace_id, ops, queue_key="web/ops", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_status_turn(workspace_id, ops, ops_conversation, seq=1, status="queued", at=base)
    await _seed_status_turn(
        workspace_id, ops, ops_conversation, seq=2, status="parked", at=base + timedelta(minutes=9)
    )
    scout_conversation = await _seed_agent_conversation(
        workspace_id, scout, queue_key="web/scout", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_status_turn(
        workspace_id, scout, scout_conversation, seq=1, status="parked", at=base
    )
    payload = (
        await client.get(STATUS_PATH, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()
    statuses = {row["agent_id"]: row for row in payload["statuses"]}
    assert set(statuses) == {str(agent_id), str(ops), str(scout), str(still)}
    assert statuses[str(agent_id)] == {
        "agent_id": str(agent_id),
        "turn": "running",
        "activity": None,
        "last_active_at": (base + timedelta(minutes=5)).isoformat(),
        "last_failed": False,
    }
    assert statuses[str(ops)]["turn"] == "queued"
    assert statuses[str(ops)]["last_active_at"] == (base + timedelta(minutes=9)).isoformat()
    assert statuses[str(scout)]["turn"] == "parked"
    assert statuses[str(still)] == {
        "agent_id": str(still),
        "turn": None,
        "activity": None,
        "last_active_at": None,
        "last_failed": False,
    }


async def test_agents_status_fences_the_turn_aggregate_to_the_readers_conversations(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, WorkspaceBlobStore, ConversationSandbox],
) -> None:
    """The main agent is every member's row and its turns are not. A turn taken in one member's
    private conversation is theirs alone: it is the liveest turn, the hub activity, the last
    movement, and the last failure on their own status read, and the colleague who shares that
    agent reads the same row as all nulls — never the running turn's id, and never the tool
    arguments `activity` would spell out of it."""
    client, workspace_id, agent_id = web
    _config, hub, _blob, _sandboxes = dbos_runtime
    mine, my_token = await _seed_member(workspace_id, "mine@example.com")
    _theirs, their_token = await _seed_member(workspace_id, "theirs@example.com")
    private = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="web/mine",
        audience=str(conversation_audience(mine)),
        member_id=mine,
    )
    base = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    await _seed_status_turn(workspace_id, agent_id, private, seq=1, status="failed", at=base)
    running = await _seed_status_turn(
        workspace_id,
        agent_id,
        private,
        seq=2,
        status="running",
        at=base + timedelta(minutes=5),
    )
    await hub.publish(running, Activity(text="Checking the private notes."))
    owner = (
        await client.get(STATUS_PATH, headers={"cookie": f"{SESSION_COOKIE}={my_token}"})
    ).json()
    assert owner["statuses"] == [
        {
            "agent_id": str(agent_id),
            "turn": "running",
            "activity": "Checking the private notes.",
            "last_active_at": (base + timedelta(minutes=5)).isoformat(),
            "last_failed": True,
        }
    ]
    colleague = (
        await client.get(STATUS_PATH, headers={"cookie": f"{SESSION_COOKIE}={their_token}"})
    ).json()
    assert colleague["statuses"] == [
        {
            "agent_id": str(agent_id),
            "turn": None,
            "activity": None,
            "last_active_at": None,
            "last_failed": False,
        }
    ]


async def test_agents_status_narrates_the_running_turn_from_the_hubs_newest_activity(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, WorkspaceBlobStore, ConversationSandbox],
) -> None:
    """`activity` is the hub's newest generated summary for the running turn. A running turn the
    hub holds no frames for states nothing."""
    client, workspace_id, agent_id = web
    _config, hub, _blob, _sandboxes = dbos_runtime
    _admin, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation = await _seed_agent_conversation(
        workspace_id, agent_id, queue_key="web/live", audience=str(SHARED_AUDIENCE), member_id=None
    )
    turn_id = await _seed_status_turn(
        workspace_id,
        agent_id,
        conversation,
        seq=1,
        status="running",
        at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
    )
    quiet = (await client.get(STATUS_PATH, headers=headers)).json()["statuses"][0]
    assert quiet["turn"] == "running"
    assert quiet["activity"] is None
    await hub.publish(turn_id, Activity(text="Checking the queue."))
    described = (await client.get(STATUS_PATH, headers=headers)).json()["statuses"][0]
    assert described["activity"] == "Checking the queue."
    await hub.publish(turn_id, Activity(text="Loading call triage."))
    loading = (await client.get(STATUS_PATH, headers=headers)).json()["statuses"][0]
    assert loading["activity"] == "Loading call triage."


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


async def test_github_coverage_route_reports_independent_legs(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    await _seed_connection(workspace_id, agent_id, member_id, "github", shared=False)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=uuid4(),
                workspace_id=workspace_id,
                backend="github",
                config={},
                subject=f"member:{member_id}",
                owner_member_id=member_id,
                next_sync_at=sa.func.now(),
                removed_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="github_app_installation",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    response = await client.get(
        "/surface/web/github/coverage", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert response.json() == {"api": True, "git_push": True, "sources": True}
    assert "sealed" not in response.text


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
    assert view.json() == {"installations": [{"surface": "slack", "agent_id": str(agent_id)}]}
    assert str(second_agent) not in view.text
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"})
    assert admin_view.json() == {
        "installations": [
            {"surface": "slack", "agent_id": str(agent_id)},
            {"surface": "teams", "agent_id": str(second_agent)},
        ]
    }
    await _grant_web_access(workspace_id, second_agent, "m@example.com")
    widened = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert widened.json() == admin_view.json()
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_first_run_states_the_tiles_and_the_connectors_real_state(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The first run's projection: the tiles a member picks what their team uses from — Slack and
    GitHub among them, because a team that uses them says so like any other tool — and the two of
    those tiles the page can install itself, beside whether the workspace holds them. Each state is
    the leg that step's own Connect act writes: Slack's surface installation, and GitHub's App
    installation credential. A broker `github` connection is not that leg, so it leaves the step
    offering the install; the credential is the whole workspace's, so the step reads connected for a
    member who owns no connection at all. Refused without a session."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    other_id, other_token = await _seed_member(workspace_id, "n@example.com")
    path = "/surface/web/workspace/first-run"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    bare = await client.get(path, headers=cookie)
    assert bare.status_code == 200
    payload = bare.json()
    assert payload["imessage"] is True
    assert {"gmail", "notion", "linear", "slack", "github"} <= {
        tile["name"] for tile in payload["providers"]
    }
    assert payload["connectors"] == [
        {"name": "slack", "label": "Slack", "installed": False},
        {"name": "github", "label": "GitHub", "installed": False},
    ]
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
    assert held.json()["connectors"] == [
        {"name": "slack", "label": "Slack", "installed": True},
        {"name": "github", "label": "GitHub", "installed": False},
    ]
    owner = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={other_token}"})
    assert owner.json()["connectors"][1] == {
        "name": "github",
        "label": "GitHub",
        "installed": False,
    }
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="github_app_installation",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    connected = await client.get(path, headers=cookie)
    assert connected.json()["connectors"][1] == {
        "name": "github",
        "label": "GitHub",
        "installed": True,
    }
    anonymous = await client.get(path)
    assert anonymous.status_code == 401
    assert set(payload) == {"providers", "connectors", "imessage"}


async def test_imessage_claim_reads_the_member_s_own_phone_claim(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The first run's iMessage watch: the claim the reading member holds on the phone the step
    reserved, as `pending` while the reservation stands, `connected` once the phone proved the
    code, and `expired` once the window lapsed without a proof. A member who reserved nothing —
    including a member whose teammate reserved their own phone — reads `expired`, since no
    reservation of theirs stands, and a member's claim never answers for another's. Refused
    without a session."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    other_id, other_token = await _seed_member(workspace_id, "n@example.com")
    path = "/surface/web/workspace/imessage-claim"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    other_cookie = {"cookie": f"{SESSION_COOKIE}={other_token}"}

    assert (await client.get(path, headers=cookie)).json() == {"state": "expired"}
    assert (await client.get(path, headers=other_cookie)).json() == {"state": "expired"}

    now = datetime.now(UTC)

    async def _claim(
        member: UUID,
        address: str,
        *,
        expires_at: datetime | None,
        proved_by: str | None,
    ) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface="imessage",
                    address=address,
                    workspace_id=workspace_id,
                    member_id=member,
                    claim_expires_at=expires_at,
                    proved_by=proved_by,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    await _claim(
        member_id,
        "+15594259991",
        expires_at=now + timedelta(minutes=30),
        proved_by=None,
    )
    held = await client.get(path, headers=cookie)
    assert held.json() == {"state": "pending"}

    await _claim(other_id, "+15594259992", expires_at=None, proved_by="turn-1")
    teammate = await client.get(path, headers=cookie)
    assert teammate.json() == {"state": "pending"}
    proven = await client.get(path, headers=other_cookie)
    assert proven.json() == {"state": "connected"}

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.surface_address)
            .where(
                tables.surface_address.c.surface == "imessage",
                tables.surface_address.c.member_id == member_id,
            )
            .values(
                claim_expires_at=now - timedelta(minutes=1),
                updated_at=sa.func.now(),
            )
        )
    lapsed = await client.get(path, headers=cookie)
    assert lapsed.json() == {"state": "expired"}

    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_imessage_claim_answers_the_strongest_of_a_members_rows(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A member holds one row per phone they stated, so a corrected typo or a second device leaves
    the lapsed row beside the proved one. The read answers the strongest row — connected once any
    phone proved the code — rather than failing on the pair, and a member holding two reservations
    reads the later one."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "two@example.com")
    path = "/surface/web/workspace/imessage-claim"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    now = datetime.now(UTC)

    async def _claim(address: str, *, expires_at: datetime | None, proved_by: str | None) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface="imessage",
                    address=address,
                    workspace_id=workspace_id,
                    member_id=member_id,
                    claim_expires_at=expires_at,
                    proved_by=proved_by,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    await _claim("+15594259001", expires_at=now - timedelta(minutes=10), proved_by=None)
    lapsed = await client.get(path, headers=cookie)
    assert lapsed.status_code == 200
    assert lapsed.json() == {"state": "expired"}

    await _claim("+15594259002", expires_at=now + timedelta(minutes=30), proved_by=None)
    reserved = await client.get(path, headers=cookie)
    assert reserved.status_code == 200
    assert reserved.json() == {"state": "pending"}

    await _claim("+15594259003", expires_at=None, proved_by="turn-2")
    proved = await client.get(path, headers=cookie)
    assert proved.status_code == 200
    assert proved.json() == {"state": "connected"}


async def test_imessage_claim_reads_a_released_row_as_expired_rather_than_pending(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The iMessage surface releases the row — `release_address` deletes it — when the phone texts
    a lapsed code or an opt-out word. The watch reads on every three seconds, so a deleted row that
    read as a reservation would take the page back from the lapsed notice to the opt-in link, and
    that link admits nothing: the surface answers no message from an address no row claims. A
    member holding no row reads `expired`, whether the row lapsed first or stood when it went."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "released@example.com")
    path = "/surface/web/workspace/imessage-claim"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    async def _claim(expires_at: datetime) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface="imessage",
                    address="+15594259004",
                    workspace_id=workspace_id,
                    member_id=member_id,
                    claim_expires_at=expires_at,
                    proved_by=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    async def _release() -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.surface_address).where(
                    tables.surface_address.c.surface == "imessage",
                    tables.surface_address.c.address == "+15594259004",
                    tables.surface_address.c.workspace_id == workspace_id,
                )
            )

    await _claim(datetime.now(UTC) - timedelta(minutes=1))
    lapsed = await client.get(path, headers=cookie)
    assert lapsed.json() == {"state": "expired"}

    await _release()
    released = await client.get(path, headers=cookie)
    assert released.status_code == 200
    assert released.json() == {"state": "expired"}

    await _claim(datetime.now(UTC) + timedelta(minutes=30))
    reserved = await client.get(path, headers=cookie)
    assert reserved.json() == {"state": "pending"}

    await _release()
    opted_out = await client.get(path, headers=cookie)
    assert opted_out.status_code == 200
    assert opted_out.json() == {"state": "expired"}


async def test_connector_catalog_searches_the_live_broker_namespace(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "m@example.com")
    path = "/surface/web/connector-catalog"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    listed = await client.get(path, headers=cookie)
    assert listed.json() == {
        "providers": [{"name": "notion", "label": "Notion"}],
        "after": "catalog-page-two",
    }
    continued = await client.get(f"{path}?after=catalog-page-two", headers=cookie)
    assert continued.json() == {
        "providers": [{"name": "salesforce", "label": "Salesforce"}],
        "after": None,
    }
    searched = await client.get(f"{path}?q=sales", headers=cookie)
    assert searched.json() == {
        "providers": [{"name": "salesforce", "label": "Salesforce"}],
        "after": None,
    }
    too_long = await client.get(f"{path}?q={'x' * 101}", headers=cookie)
    assert too_long.status_code == 400
    assert too_long.text == "The connector search is too long."
    assert too_long.headers[web_surface.REFUSAL_HEADER] == "1"
    bad_cursor = await client.get(f"{path}?after={'x' * 501}", headers=cookie)
    assert bad_cursor.status_code == 400
    assert bad_cursor.text == "The connector page cursor is invalid."
    assert (await client.get(path)).status_code == 401


def test_added_tiles_carry_the_labels_the_memory_states() -> None:
    """The added tiles: the name is the slug the connect verb dispatches on, the label is what the
    member reads and what the tooling memory writes. Which broker serves each is the catalog-wide
    broker gate's to hold."""
    tiles = {tile.name: tile.label for tile in web_panels.FIRST_RUN_PROVIDERS}
    assert tiles["googlesheets"] == "Google Sheets"
    assert tiles["attio"] == "Attio"
    assert tiles["discord"] == "Discord"


async def test_artifact_objects_list_own_files_with_links_behind_one_fence(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The artifact kind's member listing: a member reads their own conversations' shared files,
    each carrying the signed TTL download link and a signed preview link — a document's off its
    rendered first page, an image's off its own bytes; another member's files never list, and an
    admin stands behind the same fence — audience is not a role, so files shared only in members'
    private conversations are absent for them too."""
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
                (
                    f"artifacts/{uuid4()}/report.pdf",
                    "report.pdf",
                    "application/pdf",
                    f"artifacts/{uuid4()}/report.png",
                ),
                (f"artifacts/{uuid4()}/chart.png", "chart.png", "image/png", None),
            ),
        ),
        (
            member_n,
            "n@example.com",
            ((f"artifacts/{uuid4()}/notes.txt", "notes.txt", "text/plain", None),),
        ),
    )
    minute = 0
    for member_id, email, files in shared:
        _conversation, turn_id = await _seed_web_turn(
            workspace_id, agent_id, member_id, email, TerminalFrame(status="done", text="ok")
        )
        for blob_key, filename, media_type, preview_blob_key in files:
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
                        preview_blob_key=preview_blob_key,
                        preview_media_type=None if preview_blob_key is None else "image/png",
                        preview_size_bytes=None if preview_blob_key is None else 4,
                        created_at=minted + timedelta(minutes=minute),
                        updated_at=sa.func.now(),
                    )
                )
            minute += 1
    newest_first = f"{OBJECT_ARTIFACTS_PATH}?order_by=shared_at&order=desc"
    m_view = (
        await client.get(newest_first, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    ).json()
    assert _names(m_view) == ["chart.png", "report.pdf"]
    assert [entry["media_type"] for entry in m_view["objects"]] == [
        "image/png",
        "application/pdf",
    ]
    assert [entry["media"] for entry in m_view["objects"]] == ["image", "document"]
    assert {entry["owner_email"] for entry in m_view["objects"]} == {"m@example.com"}
    assert all(entry["mine"] for entry in m_view["objects"])
    chart = next(entry for entry in m_view["objects"] if entry["filename"] == "chart.png")
    report = next(entry for entry in m_view["objects"] if entry["filename"] == "report.pdf")
    assert chart["url"].startswith("https://web/")
    assert "/chart.png?exp=" in chart["url"]
    assert "/chart.png?" in chart["preview_url"]
    assert "/report.png?" in report["preview_url"]
    n_view = (
        await client.get(newest_first, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    ).json()
    assert _names(n_view) == ["notes.txt"]
    assert n_view["objects"][0]["preview_url"] is None
    admin_view = (
        await client.get(newest_first, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert _names(admin_view) == []
    anonymous = await client.get(newest_first)
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
            context=(
                TurnContext(sender="n@example.com", source=SLACK_THREAD_PERMALINK)
                if conversation_id == shared_conversation
                else None
            ),
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
    newest_first = f"{OBJECT_ARTIFACTS_PATH}?order_by=shared_at&order=desc"
    listed = (await client.get(newest_first, headers=headers)).json()["objects"]
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
    assert shared["conversation"] == str(shared_conversation)
    assert (shared["surface"], shared["source"]) == ("slack", SLACK_THREAD_PERMALINK)
    assert (report["surface"], report["source"]) == ("web", None)
    assert _names((await client.get(f"{newest_first}&q=report", headers=headers)).json()) == [
        "report.pdf"
    ]
    needle = (await client.get(f"{newest_first}&q=needle", headers=headers)).json()["objects"]
    assert {entry["filename"] for entry in needle} == {
        "shared.png",
        "report.pdf",
    }
    assert _names((await client.get(f"{newest_first}&media=image", headers=headers)).json()) == [
        "shared.png"
    ]
    assert _names((await client.get(f"{newest_first}&media=document", headers=headers)).json()) == [
        "data.csv",
        "report.pdf",
    ]
    assert _names((await client.get(f"{newest_first}&media=other", headers=headers)).json()) == [
        "archive.zip"
    ]
    assert _names((await client.get(f"{newest_first}&media=data", headers=headers)).json()) == []
    assert _names((await client.get(f"{newest_first}&mine=true", headers=headers)).json()) == [
        "archive.zip",
        "data.csv",
        "report.pdf",
    ]
    assert (await client.get(f"{newest_first}&scope=created", headers=headers)).status_code == 400


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


async def test_artifact_objects_collapse_reshares_into_versions(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Two shares of one filename in one conversation are one object — the newest share is its
    current version and the row says how many stand behind it."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    shared_at = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    await _seed_artifacts(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        (("report.md", shared_at), ("report.md", shared_at + timedelta(minutes=1))),
    )
    listed = (
        await client.get(OBJECT_ARTIFACTS_PATH, headers={"cookie": f"{SESSION_COOKIE}={token}"})
    ).json()["objects"]
    assert [entry["filename"] for entry in listed] == ["report.md"]
    assert "2 versions" in listed[0]["summary"]


def _names(payload: dict) -> list[str]:
    return [entry["filename"] for entry in payload["objects"]]


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


async def test_artifact_placement_follows_the_conversation_at_share_time(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    conversation_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=f"{agent_id}/m@example.com/{uuid4().hex}",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    await _seed_scheduled_run(
        workspace_id,
        agent_id,
        conversation_id,
        seq=1,
        text="background",
        fired=fired,
        artifact=("background.md", "text/markdown"),
    )
    await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound="keep future files here",
        speaker_member_id=member_id,
    )
    await _seed_scheduled_run(
        workspace_id,
        agent_id,
        conversation_id,
        seq=3,
        text="in the thread",
        fired=fired + timedelta(minutes=1),
        artifact=("thread.md", "text/markdown"),
    )

    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    listed = (await client.get(OBJECT_ARTIFACTS_PATH, headers=cookie)).json()
    assert _names(listed) == ["thread.md"]

    prefix = conversation_id.hex[:8]
    background = await client.get(
        f"{OBJECT_ARTIFACTS_PATH}/{prefix}-background-md?agent={agent_id}", headers=cookie
    )
    interactive = await client.get(
        f"{OBJECT_ARTIFACTS_PATH}/{prefix}-thread-md?agent={agent_id}", headers=cookie
    )
    assert background.status_code == 404
    assert interactive.status_code == 200

    radar = (await client.get(f"{REPORTS_NEWEST}&agent={agent_id}", headers=cookie)).json()
    assert {
        artifact["filename"] for report in radar["objects"] for artifact in report["artifacts"]
    } == {"background.md", "thread.md"}


async def test_report_objects_list_scheduled_runs_with_output_and_files(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The radar's rows as the report kind lists them: every reader — an admin included — gets
    only the runs of conversations whose content they read, each naming its task, carrying the
    terminal reply and signed download and preview links for the files it shared, and holding the
    digest entry written from what it published — null where the writer has not reached it. A run
    that ended well and shared nothing published nothing and is no row; another member's private
    run, a room's run, and a member turn never list; an agent outside the audience is unknown."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    shared_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="slack/radar",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
        surface_label="#eng",
    )
    task_id = await _seed_radar_task(
        workspace_id,
        agent_id,
        shared_conversation,
        name="morning-digest",
        created_by_member_id=member_m,
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    shared_run = await _seed_scheduled_run(
        workspace_id,
        agent_id,
        shared_conversation,
        key=f"{task_id}:2026-08-14T09:00:00+00:00",
        text="12 items, 2 stale",
        fired=fired,
        artifact=("queue.png", "image/png"),
        context=TurnContext(source="https://acme.slack.com/archives/C42/p1"),
    )
    failed_run = await _seed_scheduled_run(
        workspace_id,
        agent_id,
        shared_conversation,
        seq=2,
        status="failed",
        text="the queue read timed out",
        fired=fired + timedelta(minutes=3),
    )
    await _seed_scheduled_run(
        workspace_id,
        agent_id,
        shared_conversation,
        seq=3,
        text="a quiet fire that published nothing",
        fired=fired + timedelta(minutes=4),
    )
    private_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=f"{agent_id}/n@example.com/{uuid4().hex}",
        audience=str(conversation_audience(member_n)),
        member_id=member_n,
    )
    private_run = await _seed_scheduled_run(
        workspace_id,
        agent_id,
        private_conversation,
        key=f"pause-fired:{uuid4()}",
        text="resumed",
        fired=fired + timedelta(minutes=1),
        artifact=("resumed.md", "text/markdown"),
    )
    room_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="slack/room",
        audience=str(room_audience("slack", "C123")),
        member_id=None,
        surface="slack",
    )
    await _seed_scheduled_run(
        workspace_id,
        agent_id,
        room_conversation,
        text="in a room",
        fired=fired + timedelta(minutes=2),
        artifact=("room.md", "text/markdown"),
    )
    await _seed_web_turn(
        workspace_id,
        agent_id,
        member_n,
        "n@example.com",
        TerminalFrame(status="done", text="typed"),
    )
    await _seed_digest_entry(
        workspace_id,
        shared_run,
        title="The queue holds two stale items",
        summary="Twelve items are queued and two are past their deadline.",
        points=(("Two items are past their deadline", "the queue"), ("Ten are on time", "")),
    )
    await _seed_digest_entry(
        workspace_id,
        private_run,
        title="What n alone reads",
        summary="The run that reported into n's own conversation.",
    )
    m_view = (
        await client.get(REPORTS_NEWEST, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    ).json()
    assert [run["name"] for run in m_view["objects"]] == [str(failed_run), str(shared_run)]
    failed = m_view["objects"][0]
    assert (failed["status"], failed["text"]) == ("failed", "the queue read timed out")
    assert failed["entry"] is None
    run = m_view["objects"][1]
    assert run["task"] == "morning-digest"
    assert "prompt" not in run
    assert run["text"] == ""
    assert run["status"] == "done"
    assert run["surface"] == "slack"
    assert run["source"] == "https://acme.slack.com/archives/C42/p1"
    assert run["conversation"] == str(shared_conversation)
    assert run["agent_id"] == str(agent_id)
    assert [artifact["filename"] for artifact in run["artifacts"]] == ["queue.png"]
    assert run["artifacts"][0]["url"].startswith("https://web/")
    assert "preview" in run["artifacts"][0]["preview_url"]
    assert run["entry"] == {
        "title": "The queue holds two stale items",
        "summary": "Twelve items are queued and two are past their deadline.",
        "points": [
            {"text": "Two items are past their deadline", "actor": "the queue"},
            {"text": "Ten are on time", "actor": ""},
        ],
    }
    n_view = (
        await client.get(REPORTS_NEWEST, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    ).json()
    assert {run["name"] for run in n_view["objects"]} == {
        str(shared_run),
        str(failed_run),
        str(private_run),
    }
    resumed = next(run for run in n_view["objects"] if run["name"] == str(private_run))
    assert resumed["task"] is None
    assert resumed["text"] == ""
    assert resumed["entry"] == {
        "title": "What n alone reads",
        "summary": "The run that reported into n's own conversation.",
        "points": [],
    }
    admin_view = (
        await client.get(REPORTS_NEWEST, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert [run["name"] for run in admin_view["objects"]] == [str(failed_run), str(shared_run)]
    narrowed = (
        await client.get(
            f"{REPORTS_NEWEST}&agent={agent_id}",
            headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
        )
    ).json()
    assert [run["name"] for run in narrowed["objects"]] == [str(failed_run), str(shared_run)]
    unknown = await client.get(
        f"{REPORTS_NEWEST}&agent={uuid4()}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert unknown.status_code == 404
    unanchored = await client.get(
        f"{REPORTS_NEWEST}&cursor=nonsense", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert unanchored.status_code == 400
    anonymous = await client.get(REPORTS_NEWEST)
    assert anonymous.status_code == 401
    retired = await client.get(
        "/surface/web/workspace/radar", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
    )
    assert retired.status_code == 404


async def test_report_object_detail_reads_one_run(web: tuple[AsyncClient, UUID, UUID]) -> None:
    """The permalink read: one run as objects/report/{name}, its fields on the detail's status,
    its conversation on the created_in link, under the same audience fence — a run outside the
    reader's audiences is absent, and a name that is no turn reads the same way."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    private_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key=f"{agent_id}/m@example.com/{uuid4().hex}",
        audience=str(conversation_audience(member_m)),
        member_id=member_m,
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    pinned = await _seed_scheduled_run(
        workspace_id,
        agent_id,
        private_conversation,
        key=f"pin-private:{uuid4()}",
        text="12 items",
        fired=fired,
        artifact=("queue.png", "image/png"),
    )
    await _seed_digest_entry(
        workspace_id,
        pinned,
        title="The queue holds twelve items",
        summary="Twelve items are queued.",
        points=(("Twelve items are queued", "the queue"),),
    )
    answered = await client.get(
        f"{REPORTS_PATH}/{pinned}?agent={agent_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert answered.status_code == 200, answered.text
    detail = answered.json()
    assert detail["name"] == str(pinned)
    assert detail["status"]["entry"]["title"] == "The queue holds twelve items"
    assert detail["status"]["conversation"] == str(private_conversation)
    assert [link["name"] for link in detail["links"]] == [str(private_conversation)]
    fenced = await client.get(
        f"{REPORTS_PATH}/{pinned}?agent={agent_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token_n}"},
    )
    assert fenced.status_code == 404
    nameless = await client.get(
        f"{REPORTS_PATH}/not-a-turn?agent={agent_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert nameless.status_code == 404


async def test_report_objects_page_with_a_cursor(
    web: tuple[AsyncClient, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent-anchored read walks the feed through the object cursor, newest first, each page
    carrying its own rows' entries and links."""
    client, workspace_id, agent_id = web
    _member, token = await _seed_member(workspace_id, "m@example.com")
    conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="slack/radar-pages",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    fired = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    runs = [
        await _seed_scheduled_run(
            workspace_id,
            agent_id,
            conversation,
            seq=index + 1,
            key=f"{uuid4()}:2026-08-14T09:0{index}:00+00:00",
            text="t" * 2_000,
            fired=fired + timedelta(minutes=index),
            artifact=("page.md", "text/markdown"),
        )
        for index in range(3)
    ]
    for index, run in enumerate(runs):
        await _seed_digest_entry(
            workspace_id,
            run,
            title=f"Run {index}",
            summary=f"What run {index} found.",
            points=((f"Point {index}", "the runner"),),
        )
    monkeypatch.setattr(objects_module, "OBJECT_LIST_PAGE", 2)
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    first = (await client.get(f"{REPORTS_NEWEST}&agent={agent_id}", headers=headers)).json()
    assert [run["name"] for run in first["objects"]] == [str(runs[2]), str(runs[1])]
    assert first["objects"][0]["text"] == ""
    assert first["objects"][0]["task"] is None
    assert [run["entry"]["title"] for run in first["objects"]] == ["Run 2", "Run 1"]
    assert first["next_cursor"] is not None
    second = (
        await client.get(
            f"{REPORTS_NEWEST}&agent={agent_id}&cursor={quote(first['next_cursor'])}",
            headers=headers,
        )
    ).json()
    assert [run["name"] for run in second["objects"]] == [str(runs[0])]
    assert second["objects"][0]["entry"] == {
        "title": "Run 0",
        "summary": "What run 0 found.",
        "points": [{"text": "Point 0", "actor": "the runner"}],
    }
    assert second["next_cursor"] is None


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
            manifest=None,
        )
        await sites.register(
            conversation_id,
            "draft",
            3001,
            member_m,
            "private",
            conversation_audience(member_m),
            True,
            manifest=None,
        )
    path = f"/surface/web/objects/site?agent={agent_id}"
    m_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})).json()
    assert sorted(row["name"].split("-")[0] for row in m_view["objects"]) == ["draft", "landing"]
    assert sorted(m_view["fields"]) == [
        "conversation",
        "created_at",
        "deploy_generation",
        "homepage_agent",
        "mine",
        "owner_email",
        "preview_url",
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


async def test_an_app_homepage_is_no_site_the_index_lists(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An app's homepage is the app's own page, not an artifact a member made and shared, so it
    stands in no browse of the workspace's sites — not its creator's, not an admin's — while an
    ordinary workspace site beside it stands in both. By name it answers the agent's audience the
    way the frame does: any member who may open a workspace agent's page may read its site object,
    which is where the edit flow every such member may direct begins."""
    client, workspace_id, agent_id = web
    creator_id, creator_token = await _seed_member(workspace_id, "app-builder@example.com")
    _member_id, member_token = await _seed_member(workspace_id, "app-member@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "app-boss@example.com", admin=True)
    conversation_id, _turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        creator_id,
        "app-builder@example.com",
        TerminalFrame(status="done", text="ok"),
    )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "home",
            3000,
            creator_id,
            "workspace",
            conversation_audience(creator_id),
            True,
            manifest=None,
        )
        await sites.register(
            conversation_id,
            "handbook",
            3001,
            creator_id,
            "workspace",
            conversation_audience(creator_id),
            True,
            manifest=None,
        )
        assert await sites.set_homepage(agent_id, conversation_id, "home") is not None
    path = f"/surface/web/objects/site?agent={agent_id}"
    for token in (creator_token, member_token, admin_token):
        listed = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token}"})).json()
        assert [row["name"].split("-")[0] for row in listed["objects"]] == ["handbook"]

    name = site_object_name(conversation_id, "home")
    detail = f"/surface/web/objects/site/{name}?agent={agent_id}"
    read = await client.get(detail, headers={"cookie": f"{SESSION_COOKIE}={creator_token}"})
    assert read.status_code == 200
    read = await client.get(detail, headers={"cookie": f"{SESSION_COOKIE}={member_token}"})
    assert read.status_code == 200
    opened = await client.get(
        f"/surface/web/agents/{agent_id}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert opened.json()["state"] == "set"


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
                manifest=None,
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


async def test_the_conversation_index_pages_portal_rows_alone(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`portal` is a declared filter, so it narrows the page before the cut: a run of newer rows
    from other surfaces can never render an empty chat list."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    portal_row, _turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_m,
        "m@example.com",
        TerminalFrame(status="done", text="ok"),
        title="Portal thread",
    )
    await _seed_web_turn(
        workspace_id,
        agent_id,
        member_m,
        "m@example.com",
        TerminalFrame(status="done", text="ok"),
        title="Slack thread",
        surface="slack",
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token_m}"}
    base = "/surface/web/objects/conversation?order_by=last_at&order=desc"

    whole = (await client.get(base, headers=cookie)).json()
    narrowed = (await client.get(base + "&portal=true", headers=cookie)).json()

    assert {row["title"] for row in whole["objects"]} == {"Portal thread", "Slack thread"}
    assert [row["name"] for row in narrowed["objects"]] == [str(portal_row)]
    assert narrowed["objects"][0]["portal"] is True


async def test_a_fanned_out_index_walks_every_agent_on_one_token(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An agent-less index continues: the token carries one kind cursor per agent still walking,
    the next read resumes each agent's own walk, an agent whose rows ran out leaves the token, and
    a token this route never minted is refused."""
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="scout",
                prompt="be brief",
                model="claude-opus-4-8",
                reasoning="high",
                icon="binoculars",
                is_main=False,
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    short = 10
    for index in range(OBJECT_LIST_PAGE + 1):
        await _seed_web_turn(
            workspace_id,
            agent_id,
            member_m,
            "m@example.com",
            TerminalFrame(status="done", text="ok"),
            title=f"deep {index:03d}",
        )
    for index in range(short):
        await _seed_web_turn(
            workspace_id,
            second_agent,
            member_m,
            "m@example.com",
            TerminalFrame(status="done", text="ok"),
            title=f"shallow {index:02d}",
        )
    cookie = {"cookie": f"{SESSION_COOKIE}={token_m}"}
    base = "/surface/web/objects/conversation?order_by=title&order=asc"

    first = (await client.get(base, headers=cookie)).json()
    assert len(first["objects"]) == OBJECT_LIST_PAGE + short
    assert first["next_cursor"]
    second = (await client.get(base + "&cursor=" + first["next_cursor"], headers=cookie)).json()
    assert len(second["objects"]) == 1
    assert second["next_cursor"] is None
    walked = [row["name"] for row in first["objects"] + second["objects"]]
    assert len(set(walked)) == OBJECT_LIST_PAGE + 1 + short
    assert {row["agent_id"] for row in second["objects"]} == {str(agent_id)}

    stale = await client.get(base + "&cursor=deadbeef", headers=cookie)
    assert stale.status_code == 400
    assert "cursor" in stale.text


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


async def test_the_rail_lists_readable_conversations_with_the_surface_they_came_in_on(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A row names its surface and the surface's own name for it as two fields. Collapsed to one
    the `#ops` case is lossy — the rail would read `#ops` with nothing saying it is Slack — and the
    CLI, which names nothing, would have only its registered name to draw."""
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
    cli_id = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="tty:1",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="ufo",
    )
    await _seed_listed_turn(workspace_id, cli_id, agent_id, seq=1, inbound="Deploy the branch")
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

    listed = {
        row["name"]: row
        for row in await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={member_token}"})
    }
    assert set(listed) == {str(slack_id), str(cli_id)}
    assert listed[str(slack_id)]["title"] == "Review this Slack message"
    assert listed[str(slack_id)]["surface"] == "slack"
    assert listed[str(slack_id)]["surface_label"] == "Direct message"
    assert listed[str(cli_id)]["title"] == "Deploy the branch"
    assert listed[str(cli_id)]["surface"] == "ufo"
    assert listed[str(cli_id)]["surface_label"] is None

    assert await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={other_token}"}) == []
    assert await _rail_rows(client, {"cookie": f"{SESSION_COOKIE}={admin_token}"}) == []


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
        context=TurnContext(sender="Robin Vale (owner@example.com)", source=SLACK_THREAD_PERMALINK),
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
        "source": SLACK_THREAD_PERMALINK,
        "speakers": ["Robin Vale (owner@example.com)"],
        "turn_count": 1,
        "created_at": target["created_at"],
        "last_turn_at": target["last_turn_at"],
        "readable": True,
        "disclosable": False,
        "commentable": True,
    }


async def test_web_comments_continue_slack_and_terminal_conversations_and_notify_their_threads(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    slack_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1.0",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    terminal_conversation = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="terminal-1",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface="ufo",
    )
    await _seed_listed_turn(
        workspace_id,
        slack_conversation,
        agent_id,
        seq=1,
        inbound="from Slack",
        speaker_member_id=member_id,
        context=TurnContext(sender="Robin Vale (owner@example.com)"),
    )
    await _seed_listed_turn(
        workspace_id,
        terminal_conversation,
        agent_id,
        seq=1,
        inbound="from terminal",
        speaker_member_id=member_id,
        context=TurnContext(sender="owner@example.com"),
    )

    for conversation_id, author in (
        (slack_conversation, "Robin Vale"),
        (terminal_conversation, "You"),
    ):
        STREAM_GATE.arm()
        posted = await client.post(
            f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
            content=b"follow up",
            headers=cookie,
        )

        assert posted.status_code == 200
        turn_id = UUID(posted.json()["turn_id"])
        async with workspace_tx() as connection:
            comment = (
                await connection.execute(
                    sa.select(
                        tables.mid_turn_reply.c.round_index,
                        tables.mid_turn_reply.c.text,
                    ).where(tables.mid_turn_reply.c.turn_id == turn_id)
                )
            ).one()
            context = (
                await connection.execute(
                    sa.select(tables.turn.c.context).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
        url = f"https://web/surface/web#/c/{conversation_id}"
        assert (comment.round_index, comment.text) == (
            SURFACE_COMMENT_ROUND_INDEX,
            f"{author} [commented]({url}): follow up",
        )
        assert TurnContext.model_validate(context).source == f"{url} (owner@example.com)"
        await _consume(client, token, str(turn_id))

    listed = await client.get(f"/surface/web/agents/{agent_id}/conversations", headers=cookie)
    rows = {row["id"]: row for row in listed.json()["conversations"]}
    assert rows[str(slack_conversation)]["commentable"] is True
    assert rows[str(terminal_conversation)]["commentable"] is True


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
    assert [row["name"] for row in await _rail_rows(client, cookie)] == [
        landed.json()["conversation_id"]
    ]


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


async def test_web_stream_names_the_standing_connect_and_mints_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    flow = ConnectFlow(
        providers={"acme": ConnectProvider(provider="acme")},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
        labels={"acme": "Acme CRM"},
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
                inbound="connect acme",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(provider="acme", requester_member_id=member_id),
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
    assert connect_data == {"provider": "acme", "label": "Acme CRM", "turn": str(turn_id)}
    assert lines.index("event: connect") < lines.index("event: terminal")
    # The act is the asking member's, as the transcript draws it: a request another member spoke
    # names no act here either, since the grant would land on whoever pressed.
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                terminal=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(provider="acme", requester_member_id=uuid4()),
                ).model_dump(mode="json")
            )
            .where(tables.turn.c.id == turn_id)
        )
    theirs = await client.get(
        f"/surface/web/turns/{turn_id}/stream",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert "event: connect" not in theirs.text
    # Nothing is minted for a control nobody has pressed: the state a consent URL carries is signed
    # for minutes, and one drawn into the reply would be spent before the member reached it.
    assert "oauth.example.test" not in response.text
    async with workspace_tx() as connection:
        memoized_url = (
            await connection.execute(
                sa.select(tables.turn.c.connect_authorization_url).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).scalar_one()
    assert memoized_url is None


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
                routes_ingress=True,
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
        ("admin@example.com", True, True),
        ("member@example.com", False, True),
    }
    assert "seats" not in payload
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
        ("imessage", "0.1.0", False),
        ("report_digest", "0.1.0", False),
        ("scheduled_tasks", "0.1.0", False),
        ("sites", "0.1.0", False),
        ("sources", "0.1.0", False),
        ("stub", "0", False),
        ("todos", "0.1.0", False),
        ("web", "0.1.0", False),
    ]


async def test_admin_view_reports_a_member_whose_seat_an_admin_revoked(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Unseating is the one way an admin removes a person's access, so the page that offers it has
    to state which members currently hold a seat — a revoked seat that reads the same as a held
    one leaves the admin with no way to see the act landed."""
    client, workspace_id, _agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    await _seed_member(workspace_id, "member@example.com")
    async with workspace_tx() as connection:
        await Seats(workspace_id).revoke(connection, "member@example.com")
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert view.status_code == 200
    payload = view.json()
    assert {(m["email"], m["seated"]) for m in payload["members"]} == {
        ("admin@example.com", True),
        ("member@example.com", False),
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


def test_an_apps_tree_loads_whole_and_names_its_slugs_by_directory(tmp_path: Path) -> None:
    """Every file of the tree is carried whatever its suffix — the ingress serving the tree holds
    the media-type table over it, so `.html`, which this module's own asset table has no entry for,
    reaches the store like everything else. A slug is a top-level directory other than the shared
    `assets/`, so nothing reads a name out of an extension or a skill."""
    _write_apps_tree(tmp_path)

    apps = web_surface.load_apps(tmp_path)

    assert apps is not None
    assert apps.slugs == {"radar", "wiki"}
    assert ".html" not in web_surface.ASSET_MEDIA_TYPES
    assert apps.files["radar/index.html"] == b"<script src=/assets/radar-A1.js>"
    assert apps.files["assets/pages-C3.css"] == b":root{}"
    assert len(apps.files) == 6


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


def test_the_apps_digest_is_the_content_of_the_whole_tree(tmp_path: Path) -> None:
    """The digest names the fleet prefix the tree publishes under and folds to the generation the
    homepage read reports, so it has to be derived from content alone: two pods building the same
    bytes agree, a byte changed anywhere is a new digest, and a file added or removed is too."""
    first, second = tmp_path / "first", tmp_path / "second"
    _write_apps_tree(first)
    _write_apps_tree(second)
    built = web_surface.load_apps(first)
    same = web_surface.load_apps(second)
    assert built is not None and same is not None
    assert built.digest == same.digest
    assert len(built.digest) == 16

    (second / "assets/radar-A1.js").write_text("r2()")
    changed = web_surface.load_apps(second)
    assert changed is not None and changed.digest != built.digest

    (second / "assets/radar-A1.js").write_text("r()")
    (second / "assets/extra-E5.js").write_text("")
    added = web_surface.load_apps(second)
    assert added is not None and added.digest != built.digest


def test_a_deploy_that_built_no_app_pages_holds_no_bundle(tmp_path: Path) -> None:
    """An unbuilt tree loads the extension: the routes that hold no built asset go on working and
    the ones that need the tree name the build. So the load answers None rather than raising, both
    for a directory the build never created and for one it left empty."""
    assert web_surface.load_apps(tmp_path / "never-built") is None
    (tmp_path / "empty").mkdir()
    assert web_surface.load_apps(tmp_path / "empty") is None


def test_the_shipped_apps_tree_is_a_page_per_app_and_the_chunks_they_name() -> None:
    """The built-in apps as this deploy ships them: each slug one directory holding the page
    the frame origin serves at `/`, every chunk that page names present under `assets/`, and nothing
    else anywhere in the tree — the fork project is assembled by the site kind out of the app
    extension's own source and the SDK the sites extension ships, so a byte a browser never fetches
    does not ride this digest."""
    apps = web_surface.APPS
    assert apps is not None, f"the app pages are not built — run `{PORTAL_BUILD}`"
    assert apps.slugs == {
        "artifacts",
        "chat",
        "code",
        "issues",
        "meetings",
        "metrics",
        "radar",
        "wiki",
    }
    for slug in sorted(apps.slugs):
        page = apps.files[f"{slug}/index.html"].decode()
        named = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page)
        assert named, slug
        for ref in named:
            assert ref.removeprefix("/") in apps.files, ref
    assert {path for path in apps.files if not path.startswith("assets/")} == {
        f"{slug}/index.html" for slug in apps.slugs
    }


RUM_DEPLOY = {
    "UFO_WEB_RUM_APPLICATION_ID": "1ea7beef-0000-4000-8000-000000000001",
    "UFO_WEB_RUM_CLIENT_TOKEN": "pubdeadbeef",
    "UFO_WEB_RUM_SITE": "us5.datadoghq.com",
    "UFO_WEB_RUM_ENV": "testing",
    "UFO_WEB_RUM_VERSION": "abc12345",
}
RUM_SLOT = '<script type="application/json" id="rum">null</script>'


def test_a_deploy_naming_no_rum_application_records_no_session() -> None:
    assert rum_config({}) is None


def test_a_deploy_naming_a_rum_application_states_every_field_the_page_reads() -> None:
    assert rum_config(RUM_DEPLOY) == {
        "applicationId": "1ea7beef-0000-4000-8000-000000000001",
        "clientToken": "pubdeadbeef",
        "site": "us5.datadoghq.com",
        "env": "testing",
        "version": "abc12345",
    }


def test_a_half_configured_recording_is_refused_by_the_variables_it_left_unset() -> None:
    """Recording against an application the deploy half-names reaches the wrong application or
    none, and nobody looks for the sessions that never arrived — so the page is never served."""
    named = dict(RUM_DEPLOY)
    del named["UFO_WEB_RUM_CLIENT_TOKEN"]
    named["UFO_WEB_RUM_ENV"] = "  "

    with pytest.raises(RuntimeError, match="UFO_WEB_RUM_CLIENT_TOKEN, UFO_WEB_RUM_ENV"):
        rum_config(named)


def test_the_shell_carries_the_recording_configuration_in_the_block_the_page_declares() -> None:
    shell = portal_shell(f"<head>{RUM_SLOT}</head>", rum_config(RUM_DEPLOY))

    assert '"site": "us5.datadoghq.com"' in shell
    assert shell.count("</script>") == 1


def test_a_configured_value_cannot_close_the_block_it_is_written_into() -> None:
    shell = portal_shell(f"<head>{RUM_SLOT}</head>", {"env": "</script><script>alert(1)"})

    assert "<script>alert(1)" not in shell
    assert "\\u003c/script>\\u003cscript>alert(1)" in shell
    assert shell.count("</script>") == 1


def test_a_build_that_declares_no_rum_block_is_refused_rather_than_served() -> None:
    """A page with no block reads no configuration and records nothing, whatever the deploy sets."""
    with pytest.raises(RuntimeError, match="declares no rum block"):
        portal_shell("<head></head>", rum_config(RUM_DEPLOY))


async def test_the_portal_page_states_the_recording_configuration_of_its_deploy(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One image serves every deploy, so which application a session reaches is read from the
    deploy at the page rather than built into the bundle."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    for name, value in RUM_DEPLOY.items():
        monkeypatch.setenv(name, value)

    shell = await client.get("/surface/web", headers=cookie)

    assert shell.status_code == 200
    held = re.search(r'id="rum">(.*?)</script>', shell.text)
    assert held is not None
    assert json.loads(held[1]) == rum_config(RUM_DEPLOY)


INLINE_ASSET = "data:"
URL_REFERENCE = re.compile(r"""url\((?:"([^"]*)"|'([^']*)'|([^)]*))\)""")


async def test_every_asset_the_portal_references_is_served_from_the_surface_itself(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The page names no origin but this surface. Splitting the stylesheet out of the page makes
    the absence of an external URL insufficient on its own — a page referencing a file nobody
    serves carries no external origin either — so every `src` and `href` it names must resolve
    under the surface's own static path and answer with the media type its element expects. The
    stylesheet's own `url()` references are the same claim one level down: the brand faces are
    named there rather than in the page, and a font nobody serves degrades to a fallback family
    silently instead of failing a load. A reference the build inlined carries its own bytes and
    reaches no origin at all, so it is the sheet rather than something the surface serves.
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

    sheet = next(ref for ref in assets if ref.endswith(".css"))
    styles = (await client.get(sheet, headers=cookie)).text
    faces = {quoted or single or bare for quoted, single, bare in URL_REFERENCE.findall(styles)}
    served = {ref for ref in faces if not ref.startswith(INLINE_ASSET)}
    assert served, "the stylesheet names no font file"
    assets |= served

    for ref in sorted(assets):
        assert ref.startswith("/surface/web/static/"), ref
        assert (await client.get(ref)).status_code == 401, ref

        signed_in = await client.get(ref, headers=cookie)
        assert signed_in.status_code == 200
        expected = ASSET_MEDIA_TYPES[Path(ref).suffix]
        assert signed_in.headers["content-type"].startswith(expected)
        assert signed_in.content.strip()

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
    assert shell.text == PORTAL_FILE.read_text()
    assert shell.headers["cache-control"] == "no-store"
    client.cookies.clear()
    tokenless = await client.post("/surface/web", data={})
    assert tokenless.status_code == 401
    assert "Domain" not in cookie


async def test_static_assets_publish_on_the_first_page_and_serve_from_the_store(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC 0031: the first page a pod serves waits for its built assets to land in the shared
    store, and the asset route answers a name outside its own build from that store with the same
    ETag semantics as a local asset — so a page from one build resolves on a pod running another.
    A name outside the published shape, an undeclared suffix, and an unknown hash stay 404, and
    the route stays session-gated."""
    client, workspace_id, _agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    monkeypatch.setattr(web_surface, "_ASSET_PUBLISH", None)
    web_surface._STORED_ASSETS.clear()
    token = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}

    fleet = FleetBlobStore(backend=blob.backend)
    page = await client.get("/surface/web", headers=headers)
    assert page.status_code == 200
    assert web_surface.STATIC_ASSETS
    for name in web_surface.STATIC_ASSETS:
        assert await fleet.exists("static/web/" + name)

    await fleet.put("static/web/assets/peer-AbC123.js", b"export const peer = 1;\n")
    path = "/surface/web/static/assets/peer-AbC123.js"
    served = await client.get(path, headers=headers)
    assert served.status_code == 200
    assert served.text == "export const peer = 1;\n"
    assert served.headers["content-type"].startswith("text/javascript")
    assert served.headers["cache-control"] == "no-cache"
    revalidated = await client.get(
        path, headers={**headers, "if-none-match": served.headers["etag"]}
    )
    assert revalidated.status_code == 304
    missing = await client.get("/surface/web/static/assets/gone-XYZ.js", headers=headers)
    assert missing.status_code == 404
    undeclared = await client.get("/surface/web/static/assets/peer-AbC123.map", headers=headers)
    assert undeclared.status_code == 404
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


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


async def test_a_clicked_conversation_survives_the_sign_in_it_lands_in(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `view on web` click by a signed-out member keeps its target: the conversation names itself
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


async def test_transcript_reply_carries_the_question_it_asked(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A question rides the reply that asked it, so the portal draws it under those words rather
    than at the foot of the pane."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, asked_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="one question", question=QUESTION),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {asked_turn}\n</context>\nDeploy it.",
                ),
                Message(role="assistant", content="one question"),
            ),
        ),
    )
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    reply = loaded.json()["messages"][-1]
    assert reply["role"] == "assistant"
    assert reply["text"] == "one question"
    assert reply["question"]["turn_id"] == str(asked_turn)
    assert reply["question"]["title"] == "Pick a deploy window"
    assert [entry["question"] for entry in reply["question"]["questions"]] == [
        "When should the deploy run?",
        "Page the on-call?",
    ]


async def test_an_answered_ask_states_its_answers_on_its_own_card_and_draws_no_bubble(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The record of an ask is the card it was made on. The reply that asked carries what the member
    answered, entry by entry, and states itself closed once a later turn has superseded the ask — so
    the answers stand under the questions they answer and offer no control the conversation cannot
    take. Those same words draw no bubble of their own: an answer is recognized by the key it
    admitted under, so an ordinary message the member typed still draws one."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, asked_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="one question", question=QUESTION),
    )
    window = "Now · When should the deploy run?"
    page = "Yes · Page the on-call?"
    first = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound=window,
        speaker_member_id=member_id,
        idempotency_key=_answer_key(conversation_id, asked_turn, 0),
    )
    second = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=3,
        inbound=page,
        speaker_member_id=member_id,
        idempotency_key=_answer_key(conversation_id, asked_turn, 1),
    )
    said = await _seed_listed_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=4,
        inbound="and hold the release notes",
        speaker_member_id=member_id,
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {asked_turn}\n</context>\nDeploy it.",
                ),
                Message(role="assistant", content="one question"),
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {first}\n</context>\n{window}",
                ),
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {second}\n</context>\n{page}",
                ),
                Message(role="assistant", content="Deploying tonight."),
                Message(
                    role="user",
                    content=(
                        f"<context>\nmessage_ref: {said}\n</context>\nand hold the release notes"
                    ),
                ),
            ),
        ),
    )
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    messages = loaded.json()["messages"]
    assert [message["text"] for message in messages if message["role"] == "user"] == [
        "Deploy it.",
        "and hold the release notes",
    ]
    (card,) = [message["question"] for message in messages if "question" in message]
    asking = next(message for message in messages if message.get("question") == card)
    assert asking["text"] == "one question"
    assert card["turn_id"] == str(asked_turn)
    assert card["closed"] is True
    assert card["answered"] == {"0": window, "1": page}


async def test_a_reload_while_the_answers_run_states_them_on_the_card_alone(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A reload between the submit and the reply the answers opened. One answer founded the running
    turn and the next folded onto its queue, so the two live in the two id spaces a transcript names
    a message by — the key each admitted under puts both inside the card that asked, and neither
    stands as a bubble under it. A message the member typed beside them still does, and still says
    it is waiting on the turn."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    conversation_id, asked_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="one question", question=QUESTION),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {asked_turn}\n</context>\nDeploy it.",
                ),
                Message(role="assistant", content="one question"),
            ),
        ),
    )
    window = "Now · When should the deploy run?"
    page = "Yes · Page the on-call?"
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
                inbound=window,
                speaker_member_id=member_id,
                idempotency_key=_answer_key(conversation_id, asked_turn, 0),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _seed_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=1,
        body=page,
        admission_source="member",
        speaker_member_id=member_id,
        idempotency_key=_answer_key(conversation_id, asked_turn, 1),
    )
    folded = await client.post(
        f"/surface/web/agents/{agent_id}/chat?conversation={conversation_id}",
        content=b"and hold the release notes",
        headers=cookie,
    )
    assert folded.status_code == 200

    reloaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )

    assert reloaded.status_code == 200
    payload = reloaded.json()
    assert payload["turn"] == str(running)
    assert [message["text"] for message in payload["messages"]] == [
        "Deploy it.",
        "one question",
        "and hold the release notes",
    ]
    card = payload["messages"][1]["question"]
    assert card["turn_id"] == str(asked_turn)
    assert card["closed"] is True
    assert card["answered"] == {"0": window, "1": page}
    assert payload["messages"][2]["arrival_id"] == folded.json()["arrival_id"]


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


async def test_a_landed_connect_settles_on_the_reply_that_asked_for_it(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The control rides the reply that asked, so it stands where the words that named it are —
    while later turns run, and once the connect has landed, when it states the account it made
    rather than an act to press again."""
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
    read = f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}"
    try:
        pending = await client.get(read, headers=cookie)
        # A later turn in the same conversation leaves the control where it was asked for.
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=2,
                    status="done",
                    inbound="anything else",
                    speaker_member_id=member_id,
                    terminal=TerminalFrame(status="done", text="Sure.").model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        after_turn = await client.get(read, headers=cookie)
        # What the callback writes when the grant lands, stamped with the conversation that asked.
        with ws(workspace_id), bind_agent(agent_id):
            await GrantStore().record(
                provider="github",
                account_id="github-account",
                host="api.github.test",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
                account_label="Work account",
                landed_turn_id=turn_id,
            )
        settled = await client.get(read, headers=cookie)
        # A colleague's own connect on the same provider settles nothing here: the grant lands on
        # whoever presses, so this member's request is still theirs to press.
        colleague_id, _colleague_token = await _seed_member(workspace_id, "peer@example.com")
        second_conversation, second_turn = await _seed_web_turn(
            workspace_id,
            agent_id,
            member_id,
            "owner@example.com",
            TerminalFrame(
                status="done",
                text="Use the connection control.",
                connect_request=ConnectRequest(provider="asana", requester_member_id=member_id),
            ),
        )
        await _write_transcript(
            blob,
            second_conversation,
            Conversation(
                seq=1,
                messages=(
                    Message(
                        role="user",
                        content=(
                            f"<context>\nmessage_ref: {second_turn}\n</context>\nconnect asana"
                        ),
                    ),
                    Message(role="assistant", content="Use the connection control."),
                ),
            ),
        )
        with ws(workspace_id), bind_agent(agent_id):
            await GrantStore().record(
                provider="asana",
                account_id="asana-account",
                host="api.asana.test",
                grantor_member_id=colleague_id,
                conversation_id=second_conversation,
                shared=True,
                account_label="Their account",
            )
        # A second account on a provider this member already connected: the request that asked for
        # it is its own, and reads as open until its own connect lands.
        third_conversation, third_turn = await _seed_web_turn(
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
            third_conversation,
            Conversation(
                seq=1,
                messages=(
                    Message(
                        role="user",
                        content=(
                            f"<context>\nmessage_ref: {third_turn}\n</context>\nconnect github"
                        ),
                    ),
                    Message(role="assistant", content="Use the connection control."),
                ),
            ),
        )
        second_account = await client.get(
            f"/surface/web/agents/{agent_id}/transcript?conversation={third_conversation}",
            headers=cookie,
        )
        # That request's own connect lands. The account it reaches is one the member already held,
        # so the connection row still names the conversation that first made it — the stamp on this
        # turn is what settles this reply.
        with ws(workspace_id), bind_agent(agent_id):
            await GrantStore().record(
                provider="github",
                account_id="github-account",
                host="api.github.test",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=True,
                account_label="Work account",
                landed_turn_id=third_turn,
            )
        reconnected = await client.get(
            f"/surface/web/agents/{agent_id}/transcript?conversation={third_conversation}",
            headers=cookie,
        )
        theirs = await client.get(
            f"/surface/web/agents/{agent_id}/transcript?conversation={second_conversation}",
            headers=cookie,
        )
    finally:
        install_connect_flow(None)

    asked = _drawn_connect(pending)
    assert asked == {"provider": "github", "label": "GitHub", "turn": str(turn_id)}
    assert _drawn_connect(after_turn) == asked
    landed = _drawn_connect(settled)
    assert landed == {"provider": "github", "label": "GitHub", "account": "Work account"}
    assert "connect" not in settled.json()
    mine = _drawn_connect(theirs)
    assert mine == {"provider": "asana", "label": "Asana", "turn": str(second_turn)}
    again = _drawn_connect(second_account)
    assert again == {"provider": "github", "label": "GitHub", "turn": str(third_turn)}
    settled_again = _drawn_connect(reconnected)
    assert settled_again == {"provider": "github", "label": "GitHub", "account": "Work account"}


async def test_the_press_mints_the_consent_and_sends_the_window_to_the_provider(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The chip's address is this surface's own, and the press is what mints: the window lands on
    the provider by redirect, a second press hours later mints again rather than sending the member
    to a state the callback would refuse, and the request belongs to nobody but who asked."""
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
    press = f"/surface/web/turns/{turn_id}/connect"
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    _peer_id, peer_token = await _seed_member(workspace_id, "peer@example.com", admin=True)
    try:
        opened = await client.get(press, headers=cookie, follow_redirects=False)
        again = await client.get(press, headers=cookie, follow_redirects=False)
        async with workspace_tx() as connection:
            clock = (
                "datetime('now','-11 minutes')"
                if connection.dialect.name == "sqlite"
                else "now() - interval '11 minutes'"
            )
            await connection.execute(
                sa.update(tables.turn)
                .values(connect_authorized_at=sa.text(clock))
                .where(tables.turn.c.id == turn_id)
            )
        later = await client.get(press, headers=cookie, follow_redirects=False)
        stolen = await client.get(
            press,
            headers={"cookie": f"{SESSION_COOKIE}={peer_token}"},
            follow_redirects=False,
        )
    finally:
        install_connect_flow(None)
    assert opened.status_code == 303
    assert opened.headers["location"].startswith("https://oauth.example.test/authorize")
    # One press, one consent: a second press inside the window sends the member to the same page.
    assert again.headers["location"] == opened.headers["location"]
    assert later.status_code == 303
    assert later.headers["location"] != opened.headers["location"]
    assert stolen.status_code in (403, 404)
    unbrokered = await client.get(press, headers=cookie, follow_redirects=False)
    assert unbrokered.status_code == 404
    assert web_surface.CONNECT_ASK_AGAIN in unbrokered.text


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
    await _write_transcript(
        blob,
        child_conversation,
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
                    content=(
                        ToolResultBlock(
                            tool_use_id="call-1",
                            content="…",
                            activity=True,
                            activity_text="Fetching the requested page.",
                        ),
                    ),
                ),
            ),
        ),
    )
    grandchild_conversation, _grandchild_turn = await _seed_subagent(
        workspace_id, agent_id, member_id, child_turn, profile="deep_research"
    )

    events = dict(await _collect_events(client, token, turn_id))

    assert events["subagent"] == {
        "profile": "general_purpose",
        "name": "",
        "conversation_id": str(child_conversation),
        "events": [{"kind": "activity", "text": "Fetching the requested page."}],
        "output": "It shipped Tuesday.",
        "subagents": [
            {
                "profile": "deep_research",
                "name": "",
                "conversation_id": str(grandchild_conversation),
                "events": [],
                "output": "",
                "subagents": [],
            }
        ],
    }


async def test_shared_files_stream_and_reload_as_download_links(
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
        TerminalFrame(status="done", text="here is the report"),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nshare the report",
                ),
                Message(role="assistant", content="here is the report"),
            ),
        ),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=f"artifacts/{uuid4()}/report.md",
                workspace_id=workspace_id,
                filename="report.md",
                subject="the report",
                media_type="text/markdown",
                size_bytes=3,
                preview_blob_key=f"artifacts/{uuid4()}/report.png",
                preview_media_type="image/png",
                preview_size_bytes=7,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
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
    events = dict(await _collect_events(client, token, turn_id))
    streamed = {file["filename"]: file for file in events["files"]["files"]}
    assert streamed["report.md"]["size_bytes"] == 3
    assert streamed["report.md"]["media_type"] == "text/markdown"
    assert streamed["report.md"]["url"].startswith("https://web/artifacts/")
    assert streamed["report.md"]["preview_url"].startswith("https://web/artifacts/")
    assert "&preview=" in streamed["report.md"]["preview_url"]
    assert streamed["portrait.jpg"]["media_type"] == "image/jpeg"
    assert streamed["portrait.jpg"]["preview_url"].startswith("https://web/artifacts/")
    assert "&preview=" in streamed["portrait.jpg"]["preview_url"]
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    payload = loaded.json()
    assert "files" not in payload
    reply = payload["messages"][-1]
    assert reply["role"] == "assistant"
    reloaded = {file["filename"]: file for file in reply["files"]}
    assert reloaded["report.md"]["url"].startswith("https://web/artifacts/")
    assert reloaded["report.md"]["size_bytes"] == 3
    assert reloaded["report.md"]["media_type"] == "text/markdown"
    assert reloaded["report.md"]["preview_url"].startswith("https://web/artifacts/")
    assert reloaded["portrait.jpg"]["media_type"] == "image/jpeg"
    assert reloaded["portrait.jpg"]["preview_url"].startswith("https://web/artifacts/")


FRAMED_PAGE_ORIGIN = "https://siwnfzm3trn3jahsxigamfhuh56n74adgc6q.sites.example/"
"""The origin an app page is framed on: a site label of its own, never the app host. A picture link
is read from here in `test_chat_pictures_resolve_from_a_page_framed_on_its_own_origin`."""


async def test_chat_pictures_resolve_from_a_page_framed_on_its_own_origin(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """An app page draws this chat framed on a site origin of its own, so every picture link a file
    carries names the host that serves it. Resolved from that page, a link without its base
    addresses the site — where the ingress answers out of a bundle manifest that holds no picture —
    so the file the turn shared and the one the member attached are both read here as that page
    reads them."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="here is the portrait"),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=1,
            messages=(
                Message(
                    role="user",
                    content=(
                        f"<context>\nmessage_ref: {turn_id}\n</context>\nwhat is this\n\n"
                        "[Attached files, saved in the workspace: web-inbox/lights.png]"
                    ),
                ),
                Message(role="assistant", content="here is the portrait"),
            ),
        ),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
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
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert loaded.status_code == 200
    drawn = [
        file
        for message in loaded.json()["messages"]
        for file in message.get("files", ())
        if file["preview_url"] is not None
    ]
    assert {file["filename"] for file in drawn} == {"lights.png", "portrait.jpg"}
    for file in drawn:
        assert urljoin(FRAMED_PAGE_ORIGIN, file["preview_url"]) == file["preview_url"]
        assert file["preview_url"].startswith("https://web/")


async def test_a_created_app_streams_and_reloads_as_a_card_on_the_reply_that_made_it(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The turn that created an application names it on its terminal, so the stream draws the card
    on the reply that made it and a reload draws the same card in the same place. A kind the chat
    has no card for is passed over, and a turn that created nothing — one that only updated an
    app — draws none."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    digest_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=digest_id,
                workspace_id=workspace_id,
                name="daily-digest",
                prompt="summarise the day",
                model="claude-sonnet-5",
                icon="notebook",
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(
            status="done",
            text="daily-digest is set up.",
            created=(
                ObjectRef(kind="agent", name="daily-digest"),
                ObjectRef(kind="scheduled_task", name="every-morning"),
            ),
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
                    content=f"<context>\nmessage_ref: {turn_id}\n</context>\nbuild me a digest",
                ),
                Message(role="assistant", content="daily-digest is set up."),
            ),
        ),
    )
    events = dict(await _collect_events(client, token, turn_id))
    assert events["apps"] == {
        "apps": [
            {
                "id": str(digest_id),
                "name": "daily-digest",
                "model": "claude-sonnet-5",
                "icon": "notebook",
            }
        ]
    }
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=cookie,
    )
    payload = loaded.json()
    assert "apps" not in payload
    reply = payload["messages"][-1]
    assert reply["role"] == "assistant"
    assert reply["apps"] == events["apps"]["apps"]

    _second, updated_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="daily-digest now runs at 07:00."),
    )
    assert "apps" not in dict(await _collect_events(client, token, updated_turn))


async def test_a_created_app_streams_as_a_card_on_the_turn_that_created_it(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The member opens the stream while the turn is still running, so the application the turn
    creates is a row that lands after the open — and the card is still drawn, on an application
    private to the member who asked for it. The gate is armed, so the first delta publishes only
    once the tail drains: the row is written after the stream is open, not before."""
    client, workspace_id, agent_id = web
    _config, hub, _blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=False)
    conversation_id, _first = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="Ready."),
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
                inbound="create an app for support",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    STREAM_GATE.arm()
    tailing = asyncio.ensure_future(_collect_events(client, token, running))
    await hub.publish(running, TextDelta(text="support-desk is set up."))
    desk_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=desk_id,
                workspace_id=workspace_id,
                name="support-desk",
                prompt="work the support inbox",
                model="claude-sonnet-5",
                icon="notebook",
                visibility="private",
                owner_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await hub.publish(
        running,
        Terminal(
            frame=TerminalFrame(
                status="done",
                text="support-desk is set up.",
                created=(ObjectRef(kind="agent", name="support-desk"),),
            )
        ),
    )
    events = dict(await tailing)
    assert events["apps"] == {
        "apps": [
            {
                "id": str(desk_id),
                "name": "support-desk",
                "model": "claude-sonnet-5",
                "icon": "notebook",
            }
        ]
    }


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


def test_words_of_their_own_carry_no_attachments() -> None:
    """Only the note admission wrote is read as one: words that merely mention files stay words."""
    assert web_surface._member_attachments("read web-inbox/notes.txt for me") == (
        "read web-inbox/notes.txt for me",
        (),
    )


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


async def test_preview_returns_the_rendered_png(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composer uploads one file and gets back the picture the preview service produced —
    nothing stored, no turn admitted. The render itself is the service's, stubbed here; the route's
    auth, kind check, and delegation are what this proves."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")

    async def _rendered(self: SurfaceContext, kind: str, data: bytes) -> bytes:
        assert kind == "pdf"
        return b"\x89PNGrendered"

    monkeypatch.setattr(SurfaceContext, "render_preview", _rendered)
    got = await client.post(
        "/surface/web/preview",
        files=[("file", ("report.pdf", b"%PDF-1.7", "application/pdf"))],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.content == b"\x89PNGrendered"


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


async def test_preview_without_a_session_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, _workspace_id, _agent_id = web
    unauth = await client.post(
        "/surface/web/preview",
        files=[("file", ("report.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    assert unauth.status_code in (401, 403, 404)


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
                sa.select(scheduled_task).where(
                    scheduled_task.c.workspace_id == workspace_id,
                    scheduled_task.c.name == name,
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


async def test_an_intent_applies_exactly_and_the_turn_is_the_audit_record(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel contract end to end: a form-shaped POST admits a turn that dispatches the object
    verb verbatim — no model round — so the submitted values land exactly, the terminal frame
    returns synchronously, and the turn row plus its transcript are the audit record. The intent
    lane writes the transcript before publishing its terminal frame, so the synchronous read that
    follows the response sees the complete audit record."""
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
                    tables.turn.c.workspace_id,
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
    dbos_client = replay_safe_client(config.database.system_url)
    try:
        handle = await dbos_client.retrieve_workflow_async(outcome["turn_id"])
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            await handle.get_result(polling_interval_sec=0.05)
    finally:
        dbos_client.destroy()
    with ws(turn.workspace_id):
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


async def test_homepage_seed_admits_one_turn_per_agent_once(db: None) -> None:
    workspace_id, main_agent = await _seed_workspace()
    admin_id, _admin_token = await _seed_member(workspace_id, "seed-admin@example.com", admin=True)
    owner_id, _owner_token = await _seed_member(workspace_id, "seed-owner@example.com")
    owned_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=owned_agent,
                workspace_id=workspace_id,
                name="owned",
                prompt="be owned",
                model="claude-opus-4-8",
                owner_member_id=owner_id,
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
        await web_surface.seed_homepages(ctx)
        markers = await ctx.store.list(web_surface.HOMEPAGE_SEED_PREFIX)
    assert sorted(key for key, _ in markers) == sorted(
        f"{web_surface.HOMEPAGE_SEED_PREFIX}{agent_id}" for agent_id in (main_agent, owned_agent)
    )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.agent_id,
                    tables.conversation.c.member_id,
                    tables.conversation.c.audience,
                    tables.conversation.c.surface,
                    tables.conversation.c.queue_key,
                    tables.turn.c.inbound,
                    tables.turn.c.on_behalf_of_member_id,
                ).select_from(
                    tables.turn.join(
                        tables.conversation,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                )
            )
        ).all()
    assert len(rows) == 2
    assert len(dbos.enqueued) == 2
    on_behalf = {row.agent_id: row.on_behalf_of_member_id for row in rows}
    assert on_behalf == {main_agent: admin_id, owned_agent: owner_id}
    for row in rows:
        acting = on_behalf[row.agent_id]
        assert row.member_id == acting
        assert row.audience == str(conversation_audience(acting))
        assert row.surface == EXTENSION_WEB
        assert row.queue_key == f"homepage/{row.agent_id}/{acting}"
        assert row.inbound == web_surface.SEED_PROMPT


async def test_homepage_seed_skips_an_agent_whose_allowlist_lacks_the_site_tools(
    db: None,
) -> None:
    assert web_surface.HOMEPAGE_TOOLS == ("build_ufo_application",)
    workspace_id, main_agent = await _seed_workspace()
    await _seed_member(workspace_id, "seed-allow-admin@example.com", admin=True)
    walled = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled,
                workspace_id=workspace_id,
                name="specialist",
                prompt="be narrow",
                model="claude-opus-4-8",
                tools=["load_skill", "memory_search"],
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
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
    assert markers[f"{web_surface.HOMEPAGE_SEED_PREFIX}{walled}"] == "withheld-tools"
    assert sorted(markers) == sorted(
        f"{web_surface.HOMEPAGE_SEED_PREFIX}{agent_id}" for agent_id in (main_agent, walled)
    )
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


async def test_publish_assets_writes_the_apps_tree_under_its_digest(tmp_path) -> None:
    """The apps bundle publishes under its content digest, skip-if-present (content addressed →
    present is correct), so a repeat publish leaves an already-written file untouched. One listing
    of the digest prefix is what "present" is read from, so a tree an interrupted publish left half
    written is completed rather than trusted."""
    fleet = FleetBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    apps = web_surface.AppsBundle(
        files={
            "radar/index.html": b"<html>",
            "wiki/index.html": b"<html>",
            "assets/radar-AbC1.js": b"//r",
            "assets/pages-DeF2.css": b":root{}",
        },
        digest="abc123abc123abc1",
        slugs=frozenset({"radar", "wiki"}),
    )
    prefix = f"apps/{apps.digest}/"
    await web_surface._publish_assets(fleet, apps)
    for path, body in apps.files.items():
        assert await fleet.get(prefix + path) == body

    await fleet.put(prefix + "radar/index.html", b"<edited>")
    await fleet.delete(prefix + "assets/pages-DeF2.css")
    await web_surface._publish_assets(fleet, apps)
    assert await fleet.get(prefix + "radar/index.html") == b"<edited>"
    assert await fleet.get(prefix + "assets/pages-DeF2.css") == b":root{}"


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


async def test_homepage_read_serves_a_row_less_shipped_app_page(
    web_apps: tuple[AsyncClient, UUID, UUID, "FleetBlobStore"],
) -> None:
    """An app agent with no forked hosted_site row resolves to the shipped page: the read answers
    `set` with a frame link and a digest-derived generation, the publish has landed the whole built
    tree under `apps/<digest>/` — the app's own page and every shared chunk that page names — and
    the served generation is that digest's fold, so the link names the bytes just published."""
    client, workspace_id, app_agent, fleet = web_apps
    apps = web_surface.APPS
    assert apps is not None, f"the app pages are not built — run `{PORTAL_BUILD}`"
    _member_id, token = await _seed_member(workspace_id, "shipped@example.com")
    # The shipped homepage read publishes the bundle before it hands out the link.
    read = await client.get(
        f"/surface/web/agents/{app_agent}/homepage",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert read.status_code == 200
    payload = read.json()
    assert payload["state"] == "set"
    assert payload["url"].startswith("https://web/surface/sites/")
    assert set(payload) == {"state", "url", "deploy_generation"}
    assert shipped_address(payload["url"].rpartition("/")[2]) is not None
    keys = {entry.key for entry in await fleet.list(web_surface.APPS_STORE_PREFIX)}
    digests = {key.split("/")[1] for key in keys}
    assert len(digests) == 1
    digest = digests.pop()
    assert digest == apps.digest
    assert keys == {f"apps/{digest}/{path}" for path in apps.files}
    page = apps.files["radar/index.html"].decode()
    for ref in re.findall(r'(?:src|href)="(/assets/[^"]+)"', page):
        assert f"apps/{digest}{ref}" in keys, ref
    assert payload["deploy_generation"] == int(digest[:13], 16)


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


def test_every_catalog_tile_is_served_by_a_broker() -> None:
    """Every catalog tile is a connect act on the Connect page, so a tile no broker serves is a
    press that can only refuse: a slug Composio withholds by judgment stands in the catalog only
    when an explicitly registered connector claims it first."""
    assert FIRST_RUN_PROVIDER_NAMES & set(BANNED) <= set(PIPEDREAM_CONNECTORS)


def test_the_first_run_connect_steps_prepare_the_install_tools_verbatim() -> None:
    """Each connect step names the chat verb that installs its connector and nothing else: the
    panel carries no install rule of its own, so the tool's own admin gate decides who may, and
    `ToolIntent`'s whitelist is what admits the verb at all — a tool it does not name cannot be
    prepared here."""
    for verb, tool in (("connect_slack", "slack_connect"), ("connect_github", "connect_github")):
        submitted = PanelIntent.model_validate({"submitted": {"verb": verb}}).submitted
        prepared = _tool_intent(submitted, None, MEMORY_BODY_MAX_CHARS)
        assert prepared.tool == tool
        assert prepared.input == {}


def test_the_first_run_imessage_offer_prepares_the_phone_tool_verbatim() -> None:
    submitted = PanelIntent.model_validate(
        {"submitted": {"verb": "connect_imessage", "phone_number": "+1 415 555 0123"}}
    ).submitted
    assert isinstance(submitted, ConnectImessageIntent)
    prepared = _tool_intent(submitted, None, MEMORY_BODY_MAX_CHARS)
    assert prepared.tool == "imessage_connect"
    assert prepared.input == {"phone_number": "+1 415 555 0123"}


def test_the_rebuild_intents_prepare_their_own_extensions_tools_verbatim() -> None:
    """Each rebuild names the tool that owns the text being written again and carries nothing but
    the line the activity timeline reads. Neither panel holds a window, a batch size, or a rule
    about who may press it: the tool that drains the work states the first two and its own admin
    gate decides the third, so nothing about a rebuild is answered twice."""
    for verb, tool in (
        ("rebuild_reports", "rebuild_report_digest"),
        ("rebuild_page_facts", "rebuild_page_facts"),
    ):
        submitted = PanelIntent.model_validate({"submitted": {"verb": verb}}).submitted
        prepared = _tool_intent(submitted, None, MEMORY_BODY_MAX_CHARS)
        assert prepared.tool == tool
        assert prepared.input == {}


def test_a_rebuild_outcome_carries_the_tools_own_account_of_what_it_queued() -> None:
    """A rebuild changes nothing the member can see when they press it — the job it marked work for
    writes the new text minutes later — so the answer is the tool's own sentence rather than the
    bare `Saved.` a mutation gets, and a refusal reads back the way every other refusal does."""
    turn_id = uuid4()
    queued = _rebuild_outcome(
        TerminalFrame(status="done", text="The last seven days' entries are written again."),
        turn_id,
    ).body
    assert json.loads(queued) == {
        "applied": True,
        "message": "The last seven days' entries are written again.",
        "turn_id": str(turn_id),
    }
    refused = _rebuild_outcome(
        TerminalFrame(
            status="failed",
            error_message="ValueError: Only a workspace admin can write the radar entries again.",
        ),
        turn_id,
    ).body
    assert json.loads(refused) == {
        "applied": False,
        "message": "Only a workspace admin can write the radar entries again.",
        "turn_id": str(turn_id),
    }


def test_a_connect_outcome_carries_the_link_its_own_tool_minted() -> None:
    """The install link reaches the member who pressed the step, read off the turn's answer the way
    the answering tool writes it. Slack declares its result untrusted, so the state object arrives
    walled: `authorize_url` in it is the link, `events_url` is this deploy's own address and never
    the link, and `hint` says why it minted none. GitHub answers a sentence carrying its link, and
    refuses a non-admin as the turn's own refusal."""
    turn_id = uuid4()
    slack = PanelIntent.model_validate({"submitted": {"verb": "connect_slack"}}).submitted
    github = PanelIntent.model_validate({"submitted": {"verb": "connect_github"}}).submitted
    assert isinstance(slack, ConnectSlackIntent)
    assert isinstance(github, ConnectGitHubIntent)
    minted = json.loads(
        _connect_outcome(
            slack,
            TerminalFrame(
                status="done",
                text=wall(
                    "slack_connect",
                    json.dumps(
                        {
                            "state": "not_installed",
                            "hint": "Open this Add to Slack link to install ufo.",
                            "events_url": "https://web/surface/slack",
                            "authorize_url": "https://slack.com/oauth/v2/authorize?state=sealed",
                        }
                    ),
                ),
            ),
            turn_id,
        ).body
    )
    assert minted == {
        "applied": True,
        "message": "",
        "url": "https://slack.com/oauth/v2/authorize?state=sealed",
        "turn_id": str(turn_id),
    }
    stated = json.loads(
        _connect_outcome(
            slack,
            TerminalFrame(
                status="done",
                text=wall(
                    "slack_connect",
                    json.dumps(
                        {
                            "state": "not_installed",
                            "hint": "Ask a workspace admin to connect Slack.",
                            "events_url": "https://web/surface/slack",
                        }
                    ),
                ),
            ),
            turn_id,
        ).body
    )
    assert stated["url"] is None
    assert stated["message"] == "Ask a workspace admin to connect Slack."
    installed = json.loads(
        _connect_outcome(
            github,
            TerminalFrame(
                status="done",
                text=(
                    "Install the ufo GitHub App to connect this workspace: "
                    "https://github.com/apps/ufo-ai/installations/new?state=sealed\n\nChoose the "
                    "organization and which repositories it may reach."
                ),
            ),
            turn_id,
        ).body
    )
    assert installed["url"] == "https://github.com/apps/ufo-ai/installations/new?state=sealed"
    assert installed["message"] == ""
    refused = json.loads(
        _connect_outcome(
            github,
            TerminalFrame(
                status="failed",
                error_class="IntentRefused",
                error_message="ValueError: only a workspace admin can connect GitHub",
            ),
            turn_id,
        ).body
    )
    assert refused == {
        "applied": False,
        "message": "only a workspace admin can connect GitHub",
        "turn_id": str(turn_id),
    }


def test_the_imessage_outcome_carries_the_phone_claim_instruction_and_link() -> None:
    turn_id = uuid4()
    instruction = 'Text "UFO ABC123" to +14085550123 from that phone within 30 minutes.'
    link = "sms:+14085550123?&body=UFO%20ABC123"
    pending = json.loads(
        _imessage_outcome(
            TerminalFrame(
                status="done",
                text=wall(
                    "imessage_connect",
                    json.dumps(
                        {
                            "state": "pending",
                            "instruction": instruction,
                            "opt_in_link": link,
                        }
                    ),
                ),
            ),
            turn_id,
        ).body
    )
    assert pending == {
        "applied": True,
        "message": instruction,
        "url": link,
        "turn_id": str(turn_id),
    }
    connected = json.loads(
        _imessage_outcome(
            TerminalFrame(
                status="done",
                text=wall(
                    "imessage_connect",
                    json.dumps(
                        {
                            "state": "connected",
                            "instruction": "That phone is connected. Text (408) 555-0123 from it.",
                        }
                    ),
                ),
            ),
            turn_id,
        ).body
    )
    assert connected == {
        "applied": True,
        "message": "That phone is connected. Text (408) 555-0123 from it.",
        "url": None,
        "turn_id": str(turn_id),
    }
    refused = json.loads(
        _imessage_outcome(
            TerminalFrame(
                status="done",
                text=wall(
                    "imessage_connect",
                    json.dumps(
                        {
                            "state": "not_connected",
                            "instruction": "This deploy has no iMessage provider credentials.",
                        }
                    ),
                ),
            ),
            turn_id,
        ).body
    )
    assert refused == {
        "applied": False,
        "message": "This deploy has no iMessage provider credentials.",
        "url": None,
        "turn_id": str(turn_id),
    }


async def test_the_slack_step_mints_an_install_link_for_an_admin_and_no_one_else(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first run's Slack step end to end: the intent dispatches `slack_connect` on the member's
    own lane, and that tool's own admin gate is the whole gate — a member is told who installs it
    and gets no link, the admin gets the deploy's Add to Slack URL sealed to this workspace. No
    message is spoken and no chat conversation exists to speak it in: the link comes back on the
    submit."""
    client, workspace_id, agent_id = web
    config, _hub, _blob, _sandboxes = dbos_runtime
    monkeypatch.setenv(SLACK_CLIENT_ID_ENV, "slack-client")
    monkeypatch.setenv(SLACK_CLIENT_SECRET_ENV, "slack-secret")
    monkeypatch.setattr(config.connect, "public_base_url", "https://web")
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "boss@example.com", admin=True)
    path = f"/surface/web/agents/{agent_id}/intents"
    refused = await client.post(
        path, json={"verb": "connect_slack"}, headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert refused.status_code == 200
    assert refused.json()["url"] is None
    assert "admin" in refused.json()["message"]
    minted = await client.post(
        path, json={"verb": "connect_slack"}, headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert minted.status_code == 200
    outcome = minted.json()
    assert outcome["applied"] is True
    assert outcome["message"] == ""
    assert outcome["url"].startswith(SLACK_OAUTH_AUTHORIZE_URL)
    assert "client_id=slack-client" in outcome["url"]


async def test_the_imessage_step_dispatches_the_phone_tool(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    response = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "connect_imessage", "phone_number": "+14155550123"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert response.status_code == 200
    outcome = response.json()
    assert outcome["applied"] is False
    assert outcome.get("url") is None
    assert outcome["message"] == "This deploy has no iMessage provider credentials."


async def test_connect_pairs_with_the_connection_kind_exactly(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`connect` is the one verb no kind gates, so the model pins its pair: `connect` with any other
    kind is a malformed intent, and the `connection` kind takes `connect` and the `delete` that
    disconnects it and nothing else — an account is connected or disconnected, never edited or
    attached. Each of these is 400 before any turn exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    for body in (
        {"verb": "connect", "kind": "connector_grant", "name": "github"},
        {"verb": "connect", "kind": "agent", "name": "assistant"},
        {"verb": "apply", "kind": "connection", "name": "github"},
        {"verb": "detach", "kind": "connection", "name": "github"},
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


def test_the_acts_a_screen_draws_are_the_acts_this_lane_admits() -> None:
    """`applies` and `deletes` are read off the same two sets the validator refuses by, so an
    object screen cannot draw a control the lane would answer with a 400. A kind the lane only
    ever connects draws neither act; one it only ever deletes draws no create."""
    for kind in sorted(ApplyIntent.kinds()):
        for verb, admitted in (
            ("apply", kind in ApplyIntent.applying_kinds()),
            ("delete", kind in ApplyIntent.deleting_kinds()),
        ):
            spec = {"x": "y"} if verb == "apply" else None
            try:
                ApplyIntent(verb=verb, kind=kind, name="n", spec=spec)
            except ValidationError:
                validates = False
            else:
                validates = True
            assert validates is admitted, f"{verb} {kind}: lane {validates}, screen {admitted}"
    assert "connection" not in ApplyIntent.applying_kinds()
    assert "connection" in ApplyIntent.deleting_kinds()
    assert "source_trigger" not in ApplyIntent.applying_kinds()


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
    assert data["agent"]["setup"] is None
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


@pytest.mark.parametrize(
    ("base", "secure"),
    [("https://portal.example", True), ("http://ufo.localhost:8710", False)],
)
async def test_the_session_cookie_is_secure_where_the_portal_publishes_https(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
    base: str,
    secure: bool,
) -> None:
    """A browser stores no `Secure` cookie sent by a plain-http origin. A local stack publishes
    one, so a portal that marked the attribute regardless would bind a session the browser drops
    and answer the redirect it just sent with the sign-in page again.

    The scheme comes off the published base, never the request: a deploy terminates TLS at its
    ingress, so every request reaches the portal as plain http while the origin the browser holds
    is https."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id, _agent_id = await _seed_workspace()
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (web_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        SECRET,
        base,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        surface_model=lambda _name: SURFACE_MODEL[0],
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as client:
        opened = await client.post("/surface/web", data={"token": token})
    dbos_client.destroy()
    assert opened.status_code == 303
    cookie = opened.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert ("Secure" in cookie) is secure
    # The attributes that do not follow the scheme stand either way.
    assert "HttpOnly" in cookie and "domain" not in cookie.lower()


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


async def test_the_setup_read_states_the_whole_declaration_and_what_is_outstanding(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The screen a member reads asks what the app runs on, not only what is missing — so the read
    states the whole declaration, settled and outstanding together, each row carrying its own
    settled bit. Whose is never stated: an account is there or it is not, a standing order exists
    or it does not."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "reader@example.com")
    await _declare_setup(agent_id)
    state = (
        await client.get(
            f"/surface/web/agents/{agent_id}/setup",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    ).json()
    assert state["connectors"] == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": False, "required": True}
    ]
    assert state["credentials"] == [
        {"label": "ACME install", "filled": False, "provider": None, "required": False}
    ]
    assert state["standing"] == [
        {"kind": "scheduled_task", "armed": False, "required": False, "schedule": None}
    ]
    assert state["schedule"]["name"] == "acme-sweep"
    assert state["schedule"]["cadences"][0] == {"hour": None, "minute": 0, "weekdays": []}
    assert state["instructions"] == "Connect the ACME account."


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
        {"provider": "acme", "label": "acme", "summary": "", "granted": True, "required": True}
    ]
    assert await connectors(other_token) == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": False, "required": True}
    ]

    # Shared is the workspace's own: every member reads the one account, because every member's
    # turns work from it.
    shared_id = await _seed_account(workspace_id, agent_id, owner_id, "acme", shared=True)
    await _grant_account(workspace_id, agent_id, shared_id)
    assert await connectors(other_token) == [
        {"provider": "acme", "label": "acme", "summary": "", "granted": True, "required": True}
    ]


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
    ] == [{"label": "ACME install", "filled": False, "provider": None, "required": False}]

    await _fill_slot(workspace_id, "acme_api_key")
    assert (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()[
        "credentials"
    ] == [{"label": "ACME install", "filled": True, "provider": None, "required": False}]


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


async def test_an_app_with_every_account_and_no_standing_order_still_owes_one(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An account is half of what a shipped app arrives without. Connected and unarmed it holds the
    authority to work and no occasion to, and a setup read that stopped at the account would call
    that done — so the standing order is declared and read beside the accounts."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "arming@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    connection_id = await _seed_account(workspace_id, agent_id, member_id, "acme")
    await _grant_account(workspace_id, agent_id, connection_id)
    await _fill_slot(workspace_id, "acme_api_key")
    unarmed = (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()
    assert unarmed["standing"] == [
        {"kind": "scheduled_task", "armed": False, "required": False, "schedule": None}
    ]

    applied = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "acme-sweep",
            "spec": {"schedule": "0 9 * * 1-5", "prompt": "Sweep what arrived and report it."},
        },
        headers=cookie,
    )
    assert applied.status_code == 200, applied.text
    armed = (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()
    assert armed["standing"] == [
        {"kind": "scheduled_task", "armed": True, "required": False, "schedule": ARMED_CRON}
    ]
    # The offer stands after the arming: the band says what the app runs on, not only what is
    # missing, and a member who wants a different hour picks again against the same schedule.
    assert armed["schedule"]["name"] == "acme-sweep"


async def test_a_second_feature_armed_does_not_arm_the_one_the_app_arrived_holding(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One kind holds every feature's order. An app that offers a schedule names the task that
    schedule arms, and that name is what the read asks for.

    Asking the kind alone would call the app ready the moment a member turned on a second feature —
    taking away the cadence pick that arms the one it arrived holding, while that feature's band
    stayed blank and its schedule never fired."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "arming@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    connection_id = await _seed_account(workspace_id, agent_id, member_id, "acme")
    await _grant_account(workspace_id, agent_id, connection_id)
    await _fill_slot(workspace_id, "acme_api_key")

    # A feature the member asked for in chat, armed under its own name.
    other = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "acme-followups",
            "spec": {"schedule": "0 * * * *", "prompt": "Chase what is late."},
        },
        headers=cookie,
    )
    assert other.status_code == 200, other.text
    state = (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()
    assert state["standing"] == [
        {"kind": "scheduled_task", "armed": False, "required": False, "schedule": None}
    ]
    assert state["schedule"]["name"] == "acme-sweep"

    # The app's own task is what arms it.
    armed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "acme-sweep",
            "spec": {"schedule": "0 9 * * 1-5", "prompt": "Sweep what arrived and report it."},
        },
        headers=cookie,
    )
    assert armed.status_code == 200, armed.text
    settled = (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()
    assert settled["standing"] == [
        {"kind": "scheduled_task", "armed": True, "required": False, "schedule": ARMED_CRON}
    ]


async def test_the_app_reads_armed_with_its_own_task_behind_a_page_of_others(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One kind holds every feature's order and a kind's listing is paged. An app whose other tasks
    sort ahead of its own and fill a page would read its own as missing on every read — and the
    member could not clear it, because re-applying the same name only moves the row already there.

    So the name goes down to the registry and comes back as the one row it is."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "crowded@example.com", admin=True)
    await _declare_setup(agent_id)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    connection_id = await _seed_account(workspace_id, agent_id, member_id, "acme")
    await _grant_account(workspace_id, agent_id, connection_id)
    await _fill_slot(workspace_id, "acme_api_key")

    armed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "acme-sweep",
            "spec": {"schedule": "0 9 * * 1-5", "prompt": "Sweep what arrived and report it."},
        },
        headers=cookie,
    )
    assert armed.status_code == 200, armed.text
    await _crowd_the_listing(agent_id, "acme-sweep", OBJECT_LIST_PAGE)

    state = (await client.get(f"/surface/web/agents/{agent_id}/setup", headers=cookie)).json()
    assert state["standing"] == [
        {"kind": "scheduled_task", "armed": True, "required": False, "schedule": ARMED_CRON}
    ]


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


async def test_an_agent_no_extension_shipped_declares_nothing_at_all(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """An agent a member built has no shipped declaration at all, so the read has nothing to state
    and nothing to withhold — a band drawn over this says nothing rather than an empty checklist."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "builder@example.com")
    state = (
        await client.get(
            f"/surface/web/agents/{agent_id}/setup",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    ).json()
    assert state == {
        "connectors": [],
        "credentials": [],
        "standing": [],
        "schedule": None,
        "instructions": "",
        "own_page": False,
    }


async def test_the_boot_read_says_which_app_still_owes_its_member_setup(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The rail draws one row per app, and the row for an app that cannot work yet says so from the
    boot read — the poll beside it re-reads every four seconds and would pay for this per agent
    every tick.

    It is a fact about the declaration, never about the column: every provision writes one, and an
    app that needs nothing writes an empty one. A row reading `setup` as present-means-unfinished
    would mark most of the rail unfinished for good. An agent a member built declares nothing at
    all and owes nothing."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    quiet = await _seed_app_agent(workspace_id, "radar")
    wired = await _seed_app_agent(workspace_id, "meetings")
    await _declare_setup(quiet, AgentSetup())
    await _declare_setup(wired, AgentSetup(connectors=("acme",), instructions="Connect ACME."))

    async def due() -> dict[str, bool]:
        read = await client.get("/surface/web/api/agents", headers=cookie)
        return {agent["name"]: agent["setup_due"] for agent in read.json()["agents"]}

    assert await due() == {"assistant": False, "radar": False, "meetings": True}

    account = await _seed_account(workspace_id, wired, member_id, "acme")
    await _grant_account(workspace_id, wired, account)
    assert await due() == {"assistant": False, "radar": False, "meetings": False}


def test_an_app_that_a_clock_wakes_offers_the_cadences_that_arm_it() -> None:
    """The band that states a need carries the act that settles it. A `scheduled_task` need with no
    cadences would state one the member can only settle by leaving the page and composing the app's
    own job for it — so the declaration is refused where it is written."""
    with pytest.raises(ValidationError, match="offers no cadences"):
        AgentSetup(standing=(SCHEDULE_KIND,))
    # The offer is what makes it legal, and an app that a feed wakes needs none: nothing about a
    # source trigger is a question of how often.
    assert AgentSetup(standing=("source_trigger",)).schedule is None
    assert AgentSetup(
        standing=(SCHEDULE_KIND,),
        schedule=SetupSchedule(name="s", prompt="p", cadences=(SetupCadence(hour=9),)),
    ).standing == (SCHEDULE_KIND,)
    # And the other way: the row stating the need is what carries the offer, so cadences with no
    # row are cadences no screen draws a control for.
    with pytest.raises(ValidationError, match=f"declares no {SCHEDULE_KIND!r} need"):
        AgentSetup(schedule=SetupSchedule(name="s", prompt="p", cadences=(SetupCadence(),)))


def test_an_hourly_cadence_names_no_weekday() -> None:
    """A local weekday spans two UTC days, and an hourly cadence has no anchor hour to decide
    which — so the pair is refused rather than resolved to whichever day the converter guessed."""
    with pytest.raises(ValidationError, match="no anchor hour"):
        SetupCadence(weekdays=(1,))
    assert SetupCadence(hour=9, weekdays=(1,)).weekdays == (1,)


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


async def test_a_portal_act_lands_in_a_room_that_states_what_it_is(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A room is named where it is opened, because nothing else will. A conversation with no title
    lists its own first words, and a prepared intent's first words are the serialized tool call the
    lane admitted — so a member's list of conversations reads as a column of raw JSON."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "presser@example.com", admin=True)
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
    listed = (
        await client.get(f"/surface/web/agents/{agent_id}/conversations", headers=cookie)
    ).json()["conversations"]
    assert "Portal actions" in {row["description"] for row in listed}
    assert not any(row["description"].startswith("{") for row in listed)


async def test_an_armed_schedule_reports_into_the_lane_that_armed_it(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A scheduled task reports into the conversation that created it, for as long as it exists —
    so where the arming intent lands is where every run of that task lands.

    That is the member's one prepared-intent lane with this app, and it has to be: a second durable
    lane is a second partition, and a member's acts on one object could then execute out of the
    order they submitted them — a queued arm applying after the delete that followed it, leaving the
    task armed and firing. The room is named, so what it holds reads as what it is rather than as
    the serialized tool call the lane admitted."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "arming@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    armed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "scheduled_task",
            "name": "delivery-report",
            "spec": {"schedule": "0 9 * * 1", "prompt": "report the week"},
        },
        headers=cookie,
    )
    assert armed.status_code == 200, armed.text
    async with workspace_tx() as connection:
        rooms = {
            row.title: row.queue_key
            for row in await connection.execute(
                sa.select(tables.conversation.c.title, tables.conversation.c.queue_key).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        }
    assert rooms["Portal actions"].startswith("intent/")
    # One lane, so one partition: nothing else durable was opened for this member and this app.
    assert len(rooms) == 1


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


async def test_an_expanded_intent_answers_413_before_admission(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    body = {
        "verb": "apply",
        "kind": "agent",
        "name": "assistant",
        "spec": {"prompt": "x" * (web_panels.INTENT_MAX_BYTES - 100)},
    }
    encoded = json.dumps(body, separators=(",", ":")).encode()
    assert len(encoded) <= web_panels.INTENT_MAX_BYTES
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        content=encoded,
        headers={
            "content-type": "application/json",
            "cookie": f"{SESSION_COOKIE}={token}",
        },
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


async def test_a_slack_conversation_row_states_the_thread_it_came_in_on(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A listed conversation states where it was opened, as the admitting surface reported it, so a
    row leads back out to the Slack thread as well as into the transcript here, and the transcript
    leads out to the same place. The link is the opening message's permalink — the thread's own
    root, never a later message's, since one conversation has one way back to it. A portal chat
    states its source too, and it names the portal the reader is already in, which is why the
    surface and not the string decides what is drawn."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    thread = await _seed_agent_conversation(
        workspace_id,
        agent_id,
        queue_key="C1:1700000000.000100",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface="slack",
    )
    await _seed_listed_turn(
        workspace_id,
        thread,
        agent_id,
        seq=1,
        inbound="take a look at the failing deploy",
        speaker_member_id=member_id,
        context=TurnContext(sender="Mel Okafor (m@example.com)", source=SLACK_THREAD_PERMALINK),
    )
    await _seed_listed_turn(
        workspace_id,
        thread,
        agent_id,
        seq=2,
        inbound="any luck?",
        speaker_member_id=member_id,
        context=TurnContext(
            sender="Mel Okafor (m@example.com)",
            source="https://acme.slack.com/archives/C1/p1700000000000900",
        ),
    )
    portal, _turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "m@example.com",
        TerminalFrame(status="done", text="done"),
        title="Rename the deploy job",
        context=TurnContext(sender="m@example.com", source="ufo web (m@example.com)"),
    )

    listed = await client.get(
        f"/surface/web/agents/{agent_id}/conversations",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )

    assert listed.status_code == 200
    rows = {row["id"]: row for row in listed.json()["conversations"]}
    assert rows[str(thread)]["source"] == SLACK_THREAD_PERMALINK
    assert rows[str(portal)]["surface"] == "web"
    assert rows[str(portal)]["source"] is not None
    assert "slack.com" not in rows[str(portal)]["source"]


async def test_a_web_conversation_is_described_by_the_title_the_rail_shows(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """One conversation is named one way wherever the member meets it: the panel and the rail read
    the one string the conversation carries, so neither can drift from the other."""
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


async def test_a_compacted_conversation_pages_its_earlier_messages(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A compacted transcript states its tail and the cursor directly above it. Each page answers
    the messages its compaction replaced, less the kept tail the next window already shows, so
    pages and tail concatenate without a repeat or a gap; the summary message a compaction wrote
    draws no bubble on either read. A malformed cursor and another route shape are not found."""
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
    third = await _seed_listed_turn(
        workspace_id, conversation_id, agent_id, seq=3, inbound="third ask"
    )
    head = (
        Message(role="user", content=f"<context>\nmessage_ref: {first}\n</context>\nfirst ask"),
        Message(role="assistant", content="first reply"),
    )
    tail = (
        Message(role="user", content=f"<context>\nmessage_ref: {second}\n</context>\nsecond ask"),
        Message(role="assistant", content="second reply"),
    )
    summary = Message(role="user", content="Compacted context:\nthe head, summarized")
    await _write_compaction(blob, conversation_id, 1, before=(*head, *tail), after=(summary, *tail))
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(
            seq=3,
            messages=(
                summary,
                *tail,
                Message(
                    role="user", content=f"<context>\nmessage_ref: {third}\n</context>\nthird ask"
                ),
                Message(role="assistant", content="third reply"),
            ),
        ),
    )

    path = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript"
    read = await client.get(path, headers=headers)
    assert read.status_code == 200
    tail_payload = read.json()
    assert tail_payload["messages"] == [
        {"role": "user", "text": "second ask"},
        {"role": "assistant", "text": "second reply"},
        {"role": "user", "text": "third ask"},
        {"role": "assistant", "text": "third reply"},
    ]
    cursor = tail_payload["earlier_cursor"]
    assert isinstance(cursor, str)

    page = await client.get(path, headers=headers, params={"cursor": cursor})
    assert page.status_code == 200
    assert page.json() == {
        "messages": [
            {"role": "user", "text": "first ask"},
            {"role": "assistant", "text": "first reply"},
        ]
    }

    own = await client.get(
        f"/surface/web/agents/{agent_id}/transcript?conversation={conversation_id}",
        headers=headers,
    )
    assert own.status_code == 200
    assert own.json()["earlier_cursor"] == cursor

    malformed = await client.get(path, headers=headers, params={"cursor": "not-a-cursor"})
    assert malformed.status_code == 404
    unshaped = await client.get(path + "/1", headers=headers)
    assert unshaped.status_code == 404


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


async def test_pages_chain_upward_through_their_records(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """Each page names the page above it: the newest older record its own window opens with. The
    pane follows that chain instead of counting records, so twice-compacted history reads back
    whole — page one, page two, tail — with no message repeated or skipped."""
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
    turns = [first] + [
        await _seed_listed_turn(
            workspace_id, conversation_id, agent_id, seq=seq, inbound=f"ask {seq}"
        )
        for seq in (2, 3, 4)
    ]
    rounds = [
        (
            Message(
                role="user", content=f"<context>\nmessage_ref: {turn}\n</context>\nask {at + 1}"
            ),
            Message(role="assistant", content=f"reply {at + 1}"),
        )
        for at, turn in enumerate(turns)
    ]
    first_summary = Message(role="user", content="Compacted context:\nrounds one and two")
    second_summary = Message(role="user", content="Compacted context:\nthrough round three")
    await _write_compaction(
        blob,
        conversation_id,
        1,
        before=(*rounds[0], *rounds[1]),
        after=(first_summary, *rounds[1]),
    )
    await _write_compaction(
        blob,
        conversation_id,
        2,
        before=(first_summary, *rounds[1], *rounds[2]),
        after=(second_summary, *rounds[2]),
    )
    await _write_transcript(
        blob,
        conversation_id,
        Conversation(seq=4, messages=(second_summary, *rounds[2], *rounds[3])),
    )

    path = f"/surface/web/agents/{agent_id}/conversations/{conversation_id}/transcript"
    read = await client.get(path, headers=headers)
    assert read.status_code == 200
    tail_payload = read.json()
    assert tail_payload["messages"] == [
        {"role": "user", "text": "ask 3"},
        {"role": "assistant", "text": "reply 3"},
        {"role": "user", "text": "ask 4"},
        {"role": "assistant", "text": "reply 4"},
    ]
    upper = await client.get(
        path, headers=headers, params={"cursor": tail_payload["earlier_cursor"]}
    )
    assert upper.status_code == 200
    upper_payload = upper.json()
    assert upper_payload["messages"] == [
        {"role": "user", "text": "ask 2"},
        {"role": "assistant", "text": "reply 2"},
    ]
    top = await client.get(
        path, headers=headers, params={"cursor": upper_payload["earlier_cursor"]}
    )
    assert top.status_code == 200
    assert top.json() == {
        "messages": [
            {"role": "user", "text": "ask 1"},
            {"role": "assistant", "text": "reply 1"},
        ]
    }


async def test_history_pages_answer_at_the_chat_reach(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A member-private extension conversation grants its agent chat without a panel, and the
    member-chat transcript serves it — so the pages that transcript advertises answer for the same
    conversation, while another member stays refused."""
    client, workspace_id, _agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    member_id, token = await _seed_member(workspace_id, "m@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "n@example.com")
    headers = {"cookie": f"{SESSION_COOKIE}={token}"}
    review_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=review_agent,
                workspace_id=workspace_id,
                name="code-review",
                prompt="review code",
                model="claude-opus-4-8",
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
    older = await _seed_listed_turn(
        workspace_id, conversation_id, review_agent, seq=1, inbound="older ask"
    )
    briefed = await _seed_listed_turn(
        workspace_id, conversation_id, review_agent, seq=2, inbound="review this"
    )
    tail = (
        Message(role="user", content=f"<context>\nmessage_ref: {briefed}\n</context>\nreview this"),
        Message(role="assistant", content="review result"),
    )
    summary = Message(role="user", content="Compacted context:\nolder briefs")
    await _write_compaction(
        blob,
        conversation_id,
        1,
        before=(
            Message(role="user", content=f"<context>\nmessage_ref: {older}\n</context>\nolder ask"),
            *tail,
        ),
        after=(summary, *tail),
    )
    await _write_transcript(blob, conversation_id, Conversation(seq=2, messages=(summary, *tail)))

    own = await client.get(
        f"/surface/web/agents/{review_agent}/transcript?conversation={conversation_id}",
        headers=headers,
    )
    assert own.status_code == 200
    cursor = own.json()["earlier_cursor"]

    path = f"/surface/web/agents/{review_agent}/conversations/{conversation_id}/transcript"
    page = await client.get(path, headers=headers, params={"cursor": cursor})
    assert page.status_code == 200
    assert page.json() == {"messages": [{"role": "user", "text": "older ask"}]}

    refused = await client.get(
        path,
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
        params={"cursor": cursor},
    )
    assert refused.status_code == 404

    shared = await _seed_agent_conversation(
        workspace_id,
        review_agent,
        queue_key="review-room",
        audience="shared",
        member_id=None,
    )
    await _write_compaction(
        blob,
        shared,
        1,
        before=(Message(role="user", content="shared ask"),),
        after=(Message(role="user", content="Compacted context:\nshared"),),
    )
    for params in (None, {"cursor": cursor}):
        parity = await client.get(
            f"/surface/web/agents/{review_agent}/conversations/{shared}/transcript",
            headers=headers,
            params=params,
        )
        assert parity.status_code == 404


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
    with ws(workspace_id):
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
    assert artifact["preview"]["url"].startswith("https://web/artifacts/")
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
            sa.update(tables.member).where(tables.member.c.id == _member_id).values(seated_at=None)
        )
    member_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    body = member_view.json()
    assert [(entry["email"], entry["admin"], entry["seated"]) for entry in body["members"]] == [
        ("boss@example.com", True, True),
        ("m@example.com", False, False),
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


def test_a_bubble_carries_the_question_its_words_answered() -> None:
    """The read hands the projection the question each answering message settled; the bubble
    carries it for every speaker, the viewer's own included — an answer reads with what it
    answered, and unlike the speaker label there is no bubble whose question the pane already
    accounts for."""
    theirs = "77777777-7777-7777-7777-777777777777"
    mine = "88888888-8888-8888-8888-888888888888"
    rendered = _rendered_messages(
        (
            Message(role="user", content=f"<context>\nmessage_ref: {theirs}\n</context>\nShip"),
            Message(role="assistant", content="Shipping."),
            Message(role="user", content=f"<context>\nmessage_ref: {mine}\n</context>\nHold"),
        ),
        None,
        frozenset({theirs, mine}),
        frozenset(),
        {theirs: "Mel Okafor (m@example.com)"},
        asked={theirs: "Ship it?", mine: "Deploy now?"},
    )
    assert rendered == [
        {
            "role": "user",
            "text": "Ship",
            "speaker": "Mel Okafor (m@example.com)",
            "asked": "Ship it?",
        },
        {"role": "assistant", "text": "Shipping."},
        {"role": "user", "text": "Hold", "asked": "Deploy now?"},
    ]


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


async def test_the_settings_read_offers_the_setup_a_shipped_agent_still_needs(
    db: None, web: tuple[AsyncClient, UUID, UUID]
) -> None:
    """The portal is where a member finishes an install, so the agent read carries what is still
    ungranted. It is derived from the grants, not a flag: the offer is present while the connector
    is unheld and absent once any connection of that provider is granted to this agent."""
    client, workspace_id, _agent_id = web
    shipped = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=shipped,
                workspace_id=workspace_id,
                name="shipped",
                prompt="be shipped",
                model="claude-opus-4-8",
                provisioned_by="sample",
                provisioned_name="shipped",
                provisioned_version="0.1.0",
                setup={"connectors": ["acme"], "instructions": "Connect the Acme account."},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    offered = await client.get(f"/surface/web/agents/{shipped}/settings", headers=cookie)
    assert offered.status_code == 200
    assert offered.json()["agent"]["setup"] == {
        "connectors": ["acme"],
        "credentials": [],
        "standing": [],
        "schedule": None,
        "instructions": "Connect the Acme account.",
    }

    connection_id = uuid4()
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=shipped,
                surface="test",
                queue_key=conversation_id.hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="acme",
                account_id="acme-1",
                host="api.acme.test",
                owner_member_id=_member_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=shipped,
                connection_id=connection_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    settled = await client.get(f"/surface/web/agents/{shipped}/settings", headers=cookie)
    assert settled.json()["agent"]["setup"] is None


async def test_the_apps_screen_reads_the_archived_apps_its_reader_may_restore(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    client, workspace_id, agent_id = web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    archived_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=archived_id,
                workspace_id=workspace_id,
                name=f"~archived-{archived_id}",
                archived_name="invoice-intake",
                prompt="read the invoices",
                model="claude-opus-4-8",
                reasoning="high",
                icon="aten",
                visibility="workspace",
                owner_member_id=owner_id,
                archived_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    for token, names in (
        (owner_token, ["invoice-intake"]),
        (admin_token, ["invoice-intake"]),
        (other_token, []),
    ):
        index = await client.get(
            "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"}
        )
        assert index.status_code == 200
        payload = index.json()
        assert [app["name"] for app in payload["archived"]] == names
        assert [agent["id"] for agent in payload["agents"]] == [str(agent_id)]
    owner_read = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={owner_token}"}
    )
    assert owner_read.json()["archived"][0]["id"] == str(archived_id)
    assert owner_read.json()["archived"][0]["icon"] == "aten"


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

    restored = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "restore_application", "app_id": str(app_id), "name": "invoice-intake-2"},
        headers=cookie,
    )
    assert restored.status_code == 200
    assert restored.json()["applied"] is True, restored.json()["message"]
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
