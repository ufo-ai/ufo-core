"""The `extension` object kind end to end: the deploy's active manifests listed and read through
the verbs, every point the sample extension declares rendered by name, and install and removal
refused as the deploy acts they are. The sample extension is the real instance — it declares every
Manifest point, so its projection is what holds this kind and the manifest in step."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sample.objects import (
    AUDIT_ACTION,
    BESEECH_ACTION,
    BLESS_ACTION,
    CALIBRATE_ACTION,
    DIVINE_ACTION,
    ENGRAVE_ACTION,
    POLISH_ACTION,
    RELIC_KIND,
    WIDGET_KIND,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.extension_kind import EXTENSION_KIND, ExtensionObjects, named_extensions
from ufo.host.ext.loader import core_object_kinds, load_manifests, turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.manifest import CredentialSlot, Manifest
from ufo.runtime.object_name import OBJECT_NAME_MAX_LENGTH, InvalidName
from ufo.runtime.objects import UnknownObject, VerbNotSupported
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

TOOL_NARRATION = "checking what this deploy is running"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _context(workspace_id: UUID) -> ToolContext:
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
            inbound="what extensions are installed",
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="m"),
        spawn=_unavailable_spawn,
        speaker_member_id=uuid4(),
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


def _tools(manifests: tuple[Manifest, ...]) -> dict[str, ToolDef]:
    tools, _, _ = turn_tools(
        manifests,
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )
    return {tool.name: tool for tool in tools}


def _sample() -> Manifest:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    return manifest


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_active_extension_lists_and_reads_everything_it_declares(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample()
    tools = _tools((manifest,))
    with ws(workspace_id):
        ctx = _context(workspace_id)

        kinds = json.loads(await _text(tools["object_list"], ctx))["kinds"]
        assert EXTENSION_KIND in {row["kind"] for row in kinds}

        listed = json.loads(await _text(tools["object_list"], ctx, kind=EXTENSION_KIND))["objects"]
        assert listed == [
            {
                "ref": f"{EXTENSION_KIND}/{sample.NAME}",
                "name": sample.NAME,
                "summary": f"{sample.NAME} {manifest.version}, 10 tools, 2 credential slots",
                "version": manifest.version,
                "tool_count": 10,
                "credential_slot_count": 2,
            }
        ]

        read = yaml.safe_load(
            await _text(tools["object_get"], ctx, ref=f"{EXTENSION_KIND}/{sample.NAME}")
        )
        assert read["spec"] == {
            "extension": sample.NAME,
            "version": manifest.version,
            "tools": [
                "sample_echo",
                "sample_note",
                AUDIT_ACTION,
                POLISH_ACTION,
                ENGRAVE_ACTION,
                DIVINE_ACTION,
                CALIBRATE_ACTION,
                BLESS_ACTION,
                BESEECH_ACTION,
                "sample_connector_execute",
            ],
            "object_kinds": [WIDGET_KIND, RELIC_KIND],
            "credential_slots": [sample.API_SLOT, sample.MINTED_SLOT],
            "surfaces": [surface.name for surface in manifest.surfaces],
            "jobs": [job.name for job in manifest.jobs],
            "hooks": sorted({hook.event for hook in manifest.hooks}),
            "sources": [source.backend for source in manifest.sources],
            "subagents": [profile.name for profile in manifest.subagents],
        }
        assert read["status"] == {"sandbox_internet": False, "requires": []}
        assert read["created_at"] is None
        assert read["updated_at"] is None
        assert read["links"] == []

        explained = json.loads(await _text(tools["object_explain"], ctx, kind=EXTENSION_KIND))
        assert set(explained["spec_schema"]["properties"]) == {
            "extension",
            "version",
            "tools",
            "object_kinds",
            "credential_slots",
            "surfaces",
            "jobs",
            "hooks",
            "sources",
            "subagents",
        }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_extension_kind_filters_and_orders_on_its_declared_fields(db: None) -> None:
    """`version`, `tool_count`, and `credential_slot_count` ride the listing rows, so an admin sees
    which version is running and which extensions carry keys without opening each one."""
    workspace_id = await _workspace()
    keyed = Manifest(
        name="probe_keyed",
        version="2.0.0",
        credentials=(CredentialSlot(name="probe_api_key", description="Probe API key."),),
    )
    bare = Manifest(name="probe_bare", version="1.0.0")
    tools = _tools((keyed, bare))
    declared = next(
        bound.kind.list_fields
        for bound in core_object_kinds((keyed, bare))
        if bound.kind.name == EXTENSION_KIND
    )
    with ws(workspace_id):
        ctx = _context(workspace_id)
        listing = tools["object_list"]

        rows = json.loads(await _text(listing, ctx, kind=EXTENSION_KIND))["objects"]
        assert [row["name"] for row in rows] == ["probe-bare", "probe-keyed"]
        assert all(declared <= set(row) for row in rows)

        by_version = json.loads(
            await _text(listing, ctx, kind=EXTENSION_KIND, filters={"version": "2.0.0"})
        )
        assert [row["name"] for row in by_version["objects"]] == ["probe-keyed"]

        unkeyed = json.loads(
            await _text(listing, ctx, kind=EXTENSION_KIND, filters={"credential_slot_count": 0})
        )
        assert [row["name"] for row in unkeyed["objects"]] == ["probe-bare"]

        ordered = json.loads(
            await _text(
                listing,
                ctx,
                kind=EXTENSION_KIND,
                order_by="credential_slot_count",
                order="desc",
            )
        )
        assert [row["name"] for row in ordered["objects"]] == ["probe-keyed", "probe-bare"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_status_carries_what_the_manifest_asks_of_the_deploy(db: None) -> None:
    """Status is the declaration's other side, so it reads off the manifest rather than off the
    defaults every manifest shares."""
    workspace_id = await _workspace()
    asking = Manifest(
        name="probe_asking",
        version="1.0.0",
        sandbox_internet=True,
        requires=("cdp_providers", "search_providers"),
    )
    quiet = Manifest(name="probe_quiet", version="1.0.0")
    tools = _tools((asking, quiet))
    with ws(workspace_id):
        ctx = _context(workspace_id)
        asked = yaml.safe_load(
            await _text(tools["object_get"], ctx, ref=f"{EXTENSION_KIND}/probe-asking")
        )
        unasked = yaml.safe_load(
            await _text(tools["object_get"], ctx, ref=f"{EXTENSION_KIND}/probe-quiet")
        )

    assert asked["status"] == {
        "sandbox_internet": True,
        "requires": ["cdp_providers", "search_providers"],
    }
    assert unasked["status"] == {"sandbox_internet": False, "requires": []}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_absent_extension_name_is_no_object_at_all(db: None) -> None:
    workspace_id = await _workspace()
    manifest = Manifest(name="probe_only", version="1.0.0")
    store = ExtensionObjects(extensions=named_extensions((manifest,)))
    tools = _tools((manifest,))
    with ws(workspace_id):
        ctx = _context(workspace_id)
        with pytest.raises(UnknownObject):
            await _text(tools["object_get"], ctx, ref=f"{EXTENSION_KIND}/probe-absent")
        assert await store.get(ctx, "probe-absent") is None
        assert await store.status(ctx, "probe-absent", expected_generation=None) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_installing_or_removing_an_extension_is_refused_as_a_deploy_act(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample()
    tools = _tools((manifest,))
    with ws(workspace_id):
        ctx = _context(workspace_id)
        with pytest.raises(VerbNotSupported, match="ufoctl ext install"):
            await _text(
                tools["object_apply"],
                ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": EXTENSION_KIND,
                        "name": sample.NAME,
                        "spec": {"extension": sample.NAME, "version": manifest.version},
                    }
                ),
            )
        with pytest.raises(VerbNotSupported, match="ufoctl ext remove"):
            await _text(tools["object_delete"], ctx, kind=EXTENSION_KIND, name=sample.NAME)


def test_two_manifests_rendering_one_object_name_fail_loud() -> None:
    with pytest.raises(ValueError, match="both render object name 'probe-one'"):
        named_extensions(
            (Manifest(name="probe_one", version="1"), Manifest(name="probe-one", version="1"))
        )


def test_a_manifest_rendering_an_unaddressable_name_fails_loud() -> None:
    with pytest.raises(InvalidName):
        named_extensions((Manifest(name="p" * (OBJECT_NAME_MAX_LENGTH + 1), version="1"),))
    with pytest.raises(InvalidName):
        named_extensions((Manifest(name="___", version="1"),))
