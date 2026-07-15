"""The connectors extension: the broker-generic dynamic tool surface over the `ConnectorRegistry`.

The extension imports only `ufo.sdk` and owns no provider — these tests drive its four tools over a
registry holding the sample extension's ConnectorProvider (a real installed broker, echoing every
execute back as its response) exactly as `serve` threads one onto the turn's ToolContext. What is
proved here is the orchestration: listing filters the registry, describe folds an unknown slug into
`unresolved` and backfills discovery from the broker's catalog, search renders the broker's
`BrokerSearch`, and a call without the registry, or naming a provider no extension registers, fails
loud. The grant-resolving execute path keeps its end-to-end proof in the composio extension's
tests."""

import hashlib
import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import ufo_ext_connectors.manifest as connectors
import ufo_ext_connectors.tools as connector_tools
import ufo_ext_sample as sample
from ufo_ext_connectors.tools import (
    CONNECTOR_FILES_DIR,
    CallExternalToolInput,
    DescribeExternalToolsInput,
    ListExternalToolsInput,
    SearchConnectorToolsInput,
    call_external_tool,
    describe_external_tools,
    list_external_tools,
    search_connector_tools,
)

from ufo.connectors import BrokerFile, ConnectorEntry, ConnectorRegistry, StagedUpload
from ufo.ext.loader import turn_tools
from ufo.grants import Grant, GrantStore
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    WORKSPACE_DIR,
    ExecResult,
    MountSpec,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
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


def _ctx(
    registry: ConnectorRegistry | None,
    accounts: tuple[str, ...] = (),
    sandbox: SandboxSession | None = None,
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


async def _sandbox(workspace_root: Path) -> SandboxSession:
    """A real session over the local carrier: the file bridge's bash, curl, and md5 preflight run
    for real against a host-directory workspace — nothing here asserts a fake."""
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="unused",
            mount=MountSpec(kind="filesystem", host_path=str(workspace_root)),
            proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
            run_token="run-token",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


@dataclass(frozen=True)
class _FileBroker:
    """Stands in for a broker whose file store mints what the sample broker cannot — a hostile
    output name, a dash-leading URL. It only supplies those references; the assertions read the
    real workspace files and the commands the tool builds, never this stub."""

    outputs: tuple[BrokerFile, ...] = ()
    put_url: str | None = None

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
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


def _file_registry(broker: _FileBroker) -> ConnectorRegistry:
    return ConnectorRegistry(
        entries={
            sample.CONNECTOR_PROVIDER: ConnectorEntry(
                provider=sample.CONNECTOR_PROVIDER, label=sample.CONNECTOR_LABEL, broker=broker
            )
        }
    )


async def test_call_external_tool_fetches_produced_files_into_the_workspace(
    tmp_path: Path,
) -> None:
    """The download leg over the real seam: the sample broker reports produced files as URLs, and
    the sandbox itself fetches each into `connector_files/` — the result names workspace paths the
    agent can read, and the bytes are really there."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "store" / "Order_Form.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4 attachment bytes")
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
    """The upload leg through the real sample broker: a `workspace_file` argument is hashed in the
    sandbox, PUT to the slot the broker minted in its workspace store, and replaced by the broker's
    own argument — the executed call carries the store reference and the staged bytes are there."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.md5(b"a,b\n1,2\n", usedforsecurity=False).hexdigest()
    key = f"{sample.BROKER_UPLOAD_PREFIX}-{digest}-report.csv"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"media": {"workspace_file": "/workspace/report.csv"}},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["media"] == {"name": "report.csv", "mimetype": "text/csv", "s3key": key}
    assert (workspace / key).read_bytes() == b"a,b\n1,2\n"


async def test_a_produced_files_name_cannot_escape_its_workspace_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "store" / "legit.txt"
    source.parent.mkdir()
    source.write_bytes(b"payload")
    broker = _FileBroker(outputs=(BrokerFile(name="../../evil.txt", url=f"file://{source}"),))
    result = await call_external_tool(
        _ctx(_file_registry(broker), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(tool_name="ANY", source_id=sample.CONNECTOR_PROVIDER, arguments={}),
    )
    files = _payload(result)["workspace_files"]
    assert files[0]["name"] == "evil.txt"
    on_disk = workspace / Path(files[0]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == b"payload"
    assert not (tmp_path / "evil.txt").exists()


async def test_a_missing_workspace_file_argument_fails_loud(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match=r"cannot read workspace file|No such file"):
        await call_external_tool(
            _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
            CallExternalToolInput(
                tool_name=sample.BROKER_TOOL_SLUG,
                source_id=sample.CONNECTOR_PROVIDER,
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
                tool_name=sample.BROKER_TOOL_SLUG,
                source_id=sample.CONNECTOR_PROVIDER,
                arguments={"media": {"workspace_file": "/workspace/big.csv"}},
            ),
        )
    assert not any(p.name.startswith(sample.BROKER_UPLOAD_PREFIX) for p in workspace.iterdir())


async def test_a_deduped_upload_slot_skips_the_put(tmp_path: Path) -> None:
    """A content-addressed dedup hit through the real sample broker: re-staging content the store
    already indexed answers no put_url, so the sandbox transfers nothing — proven by removing the
    store object between calls and reading it back absent, while the executed argument still names
    the same key. All read off the workspace and result payloads, never a call log."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.md5(b"a,b\n1,2\n", usedforsecurity=False).hexdigest()
    key = f"{sample.BROKER_UPLOAD_PREFIX}-{digest}-report.csv"
    ctx = _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace))
    call = CallExternalToolInput(
        tool_name=sample.BROKER_TOOL_SLUG,
        source_id=sample.CONNECTOR_PROVIDER,
        arguments={"media": {"workspace_file": "/workspace/report.csv"}},
    )
    first = await call_external_tool(ctx, call)
    assert (workspace / key).read_bytes() == b"a,b\n1,2\n"
    (workspace / key).unlink()
    second = await call_external_tool(ctx, call)
    assert _payload(second)["arguments"]["media"] == _payload(first)["arguments"]["media"]
    assert not (workspace / key).exists()


async def test_transfer_urls_are_passed_as_curl_url_operands() -> None:
    """Finding: a broker-reported URL starting with `-` must never be parsed as a curl flag. Both
    legs pass the URL as the `--url` operand, so a dash-leading URL is a URL, never an option."""
    commands: list[str] = []

    class _RecordingSandbox:
        async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
            commands.append(command)
            if "hashlib" in command:
                return ExecResult(stdout="deadbeef\n7\n", stderr="", exit_code=0)
            return ExecResult(stdout="", stderr="", exit_code=0)

    broker = _FileBroker(outputs=(BrokerFile(name="out.txt", url="-oPWNED"),), put_url="-oPWNED")
    await call_external_tool(
        _ctx(_file_registry(broker), accounts=("acct-one",), sandbox=_RecordingSandbox()),
        CallExternalToolInput(
            tool_name="UPLOAD_FILE",
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"media": {"workspace_file": "/workspace/report.csv"}},
        ),
    )
    curls = [command for command in commands if command.startswith("curl")]
    assert curls, "expected upload and download curl commands"
    assert all(f"--url {shlex.quote('-oPWNED')}" in command for command in curls)
    assert not any(command.split("curl", 1)[1].strip().startswith("-oPWNED") for command in curls)
