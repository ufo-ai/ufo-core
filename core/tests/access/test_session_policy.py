import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.harness.sandbox.preview import PREVIEW_HOST
from ufo.harness.sandbox.session import RunToken, RunTokenCodec
from ufo.proxy_serve import model_bindings
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.credentials import CredentialStore, HostChoice
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    CONNECTION_SECRET_PREFIX,
    RUN_HEADER,
    UFO_MODELS_SECRET,
    Bind,
    ConnectorTransferHosts,
    HostEntry,
    PolicyScope,
    Route,
    SessionPolicy,
    connector_transfer_hosts,
    derive_artifact_store_hosts,
    derive_cli_binds,
    derive_grant_hosts,
    derive_manifest_internet,
    policy_hosts,
)
from ufo.runtime.access.grants import Grant, GrantStore, cli_accounts
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.manifest import ConnectorProvider, CredentialSlot, InjectionTarget, Manifest
from ufo.runtime.tools.bridge import TOOL_BRIDGE_HOST
from ufo.runtime.workspace import ws
from ufo.schema import tables

PROXY_SESSION_CONTRACT = Path(__file__).parent / "proxy_session_contract.json"
RUN_TOKENS = RunTokenCodec(b"session-policy-golden-secret")
WORKSPACE_ID = UUID("0b6e2f4a-1c3d-4e5f-8a9b-0c1d2e3f4a5b")
MEMBER_ID = UUID("1c7f3a5b-2d4e-4f60-9b0c-1d2e3f4a5b6c")
AGENT_ID = UUID("2d803b6c-3e5f-4071-8c1d-2e3f4a5b6c7d")
TURN_ID = UUID("3e914c7d-4f60-4182-9d2e-3f4a5b6c7d8e")
CONNECTION_ID = UUID("3f0a1d5e-7c8b-4a2e-9d61-2b7c4e8f0a11")
ACME_HOST = "api.acmekeys.com"
ACME_SLOT = CredentialSlot(
    name="acme_api_key",
    description="Acme key the proxy service binds on its API host.",
    injection=InjectionTarget(
        host=ACME_HOST,
        header="x-api-key",
        sentinel="UFO_SENTINEL_KEYED_ACME_API_KEY",
        env="ACME_KEY",
        dimension="requests",
    ),
)
ACME_SECRET = "acme-real-secret"
ANTHROPIC_SECRET = "sk-ant-real-key"
OPENAI_SECRET = "sk-openai-real-key"
GITHUB = "github"
GITHUB_HOST = "api.github.com"
GIT = GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential")
SERVE_URL = "https://serve.test"
BRIDGE_UPSTREAM = f"{SERVE_URL}/internal/egress/tool-bridge"
PREVIEW_UPSTREAM = f"{SERVE_URL}/internal/egress/preview"
CONFIG = Config(
    database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
    blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
)


@dataclass(frozen=True)
class _Tokens:
    asked: list[tuple[UUID, str]] = field(default_factory=list)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        self.asked.append((workspace_id, account_id))
        return f"token-{account_id}"


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


def _cli(tokens: _Tokens) -> CliCredential:
    return CliCredential(env="GH_TOKEN", header="authorization", secret=tokens, git=GIT)


def _run_token() -> str:
    return RUN_TOKENS.encode(RunToken(WORKSPACE_ID, TURN_ID))


def _scope(
    *,
    member_id: UUID | None = None,
    internet: bool = True,
    running: bool = True,
    run_token: str | None = None,
) -> PolicyScope:
    return PolicyScope(WORKSPACE_ID, member_id, internet, running, run_token)


async def _seed() -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=WORKSPACE_ID, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=AGENT_ID,
                workspace_id=WORKSPACE_ID,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                internet_access_allowed=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _member(MEMBER_ID, "owner@work.com")


async def _member(member_id: UUID, email: str) -> UUID:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=WORKSPACE_ID,
                email=email,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _record(account_id: str, owner: UUID, *, shared: bool, host: str = GITHUB_HOST) -> UUID:
    with ws(WORKSPACE_ID), agent(AGENT_ID):
        return await GrantStore().record(
            provider=GITHUB,
            account_id=account_id,
            host=host,
            grantor_member_id=owner,
            shared=shared,
        )


