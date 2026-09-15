"""Resolve one capability token's egress rule set — the control-plane logic core `serve` runs
behind the egress-control RPC. `PerAgentRules` derives each turn's rules fresh from its token: the
workspace-wide model base, that workspace's keyed-credential injections, and that agent's OAuth
grants, plus the liveness gate that authorizes each CONNECT. It reaches the DB under the request's
own workspace scope; the Rust data-plane proxy calls it, never touching this logic or the keys."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import warn
from ufo.harness.sandbox.preview import PREVIEW_AUTH_HEADER, PREVIEW_HOST, PREVIEW_SENTINEL
from ufo.harness.sandbox.session import SENTINEL_MODEL_KEY, ProbeToken, RunToken
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_rules import (
    ConnectorTransferHosts,
    InjectionRule,
    InternetRule,
    Rule,
    ServiceRule,
    derive_cli_rules,
    derive_credential_rules,
    derive_grant_rules,
)
from ufo.runtime.access.grants import GrantStore, scoped_cli_accounts
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.tools.bridge import TOOL_BRIDGE_HOST, ToolBridgePrincipal
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import RUNNING, TurnRuntimeConfig

EgressPrincipal = RunToken | ProbeToken
"""What a CONNECT presents itself as: a turn's run token, or one probe exec's own token. Both are
signed by the one deploy secret and name their own domain, so the wire cannot pass one as the
other."""


@dataclass(frozen=True, slots=True)
class _Scope:
    """The agent, internet policy, and exact resolved connection capabilities. `running` is
    whether a turn is still executing: a run token also answers for the detached commands a turn
    left behind until its `detached_until`, and those keep the turn's network but never the
    deployment's model key or the tool bridge, which only a live turn can hold."""

    agent_id: UUID
    internet_access_allowed: bool
    connections: tuple[UUID, ...]
    running: bool


def _turn_answers(now: datetime) -> sa.ColumnElement[bool]:
    """The rows a run token still speaks for: a turn the DB reports running, or one whose detached
    commands the deploy follows until `detached_until`."""
    return sa.or_(
        tables.turn.c.status == RUNNING,
        tables.turn.c.detached_until > now,
    )


