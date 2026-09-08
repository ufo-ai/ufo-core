"""A credential slot a workspace declares for itself, on the wires a manifest's slot already
drives: the egress proxy's rules, the sandbox's environment, and the `credential` object kind.

The declaration is a row of this extension's own, written by an admin — so the host it names is
admitted pinned, and the proxy resolves it and refuses a private answer, while a host this deploy's
own manifests name stays an unpinned exact scope."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_workspace_credentials import (
    DEFAULT_HEADER,
    NAME,
    SLOT_KIND,
    SLOT_TABLE,
    SlotInvalid,
    WorkspaceSlot,
    manifest,
    put_slot,
    read_slots,
    validate,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.exec_env import (
    CONVERSATION_ID_ENV,
    ProbeEnv,
    sandbox_exported_env,
)
from ufo.harness.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.host.ext.loader import deploy_claims, turn_tools, workspace_slot_source
from ufo.host.kinds.credential_kind import CREDENTIAL_KIND
from ufo.host.tools.builtins import RequestCredentialsInput, request_credentials_handler
from ufo.runtime.access.credentials import (
    CredentialRequests,
    CredentialStore,
    declared_slot_fingerprint,
    open_credential_request,
)
from ufo.runtime.access.egress_control import rule_json
from ufo.runtime.access.egress_rules import (
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
)
from ufo.runtime.access.workspace_slots import SlotProvider, WorkspaceSlots
from ufo.runtime.authority import WORKSPACE_AUTHORITY
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.manifest import (
    CredentialSlot,
    InjectionTarget,
    Manifest,
    WorkspaceCredentials,
    declared_slot,
)
from ufo.runtime.objects import AdminRequired, VerbNotSupported
from ufo.runtime.tools.bridge import TOOL_BRIDGE_URL_ENV
from ufo.runtime.tools.context import SpawnResult, SpeakerRequired, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ACME_HOST = "api.acmekeys.com"
DEPLOY_HOST = "api.perplexity.ai"
SECRET = "acme-real-secret"
ADMIN_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
JOINER_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)
SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"

ACME = WorkspaceSlot(
    slot="acme_api_key",
    env="ACME_API_KEY",
    host=ACME_HOST,
    header="X-Acme-Key",
    description="Acme key an admin declared in chat.",
)
DEPLOY_SLOT = CredentialSlot(
    name="perplexity_api_key",
    description="api key",
    injection=InjectionTarget(
        host=DEPLOY_HOST,
        header="authorization",
        sentinel="SENTINEL_PPLX",
        env="PPLX_API_KEY",
        dimension="requests",
    ),
)
PROBE = Manifest(
    name="probe",
    version="1",
    credentials=(
        CredentialSlot(
            name="probe_api_key",
            description="Probe API key.",
            injection=InjectionTarget(
                host="api.probe.test",
                header="X-Probe-Key",
                sentinel="S_PROBE",
                env="PROBE_KEY",
            ),
        ),
    ),
)


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


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _ext() -> ExtensionContext:
    """The extension's own workspace-scoped handle, the one its provider reads under."""
    return context_for(NAME, frozenset())


def _slots(*deploy: CredentialSlot) -> WorkspaceSlots:
    """What one workspace holds: the deploy's own slots beside this extension's live provider."""
    return replace(workspace_slot_source((manifest(),)), deploy=deploy)


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


def _tool_context(
    workspace_id: UUID,
    speaker_member_id: UUID | None = None,
    credentials: CredentialStore | None = None,
) -> ToolContext:
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
        workspace_slots=_slots(),
        requestable_credentials=(
            None
            if credentials is None
            else CredentialRequests(fernet=credentials.fernet, declared=frozenset())
        ),
    )


def _tools(store: CredentialStore) -> tuple[ToolDef, ...]:
    tools, _, _ = turn_tools((manifest(), PROBE), store, audience=conversation_audience(None))
    return tools


