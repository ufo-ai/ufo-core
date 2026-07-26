"""The connectors extension: the broker-generic dynamic tool surface over the `ConnectorRegistry`.

The extension imports only `ufo.sdk` and owns no provider — these tests drive its four tools over a
registry holding the sample extension's ConnectorProvider (a real installed broker, echoing every
execute back as its response) exactly as `serve` threads one onto the turn's ToolContext. What is
proved here is the orchestration: listing filters the registry, describe folds an unknown slug into
`unresolved` and backfills discovery from the broker's catalog, search renders the broker's
`BrokerSearch`, and a call without the registry, or naming a provider no extension registers, fails
loud. The grant-resolving execute path keeps its end-to-end proof in the composio extension's
tests."""

import asyncio
import base64
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
                grantor_member_id=uuid4(),
                shared=True,
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


async def test_provider_marked_base64_text_is_decoded_inline(tmp_path: Path) -> None:
    """The GitHub contents shape, through the real tool: a source file the provider marked base64
    reaches the model as readable UTF-8 with the marker corrected, and nothing is written to the
    workspace. The sibling `type: "file"` is GitHub's entry-kind label, not a media type — it must
    not push a small text field down the binary path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = "def main():\n    return 1\n"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={
                "content": base64.b64encode(source.encode()).decode(),
                "encoding": "base64",
                "name": "surface.py",
                "type": "file",
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == source
    assert echoed["encoding"] == "utf-8"
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments=arguments,
        ),
    )
    assert _payload(result)["arguments"] == arguments
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


async def test_a_marked_field_that_is_not_base64_is_left_as_the_provider_sent_it(
    tmp_path: Path,
) -> None:
    """A marker is a claim, not a guarantee: a provider that keeps `encoding: base64` while putting
    a placeholder in the field must not have it mangled. Lenient decoding drops every character
    outside the alphabet, so this exact placeholder decodes to 18 bytes of garbage that would be
    written to the workspace as a file; strict validation rejects it and the node stands as sent."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    placeholder = "binary file (use raw endpoint)"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"content": placeholder, "encoding": "base64", "name": "x.bin"},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == placeholder
    assert echoed["encoding"] == "base64"
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


async def test_a_base64_data_url_is_translated_like_a_marked_field(tmp_path: Path) -> None:
    """A `data:<mime>;base64,` string marks itself. Text inlines as itself; binary lands in the
    workspace under the mimetype the URL declares, and neither leaves base64 behind."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gif = b"GIF89a" + bytes(range(200))
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={
                "note": f"data:text/plain;base64,{base64.b64encode(b'hi there').decode()}",
                "avatar": f"data:image/gif;base64,{base64.b64encode(gif).decode()}",
                "link": "data:not-a-data-url",
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["note"] == "hi there"
    assert echoed["link"] == "data:not-a-data-url"
    assert echoed["avatar"]["mimetype"] == "image/gif"
    on_disk = workspace / Path(echoed["avatar"]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == gif


async def test_the_walk_reaches_base64_nested_under_a_content_key(tmp_path: Path) -> None:
    """A `content` key holding an object is not a marked field, whatever the sibling marker claims.
    The subtree under it is still walked, so base64 marked deeper down is translated rather than
    skipped along with its parent."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = "print('nested')\n"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={
                "encoding": "base64",
                "content": {
                    "entries": [
                        {
                            "content": base64.b64encode(source.encode()).decode(),
                            "encoding": "base64",
                            "name": "inner.py",
                        }
                    ]
                },
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"]["entries"][0]["content"] == source
    assert echoed["content"]["entries"][0]["encoding"] == "utf-8"
    assert echoed["encoding"] == "base64"


async def test_a_decoded_fields_name_cannot_escape_its_workspace_dir(tmp_path: Path) -> None:
    """The offloaded file is named by the provider, so it is basenamed like a produced file: a
    traversing name writes inside `connector_files/`, never above the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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


async def test_a_non_ascii_marked_field_is_left_as_the_provider_sent_it(tmp_path: Path) -> None:
    """Non-ASCII in a marked field raises a bare ValueError out of `b64decode`, not the binascii
    subclass. An i18n placeholder must take the same graceful path as any other mislabelled field —
    node untouched — rather than escaping as an error for the whole call."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    placeholder = "café binary content über placeholder"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"content": placeholder, "encoding": "base64", "name": "note.txt"},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == placeholder
    assert echoed["encoding"] == "base64"


async def test_a_response_nested_past_the_depth_cap_survives_untranslated(tmp_path: Path) -> None:
    """Broker output is untrusted, and on a broker whose `file_outputs` reads fixed keys this walk
    is the only recursive pass over it. Nesting past the cap is returned as it came rather than
    exhausting the interpreter's recursion budget and failing a call that used to work — while a
    field at ordinary depth in the same response is still translated."""
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
        CallExternalToolInput(tool_name="ANY", source_id=sample.CONNECTOR_PROVIDER, arguments={}),
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
    """The decode is one C call that holds the GIL for its whole duration, so no worker thread can
    keep a huge field off the serve loop — only refusing it can. Past the cap nothing is decoded and
    nothing is written; the engine's tool-result offload is the layer that keeps it out of context.
    """
    monkeypatch.setattr(connector_tools, "MAX_DECODE_CHARS", 16)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    encoded = base64.b64encode(b"far past the decode cap").decode()
    assert len(encoded) > 16
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"content": encoded, "encoding": "base64", "name": "big.bin"},
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == encoded
    assert echoed["encoding"] == "base64"
    assert not list(workspace.rglob(f"{CONNECTOR_FILES_DIR}/*"))


