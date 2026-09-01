from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.host.ext.loader import connector_clis
from ufo.runtime.access.connectors import CliCredential, ForwardedResponse
from ufo.runtime.access.egress_rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    REQUEST_METER_DIMENSION,
    SENTINEL_MODEL_KEY,
    ConnectorTransferHosts,
    ForwardRule,
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
    provider_host,
)
from ufo.runtime.access.grants import Grant, grant_sentinel
from ufo.runtime.authority import WORKSPACE_AUTHORITY, MemberAuthority
from ufo.runtime.ext.manifest import ConnectorProvider, Manifest


def test_provider_host_by_prefix() -> None:
    assert provider_host("claude-opus-4-8") == ANTHROPIC_HOST
    assert provider_host("gpt-5.4") == OPENAI_HOST


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


def test_unknown_model_has_no_host() -> None:
    with pytest.raises(ValueError, match="grok-9"):
        provider_host("grok-9")


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


def test_openai_injects_a_bearer_token_on_authorization() -> None:
    injection = next(
        r for r in derive_model_rules("gpt-5.4", "sk-x") if isinstance(r, InjectionRule)
    )
    assert injection.host == OPENAI_HOST
    assert injection.header == "authorization"
    assert injection.sentinel == f"Bearer {SENTINEL_MODEL_KEY}"
    assert injection.real == "Bearer sk-x"


def test_only_the_provider_host_is_allowed() -> None:
    rules = derive_model_rules("gpt-5.4", "sk-x")
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert "example.com" not in scope.allowed_hosts
    assert scope.allowed_hosts == frozenset({OPENAI_HOST})


@dataclass(frozen=True)
class _EchoForwarder:
    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        return ForwardedResponse(status=200, headers={}, body=b"")


CLI_HOST = "api.hub.test"
CLI = CliCredential(env="HUB_TOKEN", header="authorization", forward=_EchoForwarder())
ACTING = uuid4()
OTHER = uuid4()


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


def test_grant_with_a_provider_host_admits_and_meters_it() -> None:
    rules = derive_grant_rules((_grant(),))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({CLI_HOST})
    assert MeterRule(host=CLI_HOST, dimension=REQUEST_METER_DIMENSION) in rules


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


def test_brokered_grant_without_any_host_derives_no_scope_rule() -> None:
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
    assert derive_grant_rules((grant,)) == ()


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


def test_cli_rule_for_the_acting_members_own_grant() -> None:
    rules = derive_cli_rules((_grant(),), MemberAuthority(ACTING), {"hub": CLI})
    forward = next(r for r in rules if isinstance(r, ForwardRule))
    assert forward.host == CLI_HOST
    assert forward.header == "authorization"
    assert forward.sentinel == grant_sentinel("acct-1")
    assert forward.account_id == "acct-1"
    assert forward.forward is CLI.forward


def test_cli_rule_for_a_shared_grant_of_another_member() -> None:
    rules = derive_cli_rules(
        (_grant(grantor=OTHER, shared=True),), MemberAuthority(ACTING), {"hub": CLI}
    )
    assert any(isinstance(r, ForwardRule) for r in rules)


def test_no_cli_rule_for_a_foreign_private_grant() -> None:
    assert derive_cli_rules((_grant(grantor=OTHER),), MemberAuthority(ACTING), {"hub": CLI}) == ()


def test_no_cli_rule_for_a_provider_without_a_declared_cli() -> None:
    assert derive_cli_rules((_grant(),), MemberAuthority(ACTING), {}) == ()


def test_a_memberless_turn_forwards_only_shared_grants() -> None:
    grants = (_grant(), _grant(account="acct-2", grantor=OTHER, shared=True))
    rules = derive_cli_rules(grants, WORKSPACE_AUTHORITY, {"hub": CLI})
    accounts = [r.account_id for r in rules if isinstance(r, ForwardRule)]
    assert accounts == ["acct-2"]


@dataclass(frozen=True)
class _CliOAuth:
    provider: str = "hub"
    host: str = CLI_HOST

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return "https://hub.test/oauth"

    async def exchange(self, code, redirect_uri, workspace_id, state):
        raise NotImplementedError


def test_connector_clis_maps_only_declaring_providers() -> None:
    declaring = ConnectorProvider(oauth=_CliOAuth(), label="Hub", broker=object(), cli=CLI)
    silent = ConnectorProvider(
        oauth=_CliOAuth(provider="quiet", host="api.quiet.test"), label="Quiet", broker=object()
    )
    manifest = Manifest(name="t", version="0", connectors=(declaring, silent))
    assert connector_clis((manifest,)) == {"hub": CLI}


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
