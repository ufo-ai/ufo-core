"""Composition-root helpers the shared services share: the model providers every sandbox session
binds by name, the RLS-bypassing owner DSN, and the env names both read.

`serve` compiles each session's policy on these model bindings and `ingress` opens the owner DSN
through the same resolution."""

import os

from ufo.config import Config
from ufo.runtime.access.credentials import deploy_env
from ufo.runtime.access.egress_rules import (
    ANTHROPIC_HOST,
    OPENAI_HOST,
    OPENROUTER_HOST,
    PROVIDER_AUTH,
    UFO_MODELS_SECRET,
    Bind,
    HostEntry,
    policy_hosts,
)

OWNER_DSN_ENV = "UFO_OWNER_DSN"
OTLP_ENDPOINT_ENV = "UFO_OTLP_ENDPOINT"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"


def MODEL_KEY_ENVS(config: Config) -> dict[str, str]:
    """The env each model provider's platform key is read from, by the host it is bound on."""
    return {
        ANTHROPIC_HOST: config.models.anthropic_api_key_env,
        OPENAI_HOST: config.models.openai_api_key_env,
        OPENROUTER_HOST: OPENROUTER_KEY_ENV,
    }


def model_bindings(config: Config) -> tuple[tuple[HostEntry, ...], tuple[Bind, ...]]:
    """The model providers every sandbox session reaches: each of the Anthropic and OpenAI hosts
    whose key is set in env is admitted and binds `ufo/models` under its key's env name. No key set
    anywhere would leave the sandbox no model route, so it fails loud."""
    envs = MODEL_KEY_ENVS(config)
    keyed = tuple(host for host in (ANTHROPIC_HOST, OPENAI_HOST) if deploy_env(envs[host]))
    if not keyed:
        raise RuntimeError("no model provider key set; the sandbox would have no model route")
    return policy_hosts(*keyed), tuple(
        Bind(host=host, header=PROVIDER_AUTH[host], secret=UFO_MODELS_SECRET, env=envs[host])
        for host in keyed
    )


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
