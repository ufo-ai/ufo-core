import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo import product as product_module
from ufo.db import workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT, OPENAI_KEY_SLOT
from ufo.harness.models.grant import Grant, GrantRefusedRefresh, read_grant
from ufo.harness.models.interface import ModelAccountUnavailable
from ufo.harness.models.pricing import Pricing
from ufo.harness.models.registry import ModelRegistry, ModelRoute, ModelRoutes, ServingModel
from ufo.host.ext.loader import injecting_slots
from ufo.host.kinds.credential_kind import CREDENTIAL_KIND
from ufo.product import PRODUCT_ATTACH_METRIC, ProductCensus
from ufo.runtime import workspace as workspace_module
from ufo.runtime.access import grants as grants_module
from ufo.runtime.access.credentials import (
    CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS,
    CREDENTIAL_REQUEST_TTL_SECONDS,
    CredentialRequestInvalid,
    CredentialRequests,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    CredentialValueInvalid,
    HostChoice,
    credential_host,
    member_slot,
    open_credential_request,
    seal_credential_request,
)
from ufo.runtime.access.egress_rules import Bind, derive_credential_binds
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.ext.context import CredentialAccess, UndeclaredCredentialSlot
from ufo.runtime.ext.manifest import (
    CredentialSlot,
    InjectionTarget,
    Manifest,
    declared_slots,
    minted_slots,
)
from ufo.runtime.seats import create_member
from ufo.runtime.workspace import (
    KEY_FUNDED,
    PLAN_FUNDED,
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    ModelFundingChanged,
    init_workspace_credentials,
    model_credentials,
    ws,
    ws_current,
)
from ufo.schema import tables

DATADOG_HOST = "api.datadoghq.com"
US5_HOST = "api.us5.datadoghq.com"
PERPLEXITY_HOST = "api.perplexity.ai"
LOCK_OBSERVE_TIMEOUT_SECONDS = 5


DATADOG_SITES = HostChoice(
    slot="datadog_api_host",
    description="Datadog site for this org.",
    hosts=(DATADOG_HOST, US5_HOST, "api.datadoghq.eu"),
    default=DATADOG_HOST,
    env="DD_HOST",
)

"""The two models one coding turn asks OpenAI for: the one the member's account serves, and the
deploy's own background model that summarizes the same turn's tool calls."""
BACKGROUND_MODEL = "gpt-5.6-luna"
OWN_ACCOUNT_MODEL = "gpt-5.6-sol"


