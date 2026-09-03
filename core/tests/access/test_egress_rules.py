import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.egress_rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    REQUEST_METER_DIMENSION,
    SENTINEL_MODEL_KEY,
    ConnectorTransferHosts,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    connector_transfer_hosts,
    derive_artifact_store_rules,
    derive_cli_rules,
    derive_grant_rules,
    derive_manifest_rules,
    derive_model_rules,
)
from ufo.runtime.access.grants import Grant, grant_sentinel
from ufo.runtime.authority import WORKSPACE_AUTHORITY, MemberAuthority
from ufo.runtime.ext.manifest import ConnectorProvider, Manifest


def test_manifest_derives_public_internet_only_when_declared() -> None:
    closed = Manifest(name="closed", version="0")
    open_ = Manifest(name="open", version="0", sandbox_internet=True)
    assert derive_manifest_rules((closed,)) == ()
    assert derive_manifest_rules((closed, open_)) == (InternetRule(),)


async def test_artifact_store_rules_scope_the_s3_host_exactly_and_meter_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sharing a file is the sandbox PUTting it to a presigned URL, so the store's host must be
    admitted by an exact scope: an agent narrowed off the public internet still shares files, and
    only an exactly-scoped host is tunnelled opaquely, which a query-signed request needs. Nothing
    is injected — the URL carries its own authority."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "rules-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "rules-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    store = S3BlobStore(bucket="ufo-blobs", region="us-west-2")
    host = await store.put_host()

    rules = await derive_artifact_store_rules(store)

    assert host == "ufo-blobs.s3.us-west-2.amazonaws.com"
    assert rules == (
        ScopeRule(allowed_hosts=frozenset({host})),
        MeterRule(host=host, dimension=REQUEST_METER_DIMENSION),
    )


async def test_artifact_store_rules_admit_nothing_for_the_filesystem_backend(
    tmp_path: Path,
) -> None:
    """No URL to sign and no host to reach: a share on the filesystem backend streams out through
    the carrier, so it opens no egress at all."""
    assert await derive_artifact_store_rules(FilesystemBlobStore(root=tmp_path)) == ()


def test_derive_scopes_injects_and_meters_anthropic_via_x_api_key() -> None:
    rules = derive_model_rules("claude-opus-4-8", "sk-real-abc")
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    injection = next(r for r in rules if isinstance(r, InjectionRule))
    meter = next(r for r in rules if isinstance(r, MeterRule))
    assert scope.allowed_hosts == frozenset({ANTHROPIC_HOST})
    assert injection.host == ANTHROPIC_HOST
    assert injection.header == "x-api-key"
    assert injection.sentinel == SENTINEL_MODEL_KEY
    assert injection.real == "sk-real-abc"
    assert meter == MeterRule(host=ANTHROPIC_HOST, dimension="tokens")


def test_only_the_provider_host_is_allowed() -> None:
    rules = derive_model_rules("gpt-5.4", "sk-x")
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert "example.com" not in scope.allowed_hosts
    assert scope.allowed_hosts == frozenset({OPENAI_HOST})


@dataclass(frozen=True)
class _Tokens:
    """The broker's token read: deterministic per account, recorded per call, and refused for the
    accounts in `broken` — the one grant fault the derivation has to survive."""

    broken: frozenset[str] = frozenset()
    asked: list[tuple[UUID, str]] = field(default_factory=list)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        self.asked.append((workspace_id, account_id))
        if account_id in self.broken:
            raise RuntimeError(f"broker cannot authenticate {account_id}")
        return f"token-{account_id}"


CLI_HOST = "api.hub.test"
GIT = GitWire(host="github.test", basic_user="x-access-token", helper="!hub auth git-credential")
ACTING = uuid4()
OTHER = uuid4()
WORKSPACE = uuid4()


def _cli(tokens: _Tokens, git: GitWire | None = None) -> CliCredential:
    return CliCredential(env="HUB_TOKEN", header="authorization", secret=tokens, git=git)


def _grant(account: str = "acct-1", grantor=ACTING, shared: bool = False) -> Grant:
    return Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="hub",
        account_id=account,
        host=CLI_HOST,
        owner_member_id=grantor,
        owner_email="acting@x.test",
        connection_shared=shared,
    )


TRANSFER = ("files.broker.example.com",)


