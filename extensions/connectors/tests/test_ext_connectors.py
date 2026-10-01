"""The connectors extension: the broker-generic dynamic tool surface over the `ConnectorRegistry`.

The extension imports only `ufo.sdk` and owns no provider — these tests drive its four tools over a
registry holding the sample extension's ConnectorProvider (a real installed broker, echoing every
execute back as its response) exactly as `serve` threads one onto the turn's ToolContext. What is
proved here is the orchestration: listing filters the registry, describe folds an unknown slug into
`unresolved` and backfills discovery from the broker's catalog, search renders the broker's
`BrokerSearch` and falls back to the connector's top tools when it recalled nothing, and a call
without the registry, or naming a provider no extension registers, fails loud. The grant-resolving
execute path keeps its end-to-end proof in the composio extension's tests."""

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
import ufo_ext_connectors.manifest as connectors
import ufo_ext_connectors.tools as connector_tools
from ufo_ext_connectors.tools import (
    CONNECTOR_FILES_DIR,
    CallExternalToolInput,
    DescribeExternalToolsInput,
    ListExternalToolsInput,
    call_external_tool,
    call_external_tool_standing_authorization,
    describe_external_tools,
    list_external_tools,
)
from ufo_ext_sample.connectors import (
    BROKER_TOOL_SLUG,
    BROKER_UPLOAD_PREFIX,
    CONNECTOR_HOST,
    CONNECTOR_LABEL,
    CONNECTOR_PROVIDER,
    SampleBroker,
)

from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from ufo.host.ext.loader import turn_tools
from ufo.runtime.access.connectors import (
    BrokerFile,
    BrokerSearch,
    BrokerTool,
    ConnectorEntry,
    ConnectorRegistry,
    StagedUpload,
)
from ufo.runtime.access.grants import Grant, GrantStore
from ufo.runtime.engine import MAX_TOOL_RESULT_CHARS
from ufo.runtime.ext.json_value import JsonValue
from ufo.runtime.tools.context import SpeakerRequired, ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.authproxy import Credential
from ufo.sdk.tools import TRUSTED_TOOL_INPUT

TOOL_NARRATION = "using the connected account"

OTHER_PROVIDER = "other_widgets"
OTHER_LABEL = "Other Widgets"

SLACK_SEND_SLUG = "SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL"
SLACK_UPDATE_SLUG = "SLACK_UPDATES_A_SLACK_MESSAGE"
SLACK_HISTORY_SLUG = "SLACK_FETCH_CONVERSATION_HISTORY"
SLACK_SCHEDULED_LIST_SLUG = "SLACK_LIST_SCHEDULED_MESSAGES"
SLACK_SCHEDULED_DELETE_SLUG = "SLACK_DELETE_A_SCHEDULED_MESSAGE"
GMAIL_SEND_SLUG = "GMAIL_SEND_EMAIL"
TEAMS_PROVIDER = "microsoft_teams"
TEAMS_SEND_SLUG = "MICROSOFT_TEAMS_SEND_MESSAGE_TO_CHANNEL"
SLACK_BODY_BLOCKS: list[JsonValue] = [
    {"type": "section", "text": {"type": "mrkdwn", "text": "the *plan* is posted"}}
]
SLACK_ACCOUNT = "slack-acct"


GRANT_OWNER_EMAIL = "owner@x.test"


@dataclass(frozen=True)
class _Grants(GrantStore):
    accounts: tuple[str, ...]
    provider: str = CONNECTOR_PROVIDER
    revoked: asyncio.Event | None = None
    private_accounts: frozenset[str] = frozenset()

    async def active_grants(self) -> tuple[Grant, ...]:
        if self.revoked is not None and self.revoked.is_set():
            return ()
        return tuple(
            Grant(
                id=uuid5(NAMESPACE_URL, f"grant/{self.provider}/{account}"),
                connection_id=uuid5(NAMESPACE_URL, f"connection/{self.provider}/{account}"),
                provider=self.provider,
                account_id=account,
                host=CONNECTOR_HOST,
                owner_member_id=uuid5(NAMESPACE_URL, f"owner/{self.provider}/{account}"),
                owner_email=GRANT_OWNER_EMAIL,
                connection_shared=account not in self.private_accounts,
            )
            for account in self.accounts
        )


@dataclass(frozen=True)
class _UnmatchedSearchBroker(SampleBroker):
    """A broker whose search recalls nothing for the query it is given — a term matcher fed a
    use-case sentence, which is what a broker without a semantic router answers."""

    listed: list[str] = field(default_factory=list)

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        self.listed.append(query)
        return await super().tools(workspace_id, provider, query)

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return BrokerSearch(tools=())


@dataclass(frozen=True)
class _AnySlugBroker(SampleBroker):
    """The sample broker widened past its one canned slug, so a call can name a real provider tool.
    Its execute still echoes back what it was dispatched."""

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        return {"slug": slug, "arguments": dict(arguments), "account": account_id}


@dataclass(frozen=True)
class _ReadBoundaryBroker(_AnySlugBroker):
    read_only: bool = False
    described: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        self.described.append(slug)
        return BrokerTool(slug=slug, read_only=self.read_only)

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        self.executed.append(slug)
        return await super().execute(
            workspace_id, provider, slug, arguments, account_id, idempotency_key
        )


@dataclass(frozen=True, kw_only=True)
class _ChangingToolBroker(_AnySlugBroker):
    live_slug: str
    live_read_only: bool
    described: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        self.described.append(slug)
        if len(self.described) == 1:
            return BrokerTool(slug=slug, read_only=True)
        return BrokerTool(slug=self.live_slug, read_only=self.live_read_only)

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        self.executed.append(slug)
        return await super().execute(
            workspace_id, provider, slug, arguments, account_id, idempotency_key
        )


@dataclass(frozen=True, kw_only=True)
class _SlackBroker(_AnySlugBroker):
    response: object

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        if account != SLACK_ACCOUNT:
            return Credential()

        def channel_info(request: httpx.Request) -> httpx.Response:
            if request.url.path != "/api/conversations.info":
                return httpx.Response(404, json={"ok": False, "error": "not_mocked"})
            if request.url.params.get("channel") != "C1":
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            return httpx.Response(200, json=self.response)

        return Credential(transport=httpx.MockTransport(channel_info))


@dataclass(frozen=True)
class _UnreadableSlackBroker(_AnySlugBroker):
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        raise RuntimeError("connector credential unavailable")


def _registry() -> ConnectorRegistry:
    broker = SampleBroker()
    return ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER, label=CONNECTOR_LABEL, broker=broker
            ),
            OTHER_PROVIDER: ConnectorEntry(
                provider=OTHER_PROVIDER, label=OTHER_LABEL, broker=broker
            ),
        }
    )


