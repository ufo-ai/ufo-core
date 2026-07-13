"""The connectors extension: the broker-generic dynamic tool surface over the `ConnectorRegistry`.

The extension imports only `ufo.sdk` and owns no provider — these tests drive its four tools over a
registry holding the sample extension's ConnectorProvider (a real installed broker, echoing every
execute back as its response) exactly as `serve` threads one onto the turn's ToolContext. What is
proved here is the orchestration: listing filters the registry, describe folds an unknown slug into
`unresolved` and backfills discovery from the broker's catalog, search renders the broker's
`BrokerSearch`, and a call without the registry, or naming a provider no extension registers, fails
loud. The grant-resolving execute path keeps its end-to-end proof in the composio extension's
tests."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import ufo_ext_connectors.manifest as connectors
import ufo_ext_sample as sample
from ufo_ext_connectors.tools import (
    CallExternalToolInput,
    DescribeExternalToolsInput,
    ListExternalToolsInput,
    SearchConnectorToolsInput,
    call_external_tool,
    describe_external_tools,
    list_external_tools,
    search_connector_tools,
)

from ufo.connectors import ConnectorEntry, ConnectorRegistry
from ufo.ext.loader import turn_tools
from ufo.grants import Grant, GrantStore
from ufo.schema.records import Agent, Turn
from ufo.tools.context import ToolContext

OTHER_PROVIDER = "other_widgets"
OTHER_LABEL = "Other Widgets"


@dataclass(frozen=True)
class _Grants(GrantStore):
    accounts: tuple[str, ...]

    async def active_grants(self, _workspace_id: UUID, _agent_id: UUID) -> tuple[Grant, ...]:
        return tuple(
            Grant(
                provider=sample.CONNECTOR_PROVIDER,
                account_id=account,
                host=sample.CONNECTOR_HOST,
            )
            for account in self.accounts
        )


def _registry() -> ConnectorRegistry:
    broker = sample._SampleBroker()
    return ConnectorRegistry(
        entries={
            sample.CONNECTOR_PROVIDER: ConnectorEntry(
                provider=sample.CONNECTOR_PROVIDER, label=sample.CONNECTOR_LABEL, broker=broker
            ),
            OTHER_PROVIDER: ConnectorEntry(
                provider=OTHER_PROVIDER, label=OTHER_LABEL, broker=broker
            ),
        }
    )


def _ctx(registry: ConnectorRegistry | None, accounts: tuple[str, ...] = ()) -> ToolContext:
    return ToolContext(
        sandbox=None,
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
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret="",
        grants=_Grants(accounts),
        connectors=registry,
        idempotency_key="t1/call_external_tool/c1",
    )


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_manifest_declares_the_dynamic_tools_and_prompt_section() -> None:
    manifest = connectors.manifest()
    tools, _ = turn_tools((manifest,), None)
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


async def test_list_external_tools_filters_the_registry() -> None:
    result = await list_external_tools(
        _ctx(_registry()),
        ListExternalToolsInput(queries=("widgets",), user_description="find widgets"),
    )
    rows = _payload(result)["connectors"]
    assert rows == [{"source_id": OTHER_PROVIDER, "label": OTHER_LABEL}]


async def test_list_external_tools_select_prefix_fetches_one_by_exact_id() -> None:
    result = await list_external_tools(
        _ctx(_registry()),
        ListExternalToolsInput(
            queries=(f"select:{sample.CONNECTOR_PROVIDER}",), user_description="the sample"
        ),
    )
    rows = _payload(result)["connectors"]
    assert [row["source_id"] for row in rows] == [sample.CONNECTOR_PROVIDER]


async def test_describe_external_tools_fetches_schemas_from_the_broker() -> None:
    result = await describe_external_tools(
        _ctx(_registry()),
        DescribeExternalToolsInput(
            source_id=sample.CONNECTOR_PROVIDER, tool_names=(sample.BROKER_TOOL_SLUG,)
        ),
    )
    payload = _payload(result)
    assert payload["source_id"] == sample.CONNECTOR_PROVIDER
    schema = payload["schemas"][sample.BROKER_TOOL_SLUG]
    assert schema["input_schema"]["properties"] == {"limit": {"type": "integer"}}
    assert "unresolved" not in payload


async def test_describe_external_tools_marks_an_unknown_name_unresolved() -> None:
    result = await describe_external_tools(
        _ctx(_registry()),
        DescribeExternalToolsInput(
            source_id=sample.CONNECTOR_PROVIDER, tool_names=("NOT_A_REAL_SLUG",)
        ),
    )
    payload = _payload(result)
    assert payload["unresolved"] == ["NOT_A_REAL_SLUG"]
    assert [tool["slug"] for tool in payload["availableTools"]] == [sample.BROKER_TOOL_SLUG]


async def test_search_connector_tools_renders_the_brokers_search() -> None:
    result = await search_connector_tools(
        _ctx(_registry()),
        SearchConnectorToolsInput(source_id=sample.CONNECTOR_PROVIDER, query="list widgets"),
    )
    payload = _payload(result)
    assert payload["connector"] == sample.CONNECTOR_PROVIDER
    assert [tool["slug"] for tool in payload["tools"]] == [sample.BROKER_TOOL_SLUG]
    assert payload["plan"] == [sample.BROKER_SEARCH_PLAN]
    assert payload["guidance"] == []


async def test_call_external_tool_uses_the_only_connected_account() -> None:
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"limit": 2},
        ),
    )
    assert _payload(result)["account"] == "acct-one"


async def test_call_external_tool_requires_a_choice_between_connected_accounts() -> None:
    ctx = _ctx(_registry(), accounts=("acct-one", "acct-two"))
    with pytest.raises(ValueError, match=r"multiple active.*pass account_id"):
        await call_external_tool(
            ctx,
            CallExternalToolInput(
                tool_name=sample.BROKER_TOOL_SLUG,
                source_id=sample.CONNECTOR_PROVIDER,
                arguments={},
            ),
        )
    result = await call_external_tool(
        ctx,
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            account_id="acct-two",
            arguments={},
        ),
    )
    assert _payload(result)["account"] == "acct-two"


async def test_tools_fail_loud_without_the_registry_or_the_provider() -> None:
    with pytest.raises(RuntimeError, match="connector registry"):
        await list_external_tools(
            _ctx(None), ListExternalToolsInput(queries=("x",), user_description="d")
        )
    with pytest.raises(KeyError, match="no installed connector"):
        await call_external_tool(
            _ctx(_registry()),
            CallExternalToolInput(tool_name="X", source_id="unregistered", arguments={}),
        )
