"""Composition root for the shared egress proxy: one standalone service fronting every tenant.

`serve` runs the egress proxy in-process, per pod, which needs a per-tenant LoadBalancer for an
off-cluster sandbox. The proxy is multi-tenant-capable — `PerAgentRules` and `turn_live` resolve
everything from the run token, which carries `workspace_id`, and every query filters by it — so one
`ufoctl proxy` process serves all tenants. It opens the RLS-bypassing owner DSN (the resolver's
explicit `workspace_id` filters do the scoping), signs sandbox leaves from a stable platform CA (so
a sandbox's trust store validates one chain across restarts), and injects only the platform
model-provider key — it cannot inject per-tenant credential secrets, so an injecting credential slot
in the active pack fails loud."""

import asyncio
import os
from dataclasses import dataclass

from ufo.accounting import Pricing
from ufo.config import Config, load_config
from ufo.db import init_db
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import Manifest
from ufo.grants import GrantStore
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.sandbox.proxy.rules import Rule, ScopeRule, derive_model_rules
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV

MODEL_PROBES = ("claude-opus-4-8", "gpt-5")


def run() -> None:
    """Boot the shared egress proxy: load config the way `serve` does, source the owner DSN and the
    stable CA (failing loud on either unset), meter model usage against the deploy's merged price
    table, and serve forever."""
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
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
    """The stable platform CA (cert, key) the proxy signs every per-host leaf from, sourced from env
    so the same trust material spans proxy restarts and every tenant's sandbox trust store. Both
    unset means the proxy would mint a per-process CA no sandbox trusts, so it fails loud."""
    cert = os.environ.get(EGRESS_CA_CERT_ENV)
    key = os.environ.get(EGRESS_CA_KEY_ENV)
    if not cert or not key:
        raise RuntimeError(
            f"{EGRESS_CA_CERT_ENV} and {EGRESS_CA_KEY_ENV} must both hold the shared egress CA "
            "(PEM): the proxy signs sandbox leaves from a stable CA every tenant's trust store "
            "already carries, never a per-process one"
        )
    return cert, key


def _owner_dsn(config: Config) -> str:
    """The RLS-bypassing owner DSN the shared proxy opens — never the tenant-scoped `database.url`.
    One process serves every tenant, so it cannot use a tenant-pinned RLS role; the resolver's
    explicit `workspace_id` filters scope each query. Unset fails loud."""
    if not config.database.owner_url:
        raise RuntimeError(
            "[database] owner_url must be set for `ufoctl proxy` — the shared proxy bypasses RLS "
            "with the owner role and scopes every query by the run token's workspace_id"
        )
    return config.database.owner_url


@dataclass(frozen=True)
class ProxyServe:
    """The shared egress proxy run: open the owner DSN, build the workspace-wide model-rule base,
    and bind one proxy that resolves every tenant's per-turn rules from the run token."""

    config: Config
    manifests: tuple[Manifest, ...]
    owner_dsn: str
    ca_cert: str
    ca_key: str
    pricing: Pricing

    async def serve(self) -> None:
        init_db(self.owner_dsn)
        resolver = PerAgentRules(base=self._base(), grants=GrantStore())
        proxy = EgressProxy(
            resolve=resolver.resolve,
            authorize=resolver.turn_live,
            ca_cert=self.ca_cert,
            ca_key=self.ca_key,
            pricing=self.pricing,
        )
        await proxy.start(
            port=self.config.sandbox.proxy_port, public_url=self.config.sandbox.proxy_public_url
        )
        log("proxy.listening", port=self.config.sandbox.proxy_port)
        await asyncio.Event().wait()

    def _base(self) -> tuple[Rule, ...]:
        """The proxy's static rule base: model-provider egress only. Each configured provider whose
        key is set is reachable and its sentinel swaps to the real key on the wire; no key set
        anywhere means the sandbox would have no egress route, so it fails loud. The shared proxy
        cannot inject per-tenant credential secrets, so an injecting credential slot in the active
        pack fails loud — that deploy needs a per-tenant proxy, not this one."""
        injecting = sorted(
            slot.name
            for manifest in self.manifests
            for slot in manifest.credentials
            if slot.injection
        )
        if injecting:
            raise RuntimeError(
                f"the shared egress proxy cannot inject per-tenant credential secrets, but the "
                f"active pack declares injecting credential slot(s) {injecting}"
            )
        key_envs = (self.config.models.anthropic_api_key_env, self.config.models.openai_api_key_env)
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