def _ctx(
    registry: ConnectorRegistry | None,
    accounts: tuple[str, ...] = (),
    sandbox: SandboxSession | None = None,
    provider: str = CONNECTOR_PROVIDER,
    connector_read_only: bool = False,
    revoked: asyncio.Event | None = None,
    is_main: bool = False,
    private_accounts: frozenset[str] = frozenset(),
) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="use a connector",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8", is_main=is_main),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=_Grants(accounts, provider, revoked, private_accounts),
        connectors=registry,
        connector_read_only=connector_read_only,
        idempotency_key="t1/call_external_tool/c1",
    )


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_manifest_declares_the_dynamic_tools_and_prompt_section() -> None:
    manifest = connectors.manifest()
    tools, _, _ = turn_tools((manifest,), None, audience=conversation_audience(None))
    names = {tool.name for tool in tools}
    assert {
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
    } <= names
    assert manifest.connectors == ()
    assert [section.name for section in manifest.prompt_sections] == [connectors.SECTION_NAME]


def test_call_external_tool_result_is_marked_untrusted() -> None:
    by_name = {tool.name: tool for tool in connectors.manifest().tools}
    assert by_name["call_external_tool"].untrusted is True
    assert by_name["call_external_tool"].side_effecting is True
    assert by_name["list_external_tools"].untrusted is False
    assert by_name["describe_external_tools"].untrusted is False


async def test_call_external_tool_standing_scope_ignores_payload_but_not_operation_class() -> None:
    broker = _ReadBoundaryBroker(read_only=True)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER,
                label=CONNECTOR_LABEL,
                broker=broker,
            )
        }
    )
    revoked = asyncio.Event()
    ctx = _ctx(registry, accounts=("alice@example.com",), revoked=revoked)
    first = await call_external_tool_standing_authorization(
        ctx,
        CallExternalToolInput(
            source_id=CONNECTOR_PROVIDER,
            tool_name="READ_MESSAGES",
            arguments={"query": "unread"},
        ),
    )
    second = await call_external_tool_standing_authorization(
        ctx,
        CallExternalToolInput(
            source_id=CONNECTOR_PROVIDER,
            tool_name="READ_MESSAGES",
            arguments={"query": "from:finance"},
        ),
    )

    assert first.scope == second.scope
    assert first.scope.account_id == "alice@example.com"
    assert first.scope.operation == "READ_MESSAGES"
    assert first.scope.access == "read"
    assert first.input.account_id == "alice@example.com"
    assert first.context.connector_selection is not None
    assert first.context.connector_binding == first.binding
    assert first.binding.connection_id == first.context.connector_selection.id
    assert first.binding.grant_id == first.context.connector_selection.grant_id

    write = await call_external_tool_standing_authorization(
        replace(
            ctx,
            connectors=replace(
                registry,
                entries={
                    CONNECTOR_PROVIDER: ConnectorEntry(
                        provider=CONNECTOR_PROVIDER,
                        label=CONNECTOR_LABEL,
                        broker=replace(broker, read_only=False),
                    )
                },
            ),
        ),
        CallExternalToolInput(
            source_id=CONNECTOR_PROVIDER,
            tool_name="READ_MESSAGES",
            arguments={"query": "unread"},
        ),
    )
    assert write.scope.access == "write"
    assert write.scope != first.scope
    revoked.set()
    with pytest.raises(ValueError, match=r"selected .* grant is no longer active"):
        await call_external_tool(first.context, first.input)
    assert broker.executed == []


@pytest.mark.parametrize(
    ("live_slug", "live_read_only"),
    (("READ_MESSAGES", False), ("REPLACED_OPERATION", True)),
)
async def test_call_external_tool_refuses_an_operation_changed_after_authorization(
    live_slug: str, live_read_only: bool
) -> None:
    broker = _ChangingToolBroker(live_slug=live_slug, live_read_only=live_read_only)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER,
                label=CONNECTOR_LABEL,
                broker=broker,
            )
        }
    )
    authorized = await call_external_tool_standing_authorization(
        _ctx(registry, accounts=("alice@example.com",)),
        CallExternalToolInput(
            source_id=CONNECTOR_PROVIDER,
            tool_name="READ_MESSAGES",
            arguments={"query": "unread"},
        ),
    )

    with pytest.raises(ValueError, match="operation changed after member authorization"):
        await call_external_tool(authorized.context, authorized.input)

    assert broker.described == ["READ_MESSAGES", "READ_MESSAGES"]
    assert broker.executed == []


async def test_private_connector_call_needs_a_multi_speaker_requester() -> None:
    broker = _ReadBoundaryBroker(read_only=True)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER,
                label=CONNECTOR_LABEL,
                broker=broker,
            )
        }
    )
    ctx = replace(
        _ctx(
            registry,
            accounts=("alice@example.com",),
            private_accounts=frozenset({"alice@example.com"}),
        ),
        other_members_active=True,
        member_messages_active=True,
    )

    with pytest.raises(SpeakerRequired):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                source_id=CONNECTOR_PROVIDER,
                tool_name="READ_MESSAGES",
                arguments={},
            ),
        )

    assert broker.described == []
    assert broker.executed == []


async def test_list_external_tools_names_each_connected_account_owner_and_sharing() -> None:
    result = await list_external_tools(
        _ctx(_registry(), accounts=("acct-1", "acct-2")),
        ListExternalToolsInput(queries=(f"select:{CONNECTOR_PROVIDER}",)),
    )
    (row,) = _payload(result)["connectors"]
    assert row["connected_accounts"] == [
        {"account_id": "acct-1", "owner": GRANT_OWNER_EMAIL, "shared": True},
        {"account_id": "acct-2", "owner": GRANT_OWNER_EMAIL, "shared": True},
    ]


async def test_list_external_tools_hides_private_accounts_from_a_workspace_main_agent() -> None:
    result = await list_external_tools(
        _ctx(
            _registry(),
            accounts=("private", "shared"),
            is_main=True,
            private_accounts=frozenset({"private"}),
        ),
        ListExternalToolsInput(queries=(f"select:{CONNECTOR_PROVIDER}",)),
    )
    (row,) = _payload(result)["connectors"]
    assert row["connected_accounts"] == [
        {"account_id": "shared", "owner": GRANT_OWNER_EMAIL, "shared": True}
    ]


async def test_describe_external_tools_marks_an_unknown_name_unresolved() -> None:
    result = await describe_external_tools(
        _ctx(_registry()),
        DescribeExternalToolsInput(
            source_id=CONNECTOR_PROVIDER,
            tool_names=("NOT_A_REAL_SLUG",),
        ),
    )
    payload = _payload(result)
    assert payload["unresolved"] == ["NOT_A_REAL_SLUG"]
    assert [tool["slug"] for tool in payload["availableTools"]] == [BROKER_TOOL_SLUG]


async def test_call_external_tool_uses_the_only_connected_account() -> None:
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"limit": 2},
        ),
    )
    assert _payload(result)["account"] == "acct-one"


