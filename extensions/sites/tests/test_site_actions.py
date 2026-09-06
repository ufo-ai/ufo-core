import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import SITE_KIND, site_object_name
from ufo_ext_sites.store import HostedSites, hosted_site
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    Usage,
)
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import turn_hooks, turn_tools
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.compaction import Compaction
from ufo.runtime.engine import TurnEngine
from ufo.runtime.hub import InProcessHub
from ufo.runtime.objects import ObjectVerbs
from ufo.runtime.prompts.render import rendered_prompt
from ufo.runtime.queue import (
    _agent_actions,
    _agent_tools,
    _subagent_actions,
    _with_action_verbs,
)
from ufo.runtime.tools.context import SpawnResult
from ufo.runtime.tools.registry import ToolDef, ToolRegistry
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import ActivitySummarizer
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, SCHEDULED_ADMISSION, Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.objects import AGENT_KIND

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

PUBLIC_BASE_URL = "https://ufo.example.test"
SITE_ACTIONS = ("build_website", "deploy_website", "publish_website")
SITE_ACTION_IDS = frozenset(f"action:{SITE_KIND}:{name}" for name in SITE_ACTIONS)
SET_HOMEPAGE_ID = f"action:{AGENT_KIND}:set_homepage"
AGENT_NAME = "tasks"
OTHER_AGENT = "radar"


class _StubCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)


class _ActivityModel:
    model = "gpt-5.6-luna"

    async def complete(self, request: ModelRequest) -> str:
        return "Working on the request."


class _NoRecall:
    async def search(self, reader: object, queries: tuple[str, ...]) -> tuple[()]:
        return ()


class _CallsModel:
    def __init__(self, calls: tuple[tuple[str, dict[str, object]], ...]) -> None:
        self.calls = calls

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        answered = any(
            isinstance(message.content, tuple)
            and any(isinstance(block, ToolResultBlock) for block in message.content)
            for message in request.messages
        )
        if answered:
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        for index, (name, args) in enumerate(self.calls):
            call_id = f"c{index + 1}"
            yield ToolCallStart(id=call_id, name=name)
            yield ToolCallDelta(id=call_id, partial_json=json.dumps(args))
        yield Usage(input_tokens=1, output_tokens=1)


def _action_model(kind: str, action: str, **wire: object) -> _CallsModel:
    return _CallsModel((("object_action", {"kind": kind, "action": action, **wire}),))


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("this action must not spawn")


def _toolset() -> tuple[tuple[ToolDef, ...], dict, ObjectVerbs]:
    return turn_tools(
        (sites_manifest(),),
        None,
        audience=conversation_audience(None),
        public_base_url=PUBLIC_BASE_URL,
    )


async def _seed_turn(
    *, visibility: str = "private", speaker: bool = False, admin: bool = True
) -> tuple[Turn, UUID]:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="owner@example.com",
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=uuid4(),
                workspace_id=workspace_id,
                name="ufo",
                prompt="Help.",
                model="claude-opus-4-8",
                visibility="workspace",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=AGENT_NAME,
                prompt="Track the team's work.",
                model="claude-opus-4-8",
                visibility=visibility,
                is_main=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"homepage/{agent_id}/{member_id}",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="build your homepage",
                admission_source=MEMBER_ADMISSION if speaker else SCHEDULED_ADMISSION,
                speaker_member_id=member_id if speaker else None,
                on_behalf_of_member_id=None if speaker else member_id,
                terminal=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return (
        Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="queued",
            inbound="build your homepage",
            admission_source=MEMBER_ADMISSION if speaker else SCHEDULED_ADMISSION,
            speaker_member_id=member_id if speaker else None,
            on_behalf_of_member_id=None if speaker else member_id,
            created_at=created_at,
            terminal=None,
        ),
        member_id,
    )


async def _seed_agent(
    workspace_id: UUID, name: str, owner_member_id: UUID | None, visibility: str = "workspace"
) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="Watch the market.",
                model="claude-opus-4-8",
                visibility=visibility,
                is_main=False,
                owner_member_id=owner_member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _bind_other(
    turn: Turn, member_id: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ToolResultBlock:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    with ws(turn.workspace_id):
        site = await HostedSites(turn.workspace_id, workspace_tx).register(
            turn.conversation_id,
            "home",
            40000,
            member_id,
            None,
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        wire: dict[str, object] = {
            "name": OTHER_AGENT,
            "input": {"site": site_object_name(site.conversation_id, site.name)},
        }
        if turn.speaker_member_id is not None:
            wire["requested_by"] = str(turn.id)
        engine = _engine(turn, _action_model(AGENT_KIND, "set_homepage", **wire), tmp_path)
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        return await _tool_result(engine)


def _engine(turn: Turn, model: object, tmp_path: Path, spawn=_unavailable_spawn) -> TurnEngine:
    all_tools, tool_ext, verbs = _toolset()
    granted_actions = _agent_actions(verbs.actions, None, turn.admission_source)
    registry = ToolRegistry(
        _with_action_verbs(
            _agent_tools(all_tools, None, turn.admission_source), all_tools, granted_actions
        )
    )
    blob = FilesystemBlobStore(root=tmp_path)
    handle = SandboxHandle(conversation_id=turn.conversation_id, container_id="test")
    return TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        byok=False,
        system_prompt=rendered_prompt("p"),
        serving=serving_model(model),
        activity_summarizer=ActivitySummarizer(_ActivityModel()),
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            serving=serving_model(model), blob=blob, conversation_id=turn.conversation_id
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=_StubCarrier(), handle=handle),
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=registry,
        tool_ext=tool_ext,
        hooks=turn_hooks(
            (sites_manifest(),),
            CredentialStore(fernet=Fernet(Fernet.generate_key())),
            audience=conversation_audience(None),
        ),
        blob=blob,
        spawn=spawn,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=None,
        memory=_NoRecall(),
        public_base_url=PUBLIC_BASE_URL,
        verbs=verbs,
        granted_actions=granted_actions,
    )


