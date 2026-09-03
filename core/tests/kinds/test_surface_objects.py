import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import core_object_kinds, turn_tools
from ufo.host.kinds.surface_kind import SURFACE_KIND, SurfaceObjects, registered_surfaces
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.surface import SurfaceSpec
from ufo.runtime.objects import ObjectListQuery, UnknownObject, VerbNotSupported
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.audience import Audience, conversation_audience, foreign_room_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

BOUND_AT = datetime(2026, 8, 1, tzinfo=UTC)
REBOUND_AT = datetime(2026, 8, 2, tzinfo=UTC)
SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object verbs must not spawn a subagent")


async def _undeliverable_post(*args: object) -> str:
    raise AssertionError("the surface kind must not deliver")


def _manifests() -> tuple[Manifest, ...]:
    return (
        Manifest(
            name="probe_chat",
            version="1.0.0",
            surfaces=(SurfaceSpec(name="chatty", post=_undeliverable_post),),
        ),
        Manifest(
            name="probe_text",
            version="1.0.0",
            surfaces=(SurfaceSpec(name="texty", post=_undeliverable_post, addressed=True),),
        ),
        Manifest(
            name="probe_browser",
            version="1.0.0",
            surfaces=(SurfaceSpec(name="webby", home=True),),
        ),
        Manifest(name="probe_plain", version="1.0.0"),
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


async def _agent_row(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                visibility="workspace",
                internet_access_allowed=True,
                reasoning="high",
                created_at=BOUND_AT,
                updated_at=BOUND_AT,
            )
        )
    return agent_id


async def _bind(workspace_id: UUID, surface: str, agent_id: UUID, *, routes_ingress: bool) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface=surface,
                installation_id="team:T123",
                agent_id=agent_id,
                routes_ingress=routes_ingress,
                created_at=BOUND_AT,
                updated_at=REBOUND_AT,
            )
        )


def _context(workspace_id: UUID, audience: Audience | None = None) -> ToolContext:
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
            inbound="which surfaces are set up",
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="m"),
        spawn=_unavailable_spawn,
        speaker_member_id=uuid4(),
        audience=audience if audience is not None else conversation_audience(None),
        artifact_token_secret="",
    )