async def test_read_only_turn_redescribes_and_executes_a_connector_read() -> None:
    broker = _ReadBoundaryBroker(read_only=True)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER,
                label=CONNECTOR_LABEL,
                broker=broker,
            )
        }
    )

    result = await call_external_tool(
        _ctx(registry, accounts=("acct-one",), connector_read_only=True),
        CallExternalToolInput(
            tool_name="list_records",
            source_id=CONNECTOR_PROVIDER,
            arguments={},
        ),
    )

    assert _payload(result)["slug"] == "list_records"
    assert broker.described == ["list_records"]
    assert broker.executed == ["list_records"]


@pytest.mark.parametrize("slug", ("create_record", "tool_without_metadata"))
async def test_read_only_turn_refuses_mutating_or_unclassified_connector_tools(slug: str) -> None:
    """The refusal says which tool, which provider, and that re-issuing it refuses again."""
    broker = _ReadBoundaryBroker(read_only=False)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER,
                label=CONNECTOR_LABEL,
                broker=broker,
            )
        }
    )

    result = await call_external_tool(
        _ctx(registry, accounts=("acct-one",), connector_read_only=True),
        CallExternalToolInput(
            tool_name=slug,
            source_id=CONNECTOR_PROVIDER,
            arguments={},
        ),
    )

    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert failure["operation"] == f"{CONNECTOR_PROVIDER}.{slug}"
    assert slug in failure["summary"]
    assert "read" in failure["summary"]
    assert failure["applied"] == []
    assert broker.described == [slug]
    assert broker.executed == []


@pytest.mark.parametrize(
    ("response", "marked"),
    [
        ({"ok": True, "channel": {"id": "C1", "is_ext_shared": False}}, True),
        ({"ok": True, "channel": {"id": "C1", "is_ext_shared": True}}, False),
        ({"ok": True, "channel": {"id": "C1", "is_pending_ext_shared": True}}, False),
        ({"ok": True, "channel": {"id": "C1", "is_org_shared": True}}, False),
        ({"ok": True, "channel": {"id": "C1", "is_shared": True}}, False),
        ({"ok": False, "channel": {"id": "C1"}, "error": "channel_not_found"}, False),
        ({"ok": True, "channel": {}}, False),
        ([{"ok": True, "channel": {"id": "C1"}}], False),
        ("ok", False),
    ],
    ids=[
        "internal",
        "ext_shared",
        "pending_ext_shared",
        "org_shared",
        "shared",
        "unavailable",
        "incomplete",
        "list_body",
        "string_body",
    ],
)
async def test_a_slack_send_uses_its_connector_account_to_settle_the_footer(
    response: object, marked: bool
) -> None:
    broker = _SlackBroker(response=response)
    registry = ConnectorRegistry(
        entries={
            connector_tools.SLACK_PROVIDER: ConnectorEntry(
                provider=connector_tools.SLACK_PROVIDER,
                label="Slack",
                broker=broker,
            )
        }
    )
    arguments = {"channel": "C1", "text": "the plan is posted"}

    result = await call_external_tool(
        _ctx(
            registry,
            accounts=(SLACK_ACCOUNT,),
            provider=connector_tools.SLACK_PROVIDER,
        ),
        CallExternalToolInput(
            tool_name=SLACK_SEND_SLUG,
            source_id=connector_tools.SLACK_PROVIDER,
            arguments=arguments,
        ),
    )

    payload = _payload(result)
    assert payload["account"] == SLACK_ACCOUNT
    if marked:
        assert payload["arguments"]["blocks"][-1] == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "*Sent using* ufo"}],
        }
    else:
        assert payload["arguments"] == arguments


@pytest.mark.parametrize(
    "arguments",
    [
        {"channel": "C1", "text": "the plan is posted"},
        {"text": "the plan is posted"},
    ],
    ids=["credential_unavailable", "channel_missing"],
)
async def test_an_unreadable_slack_destination_is_published_unmarked(
    arguments: dict[str, JsonValue],
) -> None:
    broker = _UnreadableSlackBroker()
    registry = ConnectorRegistry(
        entries={
            connector_tools.SLACK_PROVIDER: ConnectorEntry(
                provider=connector_tools.SLACK_PROVIDER,
                label="Slack",
                broker=broker,
            )
        }
    )

    result = await call_external_tool(
        _ctx(
            registry,
            accounts=(SLACK_ACCOUNT,),
            provider=connector_tools.SLACK_PROVIDER,
        ),
        CallExternalToolInput(
            tool_name=SLACK_SEND_SLUG,
            source_id=connector_tools.SLACK_PROVIDER,
            arguments=arguments,
        ),
    )

    assert _payload(result)["arguments"] == arguments


def test_a_text_body_past_one_text_object_is_chunked_rather_than_refused() -> None:
    limit = connector_tools.SLACK_SECTION_TEXT_LIMIT
    body = "x" * (2 * limit + 1)
    attributed = connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER,
        SLACK_SEND_SLUG,
        {"channel": "C1", "text": body},
        destination_internal=True,
    )
    blocks = attributed["blocks"]
    assert isinstance(blocks, list)
    *chunks, footer = blocks
    assert [chunk["type"] for chunk in chunks] == ["section"] * 3
    assert [len(chunk["text"]["text"]) for chunk in chunks] == [limit, limit, 1]
    assert "".join(chunk["text"]["text"] for chunk in chunks) == body
    assert footer == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "*Sent using* ufo"}],
    }
    assert attributed["text"] == body


def test_a_body_is_never_retyped_into_the_other_markup_language() -> None:
    mrkdwn_body = "*posted* the <https://plan.test|plan>"
    markdown_body = "**posted** the [plan](https://plan.test)"
    for body, block in (
        (
            {"text": mrkdwn_body},
            {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn_body}},
        ),
        ({"markdown_text": markdown_body}, {"type": "markdown", "text": markdown_body}),
    ):
        attributed = connector_tools.slack_attributed(
            connector_tools.SLACK_PROVIDER,
            SLACK_SEND_SLUG,
            {"channel": "C1", **body},
            destination_internal=True,
        )
        assert attributed["blocks"] == [
            block,
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "*Sent using* ufo"}]},
        ]


