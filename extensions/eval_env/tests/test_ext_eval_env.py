"""The eval environment's seam proof: the fake mailbox and calendar are reached through the real
`call_external_tool` dispatch over a registry built from the extension's own manifest, and every
mutation lands durably in the extension's tables — read back through a fresh transaction, exactly
as an eval grader reads them from another process."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_eval_env.manifest as env
from ufo_ext_connectors.tools import (
    CallExternalToolInput,
    DescribeExternalToolsInput,
    call_external_tool,
    describe_external_tools,
)

from ufo.connectors import ConnectorEntry, ConnectorRegistry, UnknownBrokerTool
from ufo.db import workspace_tx
from ufo.grants import Grant, GrantStore
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.context import ToolContext

BOB = "bob@evalco.test"


@dataclass(frozen=True)
class _Grants(GrantStore):
    providers: tuple[tuple[str, str], ...]

    async def active_grants(self, _workspace_id: UUID, _agent_id: UUID) -> tuple[Grant, ...]:
        return tuple(
            Grant(
                provider=provider,
                account_id=env.ACCOUNT_ID,
                host=host,
                grantor_member_id=uuid4(),
                shared=True,
            )
            for provider, host in self.providers
        )


ALL_GRANTS = (
    (env.EMAIL_PROVIDER, env.EMAIL_HOST),
    (env.CALENDAR_PROVIDER, env.CALENDAR_HOST),
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
        audience_member_id=None,
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
        CallExternalToolInput(tool_name="list_emails", source_id=env.EMAIL_PROVIDER, arguments={}),
    )
    filtered = await call_external_tool(
        _ctx(workspace_id),
        CallExternalToolInput(
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


async def test_describe_exposes_the_catalog_schemas() -> None:
    result = await describe_external_tools(
        _ctx(uuid4()),
        DescribeExternalToolsInput(source_id=env.EMAIL_PROVIDER, tool_names=("send_email",)),
    )
    schema = _payload(result)["schemas"]["send_email"]
    assert set(schema["input_schema"]["properties"]) == {"to", "subject", "body"}


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
            source_id=env.EMAIL_PROVIDER, tool_names=("send_email", "bogus"), query="email"
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
                tool_name="create_event",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"title": title, "start": start, "end": end, "attendees": []},
            ),
        )

    everything = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name="list_events", source_id=env.CALENDAR_PROVIDER, arguments={}
            ),
        )
    )
    filtered = _payload(
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name="list_events",
                source_id=env.CALENDAR_PROVIDER,
                arguments={"query": "sync"},
            ),
        )
    )

    assert [event["title"] for event in everything["events"]] == ["1:1", "Design sync"]
    assert [event["title"] for event in filtered["events"]] == ["Design sync"]
