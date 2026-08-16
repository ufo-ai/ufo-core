"""Composition root for the shared egress proxy: one standalone service for every workspace.

The shared proxy resolves everything from the run token, which carries `workspace_id`, and every
query filters by it — so one `ufoctl proxy` process serves all workspaces. It opens the
RLS-bypassing owner DSN (the resolver's explicit `workspace_id` filters do the scoping), signs
sandbox leaves from a stable shared CA (so a sandbox's trust store validates one chain across
restarts), injects model-provider keys from the process environment, and injects each workspace's
own keyed-provider secrets read per turn from the credential store under that token's workspace."""

import asyncio
import os
import signal
from dataclasses import dataclass

from cryptography.fernet import Fernet

from ufo.blob import blob_store_for
from ufo.config import Config, load_config
from ufo.credentials import CredentialStore
from ufo.db import init_db, verify_db_reachable
from ufo.ext.loader import connector_clis, injecting_slots, load_manifests
from ufo.ext.manifest import CredentialSlot, Manifest
from ufo.grants import GrantStore
from ufo.models.pricing import Pricing
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.sandbox.cache import (
    CACHE_CALLBACK_HOST,
    CACHE_CALLBACK_PORT,
    CACHE_CONTROL_TOKEN_ENV,
    CACHE_HOST,
    CACHE_PKG_HOSTS,
    parse_cache_daemon,
)
from ufo.sandbox.proxy.credential_callback import CredentialCallback
from ufo.sandbox.proxy.rules import (
    Rule,
    ScopeRule,
    connector_transfer_hosts,
    derive_artifact_store_rules,
    derive_manifest_rules,
    derive_model_rules,
)
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV, RunTokenCodec

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
    """Boot the shared egress proxy: load config the way `serve` does, source the owner DSN, the
    stable CA, and — for a pack with keyed providers — the credential key (failing loud on any
    unset), export telemetry to the configured collector (`UFO_OTLP_ENDPOINT` over the baked config,
    like the owner DSN), meter model usage against the deploy's merged price table, and serve
    forever."""
    config = load_config()
    init_o11y(os.environ.get(OTLP_ENDPOINT_ENV) or config.o11y.otlp_endpoint)
    manifests = load_manifests(config.pack.name)
    ca_cert, ca_key = _egress_ca()
    server = ProxyServe(
        config=config,
        manifests=manifests,
        owner_dsn=owner_dsn(config),
        ca_cert=ca_cert,
        ca_key=ca_key,
        credentials=_credential_store(config, injecting_slots(manifests)),
        pricing=model_registry(config, manifests).pricing,
        shutdown=asyncio.Event(),
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


def owner_dsn(config: Config) -> str:
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


def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None:
    """The store the proxy decrypts each workspace's keyed-provider secrets through, sourced from
    the same credential key env `serve` reads so both processes open the one Fernet. A pack that
    declares an injecting slot with no key set would serve a sandbox reaching no keyed host, so it
    fails loud; a pack with no injecting slot needs no key and opens none."""
    key = os.environ.get(config.credentials.key_env)
    if key:
        return CredentialStore(fernet=Fernet(key.encode()))
    if slots:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but the active pack "
            f"declares injecting credential slot(s) {sorted(slot.name for slot in slots)}, whose "
            "secrets the proxy swaps onto the wire per workspace"
        )
    return None


@dataclass(frozen=True)
class ProxyServe:
    """The shared egress proxy run: open the owner DSN and bind one proxy that resolves every
    workspace's per-turn rules from the run token — the workspace-wide model base plus that
    workspace's own keyed-provider secrets, decrypted per turn through `credentials`."""

    config: Config
    manifests: tuple[Manifest, ...]
    owner_dsn: str
    ca_cert: str
    ca_key: str
    credentials: CredentialStore | None
    pricing: Pricing
    shutdown: asyncio.Event

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(shutdown_signal, self.shutdown.set)
        init_db(self.owner_dsn)
        await verify_db_reachable()
        artifacts = await derive_artifact_store_rules(blob_store_for(self.config.blob))
        cache_daemon = parse_cache_daemon(self.config.sandbox.cache_daemon)
        resolver = PerAgentRules(
            base=(*model_rule_base(self.config), *artifacts),
            grants=GrantStore(),
            credentials=self.credentials,
            slots=injecting_slots(self.manifests),
            internet=derive_manifest_rules(self.manifests),
            transfer_hosts=connector_transfer_hosts(self.manifests),
            clis=connector_clis(self.manifests),
            cache_host=CACHE_HOST if cache_daemon is not None else None,
            cache_pkg_hosts=CACHE_PKG_HOSTS if cache_daemon is not None else (),
        )
        proxy = EgressProxy(
            resolve=resolver.resolve,
            authorize=resolver.turn_live,
            ca_cert=self.ca_cert,
            ca_key=self.ca_key,
            run_tokens=RunTokenCodec.from_env(),
            pricing=self.pricing,
            cache_daemon=cache_daemon,
        )
        await proxy.start(
            port=self.config.sandbox.proxy_port, public_url=self.config.sandbox.proxy_public_url
        )
        log("proxy.listening", port=self.config.sandbox.proxy_port)
        callback_server = (
            await self._start_credential_callback() if cache_daemon is not None else None
        )
        try:
            await self.shutdown.wait()
        finally:
            if callback_server is not None:
                callback_server.close()
                await callback_server.wait_closed()
            await proxy.stop(self.config.serve.graceful_shutdown_seconds)

    async def _start_credential_callback(self) -> asyncio.AbstractServer:
        """Bind the loopback credential callback the cache daemon phones home to. The shared token
        gates it; unset when the cache is enabled fails loud, since the daemon would 401 every git
        request and cache nothing."""
        token = os.environ.get(CACHE_CONTROL_TOKEN_ENV)
        if not token:
            raise RuntimeError(
                f"{CACHE_CONTROL_TOKEN_ENV} must be set when the sandbox cache is enabled — the "
                "daemon presents it on every credential callback"
            )
        server = await CredentialCallback(
            credentials=self.credentials,
            slots=injecting_slots(self.manifests),
            token=token,
        ).serve(CACHE_CALLBACK_HOST, CACHE_CALLBACK_PORT)
        log("proxy.cache_callback_listening", port=CACHE_CALLBACK_PORT)
        return server