def test_a_markdown_body_past_the_payloads_markdown_cap_stays_one_block() -> None:
    body = "x" * (connector_tools.SLACK_MARKDOWN_TEXT_LIMIT + 1)
    attributed = connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER,
        SLACK_SEND_SLUG,
        {"channel": "C1", "markdown_text": body},
        destination_internal=True,
    )
    assert attributed == {
        "channel": "C1",
        "blocks": [
            {"type": "markdown", "text": body},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "*Sent using* ufo"}]},
        ],
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"channel": "C1", "text": "the plan is posted"},
        {"channel": "C1", "markdown_text": "the *plan* is posted"},
        {"channel": "C1", "blocks": list(SLACK_BODY_BLOCKS)},
    ],
    ids=["text", "markdown_text", "blocks"],
)
def test_a_destination_not_proved_internal_is_published_unmarked(arguments: dict) -> None:
    """The audience guard, in every body shape a send authors."""
    assert (
        connector_tools.slack_attributed(
            connector_tools.SLACK_PROVIDER,
            SLACK_SEND_SLUG,
            arguments,
            destination_internal=False,
        )
        == arguments
    )
    assert (
        connector_tools.slack_attributed(
            TEAMS_PROVIDER, TEAMS_SEND_SLUG, arguments, destination_internal=True
        )
        == arguments
    )


def test_the_model_cannot_choose_the_attribution_identity() -> None:
    """The optional bot identity belongs to the Slack hook, never the model."""
    wire = {
        "tool_name": SLACK_SEND_SLUG,
        "source_id": connector_tools.SLACK_PROVIDER,
        "arguments": {"channel": "C1", "text": "the plan is posted"},
    }
    schema = CallExternalToolInput.model_json_schema()["properties"]
    assert "attribution_bot_user_id" not in schema
    assert CallExternalToolInput.model_validate(wire).attribution_bot_user_id is None
    claimed = CallExternalToolInput.model_validate({**wire, "attribution_bot_user_id": "U0MODEL"})
    assert claimed.attribution_bot_user_id is None
    for context in (None, "TRUSTED_TOOL_INPUT", {"trusted": True}, object()):
        assert (
            CallExternalToolInput.model_validate(
                {**wire, "attribution_bot_user_id": "U0MODEL"}, context=context
            ).attribution_bot_user_id
            is None
        )
    restored = CallExternalToolInput.model_validate_json(
        claimed.model_copy(update={"attribution_bot_user_id": "U0HOOK"}).model_dump_json(),
        context=TRUSTED_TOOL_INPUT,
    )
    assert restored.attribution_bot_user_id == "U0HOOK"
    assert (
        claimed.model_copy(update={"attribution_bot_user_id": "U0HOOK"}).attribution_bot_user_id
        == "U0HOOK"
    )


@pytest.mark.parametrize(
    ("slug", "arguments"),
    [
        (SLACK_SCHEDULED_LIST_SLUG, {"channel": "C1"}),
        (SLACK_SCHEDULED_DELETE_SLUG, {"channel": "C1", "scheduled_message_id": "Q1"}),
    ],
    ids=["listing", "scheduled_delete"],
)
def test_a_call_carrying_no_body_is_never_given_one(slug: str, arguments: dict) -> None:
    """Both slugs pass the substring gate — "scheduled" carries "schedule" — and neither
    publishes a message."""
    attributed = connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER, slug, arguments, destination_internal=True
    )
    assert attributed == arguments
    assert "blocks" not in attributed


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": "the plan is posted"},
        {"markdown_text": "the *plan* is posted"},
        {"blocks": list(SLACK_BODY_BLOCKS), "text": "the plan is posted"},
        {"blocks": json.dumps(SLACK_BODY_BLOCKS)},
        {"blocks": quote(json.dumps(SLACK_BODY_BLOCKS))},
    ],
    ids=["text", "markdown_text", "blocks", "serialized_blocks", "url_encoded_blocks"],
)
def test_slack_attributed_never_stacks_the_footer_in_any_body_shape(arguments: dict) -> None:
    once = connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER, SLACK_SEND_SLUG, arguments, destination_internal=True
    )
    assert once != arguments
    assert (
        connector_tools.slack_attributed(
            connector_tools.SLACK_PROVIDER, SLACK_SEND_SLUG, once, destination_internal=True
        )
        == once
    )


def test_the_never_stack_guard_reads_a_whole_line_and_never_a_prefix() -> None:
    for footer in (
        "Sent using ufo",
        "Sent using <@U0BOT>",
        "*Sent using* ufo",
        "*Sent using* <@U0BOT>",
    ):
        footered = f"the plan is posted\n\n{footer}"
        assert connector_tools.slack_attributed(
            connector_tools.SLACK_PROVIDER,
            SLACK_SEND_SLUG,
            {"text": footered},
            destination_internal=True,
        ) == {"text": footered}
    for body in ("Sent using an iPhone", "Sent using ufo to draft this", "a Sent using ufo joke"):
        attributed = connector_tools.slack_attributed(
            connector_tools.SLACK_PROVIDER,
            SLACK_SEND_SLUG,
            {"text": body},
            destination_internal=True,
        )
        assert attributed["blocks"] == [
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "*Sent using* ufo"}]},
        ]


def test_the_inbound_strip_reaches_a_footer_the_outbound_guard_will_not_read() -> None:
    """The two reads over one footer, split because they want opposite tolerances."""
    body = "the plan is posted"
    mention_footer = connector_tools.ATTRIBUTION_MRKDWN.format(subject="<@U0BOT>")
    flattened = f"{body}  {mention_footer}"
    assert connector_tools.attribution_stripped(f"{flattened}  {mention_footer}") == body
    assert connector_tools.attribution_stripped(f"{body} Sent using ufo, and quoted") == (
        f"{body}, and quoted"
    )
    assert not connector_tools.ATTRIBUTION_LINE.search(flattened)
    ends_the_way = "we posted it. Sent using an iPhone"
    assert connector_tools.attribution_stripped(ends_the_way) == ends_the_way
    assert "blocks" in connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER,
        SLACK_SEND_SLUG,
        {"text": ends_the_way},
        destination_internal=True,
    )


async def test_tools_fail_loud_without_the_registry_or_the_provider() -> None:
    with pytest.raises(RuntimeError, match="connector registry"):
        await list_external_tools(_ctx(None), ListExternalToolsInput(queries=("x",)))
    with pytest.raises(KeyError, match="no installed connector"):
        await call_external_tool(
            _ctx(_registry()),
            CallExternalToolInput(
                tool_name="X",
                source_id="unregistered",
                arguments={},
            ),
        )


async def _sandbox(workspace_root: Path) -> SandboxSession:
    """A real session over the local carrier: the file bridge's bash, curl, and md5 preflight run
    for real against a host-directory workspace — nothing here asserts a fake."""
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="unused",
            workspace_host_path=str(workspace_root),
            proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
            run_token="run-token",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


@dataclass(frozen=True)
class _FileBroker:
    """Stands in for a broker whose file store mints what the sample broker cannot — a hostile
    output name, a dash-leading URL."""

    outputs: tuple[BrokerFile, ...] = ()
    put_url: str | None = None
    response: dict[str, object] | None = None

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if self.response is not None:
            return self.response
        return {"successful": True, "arguments": dict(arguments)}

    def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]:
        return self.outputs

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload:
        return StagedUpload(
            put_url=self.put_url,
            content_type=mimetype,
            argument={"name": filename, "mimetype": mimetype, "s3key": "store/key/1"},
        )