async def test_a_node_marking_two_content_keys_translates_both(tmp_path: Path) -> None:
    """A provider carries one marker for the node, not one per field. Both marked fields are
    translated — neither is left as raw base64 under a marker claiming otherwise — and when their
    outcomes differ the shared marker summarizes the node while each value describes its own field:
    the small one is the decoded text in place, the binary one a reference to read."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = "release notes\n"
    blob = b"\x89PNG\r\n\x1a\n" + bytes(range(128))
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={
                "content": base64.b64encode(note.encode()).decode(),
                "data": base64.b64encode(blob).decode(),
                "encoding": "base64",
                "name": "shot.png",
            },
        ),
    )
    echoed = _payload(result)["arguments"]
    assert echoed["content"] == note
    assert echoed["data"]["mimetype"] == "image/png"
    assert echoed["encoding"] == "offloaded"
    on_disk = workspace / Path(echoed["data"]["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == blob


async def test_a_huge_non_data_url_string_is_never_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching the data-URL pattern scans the whole string, so an ordinary long field that merely
    begins `data:` would cost a full pass (measured ~15 ms at the cap, ~315 ms at 32 MiB). The same
    cap that bounds the decode gates the match, so an over-cap string is never scanned at all."""
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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
    """Marked fields stand on their own. A sibling the provider mislabelled must not push a
    decodable field back into context as raw base64 — that is the whole bug — so the valid one is
    translated, the invalid one is left as sent, and the shared marker stays `base64` because a
    field on the node still is."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = "changelog\n"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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


async def test_the_same_payload_offloads_to_one_content_addressed_path(tmp_path: Path) -> None:
    """Decoded bytes name their own directory, so a payload that appears twice — the same attachment
    on two records here, the same file re-read on a later round in practice — resolves to the path
    it already wrote instead of accumulating a copy per occurrence, while a different payload under
    the same provider name still gets its own."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shot = b"\x89PNG\r\n\x1a\n" + bytes(range(96))
    other = b"\x89PNG\r\n\x1a\n" + bytes(range(96, 192))

    def record(blob: bytes) -> dict[str, object]:
        return {
            "data": base64.b64encode(blob).decode(),
            "encoding": "base64",
            "name": "shot.png",
        }

    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"records": [record(shot), record(shot), record(other)]},
        ),
    )
    paths = [row["data"]["workspace_path"] for row in _payload(result)["arguments"]["records"]]
    assert paths[0] == paths[1], "the same bytes must resolve to one path"
    assert paths[2] != paths[0], "different bytes must not collide"
    written = sorted(p for p in workspace.rglob("*") if p.is_file())
    assert len(written) == 2
    on_disk = workspace / Path(paths[0]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == shot
    assert on_disk.name == "shot.png"


async def test_a_marked_field_with_no_name_falls_back_for_both_name_and_mimetype(
    tmp_path: Path,
) -> None:
    """A provider need not tell us what it inlined. With no name key on the node there is nothing to
    infer a media type from either, so the offloaded file takes the fallback name and the fallback
    mimetype rather than an empty path segment or a guessed type."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    blob = b"\x89\xff\xfe" + bytes(range(200, 256))
    assert blob.decode("utf-8", "ignore").encode() != blob, "must be binary to reach the offload"
    result = await call_external_tool(
        _ctx(_registry(), accounts=("acct-one",), sandbox=await _sandbox(workspace)),
        CallExternalToolInput(
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
            arguments={"body": base64.b64encode(blob).decode(), "encoding": "base64"},
        ),
    )
    reference = _payload(result)["arguments"]["body"]
    assert reference["name"] == connector_tools.FALLBACK_FILENAME
    assert reference["mimetype"] == connector_tools.FALLBACK_MIMETYPE
    on_disk = workspace / Path(reference["workspace_path"]).relative_to(WORKSPACE_DIR)
    assert on_disk.read_bytes() == blob


async def test_concurrent_calls_decoding_one_payload_never_expose_a_partial_file(
    tmp_path: Path,
) -> None:
    """Content addressing means two turns that decode the same bytes write one path, and no
    carrier's write is atomic. The bytes are staged beside the target and renamed onto it, so
    concurrent calls both succeed, agree on the path, leave no staging file behind, and the file is
    only ever observable complete."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    blob = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40
    call = CallExternalToolInput(
        tool_name=sample.BROKER_TOOL_SLUG,
        source_id=sample.CONNECTOR_PROVIDER,
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
    carrier's write is atomic. The bytes must therefore reach a staging path and be renamed onto the
    target — a direct write to the shared path is what opens the truncated-read window."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="unused",
            mount=MountSpec(kind="filesystem", host_path=str(workspace)),
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
            tool_name=sample.BROKER_TOOL_SLUG,
            source_id=sample.CONNECTOR_PROVIDER,
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
