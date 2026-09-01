"""The eval environment's seam proof: the fake mailbox, calendar, and code index are reached
through the real `call_external_tool` dispatch over a registry built from the extension's own
manifest, every mutation lands durably in the extension's tables — read back through a fresh
transaction, exactly as an eval grader reads them from another process — and every seeded code page
comes back condensed by the shipped pass, landing on the side of the engine's inline budget its
eval case measures."""

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_connectors.tools as connector_tools
import ufo_ext_eval_env.manifest as env
from ufo_ext_connectors.tools import (
    CallExternalToolInput,
    DescribeExternalToolsInput,
    call_external_tool,
    describe_external_tools,
)

from evals.harness.harness import JsonObject
from evals.suites.connector_refs import (
    FLEET,
    KERNEL,
    LEDGER,
    LEGACY,
    QUERIES,
    REF_MAIN,
    token,
)
from ufo.db import workspace_tx
from ufo.host.tools.builtins import EditInput, FileEdit, ReadInput
from ufo.runtime.access.connectors import ConnectorEntry, ConnectorRegistry, UnknownBrokerTool
from ufo.runtime.access.grants import Grant, GrantStore
from ufo.runtime.engine import TOOL_RESULT_PREVIEW_CHARS
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.sdk.manifest import HookContext, PreToolUse

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "using the connected account"

BOB = "bob@evalco.test"


def _repair_hook_context(
    tool_name: str,
    tool_input,
    *,
    store: ScopedStore | None = None,
    turn: Turn | None = None,
) -> HookContext:
    return HookContext(
        ext=ExtensionContext(
            store=store or ScopedStore(extension=env.NAME),
            credentials=CredentialAccess(declared=frozenset()),
        ),
        payload=PreToolUse(tool_name=tool_name, tool_input=tool_input),
        agent=Agent(
            prompt="repair",
            model="google/gemini-3.7-flash",
            name=env.APP_QA_REPAIR_AGENT_NAME,
        ),
        turn=turn,
    )


async def test_app_qa_repair_agent_and_hook_keep_one_exact_edit_surface() -> None:
    manifest = env.manifest()
    repair = next(agent for agent in manifest.agents if agent.name == env.APP_QA_REPAIR_AGENT_NAME)

    assert repair.spec.model == "claude-opus-5"
    assert repair.spec.use_workspace_skills is False
    assert repair.tools == ("read", "edit")
    assert (
        await env.bound_app_qa_repair_tools(
            _repair_hook_context("read", ReadInput(file_path=env.APP_QA_SOURCE_PATH))
        )
        is None
    )
    wrong_path = await env.bound_app_qa_repair_tools(
        _repair_hook_context("read", ReadInput(file_path="/workspace/ufo-app/preview.svg"))
    )
    replace_all = await env.bound_app_qa_repair_tools(
        _repair_hook_context(
            "edit",
            EditInput(
                file_path=env.APP_QA_SOURCE_PATH,
                edits=(FileEdit(old_string="old", new_string="new", replace_all=True),),
            ),
        )
    )

    assert wrong_path is not None
    assert wrong_path.reason == f"app QA repair can change only {env.APP_QA_SOURCE_PATH}"
    assert replace_all is not None
    assert replace_all.reason == "app QA repair does not allow replace_all"