@dataclass(frozen=True)
class _RevokingFileBroker(_FileBroker):
    revoked: asyncio.Event = field(default_factory=asyncio.Event)
    executed: list[str] = field(default_factory=list)

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        self.executed.append(slug)
        return await super().execute(
            workspace_id, provider, slug, arguments, account_id, idempotency_key
        )

    async def stage_upload(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        filename: str,
        mimetype: str,
        md5: str,
    ) -> StagedUpload:
        self.revoked.set()
        return await super().stage_upload(workspace_id, provider, slug, filename, mimetype, md5)


def _file_registry(broker: _FileBroker) -> ConnectorRegistry:
    return ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER, label=CONNECTOR_LABEL, broker=broker
            )
        }
    )


async def test_call_external_tool_fetches_produced_files_into_the_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "store" / "Order_Form.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4 attachment bytes")
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"file_output_urls": [f"file://{source}"]},
        ),
    )
    files = _payload(result)["workspace_files"]
    assert [file["name"] for file in files] == ["Order_Form.pdf"]
    fetched = files[0]["workspace_path"]
    assert fetched.startswith(f"{WORKSPACE_DIR}/{CONNECTOR_FILES_DIR}/")
    on_disk = workspace / Path(fetched).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == source.read_bytes()


async def test_call_external_tool_stages_a_workspace_file_argument(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.md5(b"a,b\n1,2\n", usedforsecurity=False).hexdigest()
    key = f"{BROKER_UPLOAD_PREFIX}-{digest}-report.csv"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"media": {"workspace_file": "/workspace/report.csv"}},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["media"] == {"name": "report.csv", "mimetype": "text/csv", "s3key": key}
    assert (workspace / key).read_bytes() == b"a,b\n1,2\n"


async def test_a_revoked_grant_cannot_execute_after_file_staging(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.csv").write_bytes(b"a,b\n1,2\n")
    broker = _RevokingFileBroker()

    with pytest.raises(ValueError, match="grant is no longer active"):
        await call_external_tool(
            _ctx(
                _file_registry(broker),
                accounts=("acct-one",),
                sandbox=await _sandbox(workspace),
                revoked=broker.revoked,
            ),
            CallExternalToolInput(
                tool_name=BROKER_TOOL_SLUG,
                source_id=CONNECTOR_PROVIDER,
                arguments={"media": {"workspace_file": "/workspace/report.csv"}},
            ),
        )

    assert broker.executed == []


async def test_a_produced_files_name_cannot_escape_its_workspace_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "store" / "legit.txt"
    source.parent.mkdir()
    source.write_bytes(b"payload")
    broker = _FileBroker(outputs=(BrokerFile(name="../../evil.txt", url=f"file://{source}"),))
    result = await call_external_tool(
        _ctx(_file_registry(broker), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name="ANY",
            source_id=CONNECTOR_PROVIDER,
            arguments={},
        ),
    )
    files = _payload(result)["workspace_files"]
    assert files[0]["name"] == "evil.txt"
    on_disk = workspace / Path(files[0]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == b"payload"
    assert not (tmp_path / "evil.txt").exists()


async def test_a_workspace_file_argument_cannot_climb_out_of_the_workspace(tmp_path: Path) -> None:
    """The model chooses this path and the bytes leave for the provider's file store, so a name
    climbing above `/workspace` is refused before anything is measured or staged."""
    workspace = tmp_path / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (tmp_path / "outside.txt").write_bytes(b"host secret")

    with pytest.raises(ValueError, match="escapes"):
        await call_external_tool(
            _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
            CallExternalToolInput(
                tool_name=BROKER_TOOL_SLUG,
                source_id=CONNECTOR_PROVIDER,
                arguments={"media": {"workspace_file": "/workspace/sub/../../outside.txt"}},
            ),
        )

    assert not list(workspace.glob(f"{BROKER_UPLOAD_PREFIX}*"))


async def test_a_missing_workspace_file_argument_fails_loud(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match=r"cannot read workspace file|No such file"):
        await call_external_tool(
            _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
            CallExternalToolInput(
                tool_name=BROKER_TOOL_SLUG,
                source_id=CONNECTOR_PROVIDER,
                arguments={"media": {"workspace_file": "/workspace/absent.pdf"}},
            ),
        )


async def test_an_over_cap_workspace_file_is_rejected_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload leg bounds bytes at the call: routed through the real sample broker, a file over
    the cap fails loud before a slot is minted or the sandbox PUTs anything — no store object."""
    monkeypatch.setattr(connector_tools, "TRANSFER_MAX_BYTES", 8)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "big.csv").write_bytes(b"0123456789abcdef")
    with pytest.raises(ValueError, match=r"over the 8-byte limit"):
        await call_external_tool(
            _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
            CallExternalToolInput(
                tool_name=BROKER_TOOL_SLUG,
                source_id=CONNECTOR_PROVIDER,
                arguments={"media": {"workspace_file": "/workspace/big.csv"}},
            ),
        )
    assert not any(p.name.startswith(BROKER_UPLOAD_PREFIX) for p in workspace.iterdir())


async def test_a_deduped_upload_slot_skips_the_put(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.md5(b"a,b\n1,2\n", usedforsecurity=False).hexdigest()
    key = f"{BROKER_UPLOAD_PREFIX}-{digest}-report.csv"
    ctx = _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace))
    call = CallExternalToolInput(
        tool_name=BROKER_TOOL_SLUG,
        source_id=CONNECTOR_PROVIDER,
        arguments={"media": {"workspace_file": "/workspace/report.csv"}},
    )
    first = await call_external_tool(ctx, call)
    assert (workspace / key).read_bytes() == b"a,b\n1,2\n"
    (workspace / key).unlink()
    second = await call_external_tool(ctx, call)
    assert _payload(second)["arguments"]["media"] == _payload(first)["arguments"]["media"]
    assert not (workspace / key).exists()


async def test_marked_binary_content_is_written_to_the_workspace_and_referenced(
    tmp_path: Path,
) -> None:
    """Bytes that are not text never enter context: the field becomes a workspace reference, the
    decoded bytes are really on disk, and the base64 is gone from the result the model sees."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    encoded = base64.b64encode(png).decode()
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "attachments": [{"data": encoded, "encoding": "base64", "filename": "logo.png"}]
            },
        ),
    )
    attachment = _payload(result)["arguments"]["attachments"][0]
    reference = attachment["data"]
    assert set(reference) == {"name", "workspace_path", "mimetype", "bytes"}
    assert reference["name"] == "logo.png"
    assert reference["mimetype"] == "image/png"
    assert reference["bytes"] == len(png)
    assert attachment["encoding"] == "offloaded"
    on_disk = workspace / Path(reference["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == png
    assert encoded not in result.content[0].text


async def test_marked_text_over_the_inline_cap_is_offloaded_not_inlined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decoded text is only inlined while it is small enough to be worth reading: past the cap it
    lands in the workspace like binary, so a huge file cannot re-enter context on every round."""
    monkeypatch.setattr(connector_tools, "MAX_INLINE_DECODED_CHARS", 8)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    body = "a long readable document\n"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "body": base64.b64encode(body.encode()).decode(),
                "content_encoding": "base64",
                "path": "docs/notes.txt",
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content_encoding"] == "offloaded"
    assert echoed["body"]["mimetype"] == "text/plain"
    on_disk = workspace / Path(echoed["body"]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == body.encode()


async def test_base64_shaped_values_are_untouched_without_the_providers_marker(
    tmp_path: Path,
) -> None:
    """Translation keys on the marker, never on how a string looks. An id, a digest, and a content
    field under a non-base64 encoding are all valid base64 by shape and must survive verbatim."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arguments: dict[str, object] = {
        "sha": "ZGVhZGJlZWY=",
        "cursor": "Y3Vyc29yOjEyMw==",
        "page": {"content": base64.b64encode(b"hello").decode(), "encoding": "utf-8"},
        "ids": ["aGVsbG8=", "d29ybGQ="],
    }
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=arguments,
        ),
    )
    assert _payload(result)["arguments"] == arguments
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


