import asyncio
import csv
import io
import json
import shutil
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_yc.cli import (
    YC_AUTH_CLIENT_ID,
    YC_AUTH_SCOPES,
    YC_CREDENTIALS_SLOT,
    YcAuth,
    YcAuthInput,
    YcAuthResult,
    YcCli,
    YcCliError,
    YcDeviceAuthorization,
    YcRead,
    YcReadInput,
    yc_auth,
)
from ufo_ext_yc.manifest import (
    NAME,
    YC_AUTH_TOOL,
    YC_INDEX_TOOL,
    YC_READ_TOOL,
    manifest,
    setup_sources,
)
from ufo_ext_yc.source import (
    YC_GUIDANCE_COLLECTIONS,
    YC_SEARCH_COLLECTIONS,
    YC_SOURCE_BACKEND,
    YcIndexInput,
    YcSource,
    YcSourceConfig,
    YcSourceCursor,
    yc_index,
)

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialRequests, CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.sandbox.session import SandboxSession
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sources.sync import CorePageFeed, SourceAuth, StreamSkipped, SyncDriver
from ufo.tools.context import Spawn, ToolContext
from ufo.workspace import init_workspace_credentials, ws


@dataclass
class _Runner:
    responses: list[str]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def run(self, args: tuple[str, ...], session: str) -> str:
        self.calls.append(args)
        return self.responses.pop(0)


def _result(collection: str, total: int, rows: tuple[dict[str, str], ...]) -> str:
    output = io.StringIO()
    fields = tuple(rows[0]) if rows else ("id", "link")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return json.dumps(
        {
            "name": f"search.{collection}",
            "result": {
                "status": "success",
                "count": len(rows),
                "total_count": total,
                "csv_results": output.getvalue(),
            },
        }
    )


def _row(record_id: str, title: str, body: str) -> dict[str, str]:
    return {
        "displayed_attributes.body": body,
        "displayed_attributes.description": "Official YC guidance",
        "displayed_attributes.id": record_id,
        "displayed_attributes.link": (
            f"[{title}](https://bookface.ycombinator.com/knowledge/{record_id})"
        ),
        "displayed_attributes.categories": "Fundraising",
    }


def _directory_row(record_id: str) -> dict[str, str]:
    return {
        "id": record_id,
        "link": f"[Company {record_id}](https://bookface.ycombinator.com/company/{record_id})",
        "batch": "S25",
        "one_liner": f"Builds product {record_id}",
    }


@dataclass(frozen=True)
class _ToolCall:
    ext: ExtensionContext


@dataclass
class _GuidanceRunner:
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def run(self, args: tuple[str, ...], session: str) -> str:
        self.calls.append(args)
        collection = args[2].removeprefix("search.")
        return _result(
            collection,
            1,
            (
                _row(
                    collection,
                    collection.replace("_", " ").title(),
                    f"YC {collection.replace('_', ' ')} evidence.",
                ),
            ),
        )


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _credentials(created_at: int = 1) -> str:
    return json.dumps(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid",
            "created_at": created_at,
        }
    )


def test_manifest_declares_the_credential_source_tool_skill_and_onboarding() -> None:
    found = manifest()
    assert found.name == NAME
    assert [slot.name for slot in found.credentials] == ["yc_cli_credentials"]
    assert [source.backend for source in found.sources] == [YC_SOURCE_BACKEND]
    assert found.tools == (YC_AUTH_TOOL, YC_READ_TOOL, YC_INDEX_TOOL)
    assert YC_AUTH_TOOL.side_effecting
    assert not YC_READ_TOOL.side_effecting
    assert YC_INDEX_TOOL.side_effecting
    assert found.skills[0].path.name == "yc-research"
    assert [step.name for step in found.onboarding_steps] == ["yc_sources"]


