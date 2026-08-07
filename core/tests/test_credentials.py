import logging
import re
import time
from base64 import b64encode
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.connectors import CliCredential
from ufo.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CREDENTIAL_REQUEST_TTL_SECONDS,
    INSTALLATION_BINDING_PURPOSE,
    CredentialMintFailed,
    CredentialRequestInvalid,
    CredentialRequests,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    HostChoice,
    credential_host,
    open_credential_request,
    open_installation,
    seal_credential_request,
    seal_installation,
    slot_is_set,
    slot_secret,
)
from ufo.db import workspace_tx
from ufo.ext.loader import injecting_slots
from ufo.ext.manifest import (
    ConnectorProvider,
    CredentialSlot,
    InjectionTarget,
    Manifest,
)
from ufo.sandbox.proxy.rules import (
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
)
from ufo.schema import tables
from ufo.workspace import init_workspace_credentials, ws, ws_current


class _StubOAuth:
    """The connector's OAuth descriptor is irrelevant here — only its declared CLI env name is."""

    provider = "github"
    host = "api.github.com"


class _StubBroker:
    """Neither the broker nor the forwarder is called: the assertion is on boot-time name claims."""


DATADOG_HOST = "api.datadoghq.com"
US5_HOST = "api.us5.datadoghq.com"


DATADOG_SITES = HostChoice(
    slot="datadog_api_host",
    description="Datadog site for this org.",
    hosts=(DATADOG_HOST, US5_HOST, "api.datadoghq.eu"),
    default=DATADOG_HOST,
    env="DD_HOST",
)