async def test_app_qa_repair_hook_enforces_exact_edit_budget_boundaries(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        store = ScopedStore(extension=env.NAME)
        calls_turn = Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="repair",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        )
        small = EditInput(
            file_path=env.APP_QA_SOURCE_PATH,
            edits=(FileEdit(old_string="old", new_string="new"),),
        )
        for _ in range(env.APP_QA_EDIT_CALL_LIMIT):
            assert (
                await env.bound_app_qa_repair_tools(
                    _repair_hook_context("edit", small, store=store, turn=calls_turn)
                )
                is None
            )
        over_calls = await env.bound_app_qa_repair_tools(
            _repair_hook_context("edit", small, store=store, turn=calls_turn)
        )

        for field, limit in (
            ("old_string", env.APP_QA_EDIT_OLD_BYTES_LIMIT),
            ("new_string", env.APP_QA_EDIT_NEW_BYTES_LIMIT),
        ):
            exact_turn = calls_turn.model_copy(update={"id": uuid4()})
            over_turn = calls_turn.model_copy(update={"id": uuid4()})
            exact = {"old_string": "old", "new_string": "new", field: "x" * limit}
            over = {"old_string": "old", "new_string": "new", field: "x" * (limit + 1)}
            assert (
                await env.bound_app_qa_repair_tools(
                    _repair_hook_context(
                        "edit",
                        EditInput(
                            file_path=env.APP_QA_SOURCE_PATH,
                            edits=(FileEdit(**exact),),
                        ),
                        store=store,
                        turn=exact_turn,
                    )
                )
                is None
            )
            denied = await env.bound_app_qa_repair_tools(
                _repair_hook_context(
                    "edit",
                    EditInput(
                        file_path=env.APP_QA_SOURCE_PATH,
                        edits=(FileEdit(**over),),
                    ),
                    store=store,
                    turn=over_turn,
                )
            )
            assert denied is not None
            assert field.split("_")[0] in denied.reason

    assert over_calls is not None
    assert str(env.APP_QA_EDIT_CALL_LIMIT) in over_calls.reason


async def test_app_qa_repair_hook_denies_the_retained_47140_byte_full_source(db: None) -> None:
    source = (
        Path(__file__).parents[3] / "evals/fixtures/ufo_app_qa_replay/pre-meeting-briefs/app.tsx"
    ).read_text()
    assert len(source.encode()) == 47_140
    workspace_id = await _workspace()
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="repair",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    with ws(workspace_id):
        denied = await env.bound_app_qa_repair_tools(
            _repair_hook_context(
                "edit",
                EditInput(
                    file_path=env.APP_QA_SOURCE_PATH,
                    edits=(FileEdit(old_string=source, new_string="replacement"),),
                ),
                store=ScopedStore(extension=env.NAME),
                turn=turn,
            )
        )

    assert denied is not None
    assert "old_string byte limit" in denied.reason


@dataclass(frozen=True)
class _Grants(GrantStore):
    providers: tuple[tuple[str, str], ...]

    async def active_grants(self) -> tuple[Grant, ...]:
        return tuple(
            Grant(
                id=uuid4(),
                connection_id=uuid4(),
                provider=provider,
                account_id=env.ACCOUNT_ID,
                host=host,
                owner_member_id=uuid4(),
                owner_email=BOB,
                connection_shared=True,
            )
            for provider, host in self.providers
        )


ALL_GRANTS = (
    (env.EMAIL_PROVIDER, env.EMAIL_HOST),
    (env.CALENDAR_PROVIDER, env.CALENDAR_HOST),
    (env.CODE_PROVIDER, env.CODE_HOST),
)

APP_GRANTS = (
    (env.DRIVE_PROVIDER, env.DRIVE_HOST),
    (env.GITHUB_PROVIDER, env.GITHUB_HOST),
    (env.STRIPE_PROVIDER, env.STRIPE_HOST),
    (env.HUBSPOT_PROVIDER, env.HUBSPOT_HOST),
    (env.GREENHOUSE_PROVIDER, env.GREENHOUSE_HOST),
)