async def _seed_github_connection() -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=CONNECTION_ID,
                workspace_id=WORKSPACE_ID,
                provider=GITHUB,
                account_id="acct-gh",
                host=GITHUB_HOST,
                owner_member_id=MEMBER_ID,
                shared=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    assert await _record("acct-gh", MEMBER_ID, shared=True) == CONNECTION_ID


async def _deploy_rules(
    monkeypatch: pytest.MonkeyPatch, store: CredentialStore, tokens: _Tokens
) -> PerAgentRules:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_SECRET)
    monkeypatch.delenv("UFO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UFO_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "session-policy-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "session-policy-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    model_hosts, model_binds = model_bindings(CONFIG)
    artifact_hosts = await derive_artifact_store_hosts(
        S3BlobStore(
            bucket="ufo-blobs", endpoint_url="https://files.example.test", region="us-east-1"
        )
    )
    return PerAgentRules(
        hosts=(*model_hosts, *artifact_hosts),
        binds=model_binds,
        grants=GrantStore(),
        credentials=store,
        slots=WorkspaceSlots(deploy=(ACME_SLOT,)),
        internet=True,
        clis={GITHUB: _cli(tokens)},
        bridge_upstream=BRIDGE_UPSTREAM,
    )


async def _compile(rules: PerAgentRules, scope: PolicyScope) -> SessionPolicy:
    with ws(scope.workspace_id), agent(AGENT_ID):
        return await rules.session_policy(scope)


async def _seed_deploy(monkeypatch: pytest.MonkeyPatch, tokens: _Tokens) -> PerAgentRules:
    store = _store()
    await _seed()
    await _seed_github_connection()
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, ACME_SLOT.name, ACME_SECRET)
    return await _deploy_rules(monkeypatch, store, tokens)


