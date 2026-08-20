"""The eval environment's seam proof: the fake mailbox, calendar, and code index are reached
through the real `call_external_tool` dispatch over a registry built from the extension's own
manifest, every mutation lands durably in the extension's tables — read back through a fresh
transaction, exactly as an eval grader reads them from another process — and every seeded code page
comes back condensed by the shipped pass, landing on the side of the engine's inline budget its
eval case measures."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
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

from evals.connector_refs import (
    FLEET,
    KERNEL,
    LEDGER,
    LEGACY,
    POINTER,
    QUERIES,
    REF_MAIN,
    REF_SWAPPED,
    TELEMETRY,
    token,
)
from evals.harness.harness import JsonObject
from ufo.connectors import ConnectorEntry, ConnectorRegistry, UnknownBrokerTool
from ufo.db import workspace_tx
from ufo.grants import Grant, GrantStore
from ufo.loop.engine import MAX_TOOL_RESULT_CHARS, TOOL_RESULT_PREVIEW_CHARS
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.context import ScopedStore
from ufo.tools.context import ToolContext
from ufo.workspace import ws

TOOL_NARRATION = "using the connected account"

BOB = "bob@evalco.test"


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


async def test_send_email_lands_a_durable_sent_row(db: None) -> None:
    workspace_id = await _workspace()

    result = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
            user_description=TOOL_NARRATION,
            tool_name="send_email",
            source_id=env.EMAIL_PROVIDER,
            arguments={"to": [BOB], "subject": "Dinner", "body": "Friday at 7pm works."},
        ),
    )

    assert _payload(result)["status"] == "sent"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(env.eval_env_email).where(
                    env.eval_env_email.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert row.folder == "sent"
    assert tuple(row.recipients) == (BOB,)
    assert row.sender == env.OWN_ADDRESS
    assert row.subject == "Dinner"


async def test_list_emails_reads_the_seeded_folder_with_filtering(db: None) -> None:
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        for sender, subject, folder, sent_at in (
            ("dana@evalco.test", "Budget planning", "inbox", datetime(2026, 7, 14, tzinfo=UTC)),
            ("bob@evalco.test", "Lunch?", "inbox", datetime(2026, 7, 15, tzinfo=UTC)),
            (env.OWN_ADDRESS, "Re: Lunch?", "sent", datetime(2026, 7, 15, 1, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(env.eval_env_email).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    folder=folder,
                    sender=sender,
                    recipients=["member@evalco.test"],
                    subject=subject,
                    body="body",
                    sent_at=sent_at,
                )
            )

    everything = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
            user_description=TOOL_NARRATION,
            tool_name="list_emails",
            source_id=env.EMAIL_PROVIDER,
            arguments={},
        ),
    )
    filtered = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
            user_description=TOOL_NARRATION,
            tool_name="list_emails",
            source_id=env.EMAIL_PROVIDER,
            arguments={"query": "budget"},
        ),
    )

    inbox = _payload(everything)["emails"]
    assert [email["subject"] for email in inbox] == ["Lunch?", "Budget planning"]
    assert [email["from"] for email in _payload(filtered)["emails"]] == ["dana@evalco.test"]


async def test_calendar_lifecycle_updates_and_cancels_durably(db: None) -> None:
    workspace_id = await _workspace()
    ctx = _ctx(workspace_id)

    created = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
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
                user_description=TOOL_NARRATION,
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
                user_description=TOOL_NARRATION,
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
                user_description=TOOL_NARRATION,
                tool_name="update_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"event_id": str(uuid4()), "title": "renamed"},
            ),
        )


async def test_unknown_slug_raises_for_the_wrong_provider() -> None:
    broker = env.EvalEnvBroker()
    with pytest.raises(UnknownBrokerTool):
        await broker.execute(uuid4(), env.EMAIL_PROVIDER, "create_event", {}, "acct", None)


async def test_describe_exposes_the_catalog_schemas() -> None:
    result = await describe_external_tools(
        _ctx(uuid4()),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION,
            source_id=env.EMAIL_PROVIDER,
            tool_names=("send_email",),
        ),
    )
    schema = _payload(result)["schemas"]["send_email"]
    assert set(schema["input_schema"]["properties"]) == {"to", "subject", "body"}


async def test_call_without_a_grant_fails_loud() -> None:
    with pytest.raises(ValueError, match="connect one with connect_account"):
        await call_external_tool(
            _ctx(uuid4(), grants=()),
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name="send_email",
                source_id=env.EMAIL_PROVIDER,
                arguments={"to": [BOB], "subject": "s", "body": "b"},
            ),
        )


async def test_describe_backfills_discovery_and_marks_unknown_unresolved() -> None:
    result = await describe_external_tools(
        _ctx(uuid4()),
        DescribeExternalToolsInput(
            user_description=TOOL_NARRATION,
            source_id=env.EMAIL_PROVIDER,
            tool_names=("send_email", "bogus"),
            query="email",
        ),
    )

    payload = _payload(result)
    assert payload["unresolved"] == ["bogus"]
    assert set(payload["schemas"]) == {"send_email"}
    assert {tool["slug"] for tool in payload["availableTools"]} == {"send_email", "list_emails"}


async def test_list_events_reads_the_calendar_with_filtering(db: None) -> None:
    workspace_id = await _workspace()
    ctx = _ctx(workspace_id)
    events = (
        ("Design sync", "2026-07-23T14:00:00+00:00", "2026-07-23T15:00:00+00:00"),
        ("1:1", "2026-07-22T10:00:00+00:00", "2026-07-22T11:00:00+00:00"),
    )
    for title, start, end in events:
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name="create_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"title": title, "start": start, "end": end, "attendees": []},
            ),
        )

    everything = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name="list_events",
                source_id=env.CALENDAR_PROVIDER,
                arguments={},
            ),
        )
    )
    filtered = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                user_description=TOOL_NARRATION,
                tool_name="list_events",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"query": "sync"},
            ),
        )
    )

    assert [event["title"] for event in everything["events"]] == ["1:1", "Design sync"]
    assert [event["title"] for event in filtered["events"]] == ["Design sync"]


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
            user_description=TOOL_NARRATION,
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


async def test_every_seeded_page_lands_where_its_case_measures(db: None) -> None:
    """The suite's fixture guard: each page keeps (or refuses) its pointers, falls on the side of
    `MAX_TOOL_RESULT_CHARS` its case was written around, and leaves exactly the number of copies of
    the graded literal the case expects — one for a condensed page, so reporting it is evidence the
    pointed-at record was reached. A fixture that drifts off this table fails here rather than
    silently grading a model against a different payload."""
    workspace_id = await _workspace()
    assert {landing.query for landing in CODE_PAGES} == set(QUERIES)
    with ws(workspace_id):
        await _seeded_code_index()
        landed = {
            landing.query: await _searched(workspace_id, landing.query) for landing in CODE_PAGES
        }

    for landing in CODE_PAGES:
        text = landed[landing.query]
        assert (POINTER in text) is landing.pointers, f"{landing.query} pointers"
        assert (len(text) <= MAX_TOOL_RESULT_CHARS) is landing.inline, (
            f"{landing.query} landed at {len(text)} chars"
        )
        assert text.count(landing.literal) == landing.copies, f"{landing.query} literal copies"


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


async def test_the_pointers_the_semantics_cases_grade_are_the_ones_emitted(db: None) -> None:
    """Each semantics case exists for one pointer shape, and this is that shape as the shipped pass
    writes it: a reference token escaping `/` and `~`, a target at a non-zero index, and a target
    that itself holds a reference — the two-hop shape, which is as deep as it goes, since a pointer
    is never written in place of another pointer."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seeded_code_index()
        refs = await _searched(workspace_id, "ref-index")
        kernel = await _searched(workspace_id, "kernel-hold")
        nested = await _searched(workspace_id, "fleet-telemetry")

    assert f'{POINTER}refs/acme~1widgets~0main"' in refs
    assert token("LIC", REF_SWAPPED) in refs
    assert f'{POINTER}items/3/repository"' in kernel
    assert f'{POINTER}items/2/repository"' in nested
    assert f'{POINTER}items/0/repository/owner"' in nested
    assert json.loads(nested)["items"][2]["repository"]["full_name"] == TELEMETRY


