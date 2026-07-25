"""Composition root for the shared egress proxy: one standalone service for every workspace.

Dedicated `serve` runs its proxy in-process. The shared proxy resolves
everything from the run token, which carries `workspace_id`, and every query filters by it — so one
`ufoctl proxy` process serves all workspaces. It opens the RLS-bypassing owner DSN (the resolver's
explicit `workspace_id` filters do the scoping), signs sandbox leaves from a stable shared CA (so a
sandbox's trust store validates one chain across restarts), and injects only model-provider keys
from the process environment. Workspace credential injection requires a dedicated in-process
proxy, so an injecting slot in the shared pack fails loud."""

import asyncio
import os
from dataclasses import dataclass

from ufo.config import Config, load_config
from ufo.db import init_db
from ufo.ext.loader import connector_clis, load_manifests
from ufo.ext.manifest import Manifest
from ufo.grants import GrantStore
from ufo.models.pricing import Pricing
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.sandbox.fs_creds import sandbox_fs_minter
from ufo.sandbox.proxy.rules import (
    Rule,
    ScopeRule,
    connector_transfer_hosts,
    derive_manifest_rules,
    derive_model_rules,
)
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV

MODEL_PROBES = ("claude-opus-4-8", "gpt-5")
OWNER_DSN_ENV = "UFO_OWNER_DSN"
OTLP_ENDPOINT_ENV = "UFO_OTLP_ENDPOINT"


def model_rule_base(config: Config) -> tuple[Rule, ...]:
    """The proxy's model-provider egress base, shared by the standalone `ProxyServe` and serve's
    in-process local proxy: each configured provider whose key is set in env is reachable and its
    sentinel swaps to the real key on the wire; no key set anywhere means the sandbox would have no
    egress route, so it fails loud. The one place a deploy's model hosts become egress rules."""
    key_envs = (config.models.anthropic_api_key_env, config.models.openai_api_key_env)
    hosts: set[str] = set()
    rules: list[Rule] = []
    for env_name, probe in zip(key_envs, MODEL_PROBES, strict=True):
        key = os.environ.get(env_name)
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


def run() -> None:
    """Boot the shared egress proxy: load config the way `serve` does, source the owner DSN and the
    stable CA (failing loud on either unset), export telemetry to the configured collector
    (`UFO_OTLP_ENDPOINT` over the baked config, like the owner DSN), meter model usage against the
    deploy's merged price table, and serve forever."""
    config = load_config()
    init_o11y(os.environ.get(OTLP_ENDPOINT_ENV) or config.o11y.otlp_endpoint)
    manifests = load_manifests(config.pack.name)
    ca_cert, ca_key = _egress_ca()
    server = ProxyServe(
        config=config,
        manifests=manifests,
        owner_dsn=_owner_dsn(config),
        ca_cert=ca_cert,
        ca_key=ca_key,
        pricing=model_registry(config, manifests).pricing,
    )
    log("proxy.starting", port=config.sandbox.proxy_port)
    asyncio.run(server.serve())


def _egress_ca() -> tuple[str, str]:
    """The stable shared CA (cert, key) the proxy signs every per-host leaf from, sourced from env
    so the same trust material spans proxy restarts and every workspace's sandbox trust store. Both
    unset means the proxy would mint a per-process CA no sandbox trusts, so it fails loud."""
    cert = os.environ.get(EGRESS_CA_CERT_ENV)
    key = os.environ.get(EGRESS_CA_KEY_ENV)
    if not cert or not key:
        raise RuntimeError(
            f"{EGRESS_CA_CERT_ENV} and {EGRESS_CA_KEY_ENV} must both hold the shared egress CA "
            "(PEM): the proxy signs sandbox leaves from one CA every workspace trusts"
        )
    return cert, key


def _owner_dsn(config: Config) -> str:
    """The RLS-bypassing owner DSN the shared proxy opens instead of the scoped `database.url`.
    One process serves every workspace, so the resolver's explicit `workspace_id` filters scope
    each query. Read from `UFO_OWNER_DSN`, falling back to `[database] owner_url`; neither set fails
    loud. The secret's contract is a plain libpq URL, which SQLAlchemy would map to the sync
    psycopg2 dialect — pin the async psycopg driver this distribution ships."""
    dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if not dsn:
        raise RuntimeError(
            f"{OWNER_DSN_ENV} or [database] owner_url must be set for `ufoctl proxy` — the shared "
            "proxy bypasses RLS with the owner role and scopes every query by the run token's "
            "workspace_id"
        )
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


@dataclass(frozen=True)
class ProxyServe:
    """The shared egress proxy run: open the owner DSN, build the workspace-wide model-rule base,
    and bind one proxy that resolves every workspace's per-turn rules from the run token."""

    config: Config
    manifests: tuple[Manifest, ...]
    owner_dsn: str
    ca_cert: str
    ca_key: str
    pricing: Pricing

    async def serve(self) -> None:
        init_db(self.owner_dsn)
        resolver = PerAgentRules(
            base=self._base(),
            grants=GrantStore(),
            internet=derive_manifest_rules(self.manifests),
            transfer_hosts=connector_transfer_hosts(self.manifests),
            clis=connector_clis(self.manifests),
        )
        workspace_fs = sandbox_fs_minter(self.config.blob)
        proxy = EgressProxy(
            resolve=resolver.resolve,
            authorize=resolver.turn_live,
            ca_cert=self.ca_cert,
            ca_key=self.ca_key,
            pricing=self.pricing,
            workspace_credentials=None if workspace_fs is None else workspace_fs.refresh,
        )
        await proxy.start(
            port=self.config.sandbox.proxy_port, public_url=self.config.sandbox.proxy_public_url
        )
        log("proxy.listening", port=self.config.sandbox.proxy_port)
        await asyncio.Event().wait()

    def _base(self) -> tuple[Rule, ...]:
        """The proxy's static rule base: the shared model-provider egress (`model_rule_base`) and
        no workspace-specific secrets. An injecting credential slot requires a dedicated proxy and
        fails here before model rules are built."""
        injecting = sorted(
            slot.name
            for manifest in self.manifests
            for slot in manifest.credentials
            if slot.injection
        )
        if injecting:
            raise RuntimeError(
                f"the shared egress proxy cannot inject workspace credential secrets, but the "
                f"active pack declares injecting credential slot(s) {injecting}"
            )
        return model_rule_base(self.config)
