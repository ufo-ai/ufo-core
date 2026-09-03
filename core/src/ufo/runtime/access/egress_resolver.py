"""Resolve one principal's egress rule set — the control-plane logic core `serve` runs behind the
egress-control RPC. `PerAgentRules` derives each turn's rules fresh from its run/probe token: the
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
from ufo.runtime.access.grants import GrantStore, usable_cli_accounts
from ufo.runtime.agent_scope import agent
from ufo.runtime.authority import (
    ExecutionAuthority,
    MemberAuthority,
    WorkspaceAuthority,
    authority_member_id,
)
from ufo.runtime.ext.manifest import CredentialSlot
from ufo.runtime.tools.bridge import TOOL_BRIDGE_HOST
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import RUNNING, TurnRuntimeConfig

EgressPrincipal = RunToken | ProbeToken
"""What a CONNECT presents itself as: a turn's run token, or one probe exec's own token. Both are
signed by the one deploy secret and name their own domain, so the wire cannot pass one as the
other."""


def _seat_scope(
    workspace_id: UUID, authority: ExecutionAuthority
) -> tuple[sa.ColumnElement[bool], ...]:
    match authority:
        case WorkspaceAuthority():
            return ()
        case MemberAuthority(member_id):
            return (
                sa.exists(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.id == member_id,
                        tables.member.c.seated_at.is_not(None),
                    )
                ),
            )
        case _:
            raise TypeError("execution authority must be MemberAuthority or WorkspaceAuthority")


@dataclass(frozen=True, slots=True)
class _Authority:
    """Whose egress a principal carries: the agent whose rules derive, that agent's snapshotted
    internet policy, and the member whose private grants its CLI credentials may draw on."""

    agent_id: UUID
    internet_access_allowed: bool
    execution: ExecutionAuthority


@dataclass(frozen=True)
class PerAgentRules:
    """Resolve the proxy's rule set for one principal's agent, derived from its token each call: the
    workspace-wide model base, that workspace's own keyed-credential rules, and that agent's own
    OAuth grant rules. Per-agent authentication is the wire's isolation — agent A's turn resolves
    only A's grants, so A cannot inject or forward through another agent's account — and
    per-workspace resolution is the tenant's: a stored secret is read against the run token's own
    `workspace_id`, so one shared proxy injects for every workspace and none of them holds another's
    key. A missing or forged token yields the base alone; a verified principal whose authority is
    no longer live yields no rules. A resolution error raises to the proxy, which returns service
    unavailable without caching it — never a policy denial, broad allow, or another workspace's
    secret. Deriving each call (not once at boot) is the liveness: a grant recorded or a slot filled
    mid-serve is live for the next turn.

    A probe token resolves the same chain under the same agent, reached through its conversation
    rather than a turn, minus the deployment's model key."""

    base: tuple[Rule, ...]
    grants: GrantStore | None
    credentials: CredentialStore | None = None
    slots: tuple[CredentialSlot, ...] = ()
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
                    authority = await self._turn_of(principal)
                case ProbeToken():
                    authority = await self._conversation_of(principal)
            if authority is None:
                return ()
            with agent(authority.agent_id):
                # Cache routes optimize an existing InternetRule. Granting them from the agent flag
                # alone turns the cache host list into an allowlist and blocks unlisted redirects.
                internet_allowed = authority.internet_access_allowed and bool(self.internet)
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
                        *derive_grant_rules(granted, self.transfer_hosts),
                        *await derive_cli_rules(
                            granted, authority.execution, self.clis, principal.workspace_id
                        ),
                    )
                if isinstance(principal, ProbeToken):
                    return self._without_the_model_key(rules)
                return rules

    async def git_credential(
        self, principal: EgressPrincipal, host: str
    ) -> tuple[GitWire, str, str] | None:
        """The git credential the cache daemon fetches `host` with on this principal's behalf: the
        connector git wire it rides, the granted account's token, and the account itself — the
        mirror principal, so two members sharing one connected account share one mirror and a
        member's private account gets its own. The account is chosen exactly as the sandbox's own
        env export chooses it (`usable_cli_accounts`), so the daemon fetches as the identity the
        turn's `GH_TOKEN` names and never as a sibling account the authority also holds. None for a
        principal that is not live, a host no connector clones through, or an authority with no
        usable account for it — the daemon then fetches anonymously.

        Reading the account's token is a call to the broker, so one account's fault withholds that
        account and nothing more, exactly as `derive_cli_rules` withholds one grant. An account the
        broker will not authenticate and a broker that cannot be reached both end here as an
        anonymous fetch: the alternative is this call answering 500, the daemon answering 502, and
        a public clone that needs no credential at all failing with it."""
        with ws(principal.workspace_id):
            match principal:
                case RunToken():
                    authority = await self._turn_of(principal)
                case ProbeToken():
                    authority = await self._conversation_of(principal)
            if authority is None or self.grants is None:
                return None
            with agent(authority.agent_id):
                granted = await self.grants.active_grants()
            for provider, cli in self.clis.items():
                if cli.git is None or cli.git.host != host:
                    continue
                accounts = usable_cli_accounts(
                    granted, provider, authority_member_id(authority.execution)
                )
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

    async def _turn_of(self, run: RunToken) -> _Authority | None:
        """The turn's agent and effective internet policy in one indexed read."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.agent_id,
                        tables.turn.c.runtime_config,
                        tables.agent.c.internet_access_allowed,
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
                        tables.turn.c.status == RUNNING,
                        tables.agent.c.workspace_id == run.workspace_id,
                        *_seat_scope(run.workspace_id, run.authority),
                    )
                )
            ).one_or_none()
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
        return _Authority(row.agent_id, internet_access_allowed, run.authority)

    async def _conversation_of(self, probe: ProbeToken) -> _Authority | None:
        """The probed conversation's agent and snapshotted internet policy — the same two columns
        a turn's read answers, reached through the conversation because a probe names no turn. The
        member comes off the token rather than a row: whoever armed the work this exec serves, so a
        command that reached their own connected account in the arming turn keeps reaching it, the
        way a scheduled fire keeps its initiator's private connectors. A memberless probe forwards
        only what is shared with the workspace."""
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
                        *_seat_scope(probe.workspace_id, probe.authority),
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return _Authority(row.agent_id, row.internet_access_allowed, probe.authority)

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
        token names a turn the DB still reports running and any exact member authority still holds
        a seat, None otherwise. A keyed host's real-key injection is applied only for a live turn,
        so a token for a turn that has ended, a turn that never existed, or a revoked member is
        denied at CONNECT and the key never reaches the wire. Read fresh per request — never the
        per-turn rule cache — so a turn that ends between requests can no longer draw the key; the
        generation rides the same one indexed read, so the rule cache pins what it derived from
        without a second round-trip."""
        with ws(run.workspace_id):
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.status,
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
                            *_seat_scope(run.workspace_id, run.authority),
                        )
                    )
                ).one_or_none()
        if row is None or row.status != RUNNING:
            return None
        return row.egress_rules_generation

    async def probe_live(self, probe: ProbeToken) -> int | None:
        """The current rules generation while the probe is unexpired, its conversation exists,
        and any exact member authority still holds a seat."""
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
                            *_seat_scope(probe.workspace_id, probe.authority),
                        )
                    )
                ).scalar_one_or_none()