def _ctx(workspace_id: UUID, grants: tuple[tuple[str, str], ...] = ALL_GRANTS) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="use the environment",
            created_at=datetime(2026, 7, 15, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=_Grants(grants),
        connectors=ConnectorRegistry(
            entries={
                connector.oauth.provider: ConnectorEntry(
                    provider=connector.oauth.provider,
                    label=connector.label,
                    broker=connector.broker,
                )
                for connector in env.manifest().connectors
            }
        ),
        idempotency_key="t1/call_external_tool/c1",
    )


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def test_reply_all_email_derives_recipients_from_the_workspace_message(db: None) -> None:
    workspace_id = await _workspace()
    message_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(env.eval_env_email).values(
                id=message_id,
                workspace_id=workspace_id,
                folder="inbox",
                sender="dana@evalco.test",
                recipients=[env.MAILBOX_ADDRESS, BOB, "mara@evalco.test", BOB],
                subject="Offsite planning",
                body="Reply all with a yes or no.",
                sent_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
        )

    result = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
            tool_name="reply_all_email",
            source_id=env.EMAIL_PROVIDER,
            arguments={"message_id": str(message_id), "body": "Count me in."},
        ),
    )

    assert _payload(result)["to"] == [
        "dana@evalco.test",
        BOB,
        "mara@evalco.test",
    ]
    async with workspace_tx() as connection:
        sent = (
            await connection.execute(
                sa.select(env.eval_env_email).where(
                    env.eval_env_email.c.workspace_id == workspace_id,
                    env.eval_env_email.c.folder == "sent",
                )
            )
        ).one()
    assert tuple(sent.recipients) == ("dana@evalco.test", BOB, "mara@evalco.test")
    assert sent.sender == env.MAILBOX_ADDRESS
    assert sent.subject == "Re: Offsite planning"
    assert sent.body == "Count me in."


async def test_calendar_lifecycle_updates_and_cancels_durably(db: None) -> None:
    workspace_id = await _workspace()
    ctx = _ctx(workspace_id)

    created = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name="create_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={
                    "title": "Design sync",
                    "start": "2026-07-23T14:00:00+00:00",
                    "end": "2026-07-23T15:00:00+00:00",
                    "attendees": ["mara@evalco.test"],
                },
            ),
        )
    )
    moved = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name="update_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={
                    "event_id": created["id"],
                    "start": "2026-07-24T15:00:00+00:00",
                    "end": "2026-07-24T16:00:00+00:00",
                },
            ),
        )
    )
    assert moved["start"].startswith("2026-07-24T15:00")

    cancelled = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name="cancel_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"event_id": created["id"]},
            ),
        )
    )
    assert cancelled["status"] == env.CANCELLED
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(env.eval_env_event).where(env.eval_env_event.c.id == UUID(created["id"]))
            )
        ).one()
    assert row.status == env.CANCELLED
    assert row.start_at.date() == datetime(2026, 7, 24, tzinfo=UTC).date()


async def test_updating_a_missing_event_fails_loud(db: None) -> None:
    workspace_id = await _workspace()
    with pytest.raises(ValueError, match="no event"):
        await call_external_tool(
            _ctx(workspace_id),
            CallExternalToolInput(
                tool_name="update_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"event_id": str(uuid4()), "title": "renamed"},
            ),
        )


async def test_unknown_slug_raises_for_the_wrong_provider() -> None:
    broker = env.EvalEnvBroker()
    with pytest.raises(UnknownBrokerTool):
        await broker.execute(uuid4(), env.EMAIL_PROVIDER, "create_event", {}, "acct", None)


async def test_app_providers_return_only_the_seeded_tool_response(db: None) -> None:
    workspace_id = await _workspace()
    fixture = {"issues": [{"number": 42, "title": "Fix billing export"}]}
    with ws(workspace_id):
        await ScopedStore(extension=env.NAME).put(
            f"{env.APP_FIXTURE_PREFIX}{env.GITHUB_PROVIDER}:list_issues", fixture
        )
        result = await call_external_tool(
            _ctx(workspace_id, APP_GRANTS),
            CallExternalToolInput(
                tool_name="list_issues",
                source_id=env.GITHUB_PROVIDER,
                arguments={},
            ),
        )

    assert _payload(result) == fixture


async def test_an_unseeded_app_tool_fails_loud(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id), pytest.raises(ValueError, match="no app fixture is seeded"):
        await call_external_tool(
            _ctx(workspace_id, APP_GRANTS),
            CallExternalToolInput(
                tool_name="list_pull_requests",
                source_id=env.GITHUB_PROVIDER,
                arguments={},
            ),
        )


