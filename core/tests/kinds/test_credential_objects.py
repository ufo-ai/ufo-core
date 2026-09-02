"""The `credential` object kind end to end: declared BYOK slots listed filled-or-empty, read
without the value ever appearing, cleared by an admin alone. The sample extension's declared
slot is the real instance; a stored secret is asserted absent — plaintext and ciphertext — from
every verb's output."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import load_manifests, turn_tools
from ufo.host.kinds.credential_kind import CREDENTIAL_KIND
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.runtime.objects import AdminRequired, VerbNotSupported
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

TOOL_NARRATION = "checking their saved keys"

SECRET = "hunter2-super-secret-value"
ADMIN_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
JOINER_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)
SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object verbs must not spawn a subagent")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member(workspace_id: UUID, created_at: datetime) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                is_admin=created_at == ADMIN_CREATED_AT,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return member_id


def _tool_context(workspace_id: UUID, speaker_member_id: UUID | None = None) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=Path()),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


def _object_tool(name: str) -> ToolDef:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    tools, _, _ = turn_tools(
        (manifest,),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )
    return next(tool for tool in tools if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({**args}),
    )
    assert result.is_error is False
    return result.content[0].text


async def test_declared_slot_lists_reads_and_clears_without_the_value(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    outputs: list[str] = []
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        kinds = json.loads(await _text(_object_tool("object_list"), ctx))
        assert CREDENTIAL_KIND in {row["kind"] for row in kinds["kinds"]}

        empty_listing = await _text(_object_tool("object_list"), ctx, kind=CREDENTIAL_KIND)
        outputs.append(empty_listing)
        rows = json.loads(empty_listing)["objects"]
        assert [row["name"] for row in rows] == ["sample-api"]
        assert "empty" in rows[0]["summary"]

        await store.put(workspace_id, sample.API_SLOT, SECRET)

        filled_listing = await _text(_object_tool("object_list"), ctx, kind=CREDENTIAL_KIND)
        outputs.append(filled_listing)
        assert "filled" in json.loads(filled_listing)["objects"][0]["summary"]

        fetched_text = await _text(
            _object_tool("object_get"), ctx, ref=f"{CREDENTIAL_KIND}/sample-api"
        )
        outputs.append(fetched_text)
        fetched = yaml.safe_load(fetched_text)
        assert fetched["spec"] == {
            "slot": sample.API_SLOT,
            "description": "BYOK key the egress proxy swaps onto the sample host.",
            "extension": sample.NAME,
            "host": sample.INJECTION_HOST,
            "host_slot": "",
            "host_options": [],
        }
        assert fetched["status"]["filled"] is True
        assert fetched["status"]["host"] == sample.INJECTION_HOST
        assert isinstance(fetched["created_at"], str)
        assert isinstance(fetched["updated_at"], str)

        outputs.append(await _text(_object_tool("object_explain"), ctx, kind=CREDENTIAL_KIND))

        deleted_text = await _text(
            _object_tool("object_delete"), ctx, kind=CREDENTIAL_KIND, name="sample-api"
        )
        outputs.append(deleted_text)
        assert json.loads(deleted_text)["deleted"] is True

        cleared_text = await _text(
            _object_tool("object_get"), ctx, ref=f"{CREDENTIAL_KIND}/sample-api"
        )
        outputs.append(cleared_text)
        cleared = yaml.safe_load(cleared_text)
        assert cleared["status"] == {
            "filled": False,
            "host": sample.INJECTION_HOST,
        }
        assert cleared["created_at"] is None
        assert cleared["updated_at"] is None

        async with workspace_tx() as connection:
            ciphertexts = (
                (await connection.execute(sa.select(tables.credential.c.ciphertext)))
                .scalars()
                .all()
            )
        assert ciphertexts == []
    for output in outputs:
        assert SECRET not in output
        assert "ciphertext" not in output


async def test_ciphertext_never_reaches_a_verb_output(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        await store.put(workspace_id, sample.API_SLOT, SECRET)
        async with workspace_tx() as connection:
            ciphertext = (
                await connection.execute(sa.select(tables.credential.c.ciphertext))
            ).scalar_one()
        outputs = [
            await _text(_object_tool("object_list"), ctx, kind=CREDENTIAL_KIND),
            await _text(_object_tool("object_get"), ctx, ref=f"{CREDENTIAL_KIND}/sample-api"),
        ]
    sealed = ciphertext.decode()
    for output in outputs:
        assert SECRET not in output
        assert sealed not in output


async def test_fill_stays_the_request_credentials_path(db: None) -> None:
    workspace_id = await _workspace()
    apply_tool = _object_tool("object_apply")
    args = apply_tool.input_model.model_validate(
        {
            "manifest": yaml.safe_dump(
                {
                    "kind": CREDENTIAL_KIND,
                    "name": "sample-api",
                    "spec": {"slot": sample.API_SLOT},
                }
            ),
        }
    )
    with ws(workspace_id), pytest.raises(VerbNotSupported, match="request_credentials"):
        await apply_tool.handler(_tool_context(workspace_id), args)


async def test_clearing_a_slot_is_owner_gated(db: None) -> None:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    delete_tool = _object_tool("object_delete")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        await store.put(workspace_id, sample.API_SLOT, SECRET)
        args = delete_tool.input_model.model_validate(
            {"kind": CREDENTIAL_KIND, "name": "sample-api"}
        )
        with pytest.raises(AdminRequired):
            await delete_tool.handler(_tool_context(workspace_id, joiner), args)
        await _text(
            delete_tool,
            _tool_context(workspace_id, owner),
            kind=CREDENTIAL_KIND,
            name="sample-api",
        )
        async with workspace_tx() as connection:
            remaining = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.credential))
            ).scalar_one()
    assert remaining == 0


async def test_the_credential_kind_filters_and_orders_on_its_declared_fields(db: None) -> None:
    """`slot`, `extension`, and `filled` ride the listing rows, so an admin can identify the
    declaration and which slots still need a value without opening each one."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    probe = Manifest(
        name="probe",
        version="1",
        credentials=(
            CredentialSlot(name="probe_api_key", description="Probe API key."),
            CredentialSlot(name="probe_api_host", description="Probe site."),
        ),
    )
    tools, _, _ = turn_tools((probe,), store, audience=conversation_audience(None))
    listing = next(tool for tool in tools if tool.name == "object_list")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        await store.put(workspace_id, "probe_api_key", "probe-secret")
        listed = json.loads(await _text(listing, ctx, kind=CREDENTIAL_KIND))
        empty = json.loads(
            await _text(listing, ctx, kind=CREDENTIAL_KIND, filters={"filled": False})
        )
        by_extension = json.loads(
            await _text(listing, ctx, kind=CREDENTIAL_KIND, filters={"extension": "probe"})
        )
        elsewhere = json.loads(
            await _text(listing, ctx, kind=CREDENTIAL_KIND, filters={"extension": "sample"})
        )
        filled_last = json.loads(await _text(listing, ctx, kind=CREDENTIAL_KIND, order_by="filled"))

    rows = {row["name"]: row for row in listed["objects"]}
    assert rows["probe-api-key"] == {
        "ref": f"{CREDENTIAL_KIND}/probe-api-key",
        "name": "probe-api-key",
        "summary": "probe: Probe API key. — filled",
        "slot": "probe_api_key",
        "extension": "probe",
        "filled": True,
    }
    assert [row["name"] for row in empty["objects"]] == ["probe-api-host"]
    assert [row["name"] for row in by_extension["objects"]] == ["probe-api-host", "probe-api-key"]
    assert elsewhere["objects"] == []
    assert [row["name"] for row in filled_last["objects"]] == ["probe-api-host", "probe-api-key"]