@dataclass(frozen=True)
class PerAgentRules:
    """Resolve the proxy's rule set for one token's agent and live scope each call: the
    workspace-wide model base, that workspace's own keyed-credential rules, and that agent's own
    OAuth grant rules. Per-agent authentication is the wire's isolation — agent A's turn resolves
    only A's grants, so A cannot inject or forward through another agent's account — and
    per-workspace resolution is the tenant's: a stored secret is read against the run token's own
    `workspace_id`, so one shared proxy injects for every workspace and none of them holds another's
    key. A missing or forged token yields the base alone; a verified token whose run is no longer
    live yields no rules. A resolution error raises to the proxy, which returns service
    unavailable without caching it — never a policy denial, broad allow, or another workspace's
    secret. Deriving each call (not once at boot) is the liveness: a grant recorded or a slot filled
    mid-serve is live for the next turn.

    A probe token resolves the same chain under the same agent, reached through its conversation
    rather than a turn, minus the deployment's model key."""

    base: tuple[Rule, ...]
    grants: GrantStore | None
    credentials: CredentialStore | None = None
    slots: WorkspaceSlots = field(default_factory=WorkspaceSlots)
    internet: tuple[InternetRule, ...] = ()
    cache_host: str | None = None
    cache_pkg_hosts: tuple[str, ...] = ()
    preview_token: str | None = None
    transfer_hosts: ConnectorTransferHosts = field(
        default_factory=lambda: ConnectorTransferHosts(explicit={})
    )
    clis: Mapping[str, CliCredential] = field(default_factory=dict)

    async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]:
        if principal is None:
            return self.base
        with ws(principal.workspace_id):
            match principal:
                case RunToken():
                    scope = await self._turn_of(principal)
                case ProbeToken():
                    scope = await self._conversation_of(principal)
            if scope is None:
                return ()
            with agent(scope.agent_id):
                internet_allowed = scope.internet_access_allowed and bool(self.internet)
                rules = (*self.base, *self.internet) if internet_allowed else self.base
                if isinstance(principal, RunToken):
                    rules = (*rules, ServiceRule(host=TOOL_BRIDGE_HOST))
                if self.cache_host is not None and internet_allowed:
                    rules = (
                        *rules,
                        ServiceRule(host=self.cache_host),
                        *(
                            ServiceRule(host=host, daemon_prefix=f"/pkg/{host}")
                            for host in self.cache_pkg_hosts
                        ),
                    )
                if self.preview_token is not None:
                    rules = (
                        *rules,
                        ServiceRule(host=PREVIEW_HOST),
                        InjectionRule(
                            host=PREVIEW_HOST,
                            header=PREVIEW_AUTH_HEADER,
                            sentinel=PREVIEW_SENTINEL,
                            real=self.preview_token,
                        ),
                    )
                if self.credentials is not None and self.slots:
                    rules = (
                        *rules,
                        *await derive_credential_rules(
                            self.slots, principal.workspace_id, self.credentials
                        ),
                    )
                if self.grants is not None:
                    granted = await self.grants.active_grants()
                    rules = (
                        *rules,
                        *derive_grant_rules(granted, self.transfer_hosts, scope.connections),
                        *await derive_cli_rules(
                            granted,
                            self.clis,
                            principal.workspace_id,
                            scope.connections,
                        ),
                    )
                if not scope.running:
                    return self._without_the_model_key(rules)
                return rules

    async def git_credential(
        self, principal: EgressPrincipal, host: str
    ) -> tuple[GitWire, str, str] | None:
        """The git credential the cache daemon fetches `host` with on this principal's behalf: the
        connector git wire it rides, the granted account's token, and the account itself — the
        mirror principal, so every turn reaching one connected account shares one mirror. The
        account is chosen exactly as the sandbox's own env export chooses it, so the daemon fetches
        as the account the turn's `GH_TOKEN` names and never as a sibling capability. None for a
        token that is not live, a host no connector clones through, or a scope with no usable
        account for it — the daemon then fetches anonymously.

        Reading the account's token is a call to the broker, so one account's fault withholds that
        account and nothing more, exactly as `derive_cli_rules` withholds one grant. An account the
        broker will not authenticate and a broker that cannot be reached both end here as an
        anonymous fetch: the alternative is this call answering 500, the daemon answering 502, and
        a public clone that needs no credential at all failing with it."""
        with ws(principal.workspace_id):
            match principal:
                case RunToken():
                    scope = await self._turn_of(principal)
                case ProbeToken():
                    scope = await self._conversation_of(principal)
            if scope is None or self.grants is None:
                return None
            with agent(scope.agent_id):
                granted = await self.grants.active_grants()
            for provider, cli in self.clis.items():
                if cli.git is None or cli.git.host != host:
                    continue
                accounts = scoped_cli_accounts(granted, provider, scope.connections)
                if len(accounts) != 1:
                    continue
                try:
                    token = await cli.secret.secret(principal.workspace_id, accounts[0])
                except Exception as error:
                    warn(
                        "egress.git_credential_failed",
                        provider=provider,
                        account_id=accounts[0],
                        error_class=type(error).__name__,
                        error=str(error),
                    )
                    continue
                return cli.git, token, accounts[0]
        return None

    async def live_bridge_principal(self, run: RunToken) -> ToolBridgePrincipal | None:
        with ws(run.workspace_id):
            scope = await self._turn_of(run)
        return (
            None
            if scope is None or not scope.running
            else ToolBridgePrincipal(run.workspace_id, run.turn_id, scope.connections)
        )

    async def _turn_of(self, run: RunToken) -> _Scope | None:
        """The turn's agent and effective internet policy in one indexed read."""
        query = (
            sa.select(
                tables.turn.c.agent_id,
                tables.turn.c.runtime_config,
                tables.agent.c.internet_access_allowed,
                (tables.turn.c.status == RUNNING).label("running"),
            )
            .select_from(
                tables.turn.join(
                    tables.agent,
                    tables.agent.c.id == tables.turn.c.agent_id,
                )
            )
            .where(
                tables.turn.c.id == run.turn_id,
                tables.turn.c.workspace_id == run.workspace_id,
                _turn_answers(datetime.now(UTC)),
                tables.agent.c.workspace_id == run.workspace_id,
            )
        )
        if run.capability_id is not None:
            query = query.add_columns(
                tables.sandbox_call_capability.c.connections.label("capability_connections")
            ).join(
                tables.sandbox_call_capability,
                sa.and_(
                    tables.sandbox_call_capability.c.id == run.capability_id,
                    tables.sandbox_call_capability.c.turn_id == run.turn_id,
                    tables.sandbox_call_capability.c.workspace_id == run.workspace_id,
                ),
            )
        else:
            query = query.add_columns(sa.null().label("capability_connections"))
        async with workspace_tx() as connection:
            row = (await connection.execute(query)).one_or_none()
        if row is None:
            return None
        runtime_config = (
            None
            if row.runtime_config is None
            else TurnRuntimeConfig.model_validate(row.runtime_config)
        )
        internet_access_allowed = row.internet_access_allowed and (
            runtime_config is None or runtime_config.internet_access is None
        )
        connections = (
            tuple(UUID(value) for value in row.capability_connections)
            if run.capability_id is not None
            else ()
            if runtime_config is None or runtime_config.connections is None
            else runtime_config.connections
        )
        return _Scope(
            row.agent_id,
            internet_access_allowed,
            connections,
            running=bool(row.running),
        )

    async def _conversation_of(self, probe: ProbeToken) -> _Scope | None:
        """The probed conversation's agent and snapshotted internet policy — the same two columns
        a turn's read answers, reached through the conversation because a probe names no turn. The
        exact connector capabilities come off the token rather than a row."""
        if probe.expires_at <= int(datetime.now(UTC).timestamp()):
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.agent_id,
                        tables.agent.c.internet_access_allowed,
                    )
                    .select_from(
                        tables.conversation.join(
                            tables.agent,
                            tables.agent.c.id == tables.conversation.c.agent_id,
                        )
                    )
                    .where(
                        tables.conversation.c.id == probe.conversation_id,
                        tables.conversation.c.workspace_id == probe.workspace_id,
                        tables.agent.c.workspace_id == probe.workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return _Scope(
            row.agent_id,
            row.internet_access_allowed and probe.internet_access is None,
            probe.connections,
            running=False,
        )

    def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
        """`rules` minus the deployment's own model-key injection. A probe's environment exports
        no model sentinel, but a carrier's base environment does, so the withholding is made true
        at the enforcement point rather than left to what a sandbox happens to carry: a probe that
        reaches a model host reaches it unauthenticated. Everything else derives as a turn's does —
        the workspace's keyed credentials, so a `git fetch` probe still authenticates, and the
        agent's grants, so a `gh run view` probe still forwards through the broker."""
        return tuple(
            rule
            for rule in rules
            if not (isinstance(rule, InjectionRule) and SENTINEL_MODEL_KEY in rule.sentinel)
        )

    async def turn_live(self, run: RunToken) -> int | None:
        """The egress-authorization gate: the workspace's egress-rules generation while the run
        token names a turn the DB still reports running, None otherwise. A keyed host's real-key
        injection is applied only for a live turn, so a token for a turn that has ended or never
        existed is denied at CONNECT and the key never reaches the wire. Read fresh per request —
        never the per-turn rule cache — so a turn that ends between requests can no longer draw the
        key; the generation rides the same one indexed read, so the rule cache pins what it derived
        from without a second round-trip."""
        with ws(run.workspace_id):
            query = (
                sa.select(
                    tables.workspace.c.egress_rules_generation,
                )
                .select_from(
                    tables.turn.join(
                        tables.workspace,
                        tables.workspace.c.id == tables.turn.c.workspace_id,
                    )
                )
                .where(
                    tables.turn.c.id == run.turn_id,
                    tables.turn.c.workspace_id == run.workspace_id,
                    _turn_answers(datetime.now(UTC)),
                )
            )
            if run.capability_id is not None:
                query = query.where(
                    sa.exists(
                        sa.select(tables.sandbox_call_capability.c.id).where(
                            tables.sandbox_call_capability.c.id == run.capability_id,
                            tables.sandbox_call_capability.c.turn_id == run.turn_id,
                            tables.sandbox_call_capability.c.workspace_id == run.workspace_id,
                        )
                    )
                )
            async with workspace_tx() as connection:
                row = (await connection.execute(query)).one_or_none()
        if row is None:
            return None
        return row.egress_rules_generation

    async def probe_live(self, probe: ProbeToken) -> int | None:
        """The current rules generation while the probe is unexpired and its conversation exists."""
        if probe.expires_at <= int(datetime.now(UTC).timestamp()):
            return None
        with ws(probe.workspace_id):
            async with workspace_tx() as connection:
                return (
                    await connection.execute(
                        sa.select(tables.workspace.c.egress_rules_generation)
                        .select_from(
                            tables.conversation.join(
                                tables.workspace,
                                tables.workspace.c.id == tables.conversation.c.workspace_id,
                            )
                        )
                        .where(
                            tables.conversation.c.id == probe.conversation_id,
                            tables.conversation.c.workspace_id == probe.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