def _keyed_manifest() -> Manifest:
    return Manifest(
        name="keyed",
        version="1",
        credentials=(
            CredentialSlot(
                name="datadog_api_key",
                description="api key",
                injection=InjectionTarget(
                    host=DATADOG_SITES,
                    header="DD-API-KEY",
                    env="DD_API_KEY",
                ),
            ),
            CredentialSlot(
                name="datadog_application_key",
                description="application key",
                injection=InjectionTarget(
                    host=DATADOG_SITES,
                    header="DD-APPLICATION-KEY",
                    env="DD_APP_KEY",
                ),
            ),
            CredentialSlot(name="datadog_api_host", description="site host"),
        ),
    )


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_round_trips_and_is_encrypted_at_rest(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "sk-secret-value")
    assert await store.get(workspace_id, "sample_api") == "sk-secret-value"
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.credential.c.ciphertext).where(
                    tables.credential.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert b"sk-secret-value" not in stored.ciphertext


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_rotate_updates_only_the_expected_existing_value(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "oauth", "old")
    assert await store.rotate(workspace_id, "oauth", "old", "new")
    assert await store.get(workspace_id, "oauth") == "new"
    assert not await store.rotate(workspace_id, "oauth", "old", "stale")
    assert await store.get(workspace_id, "oauth") == "new"
    assert not await store.rotate(workspace_id, "missing", "old", "new")
    with pytest.raises(ValueError, match="empty"):
        await store.rotate(workspace_id, "oauth", "new", "")


async def test_credential_writes_bump_the_egress_rules_generation(db: None) -> None:
    """The proxy's rule cache pins this counter, so a filled, rotated, or cleared key re-derives
    its injection rules at the next CONNECT instead of waiting out the cache TTL."""
    workspace_id = await _workspace()
    store = _store()

    async def generation() -> int:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.workspace.c.egress_rules_generation).where(
                        tables.workspace.c.id == workspace_id
                    )
                )
            ).scalar_one()

    start = await generation()
    await store.put(workspace_id, "sample_api", "one")
    filled = await generation()
    await store.put(workspace_id, "sample_api", "two")
    refilled = await generation()
    assert await store.rotate(workspace_id, "sample_api", "two", "three")
    rotated = await generation()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.credential).where(
                tables.credential.c.workspace_id == workspace_id,
                tables.credential.c.slot == "sample_api",
            )
        )
    cleared = await generation()
    assert start == 0
    assert start < filled < refilled < rotated < cleared


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_put_rejects_an_empty_value(db: None) -> None:
    with pytest.raises(ValueError, match="empty"):
        await _store().put(await _workspace(), "sample_api", "")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_unset_slot_raises(db: None) -> None:
    store = _store()
    with pytest.raises(CredentialSlotUnset, match="missing"):
        await store.get(await _workspace(), "missing")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_stored_slot_is_told_apart_from_the_platform_default(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.setenv("SAMPLE_API", "platform-default")
    with ws(workspace_id):
        assert await ws_current().credential("sample_api") == "platform-default"
        assert not await ws_current().credential_is_stored("sample_api")
        await store.put(workspace_id, "sample_api", "workspace-owned")
        assert await ws_current().credential("sample_api") == "workspace-owned"
        assert await ws_current().credential_is_stored("sample_api")


async def test_an_extension_puts_only_the_slots_it_mints(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    access = CredentialAccess(
        declared=frozenset({"sample_minted", "sample_api"}), minted=frozenset({"sample_minted"})
    )
    with ws(workspace_id):
        await access.put("sample_minted", "first-mint")
        assert await access.get("sample_minted") == "first-mint"
        await access.put("sample_minted", "reissued")
        assert await access.get("sample_minted") == "reissued"
        with pytest.raises(UndeclaredCredentialSlot, match="sample_api"):
            await access.put("sample_api", "handed-over")
        with pytest.raises(ValueError, match="empty"):
            await access.put("sample_minted", "")
    assert await store.get(workspace_id, "sample_minted") == "reissued"
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, "sample_api")


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_a_mint_meeting_another_first_mint_in_flight_replaces_it(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    access = CredentialAccess(
        declared=frozenset({"sample_minted"}), minted=frozenset({"sample_minted"})
    )
    with ws(workspace_id):
        async with workspace_tx() as other_pod:
            holder = (await other_pod.execute(sa.text("select pg_backend_pid()"))).scalar_one()
            await other_pod.execute(
                sa.insert(tables.credential).values(
                    workspace_id=workspace_id,
                    slot="sample_minted",
                    ciphertext=store.fernet.encrypt(b"first-mint"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            mint = asyncio.create_task(access.put("sample_minted", "second-mint"))
            async with asyncio.timeout(LOCK_OBSERVE_TIMEOUT_SECONDS):
                while True:
                    async with workspace_tx() as observer:
                        blocked = (
                            await observer.execute(
                                sa.text(
                                    "select exists ("
                                    "select 1 from pg_stat_activity "
                                    "where cast(:holder as integer) = any(pg_blocking_pids(pid))"
                                    ")"
                                ),
                                {"holder": holder},
                            )
                        ).scalar_one()
                    if blocked:
                        break
        await mint
    assert await store.get(workspace_id, "sample_minted") == "second-mint"


def test_a_slot_its_extension_mints_is_no_members_to_fill() -> None:
    manifest = Manifest(
        name="minter",
        version="1",
        credentials=(
            CredentialSlot(name="minter_api_key", description="The member's API key."),
            CredentialSlot(name="minter_token", description="A token minter mints.", minted=True),
        ),
    )
    assert [slot.name for slot in declared_slots((manifest,))] == ["minter_api_key"]
    assert minted_slots(manifest) == frozenset({"minter_token"})


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_key_serves_only_a_turn_bound_to_that_member(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admitted model capability resolves only its exact credential slot."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.setenv("OPENAI_API_KEY", "platform-default")
    async with workspace_tx() as connection:
        admin = await create_member(connection, workspace_id, "admin@work.com", is_admin=True)
        teammate = await create_member(connection, workspace_id, "teammate@work.com")
        keyless = await create_member(connection, workspace_id, "keyless@work.com")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, admin), "admin-key")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, teammate), "teammate-key")
    with ws(workspace_id):
        assert await ws_current().credential(OPENAI_KEY_SLOT) == "platform-default"
        assert not await ws_current().credential_is_stored(OPENAI_KEY_SLOT)
        with model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, teammate)}):
            assert (
                await ws_current().credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
                == "teammate-key"
            )
            assert await ws_current().credential_is_stored(OPENAI_KEY_SLOT, OWN_ACCOUNT_MODEL)
        with model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, keyless)}):
            with pytest.raises(CredentialSlotUnset):
                await ws_current().credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        await store.put(workspace_id, OPENAI_KEY_SLOT, "workspace-key")
        with model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, keyless)}):
            with pytest.raises(CredentialSlotUnset):
                await ws_current().credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        assert await ws_current().credential(OPENAI_KEY_SLOT) == "workspace-key"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_model_resolution_tells_a_plan_apart_from_a_metered_key(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two things a member can connect cost different things."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "platform-default")
    async with workspace_tx() as connection:
        planned = await create_member(connection, workspace_id, "planned@work.com")
        keyed = await create_member(connection, workspace_id, "keyed@work.com")
    grant = Grant(access="oat-token", refresh="refresh", expires_at=time.time() + 3600)
    await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, planned), grant.stored())
    await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, keyed), "sk-ant-api-pasted")
    with ws(workspace_id):
        platform = await ws_current().model_payer(ANTHROPIC_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        assert (platform.funding, platform.payer) == (PLATFORM_FUNDED, PLATFORM_PAYER)
        with model_credentials({OWN_ACCOUNT_MODEL: member_slot(ANTHROPIC_KEY_SLOT, planned)}):
            plan = await ws_current().model_payer(ANTHROPIC_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
            assert (plan.funding, plan.payer) == (
                PLAN_FUNDED,
                member_slot(ANTHROPIC_KEY_SLOT, planned),
            )
        with model_credentials({OWN_ACCOUNT_MODEL: member_slot(ANTHROPIC_KEY_SLOT, keyed)}):
            member_key = await ws_current().model_payer(ANTHROPIC_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
            assert (member_key.funding, member_key.payer) == (
                KEY_FUNDED,
                member_slot(ANTHROPIC_KEY_SLOT, keyed),
            )
        await store.put(workspace_id, ANTHROPIC_KEY_SLOT, "workspace-key")
        workspace_key = await ws_current().model_payer(ANTHROPIC_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        assert (workspace_key.funding, workspace_key.payer) == (
            KEY_FUNDED,
            ANTHROPIC_KEY_SLOT,
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_fallback_payer_freezes_before_its_deploy_key_exists(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.delenv("UFO_FALLBACK_MODEL_KEY", raising=False)
    monkeypatch.delenv("FALLBACK_MODEL_KEY", raising=False)

    with ws(workspace_id):
        payer = await ws_current().model_payer(
            "fallback_model_key", "FALLBACK_MODEL_KEY", "fallback-model"
        )
        with pytest.raises(CredentialSlotUnset):
            await ws_current().model_credential(
                "fallback_model_key", "FALLBACK_MODEL_KEY", "fallback-model"
            )

    assert (payer.funding, payer.payer) == (PLATFORM_FUNDED, PLATFORM_PAYER)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_members_key_never_serves_a_slot_that_is_not_member_routed(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the slots a member signs in with resolve per person."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.setenv("SAMPLE_API", "platform-default")
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "member@work.com", is_admin=True)
    await store.put(workspace_id, member_slot("sample_api", member), "member-key")
    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        assert await ws_current().credential("sample_api", None, OWN_ACCOUNT_MODEL) == (
            "platform-default"
        )


def test_credential_request_seal_round_trips_and_expires() -> None:
    """The sealed grant a `request_credentials` call hands a surface: opens to exactly what was
    sealed, and refuses garbage, a foreign key, or a seal older than the TTL."""
    fernet = Fernet(Fernet.generate_key())
    state = CredentialRequestState(workspace_id=uuid4(), member_id=uuid4(), slots=("a", "b"))
    sealed = seal_credential_request(fernet, state)
    assert open_credential_request(fernet, sealed) == state
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, "garbage")
    stale = fernet.encrypt_at_time(
        state.model_dump_json().encode(),
        int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
    ).decode()
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, stale)
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(Fernet(Fernet.generate_key()), sealed)


def test_open_credential_request_honours_the_ttl_it_is_given() -> None:
    """A seal past the prompt's own window still opens within the renewal window a surface re-offers
    an unanswered prompt in, and not past that."""
    fernet = Fernet(Fernet.generate_key())
    state = CredentialRequestState(workspace_id=uuid4(), member_id=uuid4(), slots=("a",))
    past_prompt = fernet.encrypt_at_time(
        state.model_dump_json().encode(), int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1
    ).decode()
    past_renewal = fernet.encrypt_at_time(
        state.model_dump_json().encode(),
        int(time.time()) - CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS - 1,
    ).decode()
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, past_prompt)
    assert (
        open_credential_request(fernet, past_prompt, ttl=CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS)
        == state
    )
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, past_renewal, ttl=CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS)


def test_credential_requests_seal_only_declared_slots() -> None:
    requests = CredentialRequests(fernet=Fernet(Fernet.generate_key()), declared=frozenset({"a"}))
    assert requests.seal(uuid4(), uuid4(), ("a",))
    with pytest.raises(ValueError, match="declares credential slot"):
        requests.seal(uuid4(), uuid4(), ("a", "nope"))


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_requests_open_an_owner_bound_authorization(db: None) -> None:
    workspace_id = await _workspace()
    member_id = uuid4()
    requests = CredentialRequests(fernet=Fernet(Fernet.generate_key()), declared=frozenset({"a"}))
    with pytest.raises(ValueError, match="empty"):
        requests.authorize(workspace_id, member_id, "a", "")
    sealed = requests.authorize(workspace_id, member_id, "a", '{"device":"secret"}')
    assert (
        requests.open_authorization(sealed, workspace_id, member_id, "a") == '{"device":"secret"}'
    )
    with pytest.raises(CredentialRequestInvalid, match="workspace"):
        requests.open_authorization(sealed, uuid4(), member_id, "a")
    with pytest.raises(CredentialRequestInvalid, match="member"):
        requests.open_authorization(sealed, workspace_id, uuid4(), "a")
    with pytest.raises(CredentialRequestInvalid, match="slot"):
        requests.open_authorization(sealed, workspace_id, member_id, "other")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_unfilled_slot_opens_no_egress_and_a_code_only_slot_never_rides(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    assert (
        await derive_credential_binds(
            WorkspaceSlots(deploy=injecting_slots((_keyed_manifest(),))), workspace_id, store
        )
        == ()
    )
    code_only = Manifest(
        name="s", version="1", credentials=(CredentialSlot(name="code_only", description="x"),)
    )
    await store.put(workspace_id, "code_only", "in-process-only")
    assert injecting_slots((code_only,)) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_one_workspace_never_binds_anothers_slot(db: None) -> None:
    first, second = await _workspace(), await _workspace()
    store = _store()
    await store.put(first, "datadog_api_key", "first-secret")
    slots = WorkspaceSlots(deploy=injecting_slots((_keyed_manifest(),)))
    assert await derive_credential_binds(slots, first, store) == (
        Bind(host=DATADOG_HOST, header="DD-API-KEY", secret="datadog_api_key", env="DD_API_KEY"),
    )
    assert await derive_credential_binds(slots, second, store) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_selection_resolves_to_the_declared_literal_or_nothing(db: None) -> None:
    """The resolver's whole contract. A fixed host answers itself."""
    workspace_id = await _workspace()
    store = _store()
    assert await credential_host(store, workspace_id, "api.fixed.test") == "api.fixed.test"
    assert await credential_host(store, workspace_id, DATADOG_SITES) == DATADOG_HOST
    for selected, expected in (
        (US5_HOST, US5_HOST),
        (f"  {US5_HOST.upper()} ", US5_HOST),
        ("api.datadoghq.eu", "api.datadoghq.eu"),
        ("169.254.169.254", None),
        ("10.0.0.5", None),
        ("metadata.google.internal", None),
        ("api.us5.datadoghq.com.evil.test", None),
        ("sub.api.us5.datadoghq.com", None),
        ("https://api.us5.datadoghq.com", None),
        ("us5", None),
    ):
        await store.put(workspace_id, "datadog_api_host", selected)
        assert await credential_host(store, workspace_id, DATADOG_SITES) == expected, selected


def test_a_host_choice_must_name_a_declared_slot() -> None:
    companion = CredentialSlot(name="datadog_api_host", description="site")
    keyed = CredentialSlot(
        name="dd_key",
        description="k",
        injection=InjectionTarget(host=DATADOG_SITES, header="DD-API-KEY", env="DD_API_KEY"),
    )
    typo = replace(
        keyed,
        injection=replace(keyed.injection, host=replace(DATADOG_SITES, slot="datadog_api_hostt")),
    )
    with pytest.raises(RuntimeError, match="datadog_api_hostt"):
        injecting_slots((Manifest(name="t", version="1", credentials=(typo, companion)),))
    assert injecting_slots((Manifest(name="ok", version="1", credentials=(keyed, companion)),))
    elsewhere = Manifest(name="other", version="1", credentials=(companion,))
    assert injecting_slots((Manifest(name="k", version="1", credentials=(keyed,)), elsewhere))


def test_two_slots_claiming_one_env_fail_loud() -> None:
    first = Manifest(
        name="a",
        version="1",
        credentials=(
            CredentialSlot(
                name="a_key",
                description="key",
                injection=InjectionTarget(host="api.a.test", header="x-key", env="A_KEY"),
            ),
        ),
    )
    same_env = Manifest(
        name="c",
        version="1",
        credentials=(
            CredentialSlot(
                name="c_key",
                description="key",
                injection=InjectionTarget(host="api.c.test", header="x-key", env="A_KEY"),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="env 'A_KEY'"):
        injecting_slots((first, same_env))
    assert len(injecting_slots((first,))) == 1


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_policy_binds_nothing_for_a_selection_the_row_does_not_offer(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace()
    store = _store()
    slots = WorkspaceSlots(deploy=injecting_slots((_keyed_manifest(),)))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", "169.254.169.254")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        binds = await derive_credential_binds(slots, workspace_id, store)

    assert binds == ()
    warned = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.credential_host_unavailable"
    ]
    assert {entry["slot"] for entry in warned} == {
        "datadog_api_key",
        "datadog_application_key",
    }
    assert not any("169.254" in str(entry) for entry in warned)

    await store.put(workspace_id, "datadog_api_host", US5_HOST)
    assert {bind.host for bind in await derive_credential_binds(slots, workspace_id, store)} == {
        US5_HOST
    }


def _connector_grant(
    account: str, owner: UUID, shared: bool, provider: str = "github"
) -> grants_module.Grant:
    return grants_module.Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider=provider,
        account_id=account,
        host="api.github.com",
        owner_member_id=owner,
        owner_email="owner@x.test",
        connection_shared=shared,
    )


def test_cli_accounts_use_the_members_private_tier_else_the_shared_one() -> None:
    mine, theirs = uuid4(), uuid4()
    grants = (
        _connector_grant("acct-shared", theirs, shared=True),
        _connector_grant("acct-mine-b", mine, shared=False),
        _connector_grant("acct-mine-a", mine, shared=False),
        _connector_grant("acct-theirs", theirs, shared=False),
        _connector_grant("acct-other-provider", mine, shared=False, provider="gitlab"),
    )
    assert grants_module.cli_accounts(grants, "github", mine) == ("acct-mine-a", "acct-mine-b")
    assert grants_module.cli_accounts(grants, "github", theirs) == ("acct-theirs",)
    assert grants_module.cli_accounts(grants, "github", None) == ("acct-shared",)
    assert grants_module.cli_accounts(grants, "github", uuid4()) == ("acct-shared",)
    assert grants_module.cli_accounts(grants, "gitlab", mine) == ("acct-other-provider",)
    assert grants_module.cli_accounts(grants, "gitlab", None) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_slot_fault_withholds_its_own_host_and_leaves_the_rest_deriving(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The property that makes this derivation safe to compose: it is total."""
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "perplexity_api_key", "pplx-real")
    await _store().put(workspace_id, "datadog_api_host", DATADOG_HOST)
    fixed = Manifest(
        name="fixed",
        version="1",
        credentials=(
            CredentialSlot(
                name="perplexity_api_key",
                description="api key",
                injection=InjectionTarget(
                    host=PERPLEXITY_HOST,
                    header="authorization",
                    env="PPLX_API_KEY",
                ),
            ),
        ),
    )
    slots = WorkspaceSlots(deploy=injecting_slots((_keyed_manifest(), fixed)))

    with caplog.at_level(logging.WARNING, logger="ufo"):
        binds = await derive_credential_binds(slots, workspace_id, store)

    assert binds == (
        Bind(
            host=PERPLEXITY_HOST,
            header="authorization",
            secret="perplexity_api_key",
            env="PPLX_API_KEY",
        ),
    )
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.credential_slot_failed"
    ]
    assert [entry["slot"] for entry in withheld] == ["datadog_api_key", "datadog_application_key"]
    assert {entry["error_class"] for entry in withheld} == {"InvalidToken"}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_secret_row_this_deploy_cannot_decrypt_still_binds_by_name(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace()
    store = _store()
    await _store().put(workspace_id, "datadog_api_key", "dd-api-real")
    await _store().put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", DATADOG_HOST)
    await store.put(workspace_id, "perplexity_api_key", "pplx-real")
    fixed = Manifest(
        name="fixed",
        version="1",
        credentials=(
            CredentialSlot(
                name="perplexity_api_key",
                description="api key",
                injection=InjectionTarget(
                    host=PERPLEXITY_HOST,
                    header="authorization",
                    env="PPLX_API_KEY",
                ),
            ),
        ),
    )
    slots = WorkspaceSlots(deploy=injecting_slots((_keyed_manifest(), fixed)))

    with caplog.at_level(logging.WARNING, logger="ufo"):
        binds = await derive_credential_binds(slots, workspace_id, store)

    assert binds == (
        Bind(host=DATADOG_HOST, header="DD-API-KEY", secret="datadog_api_key", env="DD_API_KEY"),
        Bind(
            host=DATADOG_HOST,
            header="DD-APPLICATION-KEY",
            secret="datadog_application_key",
            env="DD_APP_KEY",
        ),
        Bind(
            host=PERPLEXITY_HOST,
            header="authorization",
            secret="perplexity_api_key",
            env="PPLX_API_KEY",
        ),
    )
    assert not any(
        record.getMessage() == "egress.credential_slot_failed" for record in caplog.records
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_bound_account_serves_its_own_models_and_no_other_call_in_the_turn(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    monkeypatch.setenv("OPENAI_API_KEY", "platform-default")
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), "member-account")

    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        assert (
            await ws_current().credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
            == "member-account"
        )
        assert (
            await ws_current().credential(OPENAI_KEY_SLOT, None, BACKGROUND_MODEL)
            == "platform-default"
        )
        assert not await ws_current().credential_is_stored(OPENAI_KEY_SLOT, BACKGROUND_MODEL)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_spent_grant_is_refreshed_in_place_before_a_call_gets_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account grant expires, so the row a member connected once has to keep working without
    them."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    spent = Grant(access="stale-access", refresh="refresh-1", expires_at=time.time() - 1)
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), spent.stored())

    async def buys(grant: Grant, slot: str) -> Grant:
        assert (grant.refresh, slot) == ("refresh-1", OPENAI_KEY_SLOT)
        return Grant(access="fresh-access", refresh="refresh-2", expires_at=time.time() + 3600)

    monkeypatch.setattr(workspace_module, "refreshed", buys)
    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        assert (
            await ws_current().model_credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        ).value == "fresh-access"

    written = read_grant(await store.get(workspace_id, member_slot(OPENAI_KEY_SLOT, member)))
    assert written is not None
    assert (written.access, written.refresh) == ("fresh-access", "refresh-2")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_live_grant_is_spent_as_it_stands(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grant with time left is handed straight to the call: refreshing every read would spend a
    round-trip on every turn and rotate a token nothing had finished with."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    live = Grant(access="live-access", refresh="refresh-1", expires_at=time.time() + 3600)
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), live.stored())

    async def never(grant: Grant, slot: str) -> Grant:
        raise AssertionError("a live grant must not be refreshed")

    monkeypatch.setattr(workspace_module, "refreshed", never)
    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        assert (
            await ws_current().model_credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        ).value == "live-access"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_two_turns_finding_one_grant_spent_exchange_its_token_once(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One member's work fans out, so two turns can find the same grant spent at the same moment."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    slot = member_slot(OPENAI_KEY_SLOT, member)
    spent = Grant(access="stale", refresh="refresh-1", expires_at=time.time() - 1)
    await store.put(workspace_id, slot, spent.stored())
    exchanged: list[str] = []

    async def buys(grant: Grant, named: str) -> Grant:
        exchanged.append(grant.refresh)
        await asyncio.sleep(0.05)
        return Grant(
            access=f"access-{len(exchanged)}",
            refresh=f"refresh-{len(exchanged) + 1}",
            expires_at=time.time() + 3600,
        )

    monkeypatch.setattr(workspace_module, "refreshed", buys)

    async def turn() -> str:
        with (
            ws(workspace_id),
            model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
        ):
            return (
                await ws_current().model_credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
            ).value

    served = await asyncio.gather(turn(), turn())

    assert exchanged == ["refresh-1"]
    assert served == ["access-1", "access-1"]
    written = read_grant(await store.get(workspace_id, slot))
    assert written is not None
    assert (written.access, written.refresh) == ("access-1", "refresh-2")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_no_transaction_is_held_open_across_the_provider_refresh(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ufoctl serve` is one process, and on SQLite one transaction takes a lock the whole node
    waits behind."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    slot = member_slot(OPENAI_KEY_SLOT, member)
    spent = Grant(access="stale", refresh="refresh-1", expires_at=time.time() - 1)
    await store.put(workspace_id, slot, spent.stored())
    wrote_during_refresh = False

    async def buys(grant: Grant, named: str) -> Grant:
        """A write from elsewhere in the deploy, while the provider call is in flight."""
        nonlocal wrote_during_refresh
        async with asyncio.timeout(5):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.select(tables.workspace.c.id)
                    .where(tables.workspace.c.id == workspace_id)
                    .with_for_update()
                )
        wrote_during_refresh = True
        return Grant(access="fresh", refresh="refresh-2", expires_at=time.time() + 3600)

    monkeypatch.setattr(workspace_module, "refreshed", buys)
    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        assert (
            await ws_current().model_credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL)
        ).value == "fresh"

    assert wrote_during_refresh


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_product_census_counts_no_members_own_account(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    await store.put(workspace_id, OPENAI_KEY_SLOT, "workspace-key")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), "member-account")

    counted: list[tuple[str, str]] = []

    def record(name: str, amount: int = 1, /, **dimensions: str) -> None:
        if name == PRODUCT_ATTACH_METRIC and dimensions.get("kind") == CREDENTIAL_KIND:
            counted.append((dimensions["kind"], dimensions["name"]))

    monkeypatch.setattr(product_module, "emit_metric", record)
    with ws(workspace_id):
        await ProductCensus(contributions=()).count()

    assert counted == [(CREDENTIAL_KIND, OPENAI_KEY_SLOT)]
    assert not any(str(member) in name for _kind, name in counted)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_token_rejected_mid_turn_is_refreshed_and_the_round_carries_on(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One client serves a whole turn, and a coding turn runs to a hundred rounds — longer than
    an access token's remaining life when the turn starts near expiry."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    slot = member_slot(OPENAI_KEY_SLOT, member)
    live = Grant(access="first", refresh="refresh-1", expires_at=time.time() + 3600)
    await store.put(workspace_id, slot, live.stored())
    served: list[str] = []

    class Wire:
        def __init__(self, key: str) -> None:
            self.key = key

        async def complete(self, request: object) -> AsyncIterator[str]:
            served.append(self.key)
            if self.key == "first":
                await store.put(
                    workspace_id,
                    slot,
                    Grant(
                        access="second", refresh="refresh-2", expires_at=time.time() + 3600
                    ).stored(),
                )
                raise CredentialValueInvalid("key was rejected by the provider")
            yield "round"

    spec = SimpleNamespace(
        key_slot=OPENAI_KEY_SLOT, key_env=None, client=lambda _spec, key: Wire(key)
    )
    registry = ModelRegistry(
        specs={OWN_ACCOUNT_MODEL: spec},
        pricing=Pricing(prices={}, digest="test"),
        auto_model=OWN_ACCOUNT_MODEL,
    )

    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        client = await registry.client_for(OWN_ACCOUNT_MODEL)
        assert [event async for event in client.complete(object())] == ["round"]

    assert served == ["first", "second"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_rejected_member_token_cannot_retry_on_another_payer(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    slot = member_slot(OPENAI_KEY_SLOT, member)
    await store.put(workspace_id, slot, "member-key")
    await store.put(workspace_id, OPENAI_KEY_SLOT, "workspace-key")
    served: list[str] = []

    class Wire:
        def __init__(self, key: str) -> None:
            self.key = key

        async def complete(self, request: object) -> AsyncIterator[str]:
            served.append(self.key)
            if self.key == "member-key":
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.delete(tables.credential).where(
                            tables.credential.c.workspace_id == workspace_id,
                            tables.credential.c.slot == slot,
                        )
                    )
                raise CredentialValueInvalid("member token was rejected")
            yield "platform answer"

    spec = SimpleNamespace(
        key_slot=OPENAI_KEY_SLOT, key_env=None, client=lambda _spec, key: Wire(key)
    )
    registry = ModelRegistry(
        specs={OWN_ACCOUNT_MODEL: spec},
        pricing=Pricing(prices={}, digest="test"),
        auto_model=OWN_ACCOUNT_MODEL,
    )

    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        client = await registry.client_for(OWN_ACCOUNT_MODEL)
        with pytest.raises(RuntimeError, match="exact account credential"):
            [event async for event in client.complete(object())]

    assert served == ["member-key"]


OTHER_ACCOUNT_MODEL = "claude-opus-5"


def _two_account_registry(served: list[str]) -> ModelRegistry:
    class Wire:
        def __init__(self, key: str) -> None:
            self.key = key

        async def complete(self, request: object) -> AsyncIterator[str]:
            served.append(self.key)
            yield "round"

    return ModelRegistry(
        specs={
            OWN_ACCOUNT_MODEL: SimpleNamespace(
                id=OWN_ACCOUNT_MODEL,
                key_slot=OPENAI_KEY_SLOT,
                key_env=None,
                client=lambda _spec, key: Wire(key),
            ),
            OTHER_ACCOUNT_MODEL: SimpleNamespace(
                id=OTHER_ACCOUNT_MODEL,
                key_slot=ANTHROPIC_KEY_SLOT,
                key_env=None,
                client=lambda _spec, key: Wire(key),
            ),
        },
        pricing=Pricing(prices={}, digest="test"),
        auto_model=OWN_ACCOUNT_MODEL,
    )


async def _serving_on_member_account(
    registry: ModelRegistry, *alternates: ModelRoute
) -> ServingModel:
    first = await registry.client_for(OWN_ACCOUNT_MODEL)
    return ServingModel(
        model=OWN_ACCOUNT_MODEL,
        spec=registry.spec(OWN_ACCOUNT_MODEL),
        client=first.client,
        funding=first.funding,
        payer=first.payer,
        routes=ModelRoutes(registry=registry, remaining=alternates),
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_refused_account_refresh_becomes_an_unavailable_model_account(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    await store.put(
        workspace_id,
        member_slot(OPENAI_KEY_SLOT, member),
        Grant(access="spent", refresh="refused", expires_at=0).stored(),
    )

    async def refuse(grant: Grant, slot: str) -> Grant:
        del grant
        raise GrantRefusedRefresh(slot)

    monkeypatch.setattr(workspace_module, "refreshed", refuse)
    registry = _two_account_registry([])

    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        resolved = await registry.client_for(OWN_ACCOUNT_MODEL)
        with pytest.raises(ModelAccountUnavailable, match="could not be refreshed"):
            [event async for event in resolved.client.complete(object())]

    assert (resolved.funding, resolved.payer) == (
        PLAN_FUNDED,
        member_slot(OPENAI_KEY_SLOT, member),
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_rate_limited_turn_moves_onto_the_members_other_connected_account(
    db: None,
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    for slot, access in ((OPENAI_KEY_SLOT, "openai"), (ANTHROPIC_KEY_SLOT, "anthropic")):
        await store.put(
            workspace_id,
            member_slot(slot, member),
            Grant(access=access, refresh="refresh", expires_at=time.time() + 3600).stored(),
        )
    served: list[str] = []
    registry = _two_account_registry(served)
    routes = {
        OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member),
        OTHER_ACCOUNT_MODEL: member_slot(ANTHROPIC_KEY_SLOT, member),
    }
    alternate = ModelRoute(
        OTHER_ACCOUNT_MODEL,
        PLAN_FUNDED,
        member_slot(ANTHROPIC_KEY_SLOT, member),
    )

    with ws(workspace_id), model_credentials(routes):
        serving = await _serving_on_member_account(registry, alternate)
        assert await serving.move() is True
        assert (serving.model, serving.spec) == (
            OTHER_ACCOUNT_MODEL,
            registry.spec(OTHER_ACCOUNT_MODEL),
        )
        assert [event async for event in serving.client.complete(object())] == ["round"]
        assert await serving.move() is False

    assert served == ["anthropic"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_move_never_lands_on_the_workspaces_row_or_the_deploys_key(db: None) -> None:
    """A missing exact account credential cannot fall through to workspace funding."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    live = Grant(access="openai", refresh="refresh", expires_at=time.time() + 3600)
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), live.stored())
    await store.put(
        workspace_id,
        ANTHROPIC_KEY_SLOT,
        Grant(access="workspace", refresh="refresh", expires_at=time.time() + 3600).stored(),
    )
    served: list[str] = []
    registry = _two_account_registry(served)
    routes = {
        OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member),
        OTHER_ACCOUNT_MODEL: member_slot(ANTHROPIC_KEY_SLOT, member),
    }
    alternate = ModelRoute(
        OTHER_ACCOUNT_MODEL,
        PLAN_FUNDED,
        member_slot(ANTHROPIC_KEY_SLOT, member),
    )

    with ws(workspace_id), model_credentials(routes):
        serving = await _serving_on_member_account(registry, alternate)
        assert serving.funding == PLAN_FUNDED
        with pytest.raises(RuntimeError, match="exact account credential"):
            await serving.move()

    assert (serving.model, served) == (OWN_ACCOUNT_MODEL, [])


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_move_holds_the_funding_class_the_attempt_froze(db: None) -> None:
    """The attempt began on a plan-funded grant and froze the plan rate card."""
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    live = Grant(access="openai", refresh="refresh", expires_at=time.time() + 3600)
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), live.stored())
    await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member), "sk-ant-member")
    served: list[str] = []
    registry = _two_account_registry(served)
    routes = {
        OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member),
        OTHER_ACCOUNT_MODEL: member_slot(ANTHROPIC_KEY_SLOT, member),
    }
    alternate = ModelRoute(
        OTHER_ACCOUNT_MODEL,
        PLAN_FUNDED,
        member_slot(ANTHROPIC_KEY_SLOT, member),
    )

    with ws(workspace_id), model_credentials(routes):
        serving = await _serving_on_member_account(registry, alternate)
        with pytest.raises(ModelFundingChanged, match="payer changed during route failover"):
            await serving.move()

    assert (serving.model, served) == (OWN_ACCOUNT_MODEL, [])


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_rejection_the_rebuild_cannot_fix_is_raised_after_one_retry(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    store = _store()
    init_workspace_credentials(store)
    async with workspace_tx() as connection:
        member = await create_member(connection, workspace_id, "coder@work.com")
    live = Grant(access="rejected", refresh="refresh-1", expires_at=time.time() + 3600)
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), live.stored())
    attempts: list[str] = []

    class Wire:
        def __init__(self, key: str) -> None:
            self.key = key

        async def complete(self, request: object) -> AsyncIterator[str]:
            attempts.append(self.key)
            raise CredentialValueInvalid("key was rejected by the provider")
            yield ""

    spec = SimpleNamespace(
        key_slot=OPENAI_KEY_SLOT, key_env=None, client=lambda _spec, key: Wire(key)
    )
    registry = ModelRegistry(
        specs={OWN_ACCOUNT_MODEL: spec},
        pricing=Pricing(prices={}, digest="test"),
        auto_model=OWN_ACCOUNT_MODEL,
    )

    with (
        ws(workspace_id),
        model_credentials({OWN_ACCOUNT_MODEL: member_slot(OPENAI_KEY_SLOT, member)}),
    ):
        client = await registry.client_for(OWN_ACCOUNT_MODEL)
        with pytest.raises(CredentialValueInvalid):
            [event async for event in client.complete(object())]

    assert attempts == ["rejected", "rejected"]