async def test_a_running_turn_compiles_hosts_binds_and_routes_by_name(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = _Tokens()
    rules = await _seed_deploy(monkeypatch, tokens)

    policy = await _compile(rules, _scope(run_token=_run_token()))

    connection = f"{CONNECTION_SECRET_PREFIX}{CONNECTION_ID}"
    assert policy == SessionPolicy(
        internet=True,
        hosts=policy_hosts(
            ACME_HOST,
            "api.anthropic.com",
            GITHUB_HOST,
            "api.openai.com",
            "files.example.test",
            GIT.host,
        ),
        bind=(
            Bind(host=ACME_HOST, header="x-api-key", secret="acme_api_key", env="ACME_KEY"),
            Bind(
                host="api.anthropic.com",
                header="x-api-key",
                secret=UFO_MODELS_SECRET,
                env="ANTHROPIC_API_KEY",
            ),
            Bind(host=GITHUB_HOST, header="authorization", secret=connection, env="GH_TOKEN"),
            Bind(
                host="api.openai.com",
                header="authorization",
                secret=UFO_MODELS_SECRET,
                env="OPENAI_API_KEY",
            ),
            Bind(host=GIT.host, header="authorization", secret=connection, env="GH_TOKEN"),
        ),
        routes=(
            Route(
                host=TOOL_BRIDGE_HOST,
                upstream=BRIDGE_UPSTREAM,
                headers={RUN_HEADER: _run_token()},
            ),
        ),
    )
    contract = json.loads(PROXY_SESSION_CONTRACT.read_text())
    assert contract["create_request"]["policy"] == policy.model_dump(mode="json")
    wire = policy.model_dump_json()
    assert "real" not in wire
    assert not any(
        value in wire for value in (ACME_SECRET, ANTHROPIC_SECRET, OPENAI_SECRET, "token-acct-gh")
    )
    assert tokens.asked == []


async def test_a_detached_or_probe_scope_binds_no_model_key_and_no_route(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = await _seed_deploy(monkeypatch, _Tokens())

    running = await _compile(rules, _scope(run_token=_run_token()))
    detached = await _compile(rules, _scope(running=False, run_token=_run_token()))
    probe = await _compile(rules, _scope(running=False))

    assert {bind.secret for bind in running.bind} >= {UFO_MODELS_SECRET}
    for narrowed in (detached, probe):
        assert narrowed.routes == ()
        assert narrowed.bind == tuple(
            bind for bind in running.bind if bind.secret != UFO_MODELS_SECRET
        )
        assert narrowed.hosts == running.hosts


async def test_a_private_grant_binds_for_its_owner_alone(db: None) -> None:
    await _seed()
    other = await _member(uuid4(), "other@work.com")
    owned = await _record("acct-own", MEMBER_ID, shared=False)
    rules = PerAgentRules(grants=GrantStore(), clis={GITHUB: _cli(_Tokens())})
    secret = f"{CONNECTION_SECRET_PREFIX}{owned}"

    owner = await _compile(rules, _scope(member_id=MEMBER_ID))
    another = await _compile(rules, _scope(member_id=other))
    nobody = await _compile(rules, _scope())

    assert owner.bind == (
        Bind(host=GITHUB_HOST, header="authorization", secret=secret, env="GH_TOKEN"),
        Bind(host=GIT.host, header="authorization", secret=secret, env="GH_TOKEN"),
    )
    assert another.bind == nobody.bind == ()
    assert another.hosts == nobody.hosts == ()

    await _record("acct-own-2", MEMBER_ID, shared=False)
    with ws(WORKSPACE_ID), agent(AGENT_ID):
        granted = await GrantStore().active_grants()
    assert cli_accounts(granted, GITHUB, MEMBER_ID) == ("acct-own", "acct-own-2")
    assert (await _compile(rules, _scope(member_id=MEMBER_ID))).bind == ()


async def test_a_slot_with_no_stored_value_binds_nothing(db: None) -> None:
    await _seed()
    store = _store()
    rules = PerAgentRules(credentials=store, slots=WorkspaceSlots(deploy=(ACME_SLOT,)))

    empty = await _compile(rules, _scope())
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, ACME_SLOT.name, ACME_SECRET)
    filled = await _compile(rules, _scope())

    assert (empty.hosts, empty.bind) == ((), ())
    assert filled.bind == (
        Bind(host=ACME_HOST, header="x-api-key", secret=ACME_SLOT.name, env="ACME_KEY"),
    )
    assert filled.hosts == policy_hosts(ACME_HOST)


async def test_a_slot_whose_host_choice_is_unoffered_binds_nothing(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed()
    store = _store()
    sites = HostChoice(
        slot="acme_api_host",
        description="Acme site.",
        hosts=(ACME_HOST, "eu.acmekeys.com"),
        default=ACME_HOST,
        env="ACME_HOST",
    )
    keyed = CredentialSlot(
        name="acme_api_key",
        description="Acme key.",
        injection=InjectionTarget(
            host=sites, header="x-api-key", sentinel="S_ACME", env="ACME_KEY"
        ),
    )
    companion = CredentialSlot(name=sites.slot, description=sites.description)
    rules = PerAgentRules(credentials=store, slots=WorkspaceSlots(deploy=(keyed, companion)))
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, keyed.name, ACME_SECRET)
        await store.put(WORKSPACE_ID, sites.slot, "169.254.169.254")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        unoffered = await _compile(rules, _scope())
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, sites.slot, "eu.acmekeys.com")
    offered = await _compile(rules, _scope())

    assert (unoffered.hosts, unoffered.bind) == ((), ())
    warned = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.credential_host_unavailable"
    ]
    assert [entry["slot"] for entry in warned] == [keyed.name]
    assert not any("169.254" in str(entry) for entry in warned)
    assert offered.bind == (
        Bind(host="eu.acmekeys.com", header="x-api-key", secret=keyed.name, env="ACME_KEY"),
    )


def _withheld(caplog: pytest.LogCaptureFixture) -> list[tuple[object, ...]]:
    return [
        (record.ufo["host"], record.ufo["header"], record.ufo["withheld"], record.ufo["kept"])
        for record in caplog.records
        if record.getMessage() == "egress.bind_withheld"
    ]