def _tool(store: CredentialStore, name: str) -> ToolDef:
    return next(tool for tool in _tools(store) if tool.name == name)


async def _text(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


def _declaration(name: str, **spec: object) -> dict[str, object]:
    return {"manifest": yaml.safe_dump({"kind": SLOT_KIND, "name": name, "spec": spec})}


def test_a_declaration_must_name_a_public_host_and_an_unclaimed_env() -> None:
    """The host is the row's one dangerous field: an exact scope is the proxy's allowlist, so a
    name that resolves inside the deploy's own network would point the tunnel at it. The env var is
    the sandbox's shared namespace, where the later export wins the merge — so a variable or a slot
    name the deploy already declares is refused rather than shadowed."""
    validate(ACME, declared_slots=frozenset({"other"}), declared_env=frozenset({"OTHER"}))
    for host in ("10.0.0.5", "169.254.169.254", "vault.internal", "box.local", "localhost", ""):
        with pytest.raises(SlotInvalid, match="public DNS name"):
            validate(
                WorkspaceSlot(**{**vars(ACME), "host": host}),
                declared_slots=frozenset(),
                declared_env=frozenset(),
            )
    with pytest.raises(SlotInvalid, match="already declares slot"):
        validate(ACME, declared_slots=frozenset({ACME.slot}), declared_env=frozenset())
    with pytest.raises(SlotInvalid, match="already exports"):
        validate(ACME, declared_slots=frozenset(), declared_env=frozenset({ACME.env}))
    for env in ("acme_api_key", "1ACME", "AC"):
        with pytest.raises(SlotInvalid, match="env var"):
            validate(
                WorkspaceSlot(**{**vars(ACME), "env": env}),
                declared_slots=frozenset(),
                declared_env=frozenset(),
            )
    for name in ("Acme", "acme-api-key", "ac"):
        with pytest.raises(SlotInvalid, match="credential slot name"):
            validate(
                WorkspaceSlot(**{**vars(ACME), "slot": name}),
                declared_slots=frozenset(),
                declared_env=frozenset(),
            )
    with pytest.raises(SlotInvalid, match="header"):
        validate(
            WorkspaceSlot(**{**vars(ACME), "header": "X Acme Key"}),
            declared_slots=frozenset(),
            declared_env=frozenset(),
        )
    for header in ("Connection", "proxy-connection"):
        with pytest.raises(SlotInvalid, match="not carried"):
            validate(
                WorkspaceSlot(**{**vars(ACME), "header": header}),
                declared_slots=frozenset(),
                declared_env=frozenset(),
            )


def test_a_declaration_projects_the_slot_shape_a_manifest_carries() -> None:
    """One projection feeds the proxy and the sandbox both, so the extension's row reaches exactly
    the code a manifest's declaration already drives."""
    assert ACME.credential_slot() == CredentialSlot(
        name=ACME.slot,
        description=ACME.description,
        injection=InjectionTarget(
            host=ACME_HOST,
            header="X-Acme-Key",
            sentinel="UFO_SENTINEL_WORKSPACE_ACME_API_KEY",
            env="ACME_API_KEY",
            dimension="requests",
        ),
    )
    declared = declared_slot(ACME.credential_slot(), NAME)
    assert (declared.extension, declared.host, declared.env, declared.header) == (
        NAME,
        ACME_HOST,
        "ACME_API_KEY",
        "X-Acme-Key",
    )


async def test_a_filled_workspace_slot_derives_a_pinned_scope_and_its_injection(db: None) -> None:
    """The rules a workspace's own declaration opens. The host is pinned because an admin wrote it
    and an exact scope is otherwise the one path around the proxy's private-address check, while
    the deploy's own host — named by code — rides the same derivation unpinned. Nothing is derived
    for a declaration with no secret stored, so a slot declared and not yet filled opens no
    egress."""
    workspace_id = await _workspace()
    store = _store()
    with ws(workspace_id):
        await put_slot(_ext(), workspace_id, ACME)
        assert await derive_credential_rules(_slots(DEPLOY_SLOT), workspace_id, store) == ()

        await store.put(workspace_id, ACME.slot, SECRET)
        await store.put(workspace_id, DEPLOY_SLOT.name, "pplx-real-secret")
        rules = await derive_credential_rules(_slots(DEPLOY_SLOT), workspace_id, store)

    assert rules == (
        ScopeRule(allowed_hosts=frozenset({ACME_HOST}), pinned=True),
        InjectionRule(
            host=ACME_HOST,
            header="X-Acme-Key",
            sentinel="UFO_SENTINEL_WORKSPACE_ACME_API_KEY",
            real=SECRET,
        ),
        MeterRule(host=ACME_HOST, dimension="requests"),
        ScopeRule(allowed_hosts=frozenset({DEPLOY_HOST}), pinned=False),
        InjectionRule(
            host=DEPLOY_HOST,
            header="authorization",
            sentinel="SENTINEL_PPLX",
            real="pplx-real-secret",
        ),
        MeterRule(host=DEPLOY_HOST, dimension="requests"),
    )
    assert rule_json(rules[0]) == {"kind": "scope", "hosts": [ACME_HOST], "pinned": True}
    assert rule_json(rules[3]) == {"kind": "scope", "hosts": [DEPLOY_HOST], "pinned": False}


async def test_rule_derivation_reads_a_workspace_provider_once(db: None) -> None:
    """A mutable provider's declaration and its workspace provenance are one read. An edit between
    separate reads must not turn the new host into an unpinned exact scope."""
    workspace_id = await _workspace()
    store = _store()
    reads = 0

    async def moving_provider(
        ctx: ExtensionContext, requested_workspace_id: UUID
    ) -> tuple[CredentialSlot, ...]:
        nonlocal reads
        assert requested_workspace_id == workspace_id
        reads += 1
        host = ACME_HOST if reads == 1 else "changed.acmekeys.com"
        return (WorkspaceSlot(**{**vars(ACME), "host": host}).credential_slot(),)

    slots = WorkspaceSlots(
        providers=(
            SlotProvider(
                extension="moving",
                ctx=_ext(),
                provider=WorkspaceCredentials(read=moving_provider),
            ),
        )
    )
    with ws(workspace_id):
        await store.put(workspace_id, ACME.slot, SECRET)
        rules = await derive_credential_rules(slots, workspace_id, store)

    assert reads == 1
    assert rules[0] == ScopeRule(allowed_hosts=frozenset({ACME_HOST}), pinned=True)


async def test_one_workspaces_declaration_never_reaches_another(db: None) -> None:
    """The declaration is per workspace exactly as the secret is, and both are read under the run
    token's own workspace — so the shared proxy resolving a second workspace opens neither the host
    nor the injection."""
    first, second = await _workspace(), await _workspace()
    store = _store()
    with ws(first):
        await put_slot(_ext(), first, ACME)
        await store.put(first, ACME.slot, SECRET)
        assert await derive_credential_rules(_slots(), first, store) != ()
    with ws(second):
        assert await read_slots(_ext(), second) == ()
        assert await derive_credential_rules(_slots(), second, store) == ()


async def test_a_new_deploy_declaration_cannot_claim_a_workspace_slot(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await put_slot(_ext(), workspace_id, ACME)
        collision = Manifest(
            name="new_provider",
            version="1",
            credentials=(CredentialSlot(name=ACME.slot, description="Deploy-owned ACME key."),),
        )
        slots = workspace_slot_source((manifest(), collision))
        with pytest.raises(RuntimeError, match="already claims"):
            await slots.workspace(workspace_id)


async def test_the_sandbox_exports_the_sentinel_and_never_the_secret(db: None) -> None:
    """What the agent's own client reads: the declared variable holding the slot's sentinel, which
    the proxy swaps for the secret on the wire to the declared host alone. A declaration with
    nothing stored exports nothing, so the agent finds no half-usable variable."""
    workspace_id = await _workspace()
    store = _store()
    with ws(workspace_id):
        await put_slot(_ext(), workspace_id, ACME)
        probe = ProbeEnv(credentials=store, slots=_slots())
        unfilled = await probe.exports(uuid4(), uuid4(), WORKSPACE_AUTHORITY)
        await store.put(workspace_id, ACME.slot, SECRET)
        exports = await probe.exports(uuid4(), uuid4(), WORKSPACE_AUTHORITY)

    assert ACME.env not in unfilled
    assert exports[ACME.env] == "UFO_SENTINEL_WORKSPACE_ACME_API_KEY"
    assert SECRET not in exports.values()
    assert CONVERSATION_ID_ENV in exports


async def test_a_second_slot_may_not_claim_a_variable_this_workspace_already_exports(
    db: None,
) -> None:
    """One variable carries one value, so the collision is refused at the write rather than settled
    by whichever export happens to win the sandbox's env merge. Re-declaring the same slot with the
    same variable is a rotation of the declaration and replaces the row."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        await put_slot(_ext(), workspace_id, ACME)
        with pytest.raises(SlotInvalid, match="already exports"):
            await put_slot(
                _ext(), workspace_id, WorkspaceSlot(**{**vars(ACME), "slot": "acme_other_key"})
            )
        await put_slot(
            _ext(), workspace_id, WorkspaceSlot(**{**vars(ACME), "host": "api.eu.acmekeys.com"})
        )
        assert [slot.host for slot in await read_slots(_ext(), workspace_id)] == [
            "api.eu.acmekeys.com"
        ]


async def test_the_database_enforces_one_slot_per_workspace_variable(db: None) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await put_slot(_ext(), workspace_id, ACME)
        with pytest.raises(sa.exc.IntegrityError):
            async with _ext().transaction() as connection:
                await connection.execute(
                    sa.insert(SLOT_TABLE).values(
                        workspace_id=workspace_id,
                        slot="acme_other_key",
                        env=ACME.env,
                        host=ACME.host,
                        header=ACME.header,
                        description=ACME.description,
                    )
                )


async def test_an_admin_declares_a_slot_and_the_credential_kind_lists_it(db: None) -> None:
    """The whole act through the verbs: an admin declares the slot, the `credential` kind lists it
    beside the deploy's own with this extension's name on it, and delete takes the declaration and
    the secret filled against it — nothing declares the slot once the row is gone."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        applied = await _text(
            _tool(store, "object_apply"),
            ctx,
            **_declaration(
                "acme-api-key",
                slot=ACME.slot,
                env=ACME.env,
                host=ACME_HOST,
                header="X-Acme-Key",
                description="Acme key for the billing runbook.",
            ),
        )
        assert json.loads(applied)["name"] == "acme-api-key"

        declared = json.loads(await _text(_tool(store, "object_list"), ctx, kind=SLOT_KIND))
        assert [row["name"] for row in declared["objects"]] == ["acme-api-key"]
        assert declared["objects"][0]["host"] == ACME_HOST

        listed = json.loads(await _text(_tool(store, "object_list"), ctx, kind=CREDENTIAL_KIND))
        assert [row["name"] for row in listed["objects"]] == ["acme-api-key", "probe-api-key"]
        assert listed["objects"][0]["extension"] == NAME
        assert listed["objects"][0]["filled"] is False

        fetched = yaml.safe_load(
            await _text(_tool(store, "object_get"), ctx, ref=f"{CREDENTIAL_KIND}/acme-api-key")
        )
        assert fetched["spec"] == {
            "slot": ACME.slot,
            "description": "Acme key for the billing runbook.",
            "extension": NAME,
            "host": ACME_HOST,
            "env": ACME.env,
            "header": "X-Acme-Key",
            "host_slot": "",
            "host_options": [],
        }
        assert fetched["status"]["filled"] is False

        await store.put(workspace_id, ACME.slot, SECRET)
        filled = json.loads(await _text(_tool(store, "object_list"), ctx, kind=CREDENTIAL_KIND))
        assert filled["objects"][0]["filled"] is True

        await _text(_tool(store, "object_delete"), ctx, kind=SLOT_KIND, name="acme-api-key")
        remaining = json.loads(await _text(_tool(store, "object_list"), ctx, kind=CREDENTIAL_KIND))
        assert [row["name"] for row in remaining["objects"]] == ["probe-api-key"]
        assert await read_slots(_ext(), workspace_id) == ()
        async with workspace_tx() as connection:
            stored = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.credential))
            ).scalar_one()
    assert stored == 0


