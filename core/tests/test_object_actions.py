"""The object-action registry and discovery seam: declaration and boot gates, the loader
partition, the discovery envelopes, and the grant computation — driven through the sample
extension's six actions and read back off public surfaces."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, SecretStr

from ufo.access.credentials import CredentialStore
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.ext.loader import load_manifests, turn_tools, validate_ext_tools
from ufo.ext.manifest import AgentProvision, Manifest, SubagentProfile, SubagentToolGrant
from ufo.kinds.agents import AgentSpec
from ufo.loop.queue import (
    IMPLIED_GRANTS,
    _agent_actions,
    _agent_tools,
    _subagent_actions,
    _with_action_dispatcher,
)
from ufo.objects import (
    BoundAction,
    BoundKind,
    ObjectVerbs,
    action_registry,
    object_registry,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import INTENT_ADMISSION, MEMBER_ADMISSION, Agent, Turn
from ufo.tools.bridge import bridge_tools
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import (
    ActionPresentation,
    ObjectBinding,
    ToolDef,
    ToolRegistry,
)
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws

AUDIT_ID = f"action:{sample.WORKSPACE_KIND}:{sample.AUDIT_ACTION}"
POLISH_ID = f"action:{sample.WIDGET_KIND}:{sample.POLISH_ACTION}"
ENGRAVE_ID = f"action:{sample.WIDGET_KIND}:{sample.ENGRAVE_ACTION}"
DIVINE_ID = f"action:{sample.WIDGET_KIND}:{sample.DIVINE_ACTION}"
CALIBRATE_ID = f"action:{sample.WIDGET_KIND}:{sample.CALIBRATE_ACTION}"
BLESS_ID = sample.BLESS_CANONICAL_ID
BESEECH_ID = f"action:{sample.WIDGET_KIND}:{sample.BESEECH_ACTION}"
SAMPLE_ACTION_IDS = frozenset(
    {AUDIT_ID, POLISH_ID, ENGRAVE_ID, DIVINE_ID, CALIBRATE_ID, BLESS_ID, BESEECH_ID}
)


class _ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = ""


async def _probe(ctx: ToolContext, args: _ProbeInput) -> ToolResult:
    return ToolResult(content=(TextContent(text="ok"),))


def _action(
    name: str = "probe",
    kind: str = sample.WIDGET_KIND,
    binding: str = "instance",
    **overrides: object,
) -> ToolDef:
    return ToolDef(
        name=name,
        description="d",
        input_model=_ProbeInput,
        handler=_probe,
        bound=ObjectBinding(kind=kind, binding=binding),  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


def _sample_verbs() -> tuple[dict[str, ToolDef], ObjectVerbs]:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    tools, ext_by_tool, verbs = turn_tools(
        (manifest,),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )
    assert not set(ext_by_tool) & {tool.bound and tool.name for tool in verbs.tools()}
    return {tool.name: tool for tool in tools}, verbs


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("object actions run against stores and must not reach the sandbox")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError("object actions run against stores and must not reach the sandbox")


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object discovery must not spawn a subagent")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _ctx(
    workspace_id: UUID,
    granted_actions: frozenset[str] = SAMPLE_ACTION_IDS,
    speaker_member_id: UUID | None = None,
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=Path())),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        granted_actions=granted_actions,
    )


async def _text(tools: dict[str, ToolDef], tool_name: str, ctx: ToolContext, **args: object) -> str:
    tool = tools[tool_name]
    result = await tool.handler(ctx, tool.input_model.model_validate(args))
    assert result.is_error is False
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_canonical_id_is_the_name_for_globals_and_prefixed_for_actions() -> None:
    assert _action("polish").canonical_id == f"action:{sample.WIDGET_KIND}:polish"
    unbound = ToolDef(name="probe", description="d", input_model=_ProbeInput, handler=_probe)
    assert unbound.canonical_id == "probe"


def test_wire_registry_refuses_bound_defs_and_the_action_prefix() -> None:
    with pytest.raises(ValueError, match="never enter the wire registry"):
        ToolRegistry((_action(),))
    prefixed = ToolDef(
        name="action:member:add", description="d", input_model=_ProbeInput, handler=_probe
    )
    with pytest.raises(ValueError, match="reserve the 'action:' prefix"):
        ToolRegistry((prefixed,))


def test_declaration_gates_hold_presentation_and_final_act_models() -> None:
    with pytest.raises(ValueError, match="empty label"):
        ToolRegistry(
            (
                ToolDef(
                    name="p",
                    description="d",
                    input_model=_ProbeInput,
                    handler=_probe,
                    presentation=ActionPresentation(label="  "),
                ),
            )
        )
    with pytest.raises(ValueError, match="empty confirmation"):
        ToolRegistry(
            (
                ToolDef(
                    name="p",
                    description="d",
                    input_model=_ProbeInput,
                    handler=_probe,
                    presentation=ActionPresentation(label="Run", confirm=" "),
                ),
            )
        )
    with pytest.raises(ValueError, match="profile_only"):
        ToolRegistry(
            (
                ToolDef(
                    name="p",
                    description="d",
                    input_model=_ProbeInput,
                    handler=_probe,
                    profile_only=True,
                    presentation=ActionPresentation(label="Run"),
                ),
            )
        )
    with pytest.raises(ValueError, match="no terminal frame field"):
        ToolRegistry(
            (
                ToolDef(
                    name="p",
                    description="d",
                    input_model=_ProbeInput,
                    handler=_probe,
                    final_act_model=_ProbeInput,
                ),
            )
        )


def _kinds() -> dict[str, BoundKind]:
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    return object_registry(
        tuple(
            BoundKind(kind=kind, extension=sample.NAME, context=None) for kind in manifest.objects
        )
    )


def test_action_registry_fails_each_row_of_the_boot_failure_table() -> None:
    kinds = _kinds()

    with pytest.raises(ValueError, match="targets a kind no extension registers"):
        action_registry(
            (BoundAction(action=_action(kind="workspace"), extension="probe", context=None),),
            kinds,
        )
    with pytest.raises(ValueError, match="action names are snake_case"):
        action_registry(
            (BoundAction(action=_action(name="Not-Snake"), extension="probe", context=None),),
            kinds,
        )
    with pytest.raises(ValueError, match="kind names are snake_case"):
        action_registry(
            (BoundAction(action=_action(kind="Not-Snake"), extension="probe", context=None),),
            kinds,
        )
    with pytest.raises(ValueError, match="from 'other' collides with 'probe'"):
        action_registry(
            (
                BoundAction(action=_action(binding="instance"), extension="probe", context=None),
                BoundAction(action=_action(binding="collection"), extension="other", context=None),
            ),
            kinds,
        )

    class Loose(BaseModel):
        value: str = ""

    with pytest.raises(ValueError, match='must set extra="forbid"'):
        action_registry(
            (
                BoundAction(
                    action=ToolDef(
                        name="probe",
                        description="d",
                        input_model=Loose,
                        handler=_probe,
                        bound=ObjectBinding(kind=sample.WIDGET_KIND, binding="instance"),
                    ),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )

    class Secretive(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: SecretStr

    with pytest.raises(ValueError, match="secret-bearing"):
        action_registry(
            (
                BoundAction(
                    action=ToolDef(
                        name="probe",
                        description="d",
                        input_model=Secretive,
                        handler=_probe,
                        bound=ObjectBinding(kind=sample.WIDGET_KIND, binding="instance"),
                    ),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )

    class Reserving(BaseModel):
        model_config = ConfigDict(extra="forbid")
        requested_by: str = ""

    with pytest.raises(ValueError, match="reserves 'requested_by'"):
        action_registry(
            (
                BoundAction(
                    action=ToolDef(
                        name="probe",
                        description="d",
                        input_model=Reserving,
                        handler=_probe,
                        bound=ObjectBinding(kind=sample.WIDGET_KIND, binding="instance"),
                    ),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )

    class Enveloping(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: str = ""
        name: str = ""
        generation: str = ""

    class Envelope(BaseModel):
        model_config = ConfigDict(extra="forbid")
        action: str = ""
        agent: str = ""
        input: dict[str, str] = {}

    with pytest.raises(ValueError, match="reserves 'action', 'agent', 'input'"):
        action_registry(
            (
                BoundAction(
                    action=ToolDef(
                        name="probe",
                        description="d",
                        input_model=Envelope,
                        handler=_probe,
                        bound=ObjectBinding(kind=sample.WIDGET_KIND, binding="collection"),
                    ),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )

    with pytest.raises(ValueError, match="reserves 'kind', 'name', 'generation'"):
        action_registry(
            (
                BoundAction(
                    action=ToolDef(
                        name="probe",
                        description="d",
                        input_model=Enveloping,
                        handler=_probe,
                        bound=ObjectBinding(kind=sample.WIDGET_KIND, binding="instance"),
                    ),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )
    with pytest.raises(ValueError, match="empty label"):
        action_registry(
            (
                BoundAction(
                    action=_action(presentation=ActionPresentation(label=" ")),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )
    with pytest.raises(ValueError, match="profile_only"):
        action_registry(
            (
                BoundAction(
                    action=_action(profile_only=True, presentation=ActionPresentation(label="Run")),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )
    with pytest.raises(ValueError, match="no terminal frame field"):
        action_registry(
            (
                BoundAction(
                    action=_action(final_act_model=_ProbeInput),
                    extension="probe",
                    context=None,
                ),
            ),
            kinds,
        )


def test_cross_extension_attachment_is_order_independent() -> None:
    """The loader validates only after every manifest is collected, so an action naming a kind a
    later extension registers still lands — proved through the boot gate over two synthetic
    manifests in each order."""
    kind_manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    attacher = Manifest(
        name="attacher",
        version="0.1.0",
        tools=(_action(name="probe", kind=sample.WIDGET_KIND),),
    )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    validate_ext_tools((attacher, kind_manifest), store)
    validate_ext_tools((kind_manifest, attacher), store)


def test_bridge_tools_skip_bound_defs_whatever_they_are_named() -> None:
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    squatter = Manifest(
        name="squatter",
        version="0.1.0",
        tools=(_action(name="object_list", kind=sample.WIDGET_KIND, binding="collection"),),
    )
    names = [tool.name for tool in bridge_tools((manifest, squatter))]
    assert names.count("object_list") == 1
    assert "object_action" in names


def test_loader_partitions_bound_defs_out_of_the_wire_registry() -> None:
    tools, verbs = _sample_verbs()
    action_names = {
        sample.AUDIT_ACTION,
        sample.POLISH_ACTION,
        sample.ENGRAVE_ACTION,
        sample.DIVINE_ACTION,
        sample.CALIBRATE_ACTION,
        sample.BLESS_ACTION,
        sample.BESEECH_ACTION,
    }
    assert not action_names & set(tools)
    assert "object_action" in tools
    assert set(verbs.actions[sample.WIDGET_KIND]) == action_names - {sample.AUDIT_ACTION}
    assert set(verbs.actions[sample.WORKSPACE_KIND]) == {sample.AUDIT_ACTION}
    audit = verbs.actions[sample.WORKSPACE_KIND][sample.AUDIT_ACTION]
    assert audit.extension == sample.NAME
    assert audit.context is not None
    assert audit.action.canonical_id == AUDIT_ID


def test_deploy_validation_returns_the_context_free_action_registry() -> None:
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    actions = validate_ext_tools((manifest,), CredentialStore(fernet=Fernet(Fernet.generate_key())))
    assert set(actions[sample.WIDGET_KIND]) == {
        sample.POLISH_ACTION,
        sample.ENGRAVE_ACTION,
        sample.DIVINE_ACTION,
        sample.CALIBRATE_ACTION,
        sample.BLESS_ACTION,
        sample.BESEECH_ACTION,
    }
    assert actions[sample.WORKSPACE_KIND][sample.AUDIT_ACTION].context is None


async def test_list_envelope_carries_granted_collection_actions(db: None) -> None:
    workspace_id = await _workspace()
    tools, _ = _sample_verbs()
    with ws(workspace_id):
        listing = json.loads(
            await _text(tools, "object_list", _ctx(workspace_id), kind=sample.WIDGET_KIND)
        )
        views = listing["actions"]
        assert [view["name"] for view in views] == [
            sample.BESEECH_ACTION,
            sample.CALIBRATE_ACTION,
            sample.DIVINE_ACTION,
        ]
        divine = views[2]
        assert divine["description"]
        assert divine["input_schema"]["properties"]["query"]["type"] == "string"
        assert divine["call"] == {
            "kind": sample.WIDGET_KIND,
            "action": sample.DIVINE_ACTION,
            "input": {},
        }
        assert "label" not in divine and "confirm" not in divine

        granted_one = json.loads(
            await _text(
                tools,
                "object_list",
                _ctx(workspace_id, granted_actions=frozenset({DIVINE_ID})),
                kind=sample.WIDGET_KIND,
            )
        )
        assert [view["name"] for view in granted_one["actions"]] == [sample.DIVINE_ACTION]

        ungranted = json.loads(
            await _text(
                tools,
                "object_list",
                _ctx(workspace_id, granted_actions=frozenset()),
                kind=sample.WIDGET_KIND,
            )
        )
        assert "actions" not in ungranted


async def test_kinds_listing_marks_kinds_with_granted_actions(db: None) -> None:
    workspace_id = await _workspace()
    tools, _ = _sample_verbs()
    with ws(workspace_id):
        kinds = json.loads(await _text(tools, "object_list", _ctx(workspace_id)))["kinds"]
        by_kind = {row["kind"]: row for row in kinds}
        assert by_kind[sample.WIDGET_KIND]["has_actions"] is True
        assert by_kind[sample.WORKSPACE_KIND]["has_actions"] is True
        assert "has_actions" not in by_kind[sample.RELIC_KIND]

        bare = json.loads(
            await _text(tools, "object_list", _ctx(workspace_id, granted_actions=frozenset()))
        )["kinds"]
        assert all("has_actions" not in row for row in bare)


async def test_get_envelope_binds_instance_actions_after_status(db: None) -> None:
    workspace_id = await _workspace()
    tools, _ = _sample_verbs()
    with ws(workspace_id):
        ctx = _ctx(workspace_id)
        manifest = f"kind: {sample.WIDGET_KIND}\nname: anvil\nspec:\n  color: teal\n  size: 1\n"
        await _text(tools, "object_apply", ctx, manifest=manifest)
        document = await _text(tools, "object_get", ctx, kind=sample.WIDGET_KIND, name="anvil")
        fetched = yaml.safe_load(document)
        assert list(fetched)[:6] == ["kind", "name", "spec", "status", "actions", "links"]
        views = fetched["actions"]
        assert [view["name"] for view in views] == [
            sample.BLESS_ACTION,
            sample.ENGRAVE_ACTION,
            sample.POLISH_ACTION,
        ]
        generation = fetched["generation"]
        for view in views:
            assert view["call"] == {
                "kind": sample.WIDGET_KIND,
                "action": view["name"],
                "name": "anvil",
                "generation": generation,
                "input": {},
            }

        walled = await _text(
            tools,
            "object_get",
            _ctx(workspace_id, granted_actions=frozenset()),
            kind=sample.WIDGET_KIND,
            name="anvil",
        )
        assert yaml.safe_load(walled)["actions"] == []


async def test_explain_envelope_returns_both_action_sets(db: None) -> None:
    workspace_id = await _workspace()
    tools, _ = _sample_verbs()
    with ws(workspace_id):
        explained = json.loads(
            await _text(tools, "object_explain", _ctx(workspace_id), kind=sample.WIDGET_KIND)
        )
        assert [view["name"] for view in explained["collection_actions"]] == [
            sample.BESEECH_ACTION,
            sample.CALIBRATE_ACTION,
            sample.DIVINE_ACTION,
        ]
        assert [view["name"] for view in explained["instance_actions"]] == [
            sample.BLESS_ACTION,
            sample.ENGRAVE_ACTION,
            sample.POLISH_ACTION,
        ]
        relic = json.loads(
            await _text(tools, "object_explain", _ctx(workspace_id), kind=sample.RELIC_KIND)
        )
        assert relic["collection_actions"] == []
        assert relic["instance_actions"] == []


async def test_object_action_def_handler_raises_instead_of_dispatching() -> None:
    tools, _ = _sample_verbs()
    dispatcher = tools["object_action"]
    with pytest.raises(RuntimeError, match="object_action dispatches through the engine"):
        await dispatcher.handler(
            _ctx(uuid4()),
            dispatcher.input_model.model_validate({"kind": "k", "action": "a"}),
        )


def test_agent_actions_mirror_the_allowlist_rules() -> None:
    _, verbs = _sample_verbs()
    everything = _agent_actions(verbs.actions, None, MEMBER_ADMISSION)
    assert everything == SAMPLE_ACTION_IDS - {CALIBRATE_ID}
    named = _agent_actions(verbs.actions, (POLISH_ID, CALIBRATE_ID, "bash"), MEMBER_ADMISSION)
    assert named == frozenset({POLISH_ID, CALIBRATE_ID})
    assert _agent_actions(verbs.actions, ("bash",), MEMBER_ADMISSION) == frozenset()
    speaking_intent = _agent_actions(verbs.actions, ("bash",), INTENT_ADMISSION, uuid4())
    assert speaking_intent == SAMPLE_ACTION_IDS - {CALIBRATE_ID}
    speakerless_intent = _agent_actions(verbs.actions, ("bash",), INTENT_ADMISSION)
    assert speakerless_intent == frozenset()
    absent = _agent_actions(verbs.actions, ("action:skill:skill_search",), MEMBER_ADMISSION)
    assert absent == frozenset()


def test_implied_grants_route_through_the_canonical_id_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools, verbs = _sample_verbs()
    paired = _agent_tools(tuple(tools.values()), ("load_skill",), MEMBER_ADMISSION)
    assert {tool.name for tool in paired} == {"load_skill", "skill_search"}
    assert _agent_actions(verbs.actions, ("load_skill",), MEMBER_ADMISSION) == frozenset()
    monkeypatch.setitem(IMPLIED_GRANTS, "load_skill", ("skill_search", DIVINE_ID))
    implied = _agent_actions(verbs.actions, ("load_skill",), MEMBER_ADMISSION)
    assert implied == frozenset({DIVINE_ID})
    assert _agent_actions(verbs.actions, ("bash",), MEMBER_ADMISSION) == frozenset()


def test_subagent_actions_mirror_the_profile_rules() -> None:
    _, verbs = _sample_verbs()

    class _In(BaseModel):
        task: str = ""

    class _Out(BaseModel):
        finding: str = ""

    profile = SubagentProfile(
        name="probe",
        prompt="p",
        tool_names=(POLISH_ID,),
        input_model=_In,
        output_model=_Out,
    )
    assert _subagent_actions(verbs.actions, profile, frozenset()) == frozenset({POLISH_ID})
    assert _subagent_actions(verbs.actions, profile, frozenset({DIVINE_ID})) == frozenset(
        {POLISH_ID, DIVINE_ID}
    )
    isolated = SubagentProfile(
        name="probe",
        prompt="p",
        tool_names=(POLISH_ID,),
        input_model=_In,
        output_model=_Out,
        isolated_tools=True,
    )
    assert _subagent_actions(verbs.actions, isolated, frozenset({DIVINE_ID})) == frozenset(
        {POLISH_ID}
    )


def test_dispatcher_schema_rides_the_wire_only_with_a_grant() -> None:
    tools, _ = _sample_verbs()
    all_tools = tuple(tools.values())
    selected = tuple(tool for tool in all_tools if tool.name in {"bash", "object_action"})
    assert [tool.name for tool in _with_action_dispatcher(selected, all_tools, frozenset())] == [
        "bash"
    ]
    granted = _with_action_dispatcher(
        tuple(tool for tool in all_tools if tool.name == "bash"),
        all_tools,
        frozenset({POLISH_ID}),
    )
    assert [tool.name for tool in granted] == ["bash", "object_action"]


def test_allowlist_write_points_refuse_the_dispatcher() -> None:
    with pytest.raises(ValueError, match="never the dispatcher"):
        AgentProvision(
            name="probe-agent",
            spec=AgentSpec(
                model="auto",
                reasoning="auto",
                internet_access_allowed=False,
                prompt="p",
                purpose="probes",
            ),
            tools=("object_action",),
        )

    class _In(BaseModel):
        task: str = ""

    class _Out(BaseModel):
        finding: str = ""

    with pytest.raises(ValueError, match="never the dispatcher"):
        SubagentProfile(
            name="probe",
            prompt="p",
            tool_names=("object_action",),
            input_model=_In,
            output_model=_Out,
        )
    with pytest.raises(ValueError, match="never the dispatcher"):
        SubagentToolGrant(profile="probe", tool_names=("object_action",))