def _keyed_manifest() -> Manifest:
    """A two-key provider on one host with a companion host slot — the Datadog shape exactly as the
    real table declares it: one slot per header, both metering the same host, plus the non-secret
    slot that pins this workspace's site. Both carry `dimension`, because a fixture that metered on
    only one slot would hide what two slots sharing a host do to the host's rules."""
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
                    sentinel="SENTINEL_DD_API",
                    env="DD_API_KEY",
                    dimension="requests",
                ),
            ),
            CredentialSlot(
                name="datadog_application_key",
                description="application key",
                injection=InjectionTarget(
                    host=DATADOG_SITES,
                    header="DD-APPLICATION-KEY",
                    sentinel="SENTINEL_DD_APP",
                    env="DD_APP_KEY",
                    dimension="requests",
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


async def test_put_upserts(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "one")
    await store.put(workspace_id, "sample_api", "two")
    assert await store.get(workspace_id, "sample_api") == "two"


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


async def test_put_rejects_an_empty_value(db: None) -> None:
    with pytest.raises(ValueError, match="empty"):
        await _store().put(await _workspace(), "sample_api", "")


async def test_unset_slot_raises(db: None) -> None:
    store = _store()
    with pytest.raises(CredentialSlotUnset, match="missing"):
        await store.get(await _workspace(), "missing")


async def test_a_stored_slot_is_told_apart_from_the_platform_default(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`credential` resolves stored-then-env, so both answer the same secret; a handler spending
    money on a provider key needs to know which one served it, because spend on the workspace's own
    key is already billed to it by that provider."""
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


def test_credential_request_seal_round_trips_and_expires() -> None:
    """The sealed grant a `request_credentials` call hands a surface: opens to exactly what was
    sealed, and refuses garbage, a foreign key, or a seal older than the TTL."""
    fernet = Fernet(Fernet.generate_key())
    state = CredentialRequestState(workspace_id=uuid4(), member_id=uuid4(), slots=("a", "b"))
    sealed = seal_credential_request(fernet, state)
    assert open_credential_request(fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE) == state
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, "garbage", purpose=CREDENTIAL_REQUEST_PURPOSE)
    stale = fernet.encrypt_at_time(
        state.model_dump_json().encode(),
        int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
    ).decode()
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(fernet, stale, purpose=CREDENTIAL_REQUEST_PURPOSE)
    with pytest.raises(CredentialRequestInvalid):
        open_credential_request(
            Fernet(Fernet.generate_key()), sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )


def test_a_deploy_written_slot_is_never_sealed_for_a_member_to_type() -> None:
    """A slot whose value is a seal only the install callback can compose refuses the private prompt
    outright. Gating the seal is what makes it total: a member is never handed a request to fulfill,
    so no typed value reaches the slot to be refused later by the wire — which would withhold that
    host on every turn until someone rebound it. `authorize` still passes, because that is the
    callback's own path to the same slot."""
    fernet = Fernet(Fernet.generate_key())
    requests = CredentialRequests(
        fernet=fernet,
        declared=frozenset({"github_app_installation", "github_git_token"}),
        fillable=frozenset({"github_git_token"}),
    )
    workspace_id, member_id = uuid4(), uuid4()

    assert requests.seal(workspace_id, member_id, ("github_git_token",))
    with pytest.raises(ValueError, match="written by this deploy, never entered"):
        requests.seal(workspace_id, member_id, ("github_app_installation",))
    with pytest.raises(ValueError, match="written by this deploy, never entered"):
        requests.seal(workspace_id, member_id, ("github_git_token", "github_app_installation"))
    assert requests.authorize(workspace_id, member_id, "github_app_installation", "install")


def test_credential_requests_seal_only_declared_slots() -> None:
    requests = CredentialRequests(
        fernet=Fernet(Fernet.generate_key()), declared=frozenset({"a"}), fillable=frozenset({"a"})
    )
    assert requests.seal(uuid4(), uuid4(), ("a",))
    with pytest.raises(ValueError, match="declares credential slot"):
        requests.seal(uuid4(), uuid4(), ("a", "nope"))


async def test_credential_requests_open_an_owner_bound_authorization(db: None) -> None:
    workspace_id = await _workspace()
    member_id = uuid4()
    requests = CredentialRequests(
        fernet=Fernet(Fernet.generate_key()), declared=frozenset({"yc"}), fillable=frozenset({"yc"})
    )
    with pytest.raises(ValueError, match="empty"):
        requests.authorize(workspace_id, member_id, "yc", "")
    sealed = requests.authorize(workspace_id, member_id, "yc", '{"device":"secret"}')
    assert (
        requests.open_authorization(sealed, workspace_id, member_id, "yc") == '{"device":"secret"}'
    )
    with pytest.raises(CredentialRequestInvalid, match="workspace"):
        requests.open_authorization(sealed, uuid4(), member_id, "yc")
    with pytest.raises(CredentialRequestInvalid, match="member"):
        requests.open_authorization(sealed, workspace_id, uuid4(), "yc")
    with pytest.raises(CredentialRequestInvalid, match="slot"):
        requests.open_authorization(sealed, workspace_id, member_id, "other")


async def test_two_keys_on_one_host_each_inject_their_own_header(db: None) -> None:
    """A provider taking more than one key on the wire is two injecting slots on one host: each
    swaps its own header from its own stored secret, while the host is scoped and metered exactly
    once — one physical request carries both keys, so metering per slot would double-count every
    Datadog call in `sandbox_egress_total`. The `DD-API-KEY` + `DD-APPLICATION-KEY` case, with no
    composite target."""
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    slots = injecting_slots((_keyed_manifest(),))
    rules = await derive_credential_rules(slots, workspace_id, store)
    injections = {rule.header: rule for rule in rules if isinstance(rule, InjectionRule)}
    assert injections["DD-API-KEY"] == InjectionRule(
        host=DATADOG_HOST, header="DD-API-KEY", sentinel="SENTINEL_DD_API", real="dd-api-real"
    )
    assert injections["DD-APPLICATION-KEY"] == InjectionRule(
        host=DATADOG_HOST,
        header="DD-APPLICATION-KEY",
        sentinel="SENTINEL_DD_APP",
        real="dd-app-real",
    )
    assert [rule for rule in rules if isinstance(rule, ScopeRule)] == [
        ScopeRule(allowed_hosts=frozenset({DATADOG_HOST}))
    ]
    assert [rule for rule in rules if isinstance(rule, MeterRule)] == [
        MeterRule(host=DATADOG_HOST, dimension="requests")
    ]


async def test_the_host_slot_pins_the_workspace_site(db: None) -> None:
    """The non-secret companion slot is what makes a per-account host correct: with it set, every
    rule the provider derives — scope, injection, meter — names that workspace's own host, so a US5
    org's key is admitted to US5 and never rides to the US1 default."""
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_api_host", "API.US5.datadoghq.com ")
    rules = await derive_credential_rules(
        injecting_slots((_keyed_manifest(),)), workspace_id, store
    )
    assert {rule.host for rule in rules if not isinstance(rule, ScopeRule)} == {US5_HOST}
    assert ScopeRule(allowed_hosts=frozenset({US5_HOST})) in rules
    assert not any(
        isinstance(rule, ScopeRule) and DATADOG_HOST in rule.allowed_hosts for rule in rules
    )


async def test_an_unfilled_slot_opens_no_egress_and_a_code_only_slot_never_rides(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    assert (
        await derive_credential_rules(injecting_slots((_keyed_manifest(),)), workspace_id, store)
        == ()
    )
    code_only = Manifest(
        name="s", version="1", credentials=(CredentialSlot(name="code_only", description="x"),)
    )
    await store.put(workspace_id, "code_only", "in-process-only")
    assert injecting_slots((code_only,)) == ()


async def test_one_workspace_never_derives_anothers_secret(db: None) -> None:
    """Per-workspace resolution is the tenant isolation the one shared proxy leans on: the same
    declaration resolved for a second workspace yields that workspace's own secret, or nothing."""
    first, second = await _workspace(), await _workspace()
    store = _store()
    await store.put(first, "datadog_api_key", "first-secret")
    slots = injecting_slots((_keyed_manifest(),))
    assert [
        rule.real
        for rule in await derive_credential_rules(slots, first, store)
        if isinstance(rule, InjectionRule)
    ] == ["first-secret"]
    assert await derive_credential_rules(slots, second, store) == ()


async def test_a_selection_resolves_to_the_declared_literal_or_nothing(db: None) -> None:
    """The resolver's whole contract. A fixed host answers itself. A choice answers the declared
    literal a selection names — case-insensitively, as DNS is, and canonicalised to the row's own
    spelling — the default while nothing is selected, and None for anything the set does not offer.
    Nothing a member types reaches the wire, so there is no pattern, bound or fold to get wrong."""
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
    """Every writer gates on the declared set — the sealed chat handoff and `ufoctl credential set`
    both refuse an unknown slot — so a companion nothing declares can never be filled and the host
    would sit on the default forever, reading exactly like a member who has not chosen yet. A
    companion declared by another installed extension is fine: the set spans the deploy."""
    companion = CredentialSlot(name="datadog_api_host", description="site")
    keyed = CredentialSlot(
        name="dd_key",
        description="k",
        injection=InjectionTarget(host=DATADOG_SITES, header="DD-API-KEY", sentinel="S_DD"),
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


def test_two_slots_claiming_one_sentinel_or_env_fail_loud() -> None:
    """A shared sentinel would draw whichever secret matched first and a shared env would leave one
    provider's variable holding the other's sentinel — both silent at boot and wrong at the wire, so
    collecting them refuses it across every installed extension."""
    first = Manifest(
        name="a",
        version="1",
        credentials=(
            CredentialSlot(
                name="a_key",
                description="key",
                injection=InjectionTarget(
                    host="api.a.test", header="x-key", sentinel="SHARED", env="A_KEY"
                ),
            ),
        ),
    )
    same_sentinel = Manifest(
        name="b",
        version="1",
        credentials=(
            CredentialSlot(
                name="b_key",
                description="key",
                injection=InjectionTarget(
                    host="api.b.test", header="x-key", sentinel="SHARED", env="B_KEY"
                ),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="sentinel"):
        injecting_slots((first, same_sentinel))
    same_env = Manifest(
        name="c",
        version="1",
        credentials=(
            CredentialSlot(
                name="c_key",
                description="key",
                injection=InjectionTarget(
                    host="api.c.test", header="x-key", sentinel="OWN", env="A_KEY"
                ),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="env"):
        injecting_slots((first, same_env))


def test_one_sandbox_variable_carries_one_value_whichever_field_claims_it() -> None:
    """Every exported name lands in one dict per sandbox, so the namespace is one: a slot's `env`, a
    host choice's `env`, and a connector CLI's env all compete. Two claims on one name carrying
    different values would let the later export win the merge silently, leaving a client reading a
    hostname where its auth belongs, and the same holds inside one slot naming a variable twice."""

    def keyed(name: str, **injection: object) -> Manifest:
        return Manifest(
            name=name,
            version="1",
            credentials=(
                CredentialSlot(
                    name=f"{name}_key",
                    description="key",
                    injection=InjectionTarget(
                        header="x-key",
                        sentinel=f"S_{name}",
                        **injection,  # type: ignore[arg-type]
                    ),
                ),
                CredentialSlot(name="datadog_api_host", description="site"),
            ),
        )

    clash = replace(DATADOG_SITES, env="X")
    with pytest.raises(RuntimeError, match="env 'X'"):
        injecting_slots((keyed("alpha", host="api.a.test", env="X"), keyed("beta", host=clash)))
    with pytest.raises(RuntimeError, match="env 'Y'"):
        injecting_slots((keyed("solo", host=replace(DATADOG_SITES, env="Y"), env="Y"),))
    assert injecting_slots(
        (
            keyed("alpha", host="api.a.test", env="X"),
            keyed(
                "beta",
                host=clash.__class__(
                    slot="datadog_api_host",
                    description="d",
                    hosts=DATADOG_SITES.hosts,
                    default=DATADOG_HOST,
                    env="H",
                ),
            ),
        )
    )


def test_two_slots_claiming_one_sentinel_fail_loud() -> None:
    """A shared sentinel draws whichever secret matched first, so it is refused across every
    installed extension."""

    def keyed(name: str, sentinel: str) -> Manifest:
        return Manifest(
            name=name,
            version="1",
            credentials=(
                CredentialSlot(
                    name=f"{name}_key",
                    description="key",
                    injection=InjectionTarget(
                        host=f"api.{name}.test", header="x-key", sentinel=sentinel
                    ),
                ),
            ),
        )

    with pytest.raises(RuntimeError, match="sentinel"):
        injecting_slots((keyed("a", "SHARED"), keyed("b", "SHARED")))
    assert injecting_slots((keyed("a", "OWN_A"), keyed("b", "OWN_B")))


def test_slots_sharing_a_host_env_must_select_through_one_choice() -> None:
    """Sharing one host variable is safe exactly while its sharers resolve it identically, and a
    frozen value object settles that by equality over every field it has — not a tuple of the fields
    someone remembered to list, which is what let a divergent bound through before. Two keys naming
    one choice is the point; two different choices claiming one variable is refused."""
    together = Manifest(
        name="together",
        version="1",
        credentials=(
            CredentialSlot(
                name="a_key",
                description="k",
                injection=InjectionTarget(host=DATADOG_SITES, header="A", sentinel="S_A"),
            ),
            CredentialSlot(
                name="b_key",
                description="k",
                injection=InjectionTarget(host=DATADOG_SITES, header="B", sentinel="S_B"),
            ),
            CredentialSlot(name="datadog_api_host", description="site"),
        ),
    )
    assert len(injecting_slots((together,))) == 2
    diverged = replace(DATADOG_SITES, default=US5_HOST)
    apart = Manifest(
        name="apart",
        version="1",
        credentials=(
            CredentialSlot(
                name="c_key",
                description="k",
                injection=InjectionTarget(host=diverged, header="C", sentinel="S_C"),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="DD_HOST"):
        injecting_slots((together, apart))


def test_a_keyed_export_cannot_take_a_connector_clis_env_name() -> None:
    """The sandbox env has two producers — a grant's CLI sentinel and a keyed slot's — merged into
    one dict with the keyed half last, so a row naming `GH_TOKEN` would overwrite the github grant's
    sentinel and leave that CLI authenticating as nothing. Adding a keyed provider is meant to cost
    no code and no test, so nothing but this refusal stops the next row taking a name in use."""
    connector = Manifest(
        name="broker",
        version="1",
        connectors=(
            ConnectorProvider(
                oauth=_StubOAuth(),
                label="GitHub",
                broker=_StubBroker(),
                cli=CliCredential(env="GH_TOKEN", header="authorization", forward=_StubBroker()),
            ),
        ),
    )

    def keyed(host: str | HostChoice, env: str | None) -> Manifest:
        return Manifest(
            name="keyed",
            version="1",
            credentials=(
                CredentialSlot(
                    name="k",
                    description="key",
                    injection=InjectionTarget(host=host, header="x-key", sentinel="S", env=env),
                ),
                CredentialSlot(name="datadog_api_host", description="site"),
            ),
        )

    with pytest.raises(RuntimeError, match="GH_TOKEN"):
        injecting_slots((connector, keyed("api.k.test", "GH_TOKEN")))
    with pytest.raises(RuntimeError, match="GH_TOKEN"):
        injecting_slots((connector, keyed(replace(DATADOG_SITES, env="GH_TOKEN"), None)))
    assert injecting_slots((connector, keyed("api.k.test", "K_TOKEN")))


async def test_the_proxy_withholds_every_rule_for_a_selection_the_row_does_not_offer(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The proxy's own withhold branch, reached through the derivation rather than asserted of the
    resolver alone: a stored selection the declaration does not offer emits no rule at all — not the
    default host, not a bare scope — and warns once per filled slot. Its engine-side twin has the
    same proof, and this pair has drifted apart once already."""
    workspace_id = await _workspace()
    store = _store()
    slots = injecting_slots((_keyed_manifest(),))
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", "169.254.169.254")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        rules = await derive_credential_rules(slots, workspace_id, store)

    assert rules == ()
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
    assert {
        rule.host
        for rule in await derive_credential_rules(slots, workspace_id, store)
        if isinstance(rule, InjectionRule)
    } == {US5_HOST}


def test_slots_reaching_one_host_must_meter_it_the_same_way() -> None:
    """A host is metered once however many keys reach it, so the derivation emits one meter per host
    — and a second dimension would simply be dropped. Every other cross-slot claim here fails loud;
    this one would have been the silent exception. The claim spans every host a declaration can
    reach, not the declaration itself: a fixed host and an unrelated choice offering that same
    literal resolve to one host at runtime, and keying on the declaration missed exactly that."""

    def keyed(name: str, dimension: str) -> CredentialSlot:
        return CredentialSlot(
            name=name,
            description="key",
            injection=InjectionTarget(
                host="api.one.test",
                header=f"X-{name}",
                sentinel=f"S_{name}",
                dimension=dimension,
            ),
        )

    agreed = Manifest(
        name="agreed",
        version="1",
        credentials=(keyed("a_key", "requests"), keyed("b_key", "requests")),
    )
    assert len(injecting_slots((agreed,))) == 2
    diverged = Manifest(
        name="diverged",
        version="1",
        credentials=(keyed("c_key", "requests"), keyed("d_key", "tokens")),
    )
    with pytest.raises(RuntimeError, match="metered once"):
        injecting_slots((diverged,))

    aliased = Manifest(
        name="aliased",
        version="1",
        credentials=(
            CredentialSlot(
                name="e_key",
                description="key",
                injection=InjectionTarget(
                    host=HostChoice(
                        slot="e_host",
                        description="site",
                        hosts=("api.one.test", "api.other.test"),
                        default="api.other.test",
                        env="E_HOST",
                    ),
                    header="X-E",
                    sentinel="S_E",
                    dimension="tokens",
                ),
            ),
            CredentialSlot(name="e_host", description="site"),
        ),
    )
    with pytest.raises(RuntimeError, match=re.escape("api.one.test")):
        injecting_slots((agreed, aliased))


def _git_manifest() -> Manifest:
    """A git credential slot: one stored secret, composed into Basic on the way to the wire."""
    return Manifest(
        name="git",
        version="1",
        credentials=(
            CredentialSlot(
                name="github_git_token",
                description="git token",
                injection=InjectionTarget(
                    host="github.com",
                    header="Authorization",
                    sentinel="SENTINEL_GIT",
                    dimension="requests",
                    git_basic_user="x-access-token",
                ),
            ),
        ),
    )


async def test_a_git_slot_injects_basic_composed_from_its_one_secret(db: None) -> None:
    """git smart-HTTP takes only Basic — a bearer is refused even for a public repository — so the
    stored secret rides as the password half of `x-access-token:<token>`, composed at derivation
    rather than stored composed: the token is one value the member rotates on its own. The host is
    scoped and metered like any keyed host, which is also what makes the proxy MITM it at all."""
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "github_git_token", "ghp-real")
    rules = await derive_credential_rules(injecting_slots((_git_manifest(),)), workspace_id, store)
    assert [rule for rule in rules if isinstance(rule, InjectionRule)] == [
        InjectionRule(
            host="github.com",
            header="Authorization",
            sentinel="SENTINEL_GIT",
            real=f"Basic {b64encode(b'x-access-token:ghp-real').decode()}",
        )
    ]
    assert [rule for rule in rules if isinstance(rule, ScopeRule)] == [
        ScopeRule(allowed_hosts=frozenset({"github.com"}))
    ]
    assert [rule for rule in rules if isinstance(rule, MeterRule)] == [
        MeterRule(host="github.com", dimension="requests")
    ]


async def test_a_git_slot_with_nothing_stored_derives_no_rule(db: None) -> None:
    """No credential, no rules — so `github.com` keeps riding the turn's internet policy as an
    opaque tunnel, which is what lets an anonymous clone of a public repository still work."""
    workspace_id = await _workspace()
    rules = await derive_credential_rules(
        injecting_slots((_git_manifest(),)), workspace_id, _store()
    )
    assert rules == ()


class _MintedTokens:
    """A source standing in for the GitHub App: it mints for a workspace that named an install."""

    def __init__(self, installed: UUID | None) -> None:
        self.installed = installed
        self.calls = 0
        self.presence_checks = 0

    async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
        self.calls += 1
        return "minted-installation-token" if workspace_id == self.installed else None

    async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
        self.presence_checks += 1
        return workspace_id == self.installed


async def test_a_minted_source_answers_before_the_stored_fallback(db: None) -> None:
    """A workspace that installed the App gets the token minted for this turn, not the member's
    stored one — the App's identity is what the organization granted. The stored value stays the
    fallback for a workspace with no installation, which is a repository outside that org."""
    installed = await _workspace()
    bare = await _workspace()
    store = _store()
    for workspace_id in (installed, bare):
        await store.put(workspace_id, "github_git_token", "member-pat")
    source = _MintedTokens(installed)

    assert await slot_secret("github_git_token", source, installed, store) == (
        "minted-installation-token"
    )
    assert await slot_secret("github_git_token", source, bare, store) == "member-pat"
    assert await slot_secret("github_git_token", None, installed, store) == "member-pat"
    assert source.calls == 2


async def test_a_slot_with_neither_a_mint_nor_a_stored_value_resolves_to_nothing(db: None) -> None:
    """No credential at all opens no egress: the caller skips the slot rather than deriving a rule
    that would swap in nothing, so the host keeps riding the turn's own internet policy."""
    workspace_id = await _workspace()
    assert (
        await slot_secret("github_git_token", _MintedTokens(None), workspace_id, _store()) is None
    )


SLOT_NAME = "github_app_installation"


def test_an_installation_binding_opens_only_for_the_workspace_that_sealed_it() -> None:
    """The binding is what makes a self-asserted installation id worthless. An id is a small integer
    anyone can type, and the deploy's App key mints against any installation of it — so the slot
    holds a seal only the install callback can produce, and only for the workspace it names."""
    fernet = Fernet(Fernet.generate_key())
    mine, theirs = uuid4(), uuid4()
    sealed = seal_installation(fernet, mine, SLOT_NAME, "149082716")

    assert open_installation(fernet, mine, SLOT_NAME, sealed) == "149082716"
    with pytest.raises(CredentialRequestInvalid, match="another workspace"):
        open_installation(fernet, theirs, SLOT_NAME, sealed)
    with pytest.raises(CredentialRequestInvalid, match="tampered or expired"):
        open_installation(fernet, mine, SLOT_NAME, "149082716")
    with pytest.raises(CredentialRequestInvalid, match="tampered or expired"):
        open_installation(Fernet(Fernet.generate_key()), mine, SLOT_NAME, sealed)
    with pytest.raises(CredentialRequestInvalid, match="names another slot"):
        open_installation(fernet, mine, "another_slot", sealed)


def test_a_request_seal_cannot_stand_in_for_an_installation_binding() -> None:
    """One deploy key seals both acts, so ciphertext from either opens under the other. The member
    is handed their own connect link's `state` in chat: pasted into the installation slot it must be
    refused for what it is, not decoded as a binding and not raised as a bare KeyError, which would
    fail every turn that opens a sandbox for the workspace until an operator cleared the slot."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id, member_id = uuid4(), uuid4()
    handed_to_the_member = CredentialRequests(
        fernet=fernet, declared=frozenset({SLOT_NAME}), fillable=frozenset()
    ).authorize(workspace_id, member_id, SLOT_NAME, "install")

    with pytest.raises(CredentialRequestInvalid, match="sealed for 'credential-request'"):
        open_installation(fernet, workspace_id, SLOT_NAME, handed_to_the_member)


def test_an_installation_binding_cannot_stand_in_for_a_member_authorization() -> None:
    """The same confusion in the other direction, isolated to the purpose. This seal matches
    everything `open_authorization` goes on to check — workspace, member, slot, a present payload —
    so only the purpose it was minted under refuses it. A binding sealed by `seal_installation`
    would also fail on its absent member, which would prove the member check instead of this one."""
    fernet = Fernet(Fernet.generate_key())
    workspace_id, member_id = uuid4(), uuid4()
    requests = CredentialRequests(
        fernet=fernet, declared=frozenset({SLOT_NAME}), fillable=frozenset({SLOT_NAME})
    )
    binding = seal_credential_request(
        fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=(SLOT_NAME,),
            payload="149082716",
            purpose=INSTALLATION_BINDING_PURPOSE,
        ),
    )

    with pytest.raises(CredentialRequestInvalid, match="sealed for 'installation-binding'"):
        requests.open_authorization(binding, workspace_id, member_id, SLOT_NAME)


def test_a_bare_installation_id_in_the_slot_mints_nothing(db: None) -> None:
    """The end the attack would come through: an owner (or an agent talking one into it) filling the
    installation slot by hand through the ordinary credential prompt. The value never opens, so no
    token is minted against it."""
    fernet = Fernet(Fernet.generate_key())
    with pytest.raises(CredentialRequestInvalid):
        open_installation(fernet, uuid4(), SLOT_NAME, "149082716")


def _git_slot(source: object | None) -> tuple[CredentialSlot, ...]:
    return injecting_slots(
        (
            Manifest(
                name="git",
                version="1",
                credentials=(
                    CredentialSlot(
                        name="github_git_token",
                        description="git token",
                        source=source,
                        injection=InjectionTarget(
                            host="github.com",
                            header="Authorization",
                            sentinel="SENTINEL_GIT",
                            dimension="requests",
                            git_basic_user="x-access-token",
                        ),
                    ),
                ),
            ),
        )
    )


async def test_a_minted_secret_reaches_the_proxy_rule_the_wire_uses(db: None) -> None:
    """Both ends: the value a source mints must be the one the proxy actually injects, not just what
    `slot_secret` returns. The member's stored token is present too, so a rule carrying it would
    prove the mint never reached the wire."""
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "github_git_token", "member-pat")
    rules = await derive_credential_rules(
        _git_slot(_MintedTokens(workspace_id)), workspace_id, store
    )

    (injection,) = [rule for rule in rules if isinstance(rule, InjectionRule)]
    assert (
        injection.real == f"Basic {b64encode(b'x-access-token:minted-installation-token').decode()}"
    )


async def test_a_workspace_that_did_not_install_the_app_is_set_by_its_own_token(db: None) -> None:
    """The deploy wires the App as the source for every workspace, so a workspace that never
    installed it still reaches a source that reports no binding. That is the member-token case the
    slot exists to keep working: unbound, the stored token is what makes the slot set, and the
    sandbox configures git off it exactly as it would off a minted one."""

    class _Unbound:
        def __init__(self) -> None:
            self.mints = 0

        async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
            self.mints += 1
            return None

        async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
            return False

    workspace_id = await _workspace()
    store = _store()
    source = _Unbound()

    assert await slot_is_set("github_git_token", source, workspace_id, store) is False

    await store.put(workspace_id, "github_git_token", "member-pat")

    assert await slot_is_set("github_git_token", source, workspace_id, store) is True
    assert await slot_secret("github_git_token", source, workspace_id, store) == "member-pat"
    assert source.mints == 1


async def test_a_mint_failure_withholds_only_that_host(db: None) -> None:
    """A source reaches a provider, so it can fail while the rest of the turn is fine. The host's
    rules are withheld rather than the derivation aborting — which would fail a turn that never
    touches the credential — and it never falls back to the stored token, which would authenticate
    as an identity the workspace did not bind."""

    class _Failing:
        async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
            raise CredentialMintFailed("github is down")

        async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
            return True

    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "github_git_token", "member-pat")
    await store.put(workspace_id, "datadog_api_key", "dd-api-real")
    await store.put(workspace_id, "datadog_application_key", "dd-app-real")
    await store.put(workspace_id, "datadog_api_host", DATADOG_HOST)
    slots = (*_git_slot(_Failing()), *injecting_slots((_keyed_manifest(),)))

    rules = await derive_credential_rules(slots, workspace_id, store)

    hosts = {rule.host for rule in rules if isinstance(rule, InjectionRule)}
    assert hosts == {DATADOG_HOST}
    assert ScopeRule(allowed_hosts=frozenset({DATADOG_HOST})) in rules
    assert not [
        rule for rule in rules if isinstance(rule, ScopeRule) and "github.com" in rule.allowed_hosts
    ]


async def test_any_slot_fault_withholds_its_own_host_and_leaves_the_rest_deriving(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The property that makes this derivation safe to compose: it is total. A slot's resolution is
    isolated per slot, not per fault class, so a fault nobody anticipated withholds that one host
    while every other slot still derives.

    The alternative is what a stale installation binding did once: escape the derivation, unwind the
    public-internet and grant rules composed around the call, and leave the workspace refusing every
    host but the model provider on every turn — silently, since the proxy fails closed to its base.
    Aborting was never the loud option; it was the total one.

    The fault class rides the event as data rather than branching the code, because withholding is
    the same act however the slot failed. An operator filters `error_class` to tell a provider that
    will come back from a stored value that needs a rebind."""

    class _Exploding:
        def __init__(self, error: Exception) -> None:
            self.error = error

        async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
            raise self.error

        async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
            return True

    for error in (
        CredentialRequestInvalid("stored seal is not this deploy's own"),
        CredentialMintFailed("github is unreachable"),
        RuntimeError("a fault this deploy never anticipated"),
    ):
        workspace_id = await _workspace()
        store = _store()
        await store.put(workspace_id, "github_git_token", "member-pat")
        await store.put(workspace_id, "datadog_api_key", "dd-api-real")
        await store.put(workspace_id, "datadog_application_key", "dd-app-real")
        await store.put(workspace_id, "datadog_api_host", DATADOG_HOST)
        slots = (*_git_slot(_Exploding(error)), *injecting_slots((_keyed_manifest(),)))

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="ufo"):
            rules = await derive_credential_rules(slots, workspace_id, store)

        assert {rule.host for rule in rules if isinstance(rule, InjectionRule)} == {DATADOG_HOST}
        assert ScopeRule(allowed_hosts=frozenset({DATADOG_HOST})) in rules
        assert not [
            rule
            for rule in rules
            if isinstance(rule, ScopeRule) and "github.com" in rule.allowed_hosts
        ]
        withheld = [
            record.ufo
            for record in caplog.records
            if record.getMessage() == "egress.credential_slot_failed"
        ]
        assert [entry["slot"] for entry in withheld] == ["github_git_token"]
        assert withheld[0]["error_class"] == type(error).__name__
