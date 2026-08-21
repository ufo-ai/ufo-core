"""Composition-root helpers the shared services share: the model-provider egress base every
sandbox routes model calls through, the RLS-bypassing owner DSN, and the env names both read.

`serve` builds the egress-control resolver on this base and `ingress` opens the owner DSN through
the same resolution; the egress wire itself is the standalone Rust `ufo-egress` process."""

import os

from ufo.config import Config
from ufo.credentials import deploy_env
from ufo.egress_rules import Rule, ScopeRule, derive_model_rules

MODEL_PROBES = ("claude-opus-4-8", "gpt-5")
OWNER_DSN_ENV = "UFO_OWNER_DSN"
OTLP_ENDPOINT_ENV = "UFO_OTLP_ENDPOINT"


def model_rule_base(config: Config) -> tuple[Rule, ...]:
    """The model-provider egress base of every sandbox's rule set: each configured provider whose
    key is set in env is reachable and its sentinel swaps to the real key on the wire; no key set
    anywhere means the sandbox would have no egress route, so it fails loud. The one place a
    deploy's model hosts become egress rules."""
    key_envs = (config.models.anthropic_api_key_env, config.models.openai_api_key_env)
    hosts: set[str] = set()
    rules: list[Rule] = []
    for env_name, probe in zip(key_envs, MODEL_PROBES, strict=True):
        key = deploy_env(env_name)
        if not key:
            continue
        for rule in derive_model_rules(probe, key):
            if isinstance(rule, ScopeRule):
                hosts |= rule.allowed_hosts
            else:
                rules.append(rule)
    if not hosts:
        raise RuntimeError("no model provider key set; the sandbox would have no egress route")
    return (ScopeRule(allowed_hosts=frozenset(hosts)), *rules)


def owner_dsn(config: Config) -> str:
    """The RLS-bypassing owner DSN the shared ingress opens instead of the scoped `database.url`.
    One process serves every workspace, so explicit `workspace_id` filters scope each query. Read
    from `UFO_OWNER_DSN`, falling back to `[database] owner_url`; neither set fails loud. The
    secret's contract is a plain libpq URL, which SQLAlchemy would map to the sync psycopg2
    dialect — pin the async psycopg driver this distribution ships."""
    dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if not dsn:
        raise RuntimeError(
            f"{OWNER_DSN_ENV} or [database] owner_url must be set — the shared service bypasses "
            "RLS with the owner role and scopes every query by the run token's workspace_id"
        )
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