async def test_a_decoded_fields_name_cannot_escape_its_workspace_dir(tmp_path: Path) -> None:
    """The offloaded file is named by the provider, so it is basenamed like a produced file: a
    traversing name writes inside `connector_files/`, never above the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "data": base64.b64encode(png).decode(),
                "encoding": "base64",
                "name": "../../evil.png",
            },
        ),
    )
    reference = _payload(result)["arguments"]["data"]
    assert reference["name"] == "evil.png"
    on_disk = workspace / Path(reference["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == png
    assert not (tmp_path / "evil.png").exists()

    climbed = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "data": base64.b64encode(png).decode(),
                "encoding": "base64",
                "name": "../..",
            },
        ),
    )
    escaped = _payload(climbed)["arguments"]["data"]
    assert escaped["name"] == connector_tools.FALLBACK_FILENAME
    written = Path(escaped["workspace_path"])
    assert written.parent.parent.name == CONNECTOR_FILES_DIR
    assert (workspace / written.relative_to(WORKSPACE_DIR)).read_bytes() == png


async def test_a_response_nested_past_the_depth_cap_survives_untranslated(tmp_path: Path) -> None:
    """Broker output is untrusted, and on a broker whose `file_outputs` reads fixed keys this
    walk is the only recursive pass over it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    deep: dict[str, object] = {"leaf": "bottom"}
    for _ in range(600):
        deep = {"nested": deep}
    broker = _FileBroker(
        response={
            "content": base64.b64encode(b"shallow\n").decode(),
            "encoding": "base64",
            "name": "s.txt",
            "deep": deep,
        }
    )
    result = await call_external_tool(
        _ctx(_file_registry(broker), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name="ANY",
            source_id=CONNECTOR_PROVIDER,
            arguments={},
        ),
    )
    payload = _payload(result)
    assert payload["content"] == "shallow\n"
    assert payload["encoding"] == "utf-8"
    node: object = payload["deep"]
    for _ in range(600):
        assert isinstance(node, dict)
        node = node["nested"]
    assert node == {"leaf": "bottom"}


async def test_a_field_over_the_decode_cap_is_never_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decode is one C call that holds the GIL for its whole duration, so no worker thread
    can keep a huge field off the serve loop — only refusing it can."""
    monkeypatch.setattr(connector_tools, "MAX_DECODE_CHARS", 16)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    encoded = base64.b64encode(b"far past the decode cap").decode()
    assert len(encoded) > 16
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"content": encoded, "encoding": "base64", "name": "big.bin"},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == encoded
    assert echoed["encoding"] == "base64"
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


async def test_a_huge_non_data_url_string_is_never_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching the data-URL pattern scans the whole string, so an ordinary long field that
    merely begins `data:` would cost a full pass (measured ~15 ms at the cap, ~315 ms at 32 MiB)."""
    monkeypatch.setattr(connector_tools, "MAX_DECODE_CHARS", 32)
    scanned: list[int] = []
    real_pattern = connector_tools.DATA_URL_RE

    class _RecordingPattern:
        def match(self, value: str) -> object:
            scanned.append(len(value))
            return real_pattern.match(value)

    monkeypatch.setattr(connector_tools, "DATA_URL_RE", _RecordingPattern())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prose = "data: the following rows were exported " + "x" * 200
    small = f"data:text/plain;base64,{base64.b64encode(b'hi').decode()}"
    assert len(small) <= 32
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"summary": prose, "note": small},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["summary"] == prose
    assert echoed["note"] == "hi"
    assert scanned == [len(small)], "the over-cap string must never reach the pattern"


