import pytest

from selfhost.sandbox.proxy.rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    SENTINEL_MODEL_KEY,
    InjectionRule,
    MeterRule,
    ScopeRule,
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