async def _tool_result(engine: TurnEngine) -> ToolResultBlock:
    stored = await engine.transcript.read()
    assert stored is not None
    (block,) = next(
        tuple(block for block in message.content if isinstance(block, ToolResultBlock))
        for message in stored.messages
        if isinstance(message.content, tuple)
        and any(isinstance(block, ToolResultBlock) for block in message.content)
    )
    return block


def test_the_site_family_registers_only_as_canonical_actions() -> None:
    tools, ext_by_tool, verbs = _toolset()
    assert tuple(sorted(verbs.actions[SITE_KIND])) == SITE_ACTIONS
    assert "set_homepage" in verbs.actions[AGENT_KIND]
    for bound in (*verbs.actions[SITE_KIND].values(), verbs.actions[AGENT_KIND]["set_homepage"]):
        assert bound.extension == "sites"
        assert bound.context is not None and bound.context.store.extension == "sites"
    wire = {tool.name for tool in tools}
    assert wire.isdisjoint({*SITE_ACTIONS, "set_homepage"})
    assert {"start_server", "object_action"} <= wire
    assert set(ext_by_tool).isdisjoint({*SITE_ACTIONS, "set_homepage"})
    assert SITE_ACTION_IDS | {SET_HOMEPAGE_ID} <= _agent_actions(
        verbs.actions, None, MEMBER_ADMISSION
    )
    assert _agent_actions(
        verbs.actions, ("action:site:deploy_website", "bash"), MEMBER_ADMISSION
    ) == frozenset({"action:site:deploy_website"})
    assert _agent_actions(verbs.actions, ("deploy_website",), MEMBER_ADMISSION) == frozenset()
    assert _subagent_actions(verbs.actions, WEBSITE_BUILDING_PROFILE, frozenset()) == frozenset(
        {"action:site:deploy_website", SET_HOMEPAGE_ID}
    )


async def test_a_speakerless_turn_binds_its_own_agents_homepage(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    turn, member_id = await _seed_turn(visibility="private")
    with ws(turn.workspace_id):
        site = await HostedSites(turn.workspace_id, workspace_tx).register(
            turn.conversation_id,
            "home",
            40000,
            member_id,
            None,
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        engine = _engine(
            turn,
            _action_model(
                AGENT_KIND,
                "set_homepage",
                name=AGENT_NAME,
                input={"site": site_object_name(site.conversation_id, site.name)},
            ),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        result = await _tool_result(engine)
    assert result.is_error is False
    payload = json.loads(str(result.content))
    assert payload["homepage_agent"] == str(turn.agent_id)
    assert payload["visibility"] == "private"
    async with workspace_tx() as connection:
        bound = (
            await connection.execute(
                sa.select(hosted_site.c.homepage_agent_id).where(
                    hosted_site.c.workspace_id == turn.workspace_id
                )
            )
        ).scalar_one()
    assert bound == turn.agent_id


async def test_reading_an_agent_can_not_rewrite_its_page(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn, member_id = await _seed_turn(speaker=True, admin=False)
    await _seed_agent(turn.workspace_id, OTHER_AGENT, owner_member_id=None)
    result = await _bind_other(turn, member_id, tmp_path, monkeypatch)
    assert result.is_error is True
    assert "not yours to direct" in str(result.content)
    async with workspace_tx() as connection:
        bound = (
            await connection.execute(
                sa.select(hosted_site.c.homepage_agent_id).where(
                    hosted_site.c.workspace_id == turn.workspace_id
                )
            )
        ).scalar_one()
    assert bound is None


async def test_an_agents_owner_binds_its_page_from_another_agents_turn(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn, member_id = await _seed_turn(speaker=True, admin=False)
    other = await _seed_agent(turn.workspace_id, OTHER_AGENT, owner_member_id=member_id)
    result = await _bind_other(turn, member_id, tmp_path, monkeypatch)
    assert result.is_error is False
    assert json.loads(str(result.content))["homepage_agent"] == str(other)


async def test_an_admin_binds_another_agents_page(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn, member_id = await _seed_turn(speaker=True, admin=True)
    other = await _seed_agent(turn.workspace_id, OTHER_AGENT, owner_member_id=None)
    result = await _bind_other(turn, member_id, tmp_path, monkeypatch)
    assert result.is_error is False
    assert json.loads(str(result.content))["homepage_agent"] == str(other)


async def test_a_speakerless_turn_binds_only_its_own_agent_even_for_the_owner(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn, member_id = await _seed_turn(admin=False)
    await _seed_agent(turn.workspace_id, OTHER_AGENT, owner_member_id=member_id)
    result = await _bind_other(turn, member_id, tmp_path, monkeypatch)
    assert result.is_error is True
    assert "needs its creator speaking" in str(result.content)


async def test_a_speakerless_turn_cannot_bind_a_standing_site(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    turn, member_id = await _seed_turn(visibility="private")
    with ws(turn.workspace_id):
        site = await HostedSites(turn.workspace_id, workspace_tx).register(
            uuid4(), "home", 40000, member_id, None, SHARED_AUDIENCE, True, manifest=None
        )
        engine = _engine(
            turn,
            _action_model(
                AGENT_KIND,
                "set_homepage",
                name=AGENT_NAME,
                input={"site": site_object_name(site.conversation_id, site.name)},
            ),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        result = await _tool_result(engine)
    assert result.is_error is True
    assert "needs its creator speaking" in str(result.content)