async def test_one_invalid_sibling_does_not_hold_back_a_valid_marked_field(
    tmp_path: Path,
) -> None:
    """Marked fields stand on their own."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = "changelog\n"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "content": base64.b64encode(note.encode()).decode(),
                "data": "binary file (use raw endpoint)",
                "encoding": "base64",
                "name": "n.txt",
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == note
    assert echoed["data"] == "binary file (use raw endpoint)"
    assert echoed["encoding"] == "base64"


async def test_concurrent_calls_decoding_one_payload_never_expose_a_partial_file(
    tmp_path: Path,
) -> None:
    """Content addressing means two turns that decode the same bytes write one path, and no
    carrier's write is atomic."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    blob = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40
    call = CallExternalToolInput(
        tool_name=BROKER_TOOL_SLUG,
        source_id=CONNECTOR_PROVIDER,
        arguments={
            "data": base64.b64encode(blob).decode(),
            "encoding": "base64",
            "name": "shared.png",
        },
    )
    sandboxes = [await _sandbox(workspace) for _ in range(4)]
    results = await asyncio.gather(
        *(
            call_external_tool(_ctx(_registry(), accounts=("acct-one",), sandbox=box), call)
            for box in sandboxes
        )
    )
    references = [_payload(result)["arguments"]["data"] for result in results]
    assert len({reference["workspace_path"] for reference in references}) == 1
    on_disk = workspace / Path(references[0]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == blob
    assert [p.name for p in workspace.rglob("*.part")] == []
    assert len([p for p in workspace.rglob("*") if p.is_file()]) == 1


class _RecordingCarrier:
    """The real local carrier with every copy-in path recorded, so a test can assert *where* the
    bytes were put, not just what ended up on disk. Every call delegates; nothing is simulated."""

    def __init__(self, inner: LocalCarrier) -> None:
        self.inner = inner
        self.writes: list[str] = []

    async def write(self, handle: object, path: str, content: bytes) -> None:
        self.writes.append(path)
        await self.inner.write(handle, path, content)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


async def test_decoded_bytes_are_never_written_straight_to_their_shared_path(
    tmp_path: Path,
) -> None:
    """The content-addressed path is shared by every turn that decodes the same bytes, and no
    carrier's write is atomic."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="unused",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
            run_token="run-token",
        )
    )
    recorder = _RecordingCarrier(carrier)
    blob = b"\x89PNG\r\n\x1a\n" + bytes(range(200, 256))
    result = await call_external_tool(
        _ctx(
            _registry(),
            accounts=("acct-one",),
            sandbox=SandboxSession(carrier=recorder, handle=handle),
        ),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "data": base64.b64encode(blob).decode(),
                "encoding": "base64",
                "name": "shot.png",
            },
        ),
    )
    target = _payload(result)["arguments"]["data"]["workspace_path"]
    assert recorder.writes, "the decoded bytes were never written"
    assert target not in recorder.writes, "bytes went straight to the shared content-addressed path"
    assert all(path.endswith(".part") for path in recorder.writes)
    assert (workspace / Path(target).relative_to(WORKSPACE_DIR)).read_bytes() == blob


REPOSITORY_URL_KEYS = (
    "forks",
    "keys",
    "collaborators",
    "teams",
    "hooks",
    "issue_events",
    "events",
    "assignees",
    "branches",
    "tags",
    "blobs",
    "git_tags",
    "git_refs",
    "trees",
    "statuses",
    "languages",
    "stargazers",
    "contributors",
    "subscribers",
    "subscription",
    "commits",
    "git_commits",
    "comments",
    "issue_comment",
    "contents",
    "compare",
    "merges",
    "archive",
    "downloads",
    "issues",
    "pulls",
    "milestones",
    "notifications",
    "labels",
    "releases",
    "deployments",
)


def _repository(owner: str = "acme", name: str = "widgets") -> dict[str, object]:
    api = f"https://api.github.com/repos/{owner}/{name}"
    return {
        "id": 1292760912 + len(f"{owner}/{name}"),
        "node_id": f"R_kgDOTQ33{owner}",
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": True,
        "owner": {
            "login": owner,
            "id": 295985267 + len(owner),
            "node_id": f"O_kgDOEaRgcw{owner}",
            "avatar_url": f"https://avatars.githubusercontent.com/u/{owner}?v=4",
            "gravatar_id": "",
            "url": f"https://api.github.com/users/{owner}",
            "html_url": f"https://github.com/{owner}",
            "followers_url": f"https://api.github.com/users/{owner}/followers",
            "following_url": f"https://api.github.com/users/{owner}/following{{/other_user}}",
            "gists_url": f"https://api.github.com/users/{owner}/gists{{/gist_id}}",
            "starred_url": f"https://api.github.com/users/{owner}/starred{{/owner}}{{/repo}}",
            "subscriptions_url": f"https://api.github.com/users/{owner}/subscriptions",
            "organizations_url": f"https://api.github.com/users/{owner}/orgs",
            "repos_url": f"https://api.github.com/users/{owner}/repos",
            "events_url": f"https://api.github.com/users/{owner}/events{{/privacy}}",
            "received_events_url": f"https://api.github.com/users/{owner}/received_events",
            "type": "Organization",
            "user_view_type": "public",
            "site_admin": False,
        },
        "html_url": f"https://github.com/{owner}/{name}",
        "description": None,
        "fork": False,
        "url": api,
        **{f"{key}_url": f"{api}/{key}{{/id}}" for key in REPOSITORY_URL_KEYS},
        "git_url": f"git://github.com/{owner}/{name}.git",
        "ssh_url": f"git@github.com:{owner}/{name}.git",
        "clone_url": f"https://github.com/{owner}/{name}.git",
        "svn_url": f"https://github.com/{owner}/{name}",
        "homepage": None,
        "size": 148_320,
    }


def _code_search(hits: int, spread: bool = False) -> dict[str, object]:
    """A denormalized list response: every hit self-contained, so the repository it belongs to is
    repeated in full per hit."""
    return {
        "total_count": 72,
        "incomplete_results": False,
        "items": [
            {
                "name": f"stage-{index}.md",
                "path": f"docs/handbook/stage-{index}.md",
                "sha": f"{index:040x}",
                "url": f"https://api.github.com/repositories/1292760912/contents/{index}",
                "git_url": f"https://api.github.com/repositories/1292760912/git/blobs/{index}",
                "html_url": f"https://github.com/acme/widgets/blob/main/docs/{index}.md",
                "score": 1.0,
                "repository": _repository(f"org{index}", f"repo{index}")
                if spread
                else _repository(),
            }
            for index in range(hits)
        ],
    }


def _resolved(pointer: str, document: object) -> object:
    node = document
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def _expanded(value: object, document: object) -> object:
    """Every `same_as` pointer resolved back to the object it names, recursively — the inverse the
    losslessness claim rests on. A pointer always names an earlier node, so this terminates."""
    key = connector_tools.DEDUPE_REFERENCE_KEY
    match value:
        case Mapping() if set(value) == {key}:
            return _expanded(_resolved(value[key], document), document)
        case Mapping():
            return {key: _expanded(item, document) for key, item in value.items()}
        case list():
            return [_expanded(item, document) for item in value]
        case _:
            return value


async def test_a_repeated_parent_object_crosses_once_and_expands_to_the_original(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    search = _code_search(30)
    original = json.dumps(search)
    assert len({json.dumps(hit["repository"]) for hit in search["items"]}) == 1
    assert len(json.dumps(search["items"][0]["repository"])) > 3_500
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=search,
        ),
    )
    payload = _payload(result)
    items = payload["arguments"]["items"]
    assert items[0]["repository"] == _repository()
    assert [item["repository"] for item in items[1:]] == [
        {"same_as": "/arguments/items/0/repository"}
    ] * 29
    condensed = json.dumps(payload["arguments"])
    assert len(condensed) < len(original) / 4, f"{len(original)} chars became {len(condensed)}"
    assert json.dumps(_expanded(payload, payload)["arguments"]) == original


async def test_trusted_connector_read_keeps_repeated_records_without_changing_authority() -> None:
    broker = _ReadBoundaryBroker(read_only=True)
    registry = ConnectorRegistry(
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER, label=CONNECTOR_LABEL, broker=broker
            )
        }
    )
    args = CallExternalToolInput(
        tool_name="READ_RECORDS",
        source_id=CONNECTOR_PROVIDER,
        account_id="acct-one",
        arguments={
            **_code_search(3),
            "attachment": {
                "encoding": "base64",
                "content": base64.b64encode(b"hello").decode(),
            },
        },
    )
    assert "translated_json" not in CallExternalToolInput.model_json_schema()["properties"]
    ctx = _ctx(registry, accounts=("acct-one",), connector_read_only=True)
    model_result = _payload(await call_external_tool(ctx, args))
    assert model_result["arguments"]["items"][1]["repository"] == {
        "same_as": "/arguments/items/0/repository"
    }
    code_result = _payload(await call_external_tool(ctx, args, translated_json=True))
    assert [item["repository"] for item in code_result["arguments"]["items"]] == [_repository()] * 3
    assert code_result["arguments"]["attachment"]["content"] == "hello"
    assert "translated_json" not in code_result

    writer = replace(broker, read_only=False)
    write_registry = replace(
        registry,
        entries={
            CONNECTOR_PROVIDER: ConnectorEntry(
                provider=CONNECTOR_PROVIDER, label=CONNECTOR_LABEL, broker=writer
            )
        },
    )
    refused = await call_external_tool(
        replace(ctx, connectors=write_registry), args, translated_json=True
    )
    assert refused.is_error
    assert writer.executed == broker.executed

    other_member = replace(
        _ctx(
            registry,
            accounts=("acct-one",),
            private_accounts=frozenset({"acct-one"}),
            connector_read_only=True,
        ),
        speaker_member_id=uuid4(),
    )
    with pytest.raises(ValueError, match=r"no active .* account"):
        await call_external_tool(other_member, args, translated_json=True)
    assert broker.executed == ["READ_RECORDS", "READ_RECORDS"]


async def test_a_cross_repo_search_still_offloads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    search = _code_search(30, spread=True)
    assert len({json.dumps(hit["repository"]) for hit in search["items"]}) == 30
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=search,
        ),
    )
    text = result.content[0].text
    assert connector_tools.DEDUPE_REFERENCE_KEY not in text
    assert len(text) > MAX_TOOL_RESULT_CHARS, f"{len(text)} chars would wrongly stay inline"
    assert _payload(result)["arguments"] == search


async def test_a_repeat_across_two_lists_points_at_the_first_occurrence(tmp_path: Path) -> None:
    """The walk is over the whole result, not one list: the same record embedded under a second key
    is the same waste, and it resolves against the copy that already crossed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "hits": [{"path": "a.md", "repository": _repository()}],
                "pinned": {"path": "b.md", "repository": _repository()},
            },
        ),
    )
    payload = _payload(result)
    assert payload["arguments"]["pinned"]["repository"] == {
        "same_as": "/arguments/hits/0/repository"
    }
    assert _expanded(payload, payload)["arguments"]["pinned"]["repository"] == _repository()