async def test_the_model_key_holds_its_host_and_header_against_a_slot_while_the_turn_runs(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    own = CredentialSlot(
        name="own_openai_key",
        description="The workspace's own OpenAI key.",
        injection=InjectionTarget(
            host="api.openai.com", header="Authorization", sentinel="S_OWN", env="OWN_OPENAI_KEY"
        ),
    )
    store = _store()
    await _seed()
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, own.name, "sk-own-openai")
    rules = replace(
        await _deploy_rules(monkeypatch, store, _Tokens()), slots=WorkspaceSlots(deploy=(own,))
    )

    with caplog.at_level(logging.WARNING, logger="ufo"):
        running = await _compile(rules, _scope(run_token=_run_token()))
    detached = await _compile(rules, _scope(running=False))

    assert [bind for bind in running.bind if bind.host == "api.openai.com"] == [
        Bind(
            host="api.openai.com",
            header="authorization",
            secret=UFO_MODELS_SECRET,
            env="OPENAI_API_KEY",
        )
    ]
    assert _withheld(caplog) == [("api.openai.com", "authorization", own.name, UFO_MODELS_SECRET)]
    assert detached.bind == (
        Bind(host="api.openai.com", header="authorization", secret=own.name, env="OWN_OPENAI_KEY"),
    )


async def test_two_slots_on_one_host_and_header_bind_the_one_listed_first(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    staging = CredentialSlot(
        name="acme_staging_key",
        description="Acme staging key.",
        injection=InjectionTarget(
            host=ACME_HOST, header="X-Acme-Key", sentinel="S_STAGING", env="ACME_STAGING_KEY"
        ),
    )
    prod = CredentialSlot(
        name="acme_prod_key",
        description="Acme production key.",
        injection=InjectionTarget(
            host=ACME_HOST, header="x-acme-key", sentinel="S_PROD", env="ACME_PROD_KEY"
        ),
    )
    await _seed()
    store = _store()
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, staging.name, "staging-secret")
        await store.put(WORKSPACE_ID, prod.name, "prod-secret")
    rules = PerAgentRules(credentials=store, slots=WorkspaceSlots(deploy=(staging, prod)))

    with caplog.at_level(logging.WARNING, logger="ufo"):
        policy = await _compile(rules, _scope())

    assert policy.bind == (
        Bind(host=ACME_HOST, header="x-acme-key", secret=staging.name, env="ACME_STAGING_KEY"),
    )
    assert _withheld(caplog) == [(ACME_HOST, "x-acme-key", prod.name, staging.name)]


