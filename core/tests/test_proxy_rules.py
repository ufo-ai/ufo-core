from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

import pytest

from ufo.connectors import CliCredential, ForwardedResponse
from ufo.ext.loader import connector_clis
from ufo.ext.manifest import ConnectorProvider, Manifest
from ufo.grants import Grant, grant_sentinel
from ufo.sandbox.proxy.rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    SENTINEL_MODEL_KEY,
    ForwardRule,
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_cli_rules,
    derive_model_rules,
    provider_host,
)


def test_provider_host_by_prefix() -> None:
    assert provider_host("claude-opus-4-8") == ANTHROPIC_HOST
    assert provider_host("gpt-5.4") == OPENAI_HOST


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
        provider="hub", account_id=account, host=CLI_HOST, grantor_member_id=grantor, shared=shared
    )


def test_grant_sentinel_is_deterministic_per_account() -> None:
    assert grant_sentinel("acct-1") == grant_sentinel("acct-1")
    assert grant_sentinel("acct-1") != grant_sentinel("acct-2")
    assert "acct-1" in grant_sentinel("acct-1")


def test_cli_rule_for_the_acting_members_own_grant() -> None:
    rules = derive_cli_rules((_grant(),), ACTING, {"hub": CLI})
    forward = next(r for r in rules if isinstance(r, ForwardRule))
    assert forward.host == CLI_HOST
    assert forward.header == "authorization"
    assert forward.sentinel == grant_sentinel("acct-1")
    assert forward.account_id == "acct-1"
    assert forward.forward is CLI.forward


def test_cli_rule_for_a_shared_grant_of_another_member() -> None:
    rules = derive_cli_rules((_grant(grantor=OTHER, shared=True),), ACTING, {"hub": CLI})
    assert any(isinstance(r, ForwardRule) for r in rules)


def test_no_cli_rule_for_a_foreign_private_grant() -> None:
    assert derive_cli_rules((_grant(grantor=OTHER),), ACTING, {"hub": CLI}) == ()


def test_no_cli_rule_for_a_provider_without_a_declared_cli() -> None:
    assert derive_cli_rules((_grant(),), ACTING, {}) == ()


def test_a_memberless_turn_forwards_only_shared_grants() -> None:
    grants = (_grant(), _grant(account="acct-2", grantor=OTHER, shared=True))
    rules = derive_cli_rules(grants, None, {"hub": CLI})
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