async def test_a_declaration_defaults_its_header_and_is_addressed_by_its_slot(db: None) -> None:
    """`header` is optional because most providers read `Authorization`, and the object's name is
    the slot as a slug — so a name that addresses some other slot is refused rather than writing a
    declaration no read would find."""
    workspace_id = await _workspace()
    store = _store()
    apply_tool = _tool(store, "object_apply")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        await _text(
            apply_tool,
            ctx,
            **_declaration("acme-api-key", slot=ACME.slot, env=ACME.env, host=ACME_HOST),
        )
        assert [slot.header for slot in await read_slots(_ext(), workspace_id)] == [DEFAULT_HEADER]
        with pytest.raises(ValueError, match="is addressed as"):
            await apply_tool.handler(
                ctx,
                apply_tool.input_model.model_validate(
                    _declaration("acme-other", slot=ACME.slot, env=ACME.env, host=ACME_HOST)
                ),
            )


async def test_declaring_a_slot_is_admin_gated(db: None) -> None:
    """The declaration writes the proxy's allowlist and the sandbox's environment, so it is the
    workspace admin's act — and the gate answers who is speaking before what they may do."""
    workspace_id = await _workspace()
    store = _store()
    apply_tool = _tool(store, "object_apply")
    args = apply_tool.input_model.model_validate(
        _declaration("acme-api-key", slot=ACME.slot, env=ACME.env, host=ACME_HOST)
    )
    with ws(workspace_id):
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        with pytest.raises(SpeakerRequired, match="requested_by"):
            await apply_tool.handler(_tool_context(workspace_id), args)
        with pytest.raises(AdminRequired, match="declare a credential slot"):
            await apply_tool.handler(_tool_context(workspace_id, joiner), args)
        assert await read_slots(_ext(), workspace_id) == ()