async def test_a_slot_holds_a_cli_accounts_api_host_and_its_git_host_stays_bound(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    bot = CredentialSlot(
        name="github_bot_token",
        description="A GitHub bot token.",
        injection=InjectionTarget(
            host=GITHUB_HOST, header="Authorization", sentinel="S_BOT", env="GITHUB_BOT_TOKEN"
        ),
    )
    await _seed()
    await _seed_github_connection()
    store = _store()
    with ws(WORKSPACE_ID):
        await store.put(WORKSPACE_ID, bot.name, "ghp-bot")
    rules = PerAgentRules(
        grants=GrantStore(),
        credentials=store,
        slots=WorkspaceSlots(deploy=(bot,)),
        clis={GITHUB: _cli(_Tokens())},
    )

    with caplog.at_level(logging.WARNING, logger="ufo"):
        policy = await _compile(rules, _scope(member_id=MEMBER_ID))

    connection = f"{CONNECTION_SECRET_PREFIX}{CONNECTION_ID}"
    assert policy.bind == (
        Bind(host=GITHUB_HOST, header="authorization", secret=bot.name, env="GITHUB_BOT_TOKEN"),
        Bind(host=GIT.host, header="authorization", secret=connection, env="GH_TOKEN"),
    )
    assert _withheld(caplog) == [(GITHUB_HOST, "authorization", connection, bot.name)]


async def test_a_cli_whose_git_host_is_its_api_host_binds_it_once(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed()
    connection_id = await _record("acct-forge", MEMBER_ID, shared=True, host="forge.test")
    forge = GitWire(host="forge.test", basic_user="oauth2", helper="!forge auth git-credential")
    cli = CliCredential(env="FORGE_TOKEN", header="authorization", secret=_Tokens(), git=forge)
    rules = PerAgentRules(grants=GrantStore(), clis={GITHUB: cli})

    with caplog.at_level(logging.WARNING, logger="ufo"):
        policy = await _compile(rules, _scope(member_id=MEMBER_ID))

    assert policy.bind == (
        Bind(
            host="forge.test",
            header="authorization",
            secret=f"{CONNECTION_SECRET_PREFIX}{connection_id}",
            env="FORGE_TOKEN",
        ),
    )
    assert _withheld(caplog) == []


async def test_internet_needs_the_deploy_and_the_agent() -> None:
    for deploy in (False, True):
        for allowed in (False, True):
            policy = await _compile(PerAgentRules(internet=deploy), _scope(internet=allowed))
            assert policy.internet is (deploy and allowed)


async def test_the_digest_is_stable_and_moves_with_a_grant(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = await _seed_deploy(monkeypatch, _Tokens())

    first = await _compile(rules, _scope(run_token=_run_token()))
    second = await _compile(rules, _scope(run_token=_run_token()))
    await _record("acct-new", MEMBER_ID, shared=True, host="api.github.example")
    moved = await _compile(rules, _scope(run_token=_run_token()))

    assert first.digest() == second.digest()
    assert len(first.digest()) == 64
    assert moved.digest() != first.digest()


async def test_no_public_base_url_compiles_no_route() -> None:
    token = _run_token()
    served = PerAgentRules(bridge_upstream=BRIDGE_UPSTREAM, preview_upstream=PREVIEW_UPSTREAM)
    unserved = PerAgentRules(preview_upstream=PREVIEW_UPSTREAM)

    routes = (await _compile(served, _scope(run_token=token))).routes

    assert routes == (
        Route(host=PREVIEW_HOST, upstream=PREVIEW_UPSTREAM, headers={RUN_HEADER: token}),
        Route(host=TOOL_BRIDGE_HOST, upstream=BRIDGE_UPSTREAM, headers={RUN_HEADER: token}),
    )
    assert (await _compile(unserved, _scope(run_token=token))).routes == ()
    assert (await _compile(served, _scope())).routes == ()


def test_policy_hosts_are_sorted_unique_and_never_empty() -> None:
    assert policy_hosts("b.test", "", "a.test", "b.test") == (
        HostEntry(host="a.test"),
        HostEntry(host="b.test"),
    )


def test_manifest_opens_public_internet_only_when_declared() -> None:
    closed = Manifest(name="closed", version="0")
    open_ = Manifest(name="open", version="0", sandbox_internet=True)
    assert derive_manifest_internet((closed,)) is False
    assert derive_manifest_internet((closed, open_)) is True


async def test_artifact_store_hosts_name_the_s3_put_host_and_nothing_on_a_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "session-policy-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "session-policy-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    store = S3BlobStore(bucket="ufo-blobs", region="us-west-2")

    assert await derive_artifact_store_hosts(store) == policy_hosts(
        "ufo-blobs.s3.us-west-2.amazonaws.com"
    )
    assert await derive_artifact_store_hosts(FilesystemBlobStore(root=tmp_path)) == ()


ACTING = uuid4()
OTHER = uuid4()
TRANSFER = ("files.broker.example.com",)


def _grant(
    account: str = "acct-1",
    owner: UUID = ACTING,
    shared: bool = False,
    host: str = "api.hub.test",
    provider: str = "hub",
) -> Grant:
    return Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider=provider,
        account_id=account,
        host=host,
        owner_member_id=owner,
        owner_email="acting@x.test",
        connection_shared=shared,
    )


def test_a_grant_admits_the_members_own_hosts_and_the_shared_ones() -> None:
    own = _grant(host="api.own.test")
    foreign = _grant(account="acct-2", owner=OTHER, host="api.foreign.test")
    shared = _grant(account="acct-3", owner=OTHER, shared=True, host="api.shared.test")
    hosts = ConnectorTransferHosts({})

    assert derive_grant_hosts((own, foreign, shared), hosts, ACTING) == policy_hosts(
        own.host, shared.host
    )
    assert derive_grant_hosts((own, foreign, shared), hosts, None) == policy_hosts(shared.host)


def test_a_brokered_grant_admits_only_its_transfer_hosts_never_an_empty_host() -> None:
    brokered = _grant(host="", provider="notion")
    assert derive_grant_hosts(
        (brokered,), ConnectorTransferHosts({}, default=TRANSFER), ACTING
    ) == policy_hosts(*TRANSFER)


def test_an_explicit_mapping_wins_over_the_open_namespace_default() -> None:
    hosts = ConnectorTransferHosts({"hub": ("hub.files.example.com",)}, default=TRANSFER)
    assert derive_grant_hosts((_grant(),), hosts, ACTING) == policy_hosts(
        "api.hub.test", "hub.files.example.com"
    )


def test_a_cli_binds_the_acting_members_connection_on_its_api_and_git_hosts() -> None:
    tokens = _Tokens()
    grant = _grant()
    cli = CliCredential(env="HUB_TOKEN", header="authorization", secret=tokens, git=GIT)
    secret = f"{CONNECTION_SECRET_PREFIX}{grant.connection_id}"

    assert derive_cli_binds((grant,), {"hub": cli}, ACTING) == (
        Bind(host="api.hub.test", header="authorization", secret=secret, env="HUB_TOKEN"),
        Bind(host=GIT.host, header="authorization", secret=secret, env="HUB_TOKEN"),
    )
    assert tokens.asked == []


def test_no_cli_bind_for_another_members_grant_or_an_undeclared_cli() -> None:
    cli = CliCredential(env="HUB_TOKEN", header="authorization", secret=_Tokens())
    assert derive_cli_binds((_grant(owner=OTHER),), {"hub": cli}, ACTING) == ()
    assert derive_cli_binds((_grant(owner=OTHER),), {"hub": cli}, None) == ()
    assert derive_cli_binds((_grant(),), {}, ACTING) == ()


def test_cli_binds_prefer_the_members_private_account_and_fall_back_to_shared() -> None:
    mine = _grant()
    shared = _grant(account="acct-2", owner=OTHER, shared=True)
    theirs = _grant(account="acct-3", owner=OTHER)
    cli = {"hub": CliCredential(env="HUB_TOKEN", header="authorization", secret=_Tokens())}

    def bound(member_id: UUID | None) -> set[str]:
        return {bind.secret for bind in derive_cli_binds((mine, shared, theirs), cli, member_id)}

    assert bound(None) == {f"{CONNECTION_SECRET_PREFIX}{shared.connection_id}"}
    assert bound(ACTING) == {f"{CONNECTION_SECRET_PREFIX}{mine.connection_id}"}
    assert bound(OTHER) == {f"{CONNECTION_SECRET_PREFIX}{theirs.connection_id}"}


@dataclass(frozen=True)
class _CliOAuth:
    provider: str = "hub"
    host: str = "api.hub.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return "https://hub.test/oauth"

    async def exchange(self, code, redirect_uri, workspace_id, state):
        raise NotImplementedError


@dataclass(frozen=True)
class _Namespace:
    transfer_hosts: tuple[str, ...]


def test_transfer_hosts_default_reaches_only_unregistered_providers() -> None:
    hostful = ConnectorProvider(
        oauth=_CliOAuth(provider="hub", host="api.hub.test"),
        label="Hub",
        broker=object(),
        transfer_hosts=("files.hub.test",),
    )
    hostless = ConnectorProvider(
        oauth=_CliOAuth(provider="quiet", host="api.quiet.test"), label="Quiet", broker=object()
    )
    manifest = Manifest(
        name="t",
        version="0",
        connectors=(hostful, hostless),
        connector_resolver=_Namespace(("cdn.broker.test",)),
    )
    hosts = connector_transfer_hosts((manifest,))
    assert hosts.of("hub") == ("files.hub.test",)
    assert hosts.of("quiet") == ()
    assert hosts.of("unregistered") == ("cdn.broker.test",)


def test_transfer_hosts_fail_loud_on_two_open_namespaces() -> None:
    a = Manifest(name="a", version="0", connector_resolver=_Namespace(("cdn.a.test",)))
    b = Manifest(name="b", version="0", connector_resolver=_Namespace(("cdn.b.test",)))
    with pytest.raises(RuntimeError, match="two extensions register an open connector namespace"):
        connector_transfer_hosts((a, b))