async def test_a_host_choice_slot_renders_its_options_through_tool_dispatch(db: None) -> None:
    """The agent reads a slot through `object_get`, so what matters is the text the verb
    serializes — and until now the only slot driven through dispatch had a plain-string host, so
    `host_slot` and `host_options` were always empty there. This drives a real choice-bearing slot
    through the real tool, so the options the member picks from and the host this workspace resolved
    both have to survive serialization, not merely exist on the dataclass."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    choice = HostChoice(
        slot="probe_api_host",
        description="Probe site for this org.",
        hosts=("api.one.test", "api.two.test"),
        default="api.one.test",
        env="PROBE_HOST",
    )
    probe = Manifest(
        name="probe",
        version="1",
        credentials=(
            CredentialSlot(
                name="probe_api_key",
                description="Probe API key.",
                injection=InjectionTarget(
                    host=choice, header="X-Probe-Key", sentinel="S_PROBE", env="PROBE_KEY"
                ),
            ),
            CredentialSlot(name="probe_api_host", description="Probe site."),
        ),
    )
    tools, _, _ = turn_tools((probe,), store, audience=conversation_audience(None))
    get_tool = next(tool for tool in tools if tool.name == "object_get")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        await store.put(workspace_id, "probe_api_key", "probe-secret")
        default_text = await _text(get_tool, ctx, ref=f"{CREDENTIAL_KIND}/probe-api-key")
        await store.put(workspace_id, "probe_api_host", "api.two.test")
        chosen_text = await _text(get_tool, ctx, ref=f"{CREDENTIAL_KIND}/probe-api-key")

    unchosen = yaml.safe_load(default_text)
    assert unchosen["spec"]["host_slot"] == "probe_api_host"
    assert unchosen["spec"]["host_options"] == ["api.one.test", "api.two.test"]
    assert unchosen["spec"]["host"] == ""
    assert unchosen["status"]["host"] == "api.one.test"
    assert yaml.safe_load(chosen_text)["status"]["host"] == "api.two.test"