async def test_raising_the_dedupe_floor_takes_the_pointers_away(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor is what makes these pages point at all: above every record's size nothing is
    replaced, the single-repo page goes back to 30 full copies, and it lands past the inline budget
    again — so the suite's fixture guard fails rather than grading a payload with no references in
    it."""
    workspace_id = await _workspace()
    monkeypatch.setattr(connector_tools, "MIN_DEDUPE_BYTES", 1_000_000)
    with ws(workspace_id):
        await _seeded_code_index()
        text = await _searched(workspace_id, "widget-reserve")

    assert POINTER not in text
    assert len(text) > MAX_TOOL_RESULT_CHARS
    assert text.count(token("LIC", FLEET)) == 30


def _ranked(query: str, path: tuple[str, ...]) -> JsonObject:
    page = json.loads(json.dumps(QUERIES[query]))
    for index, hit in enumerate(page["items"]):
        node = hit
        for key in path:
            node = node[key]
        node["result_rank"] = index
    return page


async def test_pages_point_because_their_repeats_are_identical_at_each_level(db: None) -> None:
    """The other direction, one level at a time: the pages point because the repeated records are
    byte-identical, not because they look alike. Distinguishing each repository leaves the owner it
    embeds still identical, so that collapses and the page still points — the pass compares at
    every level, not only the outermost record. Distinguishing the owner too leaves nothing
    identical anywhere, no pointer is written, and the page lands past the inline budget again."""
    workspace_id = await _workspace()
    store = ScopedStore(extension=env.NAME)
    with ws(workspace_id):
        await store.put(
            f"{env.CODE_FIXTURE_PREFIX}ranked", _ranked("widget-reserve", ("repository",))
        )
        await store.put(
            f"{env.CODE_FIXTURE_PREFIX}distinct", _ranked("widget-reserve", ("repository", "owner"))
        )
        ranked = await _searched(workspace_id, "ranked")
        distinct = await _searched(workspace_id, "distinct")

    assert ranked.count(f'{POINTER}items/0/repository/owner"') == 29
    assert POINTER not in distinct
    assert len(distinct) > MAX_TOOL_RESULT_CHARS
