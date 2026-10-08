"""The proxy sessions a turn and an off-turn probe egress under, and the authorization that lays the
acting member's session over a turn's sandbox.

A turn holds one session per acting member, so a dispatch acting for one member never runs under a
session bound to another's private connections. Each is opened on first use under an idempotency
key a recovered workflow's replay reuses, reconciled and renewed at every dispatch that reaches an
enforced sandbox, and narrowed or revoked when the turn ends. A probe holds one session for one
exec, revoked when the exec ends."""

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import log, warn
from ufo.harness.sandbox.exec_env import GIT_IDENTITY_ENV, git_identity_env
from ufo.harness.sandbox.session import RunActor, RunToken, RunTokenCodec, Sandbox, actor_wire
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import PolicyScope, SessionPolicy
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.access.proxy_sessions import (
    PROXY_SESSION_MAX_TTL_SECONDS,
    PROXY_SESSION_TTL_SECONDS,
    SESSION_EXPIRED_CODE,
    ProxyRefused,
    ProxySessions,
    SessionCreated,
)
from ufo.runtime.agent_scope import agent
from ufo.runtime.billing.accounting import (
    AGENT_LABEL,
    CONVERSATION_LABEL,
    MEMBER_LABEL,
    PROBE_LABEL,
    TURN_LABEL,
)
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.records import NON_TERMINAL_STATUSES, Turn, TurnStatus

POLICY_KEY_DIGEST_CHARS = 16
PROBE_SESSION_MARGIN_SECONDS = 60


# The proxy service withholds a route's header values in every answer, so a changed policy is
# found by comparing the one core last sent.
@dataclass(frozen=True)
class _Held:
    session: SessionCreated
    policy: SessionPolicy


