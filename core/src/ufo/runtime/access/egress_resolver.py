"""Compile one session's egress policy, and answer the two questions the tool bridge and the cache
daemon still ask of a run: whether its turn is live, and which git credential a cached fetch rides.
`PerAgentRules` derives everything fresh each call from the deploy's own declarations, the
workspace's keyed credentials, and the bound agent's grants, under the caller's workspace scope."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import warn
from ufo.harness.sandbox.preview import PREVIEW_HOST
from ufo.harness.sandbox.session import ProbeToken, RunToken
from ufo.runtime.access.connectors import CliCredential, GitWire
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_rules import (
    RUN_HEADER,
    Bind,
    ConnectorTransferHosts,
    HostEntry,
    PolicyScope,
    Route,
    SessionPolicy,
    derive_cli_binds,
    derive_credential_binds,
    derive_grant_hosts,
    policy_hosts,
)
from ufo.runtime.access.grants import GrantStore, cli_accounts
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.tools.bridge import TOOL_BRIDGE_HOST, ToolBridgePrincipal
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import RUNNING

EgressPrincipal = RunToken | ProbeToken
"""What a git-credential call presents: a turn's run token, or one probe exec's own token. Both
are signed by the one deploy secret and name their own domain, so neither passes as the other."""


@dataclass(frozen=True, slots=True)
class _Scope:
    """A run token also answers for a turn's detached commands until `detached_until`; only a
    running turn reaches the tool bridge."""

    agent_id: UUID
    member_id: UUID | None
    running: bool


@dataclass(frozen=True)
class PerAgentRules:
    """Compile the session policy for one acting scope each call: the deploy's own hosts and model
    binds, that workspace's keyed-credential binds, and the bound agent's grant hosts and CLI binds.
    Per-agent compilation is the wire's isolation — agent A's session names only A's grants, so A
    cannot reach another agent's account — and per-workspace compilation is the tenant's: a slot is
    bound only for the workspace that holds its value. Deriving each call (not once at boot) is the
    liveness: a grant recorded or a slot filled mid-serve is in the next compile.

    `hosts` and `binds` are the deploy's own: its model providers bound to `ufo/models` and its
    artifact store. `bridge_upstream` and `preview_upstream` are the routes core serves for a
    running turn, each stamped with the turn's run token."""

    hosts: tuple[HostEntry, ...] = ()
    binds: tuple[Bind, ...] = ()
    grants: GrantStore | None = None
    credentials: CredentialStore | None = None
    slots: WorkspaceSlots = field(default_factory=WorkspaceSlots)
    internet: bool = False
    transfer_hosts: ConnectorTransferHosts = field(
        default_factory=lambda: ConnectorTransferHosts(explicit={})
    )
    clis: Mapping[str, CliCredential] = field(default_factory=dict)
    bridge_upstream: str | None = None
    preview_upstream: str | None = None

    async def session_policy(self, scope: PolicyScope) -> SessionPolicy:
        """The policy a session acting in `scope` runs under, naming every secret and carrying no
        value. Runs under the caller's `ws()` and `agent()`. Only a running scope binds the deploy's
        model key and the routes core serves; a public host beyond the named ones needs both the
        deploy and the agent to allow the internet."""
        credential_binds = (
            await derive_credential_binds(self.slots, scope.workspace_id, self.credentials)
            if self.credentials is not None and self.slots
            else ()
        )
        granted = await self.grants.active_grants() if self.grants is not None else ()
        cli_binds = derive_cli_binds(granted, self.clis, scope.member_id)
        grant_hosts = derive_grant_hosts(granted, self.transfer_hosts, scope.member_id)
        binds = (*(self.binds if scope.running else ()), *credential_binds, *cli_binds)
        return SessionPolicy(
            internet=scope.internet_access_allowed and self.internet,
            hosts=policy_hosts(
                *(entry.host for entry in (*self.hosts, *grant_hosts)),
                *(bind.host for bind in (*credential_binds, *cli_binds)),
            ),
            bind=tuple(sorted(binds, key=lambda bind: (bind.host, bind.header, bind.env))),
            routes=self._routes(scope),
        )

    def _routes(self, scope: PolicyScope) -> tuple[Route, ...]:
        if scope.run_token is None or not scope.running or self.bridge_upstream is None:
            return ()
        stamp = {RUN_HEADER: scope.run_token}
        routes = [Route(host=TOOL_BRIDGE_HOST, upstream=self.bridge_upstream, headers=stamp)]
        if self.preview_upstream is not None:
            routes.append(Route(host=PREVIEW_HOST, upstream=self.preview_upstream, headers=stamp))
        return tuple(sorted(routes, key=lambda route: route.host))

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
        account and nothing more. An account the broker will not authenticate and a broker that
        cannot be reached both end here as an anonymous fetch: the alternative is this call
        answering 500, the daemon answering 502, and a public clone that needs no credential at all
        failing with it."""
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
                accounts = cli_accounts(granted, provider, scope.member_id)
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
            else ToolBridgePrincipal(run.workspace_id, run.turn_id, scope.member_id)
        )

    async def _turn_of(self, run: RunToken) -> _Scope | None:
        """The turn's agent, the member a run acts for, and whether it runs, in one indexed read."""
        query = (
            sa.select(
                tables.turn.c.agent_id,
                tables.turn.c.member_id,
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
                sa.or_(
                    tables.turn.c.status == RUNNING,
                    tables.turn.c.detached_until > datetime.now(UTC),
                ),
                tables.agent.c.workspace_id == run.workspace_id,
            )
        )
        async with workspace_tx() as connection:
            row = (await connection.execute(query)).one_or_none()
        if row is None:
            return None
        return _Scope(
            row.agent_id,
            (
                row.member_id
                if run.acts_for == "turn"
                else None
                if run.acts_for == "nobody"
                else run.acts_for
            ),
            running=bool(row.running),
        )

    async def _conversation_of(self, probe: ProbeToken) -> _Scope | None:
        if probe.expires_at <= int(datetime.now(UTC).timestamp()):
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id)
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
        return _Scope(row.agent_id, probe.member_id, running=False)