async def test_device_auth_starts_in_chat_and_fulfills_the_encrypted_slot(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    owner_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    created_at = datetime(2026, 7, 12, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=owner_id,
                workspace_id=workspace_id,
                email="owner@yc.test",
                created_at=created_at,
                updated_at=created_at,
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    ext = context_for(NAME, frozenset({YC_CREDENTIALS_SLOT}))
    context = ToolContext(
        sandbox=cast(SandboxSession, None),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="connect YC",
            created_at=created_at,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=cast(Spawn, None),
        speaker_member_id=owner_id,
        audience_member_id=owner_id,
        artifact_token_secret="",
        idempotency_key="turn/tool/call",
        ext=ext,
        requestable_credentials=CredentialRequests(
            fernet=store.fernet,
            declared=frozenset({YC_CREDENTIALS_SLOT}),
        ),
    )
    authorization_calls = 0
    token_calls = 0

    def auth(request: httpx.Request) -> httpx.Response:
        nonlocal authorization_calls, token_calls
        payload = json.loads(request.content)
        if request.url.path == "/oauth/authorize_device":
            authorization_calls += 1
            assert payload == {
                "client_id": YC_AUTH_CLIENT_ID,
                "scope": " ".join(YC_AUTH_SCOPES),
            }
            return httpx.Response(
                200,
                json={
                    "device_code": "provider-device-secret",
                    "user_code": "YC-ABCD",
                    "verification_uri": "https://account.ycombinator.com/device",
                    "verification_uri_complete": "https://account.ycombinator.com/device?code=YC-ABCD",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        assert request.url.path == "/oauth/token"
        assert payload["device_code"] == "provider-device-secret"
        token_calls += 1
        if token_calls == 1:
            return httpx.Response(400, json={"error": "authorization_pending"})
        if token_calls == 2:
            return httpx.Response(200, json=json.loads(_credentials(created_at=2)))
        return httpx.Response(400, json={"error": "access_denied"})

    try:
        async_client = httpx.AsyncClient
        monkeypatch.setattr(
            "ufo_ext_yc.cli.httpx.AsyncClient",
            lambda **kwargs: async_client(transport=httpx.MockTransport(auth)),
        )
        async with async_client(transport=httpx.MockTransport(auth)) as http:
            with ws(workspace_id):
                with pytest.raises(ValueError, match="private audience"):
                    await YcAuth(replace(context, audience_member_id=None), http).run(
                        "start", "session"
                    )
                non_owner = uuid4()
                with pytest.raises(ValueError, match="workspace owner"):
                    await YcAuth(
                        replace(
                            context,
                            speaker_member_id=non_owner,
                            audience_member_id=non_owner,
                        ),
                        http,
                    ).run("start", "session")
                assert authorization_calls == 0
        with ws(workspace_id):
            tool_result = await yc_auth(context, YcAuthInput(action="start"))
            started = YcAuthResult.model_validate_json(tool_result.content[0].text)
        async with async_client(transport=httpx.MockTransport(auth)) as http:
            with ws(workspace_id):
                repeated = await YcAuth(context, http).run("start", "session")
                sealed = await ext.store.get("device_authorization")
                assert "provider-device-secret" not in json.dumps(sealed)
                pending = await YcAuth(context, http).run("complete", "session")
                connected = await YcAuth(context, http).run("complete", "session")
                interrupted = replace(context, idempotency_key="interrupted")
                await YcAuth(interrupted, http).run("start", "session")
                await store.put(workspace_id, YC_CREDENTIALS_SLOT, _credentials(created_at=3))
                recovered = await YcAuth(interrupted, http).run("complete", "session")
                denied = replace(context, idempotency_key="denied")
                await YcAuth(denied, http).run("start", "session")
                with pytest.raises(YcCliError, match="token exchange failed"):
                    await YcAuth(denied, http).run("complete", "session")
                assert await ext.store.get("device_authorization") is None
                await YcAuth(denied, http).run("start", "session")
                await ext.store.delete("device_authorization")
        assert started == repeated
        assert started.verification_url == ("https://account.ycombinator.com/device?code=YC-ABCD")
        assert started.user_code == "YC-ABCD"
        assert pending.status == "pending"
        assert connected.status == "connected"
        assert recovered.status == "connected"
        assert token_calls == 3
        assert authorization_calls == 4
        assert json.loads(await store.get(workspace_id, YC_CREDENTIALS_SLOT))["created_at"] == 3
        script = """
import json
from pathlib import Path
credentials = json.loads((Path.home() / ".yc/credentials.json").read_text())
print(credentials["access_token"])
"""
        with ws(workspace_id):
            assert (await ext.store.get("device_authorization")) is None
            output = await YcCli(ext.credentials, executable=sys.executable).run(
                ("-c", script), "session"
            )
        assert output.strip() == "access"
    finally:
        init_workspace_credentials(None)


async def test_source_pages_through_the_supported_search_api_and_advances_cursor() -> None:
    runner = _Runner(
        responses=[
            _result(
                "user_manuals",
                201,
                (_row("1Z", "Fundraising", "Raise from evidence."),),
            ),
            _result(
                "user_manuals",
                201,
                (_row("2A", "Metrics", "Define ARR honestly."),),
            ),
        ]
    )
    source = YcSource(runner)
    workspace_id = uuid4()
    result = await source.fetch(
        YcSourceConfig(collection="user_manuals"),
        None,
        SourceAuth(workspace_id=workspace_id),
    )
    assert result.snapshot
    assert [page.source_ref for page in result.pages] == [
        "user_manuals/1Z",
        "user_manuals/2A",
    ]
    assert "Raise from evidence." in result.pages[0].body
    assert result.pages[0].digest.startswith("sha256:")
    assert result.next_cursor is not None
    assert YcSourceCursor.model_validate_json(result.next_cursor).synced_at.tzinfo is not None
    assert len(runner.calls) == 2
    assert "search.user_manuals" in runner.calls[0]


async def test_source_holds_a_fresh_cursor_without_calling_yc() -> None:
    runner = _Runner(responses=[])
    cursor = YcSourceCursor(synced_at=datetime.now(UTC)).model_dump_json()
    result = await YcSource(runner).fetch(
        YcSourceConfig(collection="startup_library"),
        cursor,
        SourceAuth(workspace_id=uuid4()),
    )
    assert result.pages == ()
    assert result.next_cursor == cursor
    assert not result.snapshot
    assert runner.calls == []


async def test_source_fetch_is_bounded_below_the_claim_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass
    class WaitingRunner:
        async def run(self, args: tuple[str, ...], session: str) -> str:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    monkeypatch.setattr("ufo_ext_yc.source.YC_SOURCE_FETCH_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(TimeoutError):
        await YcSource(WaitingRunner()).fetch(
            YcSourceConfig(collection="user_manuals"),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


def test_targeted_source_config_requires_a_bounded_shared_search() -> None:
    for collection in YC_SEARCH_COLLECTIONS:
        config = YcSourceConfig(collection=collection, query="  climate infrastructure  ")
        assert config.query == "climate infrastructure"
        assert config.max_results == 1000
    assert YcSourceConfig(collection="user_manuals").model_dump(mode="json") == {
        "collection": "user_manuals",
        "query": None,
        "max_results": None,
        "refresh_seconds": 3600,
    }
    with pytest.raises(ValidationError, match="require a query"):
        YcSourceConfig(collection="companies")
    with pytest.raises(ValidationError, match="do not accept a query"):
        YcSourceConfig(collection="user_manuals", query="fundraising")
    with pytest.raises(ValidationError, match="do not accept a result bound"):
        YcSourceConfig(collection="user_manuals", max_results=100)
    with pytest.raises(ValidationError):
        YcSourceConfig(collection="companies", query="robotics", max_results=5001)
    with pytest.raises(ValidationError):
        YcIndexInput(entity="candidates", query="robotics")


async def test_targeted_source_keeps_page_size_and_slices_the_snapshot_to_its_bound() -> None:
    runner = _Runner(
        responses=[
            _result(
                "companies",
                450,
                tuple(_directory_row(str(index)) for index in range(200)),
            ),
            _result(
                "companies",
                450,
                tuple(_directory_row(str(index)) for index in range(200, 400)),
            ),
        ]
    )
    result = await YcSource(runner).fetch(
        YcSourceConfig(collection="companies", query="robotics", max_results=201),
        None,
        SourceAuth(workspace_id=uuid4()),
    )
    assert len(result.pages) == 201
    assert result.pages[-1].source_ref == "companies/200"
    requests = [json.loads(call[4]) for call in runner.calls]
    assert requests == [
        {"limit": 200, "page": 0, "query": "robotics"},
        {"limit": 200, "page": 1, "query": "robotics"},
    ]


async def test_source_rejects_an_upstream_row_shape_change() -> None:
    runner = _Runner(
        responses=[
            json.dumps(
                {
                    "name": "search.user_manuals",
                    "result": {
                        "status": "success",
                        "count": 1,
                        "total_count": 1,
                        "csv_results": "id,body\n1,changed\n",
                    },
                }
            )
        ]
    )
    with pytest.raises(ValueError):
        await YcSource(runner).fetch(
            YcSourceConfig(collection="user_manuals"),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


async def test_targeted_source_rejects_a_directory_row_shape_change() -> None:
    runner = _Runner(
        responses=[
            _result(
                "companies",
                1,
                ({"id": "1", "one_liner": "The link field disappeared"},),
            )
        ]
    )
    with pytest.raises(ValueError):
        await YcSource(runner).fetch(
            YcSourceConfig(collection="companies", query="robotics"),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


@pytest.mark.parametrize(
    ("input", "expected"),
    [
        (YcReadInput(action="ask", query="What is the seed bar?"), ("agent",)),
        (
            YcReadInput(action="search", query="infra", entity="companies"),
            ("search",),
        ),
        (YcReadInput(action="skills_list"), ("skills", "list")),
        (YcReadInput(action="skills_read", name="set-my-metrics"), ("skills", "read")),
        (YcReadInput(action="tools_context"), ("tools", "context")),
    ],
)
async def test_read_tool_dispatches_only_read_commands(
    input: YcReadInput, expected: tuple[str, ...]
) -> None:
    runner = _Runner(responses=["ok"])
    assert await YcRead(runner).run(input, "conversation") == "ok"
    assert runner.calls[0][: len(expected)] == expected


def test_read_input_rejects_missing_or_misplaced_arguments() -> None:
    with pytest.raises(ValueError, match="requires query"):
        YcReadInput(action="search")
    with pytest.raises(ValueError, match="requires name"):
        YcReadInput(action="skills_read")
    with pytest.raises(ValueError, match="valid only for search"):
        YcReadInput(action="ask", query="x", entity="companies")


def test_device_authorization_rejects_another_verification_host() -> None:
    with pytest.raises(ValidationError, match="another verification host"):
        YcDeviceAuthorization(
            device_code="device",
            user_code="code",
            verification_uri="https://attacker.test/device",
            expires_in=600,
        )


async def test_cli_uses_the_encrypted_slot_and_an_allowlisted_environment(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    credentials = _credentials()
    await store.put(workspace_id, YC_CREDENTIALS_SLOT, credentials)
    monkeypatch.setenv("UFO_DEPLOY_SECRET", "must-not-reach-yc")
    script = """
import json
import os
from pathlib import Path
assert "UFO_DEPLOY_SECRET" not in os.environ
path = Path.home() / ".yc/credentials.json"
credentials = json.loads(path.read_text())
credentials["created_at"] = 2
path.write_text(json.dumps(credentials))
print('{"authenticated":true}')
"""
    with ws(workspace_id):
        output = await YcCli(
            context_for(NAME, frozenset({YC_CREDENTIALS_SLOT})).credentials,
            executable=sys.executable,
        ).run(("-c", script), "test-session")
    assert json.loads(output) == {"authenticated": True}
    refreshed = json.loads(await store.get(workspace_id, YC_CREDENTIALS_SLOT))
    assert refreshed["created_at"] == 2


async def test_cli_persists_a_token_refresh_after_the_command_fails(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, YC_CREDENTIALS_SLOT, _credentials())
    script = """
import json
from pathlib import Path
path = Path.home() / ".yc/credentials.json"
credentials = json.loads(path.read_text())
credentials["created_at"] = 2
path.write_text(json.dumps(credentials))
raise SystemExit("command failed")
"""
    with ws(workspace_id):
        with pytest.raises(YcCliError, match="command failed"):
            await YcCli(
                context_for(NAME, frozenset({YC_CREDENTIALS_SLOT})).credentials,
                executable=sys.executable,
            ).run(("-c", script), "test-session")
    refreshed = json.loads(await store.get(workspace_id, YC_CREDENTIALS_SLOT))
    assert refreshed["created_at"] == 2


async def test_cli_leaves_refreshed_env_credentials_unstored(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    monkeypatch.setenv("YC_CLI_CREDENTIALS", _credentials())
    script = """
import json
from pathlib import Path
path = Path.home() / ".yc/credentials.json"
credentials = json.loads(path.read_text())
credentials["created_at"] = 2
path.write_text(json.dumps(credentials))
print('{"authenticated":true}')
"""
    with ws(workspace_id):
        output = await YcCli(
            context_for(NAME, frozenset({YC_CREDENTIALS_SLOT})).credentials,
            executable=sys.executable,
        ).run(("-c", script), "test-session")
    assert json.loads(output) == {"authenticated": True}
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, YC_CREDENTIALS_SLOT)


async def test_cli_keeps_the_latest_concurrent_refresh(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    original = _credentials()
    await store.put(workspace_id, YC_CREDENTIALS_SLOT, original)
    cli = YcCli(context_for(NAME, frozenset({YC_CREDENTIALS_SLOT})).credentials)
    older_home = await asyncio.to_thread(cli._prepare_home, original)
    newer_home = await asyncio.to_thread(cli._prepare_home, original)
    try:
        await asyncio.to_thread(
            (older_home / ".yc/credentials.json").write_text,
            _credentials(created_at=2),
            encoding="utf-8",
        )
        await asyncio.to_thread(
            (newer_home / ".yc/credentials.json").write_text,
            _credentials(created_at=3),
            encoding="utf-8",
        )
        with ws(workspace_id):
            await cli._persist_refresh(older_home, original)
            await cli._persist_refresh(newer_home, original)
        assert json.loads(await store.get(workspace_id, YC_CREDENTIALS_SLOT))["created_at"] == 3
    finally:
        await asyncio.to_thread(shutil.rmtree, older_home)
        await asyncio.to_thread(shutil.rmtree, newer_home)


async def test_onboarding_registers_both_indexed_collections(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await setup_sources(context_for(NAME, frozenset({"yc_cli_credentials"})))
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.source.c.backend, tables.source.c.config).where(
                    tables.source.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert {row.backend for row in rows} == {YC_SOURCE_BACKEND}
    assert {row.config["collection"] for row in rows} == set(YC_GUIDANCE_COLLECTIONS)


async def test_onboarded_source_skips_without_auth_instead_of_backing_off(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    try:
        with ws(workspace_id), pytest.raises(StreamSkipped, match="not connected"):
            await YcSource(
                YcCli(context_for(NAME, frozenset({YC_CREDENTIALS_SLOT})).credentials)
            ).fetch(
                YcSourceConfig(collection="user_manuals"),
                None,
                SourceAuth(workspace_id=workspace_id),
            )
    finally:
        init_workspace_credentials(None)


async def test_index_tool_requires_auth_and_registers_one_idempotent_source(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    context = cast(
        ToolContext,
        _ToolCall(context_for(NAME, frozenset({YC_CREDENTIALS_SLOT}))),
    )
    try:
        with ws(workspace_id):
            with pytest.raises(CredentialSlotUnset):
                await yc_index(
                    context,
                    YcIndexInput(entity="companies", query="climate infrastructure"),
                )
        await store.put(workspace_id, YC_CREDENTIALS_SLOT, '{"authenticated":true}')
        with ws(workspace_id):
            first = await yc_index(
                context,
                YcIndexInput(
                    entity="companies",
                    query="  climate infrastructure  ",
                    max_results=201,
                ),
            )
            second = await yc_index(
                context,
                YcIndexInput(
                    entity="companies",
                    query="climate infrastructure",
                    max_results=201,
                ),
            )
        assert first == second
        assert "up to 201 companies" in first.content[0].text
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.source.c.backend, tables.source.c.config).where(
                        tables.source.c.workspace_id == workspace_id
                    )
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].backend == YC_SOURCE_BACKEND
        assert rows[0].config == YcSourceConfig(
            collection="companies",
            query="climate infrastructure",
            max_results=201,
        ).model_dump(mode="json")
    finally:
        init_workspace_credentials(None)


async def test_onboarded_collections_sync_to_the_durable_page_feed(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    runner = _GuidanceRunner()
    blob = FilesystemBlobStore(root=tmp_path)
    driver = SyncDriver(
        backends={YC_SOURCE_BACKEND: YcSource(runner)},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    with ws(workspace_id):
        await setup_sources(context_for(NAME, frozenset({"yc_cli_credentials"})))
        await driver.run()
        changes = (await CorePageFeed(blob).pages_changed_since(None, 10)).changes
    assert len(changes) == len(YC_GUIDANCE_COLLECTIONS)
    assert {change.body.splitlines()[2] for change in changes} == {"Official YC guidance"}
    assert {"YC user manuals evidence.", "YC startup library evidence."} == {
        line
        for change in changes
        for line in change.body.splitlines()
        if line.endswith("evidence.")
    }