@dataclass(frozen=True)
class TurnSessions:
    """The proxy sessions one turn holds, one per acting member: opened on first use under an
    idempotency key the replay of a recovered workflow reuses, reconciled and renewed at every
    dispatch that reaches an enforced sandbox, narrowed or revoked when the turn ends. With no
    `proxy` it opens nothing."""

    proxy: ProxySessions | None
    rules: PerAgentRules
    run_tokens: RunTokenCodec
    turn: Turn
    agent_id: UUID
    internet_access_allowed: bool
    opened: dict[RunActor, _Held] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def actor(self, acting_member_id: UUID | None) -> RunActor:
        """Whom a dispatch acting for `acting_member_id` runs as: the turn's own member, nobody, or
        another member."""
        if acting_member_id == self.turn.member_id:
            return "turn"
        return "nobody" if acting_member_id is None else acting_member_id

    async def open(self, actor: RunActor = "turn") -> SessionCreated | None:
        """The session `actor` egresses under, opened once per turn and actor."""
        if self.proxy is None:
            return None
        async with self.lock:
            return (await self._opened(self.proxy, actor)).session

    async def reconcile(self, actor: RunActor) -> SessionCreated | None:
        """The session `actor` egresses under for the next dispatch: its policy recompiled and
        patched when it moved since the last write, its deadline renewed. A session that expired
        since the last dispatch gives way to a new one; a revoked one stays refused."""
        if self.proxy is None:
            return None
        async with self.lock:
            try:
                held = await self._renewed(self.proxy, actor)
            except ProxyRefused as refused:
                if refused.error.code != SESSION_EXPIRED_CODE:
                    raise
                del self.opened[actor]
                held = await self._opened(self.proxy, actor)
            self.opened[actor] = held
            return held.session

    async def close(self) -> None:
        """End the turn's authority once its terminal is committed: a turn that left detached
        commands keeps every live session under its label, an earlier run's included, narrowed to
        a policy that binds no model key and no route, until the last command's follow ends; any
        other has every session under its label revoked. A turn whose row is not terminal yet, as
        when a cancel has stopped the workflow but not committed `cancelled`, keeps its sessions
        until their deadline. A fault is logged and never raised, since each session's deadline
        bounds what it leaves behind."""
        if self.proxy is None:
            return
        try:
            status, detached_until = await self._ending()
            now = datetime.now(UTC)
            if status in NON_TERMINAL_STATUSES:
                log("turn.sessions.left_open", turn_id=str(self.turn.id), status=status)
                return
            if detached_until is None or detached_until <= now:
                revoked = await self.proxy.revoke_labelled(
                    self.turn.workspace_id, TURN_LABEL, str(self.turn.id)
                )
                log("turn.sessions.revoked", turn_id=str(self.turn.id), count=revoked)
                return
        except Exception as error:
            warn(
                "turn.sessions.close_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
            return
        remaining = math.ceil((detached_until - now).total_seconds())
        ttl_s = min(remaining, PROXY_SESSION_MAX_TTL_SECONDS)
        try:
            listed = await self.proxy.labelled(
                self.turn.workspace_id, TURN_LABEL, str(self.turn.id)
            )
        except Exception as error:
            warn(
                "turn.sessions.close_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
            return
        for session in listed:
            if session.expires_at <= now:
                continue
            member = session.labels.get(MEMBER_LABEL)
            actor = self.actor(None if member is None else UUID(member))
            try:
                narrowed = await self._policy(actor, running=False)
                await self.proxy.update(self.turn.workspace_id, session.id, narrowed)
                await self.proxy.renew(self.turn.workspace_id, session.id, ttl_s)
            except Exception as error:
                warn(
                    "turn.sessions.close_failed",
                    turn_id=str(self.turn.id),
                    session_id=str(session.id),
                    error_class=type(error).__name__,
                )

    def labels(self, actor: RunActor) -> dict[str, str]:
        """The labels a session of this turn carries: the turn, its conversation and agent, and the
        member `actor` acts for when it acts for one."""
        member_id = self._member(actor)
        return {
            TURN_LABEL: str(self.turn.id),
            CONVERSATION_LABEL: str(self.turn.conversation_id),
            AGENT_LABEL: str(self.agent_id),
            **({} if member_id is None else {MEMBER_LABEL: str(member_id)}),
        }

    async def _opened(self, proxy: ProxySessions, actor: RunActor) -> _Held:
        held = self.opened.get(actor)
        if held is not None:
            return held
        policy = await self._policy(actor, running=True)
        session = await proxy.open(
            self.turn.workspace_id,
            key=f"turn:{self.turn.id}:{actor_wire(actor)}:"
            f"{policy.digest()[:POLICY_KEY_DIGEST_CHARS]}",
            labels=self.labels(actor),
            policy=policy,
        )
        held = _Held(session, policy)
        self.opened[actor] = held
        return held

    async def _renewed(self, proxy: ProxySessions, actor: RunActor) -> _Held:
        held = await self._opened(proxy, actor)
        policy = await self._policy(actor, running=True)
        if policy.digest() != held.policy.digest():
            updated = await proxy.update(self.turn.workspace_id, held.session.id, policy)
            held = _Held(
                held.session.model_copy(update={"version": updated.version, "env": updated.env}),
                policy,
            )
        renewed = await proxy.renew(
            self.turn.workspace_id, held.session.id, PROXY_SESSION_TTL_SECONDS
        )
        return replace(
            held, session=held.session.model_copy(update={"expires_at": renewed.expires_at})
        )

    async def _policy(self, actor: RunActor, running: bool) -> SessionPolicy:
        workspace_id = self.turn.workspace_id
        run_token = self.run_tokens.encode(RunToken(workspace_id, self.turn.id, acts_for=actor))
        with ws(workspace_id), agent(self.agent_id):
            return await self.rules.session_policy(
                PolicyScope(
                    workspace_id=workspace_id,
                    member_id=self._member(actor),
                    internet_access_allowed=self.internet_access_allowed,
                    running=running,
                    run_token=run_token,
                )
            )

    def _member(self, actor: RunActor) -> UUID | None:
        match actor:
            case "turn":
                return self.turn.member_id
            case "nobody":
                return None
            case _:
                return actor

    async def _ending(self) -> tuple[TurnStatus, datetime | None]:
        with ws(self.turn.workspace_id):
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.status, tables.turn.c.detached_until).where(
                            tables.turn.c.id == self.turn.id,
                            tables.turn.c.workspace_id == self.turn.workspace_id,
                        )
                    )
                ).one()
        until = row.detached_until
        if until is None or until.tzinfo is not None:
            return row.status, until
        return row.status, until.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SandboxAuthorizer:
    """Lay the acting member's committer identity over the turn's sandbox for one dispatch, after
    dropping every connector CLI variable and identity a previous authority set, and, on a carrier
    that enforces egress, the acting member's session env, reconciled when the dispatch first
    reaches the sandbox: a dispatch that never does, or an in-cluster carrier, opens no session."""

    sandbox: Sandbox
    sessions: TurnSessions
    grants: GrantStore | None
    clis: Mapping[str, CliCredential]
    turn: Turn

    async def authorize(self, acting_member_id: UUID | None) -> Sandbox:
        actor = self.sessions.actor(acting_member_id)

        async def egress() -> Mapping[str, str]:
            session = await self.sessions.reconcile(actor)
            return {} if session is None else session.env

        return await self.sandbox.authorize(
            frozenset(cli.env for cli in self.clis.values()) | GIT_IDENTITY_ENV,
            await git_identity_env(self.grants, self.clis, self.turn.id, acting_member_id),
            egress,
        )