async def test_app_action_applies_once_without_mutating_the_connector_fixture(db: None) -> None:
    workspace_id = await _workspace()
    fixture = {
        "repository": "evalco/app",
        "issues": [
            {
                "number": 521,
                "title": "Webhook retries lose delivery order",
                "owner": None,
            }
        ],
    }
    with ws(workspace_id):
        store = ScopedStore(extension=env.NAME)
        await store.put(f"{env.APP_FIXTURE_PREFIX}{env.GITHUB_PROVIDER}:list_issues", fixture)
        ctx = replace(
            _ctx(workspace_id, APP_GRANTS),
            ext=ExtensionContext(
                store=store,
                credentials=CredentialAccess(declared=frozenset()),
            ),
        )
        action = env.AppActionSpec(
            case="issue-owner",
            action="assign_issue",
            target="521",
            value="alex",
        )

        await env.AppActionStore().apply(
            ctx,
            "assign-521",
            action,
            None,
            expected_generation=None,
        )
        await env.AppActionStore().apply(
            ctx,
            "assign-521",
            action,
            action,
            expected_generation=None,
        )
        response = await env.EvalEnvBroker().execute(
            workspace_id,
            env.GITHUB_PROVIDER,
            "list_issues",
            {},
            env.ACCOUNT_ID,
            None,
        )
        stored = await env.AppActionStore().status(
            ctx,
            "assign-521",
            expected_generation=None,
        )
        action_fixture = await store.get(env.APP_ACTION_FIXTURE_PREFIX + "assign-521")

    assert response == fixture
    assert isinstance(action_fixture, dict)
    assert action_fixture["issues"][0]["owner"] == "alex"
    assert action_fixture["issues"][0]["project_status"] == "Assigned"
    assert stored == {"state": "applied", "result": "Issue #521 assigned to alex."}


async def test_parallel_app_actions_keep_case_fixture_state_isolated(db: None) -> None:
    workspace_id = await _workspace()
    fixture = {
        "repository": "evalco/app",
        "issues": [
            {
                "number": 521,
                "title": "Webhook retries lose delivery order",
                "owner": None,
            }
        ],
    }
    with ws(workspace_id):
        store = ScopedStore(extension=env.NAME)
        await store.put(f"{env.APP_FIXTURE_PREFIX}{env.GITHUB_PROVIDER}:list_issues", fixture)
        ctx = replace(
            _ctx(workspace_id, APP_GRANTS),
            ext=ExtensionContext(
                store=store,
                credentials=CredentialAccess(declared=frozenset()),
            ),
        )
        action = env.AppActionSpec(
            case="issue-owner",
            action="assign_issue",
            target="521",
            value="alex",
        )
        first_name = f"{uuid4().hex}-issue-owner"
        second_name = f"{uuid4().hex}-issue-owner"

        await asyncio.gather(
            env.AppActionStore().apply(ctx, first_name, action, None, expected_generation=None),
            env.AppActionStore().apply(ctx, second_name, action, None, expected_generation=None),
        )
        await env.AppActionStore().apply(ctx, first_name, action, action, expected_generation=None)
        first = await store.get(env.APP_ACTION_FIXTURE_PREFIX + first_name)
        second = await store.get(env.APP_ACTION_FIXTURE_PREFIX + second_name)
        shared = await store.get(f"{env.APP_FIXTURE_PREFIX}{env.GITHUB_PROVIDER}:list_issues")

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["issues"] == [
        {
            "number": 521,
            "title": "Webhook retries lose delivery order",
            "owner": "alex",
            "project_status": "Assigned",
        }
    ]
    assert second == first
    assert shared == fixture


async def test_call_without_a_grant_fails_loud() -> None:
    with pytest.raises(ValueError, match="connect one with connect_account"):
        await call_external_tool(
            _ctx(uuid4(), grants=()),
            CallExternalToolInput(
                tool_name="send_email",
                source_id=env.EMAIL_PROVIDER,
                arguments={"to": [BOB], "subject": "s", "body": "b"},
            ),
        )


async def test_describe_backfills_discovery_and_marks_unknown_unresolved() -> None:
    result = await describe_external_tools(
        _ctx(uuid4()),
        DescribeExternalToolsInput(
            source_id=env.EMAIL_PROVIDER,
            tool_names=("send_email", "bogus"),
            query="email",
        ),
    )

    payload = _payload(result)
    assert payload["unresolved"] == ["bogus"]
    assert set(payload["schemas"]) == {"send_email"}
    assert {tool["slug"] for tool in payload["availableTools"]} == {
        "send_email",
        "reply_all_email",
        "list_emails",
    }