async def test_a_declaration_refuses_what_the_deploy_already_claims(db: None) -> None:
    """The three fields an admin could point at this deploy rather than at a provider: a host that
    resolves inside its network, a sandbox variable an installed extension already exports — where
    the later export would silently win the merge — and a slot name an extension declares."""
    workspace_id = await _workspace()
    store = _store()
    apply_tool = _tool(store, "object_apply")

    def _args(**spec: object) -> object:
        return apply_tool.input_model.model_validate(_declaration("acme-api-key", **spec))

    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        with pytest.raises(SlotInvalid, match="public DNS name"):
            await apply_tool.handler(
                ctx, _args(slot=ACME.slot, env=ACME.env, host="vault.internal")
            )
        with pytest.raises(SlotInvalid, match="public DNS name"):
            await apply_tool.handler(
                ctx, _args(slot=ACME.slot, env=ACME.env, host="169.254.169.254")
            )
        with pytest.raises(SlotInvalid, match="already exports"):
            await apply_tool.handler(ctx, _args(slot=ACME.slot, env="PROBE_KEY", host=ACME_HOST))
        with pytest.raises(SlotInvalid, match="already declares slot"):
            await apply_tool.handler(
                ctx,
                apply_tool.input_model.model_validate(
                    _declaration(
                        "probe-api-key", slot="probe_api_key", env=ACME.env, host=ACME_HOST
                    )
                ),
            )
        assert await read_slots(_ext(), workspace_id) == ()