def test_brokered_grant_admits_only_its_transfer_hosts_never_an_empty_host() -> None:
    """A grant with no provider host (server-side execution) admits only its broker file-store hosts
    from the open namespace default — never a ScopeRule allowing the empty host."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="notion",
        account_id="a",
        host="",
        owner_member_id=ACTING,
        owner_email="acting@x.test",
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,), ConnectorTransferHosts({}, default=TRANSFER))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset(TRANSFER)
    assert "" not in scope.allowed_hosts
    assert MeterRule(host=TRANSFER[0], dimension=REQUEST_METER_DIMENSION) in rules


def test_an_explicit_mapping_wins_over_the_open_namespace_default() -> None:
    hosts = ConnectorTransferHosts({"hub": ("hub.files.example.com",)}, default=TRANSFER)
    rules = derive_grant_rules((_grant(),), hosts)
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({CLI_HOST, "hub.files.example.com"})
    assert TRANSFER[0] not in scope.allowed_hosts


def test_grant_sentinel_is_deterministic_per_account() -> None:
    assert grant_sentinel("acct-1") == grant_sentinel("acct-1")
    assert grant_sentinel("acct-1") != grant_sentinel("acct-2")
    assert "acct-1" in grant_sentinel("acct-1")


async def test_cli_rule_injects_the_acting_members_own_token_on_the_grant_host() -> None:
    tokens = _Tokens()
    rules = await derive_cli_rules(
        (_grant(),), MemberAuthority(ACTING), {"hub": _cli(tokens)}, WORKSPACE
    )
    assert rules == (
        InjectionRule(
            host=CLI_HOST,
            header="authorization",
            sentinel=grant_sentinel("acct-1"),
            real="token-acct-1",
        ),
    )
    assert tokens.asked == [(WORKSPACE, "acct-1")]


async def test_no_cli_rule_for_a_foreign_private_grant() -> None:
    tokens = _Tokens()
    rules = await derive_cli_rules(
        (_grant(grantor=OTHER),), MemberAuthority(ACTING), {"hub": _cli(tokens)}, WORKSPACE
    )
    assert rules == ()
    assert tokens.asked == []


async def test_no_cli_rule_for_a_provider_without_a_declared_cli() -> None:
    assert await derive_cli_rules((_grant(),), MemberAuthority(ACTING), {}, WORKSPACE) == ()


async def test_workspace_authority_injects_shared_grants_and_a_member_adds_its_own() -> None:
    grants = (
        _grant(),
        _grant(account="acct-2", grantor=OTHER, shared=True),
        _grant(account="acct-3", grantor=OTHER),
    )
    clis = {"hub": _cli(_Tokens())}

    memberless = await derive_cli_rules(grants, WORKSPACE_AUTHORITY, clis, WORKSPACE)
    acting = await derive_cli_rules(grants, MemberAuthority(ACTING), clis, WORKSPACE)

    assert [r.sentinel for r in memberless] == [grant_sentinel("acct-2")]
    assert [r.sentinel for r in acting] == [grant_sentinel("acct-1"), grant_sentinel("acct-2")]


async def test_a_git_wire_scopes_meters_and_injects_basic_on_the_git_host_once() -> None:
    """The API host takes the raw token as the CLI sends it; the git host takes the same token as
    the password half of a Basic credential, under the same sentinel. Two usable accounts inject
    twice on the git host but scope and meter it once — a request is one request however many
    accounts could authenticate it."""
    grants = (_grant(), _grant(account="acct-2", grantor=OTHER, shared=True))
    rules = await derive_cli_rules(
        grants, MemberAuthority(ACTING), {"hub": _cli(_Tokens(), git=GIT)}, WORKSPACE
    )
    assert rules == (
        InjectionRule(
            host=CLI_HOST,
            header="authorization",
            sentinel=grant_sentinel("acct-1"),
            real="token-acct-1",
        ),
        InjectionRule(
            host=CLI_HOST,
            header="authorization",
            sentinel=grant_sentinel("acct-2"),
            real="token-acct-2",
        ),
        ScopeRule(allowed_hosts=frozenset({GIT.host})),
        InjectionRule(
            host=GIT.host,
            header="authorization",
            sentinel=grant_sentinel("acct-1"),
            real="token-acct-1",
        ),
        InjectionRule(
            host=GIT.host,
            header="authorization",
            sentinel=grant_sentinel("acct-2"),
            real="token-acct-2",
        ),
        MeterRule(host=GIT.host, dimension=REQUEST_METER_DIMENSION),
    )


async def test_a_grant_whose_token_the_broker_refuses_is_withheld_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    grants = (_grant(), _grant(account="acct-2", grantor=OTHER, shared=True))
    clis = {"hub": _cli(_Tokens(broken=frozenset({"acct-1"})), git=GIT)}

    with caplog.at_level(logging.WARNING, logger="ufo"):
        rules = await derive_cli_rules(grants, MemberAuthority(ACTING), clis, WORKSPACE)

    assert [r.sentinel for r in rules if isinstance(r, InjectionRule)] == [
        grant_sentinel("acct-2"),
        grant_sentinel("acct-2"),
    ]
    assert ScopeRule(allowed_hosts=frozenset({GIT.host})) in rules
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.cli_credential_failed"
    ]
    assert [(entry["provider"], entry["account_id"]) for entry in withheld] == [("hub", "acct-1")]
    assert withheld[0]["error_class"] == "RuntimeError"


@dataclass(frozen=True)
class _CliOAuth:
    provider: str = "hub"
    host: str = CLI_HOST

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return "https://hub.test/oauth"

    async def exchange(self, code, redirect_uri, workspace_id, state):
        raise NotImplementedError


@dataclass(frozen=True)
class _Namespace:
    transfer_hosts: tuple[str, ...]


def test_transfer_hosts_default_reaches_only_unregistered_providers() -> None:
    """A registered connector that declares no transfer hosts admits none — never the open
    namespace's default, which is for a slug no connector registered. Distinguishing the two is the
    whole point of keying every registered provider, so a hostless connector cannot silently inherit
    the broker's file-store egress scope."""
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
    """The transfer-host derivation routes through the same one-namespace guard as the connect flow
    and registry, so a second open namespace fails loud here too rather than silently scoping every
    unregistered slug to the first-declared broker's file store."""
    a = Manifest(name="a", version="0", connector_resolver=_Namespace(("cdn.a.test",)))
    b = Manifest(name="b", version="0", connector_resolver=_Namespace(("cdn.b.test",)))
    with pytest.raises(RuntimeError, match="two extensions register an open connector namespace"):
        connector_transfer_hosts((a, b))