def _tools(manifests: tuple[Manifest, ...]) -> dict[str, ToolDef]:
    tools, _, _ = turn_tools(manifests, None, audience=conversation_audience(None))
    return {tool.name: tool for tool in tools}


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_every_registered_surface_lists_and_reads_its_declaration(db: None) -> None:
    workspace_id = await _workspace()
    tools = _tools(_manifests())
    with ws(workspace_id):
        ctx = _context(workspace_id)

        kinds = json.loads(await _text(tools["object_list"], ctx))["kinds"]
        assert SURFACE_KIND in {row["kind"] for row in kinds}

        listed = json.loads(await _text(tools["object_list"], ctx, kind=SURFACE_KIND))["objects"]
        assert listed == [
            {
                "ref": f"{SURFACE_KIND}/chatty",
                "name": "chatty",
                "summary": "probe_chat: not bound",
                "extension": "probe_chat",
                "addressed": False,
                "durable": True,
                "home": False,
                "bound": False,
            },
            {
                "ref": f"{SURFACE_KIND}/texty",
                "name": "texty",
                "summary": "probe_text: not bound",
                "extension": "probe_text",
                "addressed": True,
                "durable": True,
                "home": False,
                "bound": False,
            },
            {
                "ref": f"{SURFACE_KIND}/webby",
                "name": "webby",
                "summary": "probe_browser: not bound",
                "extension": "probe_browser",
                "addressed": False,
                "durable": False,
                "home": True,
                "bound": False,
            },
        ]

        read = yaml.safe_load(await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/texty"))
        assert read["spec"] == {
            "surface": "texty",
            "extension": "probe_text",
            "addressed": True,
            "durable": True,
            "home": False,
        }
        assert read["status"] == {"bound": False}
        assert read["created_at"] is None
        assert read["updated_at"] is None

        explained = json.loads(await _text(tools["object_explain"], ctx, kind=SURFACE_KIND))
        assert set(explained["spec_schema"]["properties"]) == {
            "surface",
            "extension",
            "addressed",
            "durable",
            "home",
        }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_status_reads_the_workspace_binding_truthfully(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = await _agent_row(workspace_id, "assistant")
    await _bind(workspace_id, "chatty", agent_id, routes_ingress=True)
    tools = _tools(_manifests())
    with ws(workspace_id):
        ctx = _context(workspace_id)

        bound = yaml.safe_load(await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/chatty"))
        assert bound["status"] == {
            "bound": True,
            "agent": "assistant",
            "archived": False,
            "routes_ingress": True,
        }
        assert datetime.fromisoformat(bound["created_at"]).replace(tzinfo=UTC) == BOUND_AT
        assert datetime.fromisoformat(bound["updated_at"]).replace(tzinfo=UTC) == REBOUND_AT

        unbound = yaml.safe_load(await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/texty"))
        assert unbound["status"] == {"bound": False}

        listed = json.loads(await _text(tools["object_list"], ctx, kind=SURFACE_KIND))["objects"]
        by_name = {row["name"]: row for row in listed}
        assert by_name["chatty"]["summary"] == "probe_chat: bound to assistant"
        assert by_name["chatty"]["bound"] is True
        assert by_name["texty"]["bound"] is False

        still_to_bind = json.loads(
            await _text(tools["object_list"], ctx, kind=SURFACE_KIND, filters={"bound": False})
        )["objects"]
        assert [row["name"] for row in still_to_bind] == ["texty", "webby"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_binding_to_an_archived_agent_keeps_its_name_and_says_so(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = await _agent_row(workspace_id, "assistant")
    await _bind(workspace_id, "chatty", agent_id, routes_ingress=True)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == agent_id)
            .values(
                name=f"~archived-{agent_id}",
                archived_name="assistant",
                archived_at=REBOUND_AT,
                is_main=False,
            )
        )
    tools = _tools(_manifests())
    with ws(workspace_id):
        ctx = _context(workspace_id)
        read = yaml.safe_load(await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/chatty"))
        listed = json.loads(await _text(tools["object_list"], ctx, kind=SURFACE_KIND))["objects"]
    assert read["status"] == {
        "bound": True,
        "agent": "assistant",
        "archived": True,
        "routes_ingress": True,
    }
    assert listed[0]["summary"] == "probe_chat: bound to assistant (archived)"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_binding_in_another_workspace_reads_as_unbound(db: None) -> None:
    other_workspace = await _workspace()
    other_agent = await _agent_row(other_workspace, "neighbor")
    await _bind(other_workspace, "chatty", other_agent, routes_ingress=True)
    workspace_id = await _workspace()
    tools = _tools(_manifests())
    with ws(workspace_id):
        ctx = _context(workspace_id)
        read = yaml.safe_load(await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/chatty"))
    assert read["status"] == {"bound": False}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_foreign_audiences_read_no_surfaces(db: None) -> None:
    workspace_id = await _workspace()
    tools = _tools(_manifests())
    store = SurfaceObjects(surfaces=registered_surfaces(_manifests()))
    with ws(workspace_id):
        ctx = _context(workspace_id, audience=foreign_room_audience("chatty", "ROOM7"))
        listed = json.loads(await _text(tools["object_list"], ctx, kind=SURFACE_KIND))["objects"]
        assert listed == []
        with pytest.raises(UnknownObject):
            await _text(tools["object_get"], ctx, ref=f"{SURFACE_KIND}/chatty")
        assert await store.status(ctx, "chatty", expected_generation=None) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_binding_and_removal_are_refused_as_acts_that_live_elsewhere(db: None) -> None:
    workspace_id = await _workspace()
    tools = _tools(_manifests())
    with ws(workspace_id):
        ctx = _context(workspace_id)
        with pytest.raises(VerbNotSupported, match="connect flow binds the installation"):
            await _text(
                tools["object_apply"],
                ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": SURFACE_KIND,
                        "name": "chatty",
                        "spec": {"surface": "chatty", "extension": "probe_chat", "durable": True},
                    }
                ),
            )
        with pytest.raises(VerbNotSupported, match="leaves with the extension"):
            await _text(tools["object_delete"], ctx, kind=SURFACE_KIND, name="chatty")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_portal_reads_answer_an_admin_alone(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = await _agent_row(workspace_id, "assistant")
    await _bind(workspace_id, "chatty", agent_id, routes_ingress=True)
    bound_kind = next(
        bound for bound in core_object_kinds(_manifests()) if bound.kind.name == SURFACE_KIND
    )
    store = bound_kind.kind.store
    assert isinstance(store, SurfaceObjects)
    query = ObjectListQuery(supported_fields=bound_kind.kind.list_fields)
    member_id = uuid4()
    with ws(workspace_id):
        plain = await store.member_page(None, member_id=member_id, admin=False, query=query)
        assert plain.rows == ()
        assert await store.member_detail(None, "chatty", member_id=member_id, admin=False) is None

        page = await store.member_page(None, member_id=member_id, admin=True, query=query)
        assert [row.name for row in page.rows] == ["chatty", "texty", "webby"]
        read = await store.member_detail(None, "chatty", member_id=member_id, admin=True)
        assert read is not None
        assert read.row.fields["bound"] is True
        assert read.detail.spec.extension == "probe_chat"


def test_two_extensions_registering_one_surface_name_fail_loud() -> None:
    with pytest.raises(ValueError, match="both register surface 'twin'"):
        registered_surfaces(
            (
                Manifest(name="probe_one", version="1", surfaces=(SurfaceSpec(name="twin"),)),
                Manifest(name="probe_two", version="1", surfaces=(SurfaceSpec(name="twin"),)),
            )
        )