def test_the_deploys_claim_reserves_the_variables_core_exports_to_a_sandbox() -> None:
    """The sandbox's namespace is wider than what manifests declare: core exports the model-key
    sentinels, the proxy URLs and the CA paths on every open. The deploy's claim carries them, so
    a workspace declaration is refused on a name core already exports."""
    claimed = deploy_claims((manifest(), PROBE)).env
    assert sandbox_exported_env({}) <= claimed
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
        "GIT_CONFIG_COUNT",
        TOOL_BRIDGE_URL_ENV,
    ):
        assert env in claimed
        with pytest.raises(SlotInvalid, match="already exports"):
            validate(
                WorkspaceSlot(**{**vars(ACME), "env": env}),
                declared_slots=frozenset(),
                declared_env=claimed,
            )


async def test_a_declaration_refuses_a_variable_core_exports_to_every_sandbox(db: None) -> None:
    """A slot claiming `ANTHROPIC_API_KEY` would export the workspace's own sentinel over the
    platform one, and the proxy holds no rule pairing that sentinel with the deploy's model host —
    every model call from the sandbox would carry a sentinel nothing swaps and 401."""
    workspace_id = await _workspace()
    store = _store()
    apply_tool = _tool(store, "object_apply")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SSL_CERT_FILE"):
            args = apply_tool.input_model.model_validate(
                _declaration("acme-api-key", slot=ACME.slot, env=env, host=ACME_HOST)
            )
            with pytest.raises(SlotInvalid, match="already exports"):
                await apply_tool.handler(ctx, args)
        assert await read_slots(_ext(), workspace_id) == ()