async def test_a_pointer_escapes_a_key_holding_a_slash_or_a_tilde(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arguments = {
        "acme/widgets~main": {"repository": _repository()},
        "acme/widgets~dev": {"repository": _repository()},
    }
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=arguments,
        ),
    )
    payload = _payload(result)
    assert payload["arguments"]["acme/widgets~dev"] == {"same_as": "/arguments/acme~1widgets~0main"}
    assert json.dumps(_expanded(payload, payload)["arguments"]) == json.dumps(arguments)


async def test_a_repeated_array_keeps_its_type(tmp_path: Path) -> None:
    """Only an object is replaced."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={
                "first": {"owner": "acme", "repositories": [_repository()]},
                "second": {"owner": "globex", "repositories": [_repository()]},
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert isinstance(echoed["second"]["repositories"], list)
    assert echoed["second"]["repositories"] == [{"same_as": "/arguments/first/repositories/0"}]


async def test_objects_differing_only_in_key_order_both_cross_whole(tmp_path: Path) -> None:
    """Identity is the bytes a node serializes to, key order included."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = _repository()
    reordered = {key: repository[key] for key in reversed(list(repository))}
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments={"a": repository, "b": reordered},
        ),
    )
    payload = _payload(result)
    echoed = payload["arguments"]
    assert json.dumps(echoed["a"]) == json.dumps(repository)
    assert list(echoed["b"]) == list(reordered)
    assert echoed["b"]["owner"] == {"same_as": "/arguments/a/owner"}
    assert json.dumps(_expanded(payload, payload)["arguments"]["b"]) == json.dumps(reordered)


async def test_a_payload_that_uses_the_reference_key_itself_is_never_rewritten(
    tmp_path: Path,
) -> None:
    """A pointer only reads as ours while the provider's own JSON does not use the key."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arguments = {
        "same_as": "provider field",
        "rows": [{"repository": _repository()}, {"repository": _repository()}],
    }
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=arguments,
        ),
    )
    assert _payload(result)["arguments"] == arguments


async def test_a_response_past_the_dedupe_cap_is_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_tools, "MAX_DEDUPE_CHARS", 4_096)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    search = _code_search(4)
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=search,
        ),
    )
    assert _payload(result)["arguments"] == search


async def test_records_differing_only_in_a_leafs_type_are_never_collapsed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    leaves: tuple[object, ...] = (1, 1.0, True, "1", None)
    arguments = {
        f"record{index}": {"repository": _repository(), "count": leaf}
        for index, leaf in enumerate(leaves)
    }
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=arguments,
        ),
    )
    payload = _payload(result)
    echoed = payload["arguments"]
    assert [echoed[f"record{index}"]["count"] for index in range(len(leaves))] == list(leaves)
    assert echoed["record0"]["repository"] == _repository()
    assert [echoed[f"record{index}"]["repository"] for index in range(1, len(leaves))] == [
        {"same_as": "/arguments/record0/repository"}
    ] * (len(leaves) - 1)
    assert json.dumps(_expanded(payload, payload)["arguments"]) == json.dumps(arguments)


async def test_a_node_dense_response_is_never_walked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    dense = {
        "hits": _code_search(2)["items"],
        "ids": [f"v{index:06d}" for index in range(connector_tools.MAX_DEDUPE_TOKENS)],
    }
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=BROKER_TOOL_SLUG,
            source_id=CONNECTOR_PROVIDER,
            arguments=dense,
        ),
    )
    assert _payload(result)["arguments"] == dense


async def test_deep_subtrees_are_never_judged_identical_past_the_depth_cap(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def chain(leaf: str) -> dict[str, object]:
        node: dict[str, object] = {"leaf": leaf}
        for _ in range(connector_tools.MAX_TRANSLATE_DEPTH + 20):
            node = {"nested": node, "filler": "x" * 40}
        return node

    broker = _FileBroker(
        response={
            "left": chain("left"),
            "right": chain("right"),
            "one": _repository(),
            "two": _repository(),
        }
    )
    result = await call_external_tool(
        _ctx(_file_registry(broker), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name="ANY",
            source_id=CONNECTOR_PROVIDER,
            arguments={},
        ),
    )
    payload = _payload(result)
    assert payload["two"] == {"same_as": "/one"}
    assert json.dumps(payload["left"]) == json.dumps(chain("left"))
    assert json.dumps(payload["right"]) == json.dumps(chain("right"))
    assert json.dumps(_expanded(payload, payload)) == json.dumps(broker.response)