@dataclass(frozen=True)
class IntentRenewal:
    """The turn's sandbox for an intent dispatch, under the turn's own session and no acting
    member's identity: a dispatch that reaches an enforced sandbox reconciles and renews that
    session as any turn's dispatch does."""

    sandbox: Sandbox
    sessions: TurnSessions

    async def authorize(self, acting_member_id: UUID | None) -> Sandbox:
        async def egress() -> Mapping[str, str]:
            session = await self.sessions.reconcile("turn")
            return {} if session is None else session.env

        return await self.sandbox.authorize(frozenset(), {}, egress)


@dataclass(frozen=True)
class ProbeSessions:
    """The session one off-turn probe exec egresses under: its policy compiled for a scope that is
    not running, so it binds no model key and no route, keyed by the probe so an open asked twice
    answers one session, and living a minute past the exec's own timeout."""

    proxy: ProxySessions
    rules: PerAgentRules

    async def open(
        self,
        probe_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        member_id: UUID | None,
        internet_access_allowed: bool,
        timeout_s: int,
    ) -> SessionCreated:
        """Open the probe's session under the ambient workspace."""
        workspace_id = ws_current().workspace_id
        with agent(agent_id):
            policy = await self.rules.session_policy(
                PolicyScope(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    internet_access_allowed=internet_access_allowed,
                    running=False,
                    run_token=None,
                )
            )
        return await self.proxy.open(
            workspace_id,
            key=f"probe:{probe_id}",
            labels={
                CONVERSATION_LABEL: str(conversation_id),
                AGENT_LABEL: str(agent_id),
                PROBE_LABEL: str(probe_id),
                **({} if member_id is None else {MEMBER_LABEL: str(member_id)}),
            },
            policy=policy,
            ttl_s=timeout_s + PROBE_SESSION_MARGIN_SECONDS,
        )

    async def revoke(self, workspace_id: UUID, session_id: UUID) -> None:
        """End the probe's session; a fault is logged, since the session's deadline bounds it."""
        try:
            await self.proxy.revoke(workspace_id, session_id)
        except Exception as error:
            warn(
                "probe.sessions.revoke_failed",
                session_id=str(session_id),
                error_class=type(error).__name__,
            )