async def test_a_manifests_slot_is_still_filled_through_its_private_handoff(db: None) -> None:
    """The `credential` kind carries no declaration of its own: apply on it is refused whichever
    half declared the slot, and the value arrives through `request_credentials`."""
    workspace_id = await _workspace()
    store = _store()
    apply_tool = _tool(store, "object_apply")
    args = apply_tool.input_model.model_validate(
        {
            "manifest": yaml.safe_dump(
                {
                    "kind": CREDENTIAL_KIND,
                    "name": "probe-api-key",
                    "spec": {"slot": "probe_api_key", "env": ACME.env, "host": ACME_HOST},
                }
            )
        }
    )
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        with pytest.raises(VerbNotSupported, match="request_credentials"):
            await apply_tool.handler(_tool_context(workspace_id, owner), args)


async def test_request_credentials_seals_a_slot_this_workspace_declared(db: None) -> None:
    """A slot no manifest names is fillable exactly as a manifest's is: the seal reads this
    workspace's own declarations live, so a slot declared earlier in the turn is fillable in it,
    and a slot nobody declares is refused."""
    workspace_id = await _workspace()
    store = _store()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner, credentials=store)
        await put_slot(_ext(), workspace_id, ACME)
        result = await request_credentials_handler(
            ctx,
            RequestCredentialsInput.model_validate(
                {
                    "reason": "the acme runbook needs its key",
                    "prompts": [{"slot": ACME.slot, "prompt": "Acme API key"}],
                }
            ),
        )
        assert result.is_error is False
        assert ACME.slot in result.content[0].text
        request = json.loads(result.content[0].text.splitlines()[-1])
        state = open_credential_request(store.fernet, request["sealed"])
        assert state.workspace_declarations == {
            ACME.slot: declared_slot_fingerprint(declared_slot(ACME.credential_slot(), NAME))
        }
        with pytest.raises(ValueError, match="declares credential slot"):
            await request_credentials_handler(
                ctx,
                RequestCredentialsInput.model_validate(
                    {
                        "reason": "unknown provider",
                        "prompts": [{"slot": "nobody_api_key", "prompt": "key"}],
                    }
                ),
            )