@dataclass(frozen=True)
class _Landing:
    """Where one seeded page lands once the shipped condensing has run: whether it carries pointers
    at all, which side of the engine's inline budget it falls on, and how many copies of the literal
    its eval case grades survive. A case is only meaningful while its page lands here."""

    query: str
    pointers: bool
    inline: bool
    literal: str
    copies: int = 1


CODE_PAGES = (
    _Landing("widget-reserve", pointers=True, inline=True, literal=token("LIC", FLEET)),
    _Landing("widget-lease", pointers=True, inline=False, literal=token("LIC", FLEET)),
    _Landing("widget-capacity", pointers=True, inline=False, literal=token("LIC", FLEET)),
    _Landing("telemetry-flush", pointers=True, inline=True, literal=token("OWN", "orbital")),
    _Landing("ref-index", pointers=True, inline=True, literal=token("LIC", REF_MAIN)),
    _Landing("kernel-hold", pointers=True, inline=True, literal=token("LIC", KERNEL)),
    _Landing("fleet-telemetry", pointers=True, inline=True, literal=token("OWN", "orbital")),
    _Landing("catalog-sync", pointers=False, inline=True, literal=token("LIC", FLEET), copies=3),
    _Landing("ledger-post", pointers=True, inline=False, literal=token("LIC", LEDGER)),
    _Landing("ledger-audit", pointers=False, inline=False, literal=token("LIC", "svc22/api22")),
)


async def _seeded_code_index() -> None:
    store = ScopedStore(extension=env.NAME)
    for query, payload in QUERIES.items():
        await store.put(f"{env.CODE_FIXTURE_PREFIX}{query}", payload)


async def _searched(workspace_id: UUID, query: str) -> str:
    result = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
            tool_name="search_code",
            source_id=env.CODE_PROVIDER,
            arguments={"query": query},
        ),
    )
    return result.content[0].text


async def test_a_seeded_code_page_crosses_condensed_through_the_real_dispatch(db: None) -> None:
    """The eval substrate end to end: the page the suite seeds comes back through the production
    connector dispatch with the repeated repository replaced by a pointer to its first copy — so a
    case that grades the model's use of that pointer is grading the shipped seam."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seeded_code_index()
        text = await _searched(workspace_id, "widget-reserve")

    page = json.loads(text)
    assert page["items"][0]["repository"]["full_name"] == FLEET
    assert [hit["repository"] for hit in page["items"][1:]] == [
        {connector_tools.DEDUPE_REFERENCE_KEY: "/items/0/repository"}
    ] * 29


async def test_an_unseeded_code_query_fails_loud(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id), pytest.raises(ValueError, match="no code-search fixture"):
        await _searched(workspace_id, "never-seeded")


async def test_the_offloaded_pages_place_their_literal_and_decoy_across_the_preview(
    db: None,
) -> None:
    """What makes the two offloaded resolution cases bite. On `widget-lease` the graded literal is
    still inside the 6,144-char preview, so that case rests on its trajectory check: an answer that
    never opened the workspace file only matched the record it happened to see first. On
    `widget-capacity` the graded literal sits past the preview while a *different* repository's
    literal sits inside it, so the visible record is the wrong answer and no shortcut reaches the
    right one."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seeded_code_index()
        lease = await _searched(workspace_id, "widget-lease")
        capacity = await _searched(workspace_id, "widget-capacity")

    assert 0 < lease.find(token("LIC", FLEET)) < TOOL_RESULT_PREVIEW_CHARS
    assert capacity.find(token("LIC", FLEET)) > TOOL_RESULT_PREVIEW_CHARS
    assert 0 < capacity.find(token("LIC", LEGACY)) < TOOL_RESULT_PREVIEW_CHARS


def _ranked(query: str, path: tuple[str, ...]) -> JsonObject:
    page = json.loads(json.dumps(QUERIES[query]))
    for index, hit in enumerate(page["items"]):
        node = hit
        for key in path:
            node = node[key]
        node["result_rank"] = index
    return page
