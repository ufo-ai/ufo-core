"""The signup Slack Connect invitation: one operator-workspace channel per new customer.

A completed invite-wall claim — one that both burned an invite code and created a workspace — earns
one public channel in UFO's *own* Slack workspace and one Slack-generated Slack Connect invitation
to the email that signed up. Signup never waits for any of it: the completed claim is the durable
event source, so there is no enqueue transaction to lose, and this workflow polls in the background
on either gateway replica. A member joining an existing workspace never burns a code, so the same
eligibility test excludes them; only the earliest completed claim of a workspace materializes, so a
customer gets exactly one channel and never a second invitation.

The app behind `UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN` is UFO's own, installed only in the operator
workspace, and makes outbound Web API calls only — no client id, client secret, signing secret,
redirect URL, event endpoint, or workspace credential slot, and nothing to do with the customer
Slack extension's per-workspace app. `auth.test` must name `UFO_CONTROL_SLACK_CONNECT_TEAM_ID`
before any channel is mutated; another team fails that delivery for operator review.

The customer's channel is the idempotency boundary, and `CHANNEL_NAME_SQL` is what makes it one.
The name it derives is `ext-<domain-label>-flyingobject` — the domain's first label only, never an
email address, since the local part and the TLD both stay out of it — and it derives it where the
claim lives, so materializing stays one statement and every replica reaches the same name. It is
bounded to 77 characters, inside Slack's 80-character limit.

Dropping the TLD means the name is *not* unique across customers: `acme.com` and `acme.io` both
derive `ext-acme-flyingobject`. That is a deliberate readability trade. The `channel_name` unique
constraint is what keeps it safe — the second customer's row is skipped rather than created, so two
customers can never be pointed at one channel. A skipped customer needs a channel by hand.

Determinism is what makes the losses recoverable. A lost `conversations.create` response is
recovered by exact-name lookup, and `invite_attempted_at` is written *before* the invitation call,
so a lost `conversations.inviteShared` response is recovered from Slack's own outgoing-invite and
channel-sharing state rather than a blind second invitation. When Slack cannot expose that state the
row lands `failed` for operator review — never re-invited.
"""

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from ufo_control import gateway_store

logger = logging.getLogger(__name__)

TABLE = f"{gateway_store.SCHEMA}.slack_connect_delivery"
DUE_INDEX = "slack_connect_delivery_due"

ENABLED_ENV = "UFO_CONTROL_SLACK_CONNECT_ENABLED"
BOT_TOKEN_ENV = "UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN"
TEAM_ID_ENV = "UFO_CONTROL_SLACK_CONNECT_TEAM_ID"

SLACK_API_BASE = "https://slack.com/api"
SLACK_TIMEOUT_SECONDS = 10.0
CHANNEL_PAGE_SIZE = 200
INVITE_PAGE_SIZE = 100
MAX_PAGES = 20
ERROR_CHARS = 500
MAX_EMAIL_CHARS = 254
REDACTION = "«token»"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500

CHANNEL_PREFIX = "ext"
CHANNEL_SUFFIX = "flyingobject"
MAX_DOMAIN_LABEL_CHARS = 60

POLL_INTERVAL_SECONDS = 15.0
LEASE = timedelta(seconds=120)
LEASE_RENEW_SECONDS = 30.0
MAX_ATTEMPTS = 8
RETRY_BACKOFF_SECONDS = 30
RETRY_BACKOFF_MAX_SECONDS = 3600
MAX_RETRY_AFTER_SECONDS = 60.0

STATE_PENDING = "pending"
STATE_CLAIMED = "claimed"
STATE_DELIVERED = "delivered"
STATE_FAILED = "failed"
DELIVERY_STATES = (STATE_PENDING, STATE_CLAIMED, STATE_DELIVERED, STATE_FAILED)
STATE_LITERALS = ", ".join(f"'{state}'" for state in DELIVERY_STATES)

NAME_TAKEN = "name_taken"
LIVE_INVITE_STATUSES = frozenset({"sent", "accepted"})
DEAD_INVITE_STATUSES = frozenset({"revoked", "declined", "expired"})
TRANSIENT_SLACK_ERRORS = frozenset(
    {
        "ratelimit",
        "ratelimited",
        "accesslimited",
        "service_unavailable",
        "internal_error",
        "fatal_error",
        "request_timeout",
    }
)

CHANNEL_NAME_SQL = (
    f"'{CHANNEL_PREFIX}-' "
    "|| btrim(left(regexp_replace(split_part(lower(email_domain), '.', 1),"
    f"  '[^a-z0-9]+', '-', 'g'), {MAX_DOMAIN_LABEL_CHARS}), '-') "
    f"|| '-{CHANNEL_SUFFIX}'"
)

DDL = (
    f"create table if not exists {TABLE} ("
    f"  onboard_claim_id uuid primary key references {gateway_store.TABLE} (id) on delete cascade,"
    f"  state text not null check (state in ({STATE_LITERALS})),"
    "  channel_name text not null unique,"
    "  channel_id text,"
    "  slack_invitation_id text,"
    "  invite_attempted_at timestamptz,"
    "  worker_id text,"
    "  claim_expires_at timestamptz,"
    "  next_attempt_at timestamptz,"
    "  attempts integer not null default 0,"
    "  last_error text,"
    "  created_at timestamptz not null default now(),"
    "  updated_at timestamptz not null default now(),"
    "  delivered_at timestamptz)",
    f"create index if not exists {DUE_INDEX} on {TABLE} (state, next_attempt_at)",
)

MATERIALIZE = (
    f"insert into {TABLE} (onboard_claim_id, state, channel_name)"
    "  select distinct on (resulting_workspace_id)"
    f"    id, '{STATE_PENDING}', {CHANNEL_NAME_SQL}"
    f"  from {gateway_store.TABLE}"
    "  where invite_id is not null and resulting_workspace_id is not null"
    "  order by resulting_workspace_id, created_at, id"
    " on conflict do nothing"
)

CLAIM = (
    "with candidate as ("
    f"  select onboard_claim_id from {TABLE}"
    f"  where (state = '{STATE_PENDING}'"
    "         and (next_attempt_at is null or next_attempt_at <= now()))"
    f"     or (state = '{STATE_CLAIMED}' and claim_expires_at <= now())"
    "  order by created_at, onboard_claim_id"
    "  for update skip locked"
    "  limit 1"
    "), leased as ("
    f"  update {TABLE} d"
    f"  set state = '{STATE_CLAIMED}', worker_id = $1,"
    "      claim_expires_at = now() + $2::interval,"
    "      attempts = d.attempts + 1, updated_at = now()"
    "  from candidate c"
    "  where d.onboard_claim_id = c.onboard_claim_id"
    "  returning d.onboard_claim_id, d.channel_name, d.channel_id, d.slack_invitation_id,"
    "            d.invite_attempted_at, d.attempts"
    ")"
    " select l.*, k.email from leased l"
    f"  join {gateway_store.TABLE} k on k.id = l.onboard_claim_id"
)


class SlackTransientError(RuntimeError):
    """Proven external uncertainty — transport, timeout, 429, 5xx, or a documented transient Slack
    error. The row returns to `pending` behind a bounded schedule."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SlackTerminalError(RuntimeError):
    """Authentication, scope, plan, policy, recipient, or channel state no retry can fix — and any
    reconciliation Slack refuses to expose. The row lands `failed` for operator review."""


class SlackNameTakenError(SlackTerminalError):
    """`conversations.create` refused the deterministic name: this customer's channel already
    exists, so the exact name resolves its ID. Terminal wherever no caller recovers it."""


class SlackConnectConfigError(SlackTerminalError):
    """The configured token does not belong to the expected operator team. Terminal for the row it
    was found on — no channel is mutated and the poller stays alive, so correcting the deploy is all
    it takes for the next signup to deliver and `slack-connect-retry` to re-arm the rows it hit."""


async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None:
    """The operator recovery surface: re-arm one failed row once its cause is corrected, returning
    when it last changed so the verb can report how long it sat. None when nothing was re-armed — a
    delivered row is untouchable, and this never speaks to Slack itself."""
    return await pool.fetchval(
        f"with previous as ("
        f"  select onboard_claim_id, updated_at from {TABLE}"
        f"  where onboard_claim_id = $1 and state = '{STATE_FAILED}' for update"
        ")"
        f" update {TABLE} d set state = '{STATE_PENDING}', worker_id = null,"
        "   claim_expires_at = null, next_attempt_at = null, attempts = 0, last_error = null,"
        "   updated_at = now()"
        " from previous p where d.onboard_claim_id = p.onboard_claim_id"
        " returning p.updated_at",
        onboard_claim_id,
    )


@dataclass(frozen=True)
class SlackConnectClient:
    """Outbound Slack Web API calls under one bot token. Every payload is bounded next to its call,
    no message this raises carries the token, and `repr=False` keeps the token out of every repr —
    so a traceback, an assertion, or a tool that captures locals cannot print it either."""

    bot_token: str = field(repr=False)
    timeout: float = SLACK_TIMEOUT_SECONDS

    async def team_id(self) -> str:
        return self._text(await self._call("auth.test", {}), "team_id")

    async def create_channel(self, name: str) -> str:
        payload = await self._call("conversations.create", {"name": name})
        return self._text(payload, "channel", "id")

    async def channel_id_by_name(self, name: str) -> str:
        """The exact deterministic name, archived channels included — a name-taken refusal names
        one of them. Anything else is inconsistent channel state, not a retryable condition."""
        cursor = ""
        for _ in range(MAX_PAGES):
            payload = await self._call(
                "conversations.list",
                {
                    "types": "public_channel",
                    "exclude_archived": "false",
                    "limit": CHANNEL_PAGE_SIZE,
                    "cursor": cursor,
                },
            )
            for channel in payload.get("channels", []):
                if not isinstance(channel, dict) or channel.get("name") != name:
                    continue
                return self._text(channel, "id")
            cursor = payload.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        raise SlackTerminalError(f"conversations.create refused {name} but no channel carries it")

    async def outgoing_invite_id(self, channel_id: str) -> str | None:
        """The *live* Slack Connect invitation for `channel_id`, or None when this channel has none.

        Slack keeps listing an invitation after it dies — archiving a channel flips its invite to
        `revoked` and leaves it in the list — so matching the channel alone would read a dead
        invitation as proof one was sent and settle a customer who never got a working invite. Only
        `LIVE_INVITE_STATUSES` reconcile; the dead ones mean it is safe to invite again, which is
        evidence rather than a blind duplicate. A status this code does not recognize, and a walk
        that exceeds its bound, are both ambiguous — they raise for operator review."""
        cursor = ""
        unrecognized: set[str] = set()
        for _ in range(MAX_PAGES):
            payload = await self._call(
                "conversations.listConnectInvites", {"count": INVITE_PAGE_SIZE, "cursor": cursor}
            )
            for entry in payload.get("invites", []):
                channel = entry.get("channel") if isinstance(entry, dict) else None
                if not isinstance(channel, dict) or channel.get("id") != channel_id:
                    continue
                status = str(entry.get("status", ""))
                if status in LIVE_INVITE_STATUSES:
                    return self._text(entry, "invite", "id")
                if status not in DEAD_INVITE_STATUSES:
                    unrecognized.add(status)
            cursor = payload.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                if unrecognized:
                    raise SlackTerminalError(
                        f"Slack Connect invite status {sorted(unrecognized)} is unrecognized"
                    )
                return None
        raise SlackTerminalError("outgoing Slack Connect invites exceed the bounded walk")

    async def is_externally_shared(self, channel_id: str) -> bool:
        payload = await self._call("conversations.info", {"channel": channel_id})
        channel = payload.get("channel", {})
        return bool(channel.get("is_ext_shared")) or bool(channel.get("is_pending_ext_shared"))

    async def invite_shared(self, channel_id: str, email: str) -> str:
        if len(email) > MAX_EMAIL_CHARS:
            raise SlackTerminalError(f"recipient email exceeds {MAX_EMAIL_CHARS} characters")
        payload = await self._call(
            "conversations.inviteShared",
            {"channel": channel_id, "emails": email, "external_limited": "false"},
        )
        return self._text(payload, "invite_id")

    def redact(self, message: str) -> str:
        return message.replace(self.bot_token, REDACTION)[:ERROR_CHARS]

    async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{SLACK_API_BASE}/{method}",
                    data={key: value for key, value in params.items() if value != ""},
                    headers={"authorization": f"Bearer {self.bot_token}"},
                )
        except httpx.HTTPError as transport:
            raise SlackTransientError(f"{method} transport failure: {transport}") from transport
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise SlackTransientError(
                f"{method} was rate limited", retry_after=_retry_after(response)
            )
        if response.status_code >= HTTP_SERVER_ERROR:
            raise SlackTransientError(f"{method} returned {response.status_code}")
        if response.is_error:
            raise SlackTerminalError(
                f"{method} returned {response.status_code}: {response.text[:ERROR_CHARS]}"
            )
        payload = response.json()
        if payload.get("ok"):
            return payload
        error = str(payload.get("error", "unknown"))
        if error == NAME_TAKEN:
            raise SlackNameTakenError(f"{method}: {error}")
        if error in TRANSIENT_SLACK_ERRORS:
            raise SlackTransientError(f"{method}: {error}")
        raise SlackTerminalError(f"{method}: {error}")

    def _text(self, payload: dict[str, Any], *path: str) -> str:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                raise SlackTerminalError(f"Slack response is missing {'.'.join(path)}")
            value = value[key]
        return str(value)


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header is None or not header.strip().isdigit():
        return None
    return min(float(header.strip()), MAX_RETRY_AFTER_SECONDS)


@dataclass(frozen=True)
class _Delivery:
    onboard_claim_id: UUID
    email: str
    channel_name: str
    channel_id: str | None
    slack_invitation_id: str | None
    invite_attempted_at: datetime | None
    attempts: int


class _LeaseLost(RuntimeError):
    """Another replica owns this row now, so this worker abandons its writeback untouched."""


@dataclass(frozen=True)
class SlackConnectInviter:
    pool: asyncpg.Pool
    slack: SlackConnectClient
    team_id: str
    worker_id: str
    poll_interval: float = POLL_INTERVAL_SECONDS

    async def run(self) -> None:
        """The gateway lifespan's task: sweep, and wait a whole interval only when nothing was due.
        Only cancellation ends this loop. Nothing a sweep raises retires the poller — a dead poller
        would strand every later signup's row behind a green `/healthz`, and gateway health stays
        clear of Slack — so every fault lands in the row instead: transient ones behind a schedule,
        terminal ones (a wrong-team token included) as `failed` for operator review.

        A sweep that fails before any row is claimed — our own database, not Slack — is reported
        with its consecutive count rather than masked: a quiet tick logs nothing at all, so one
        stack trace is a blip and a climbing count is an internal fault to root-cause. It is
        deliberately not fatal, because a poller that dies here stops every later signup behind a
        healthy `/healthz`."""
        consecutive_failures = 0
        while True:
            try:
                claimed = await self.poll()
                consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                logger.exception("slack_connect.sweep.failed consecutive=%s", consecutive_failures)
                claimed = False
            if not claimed:
                await asyncio.sleep(self.poll_interval)

    async def poll(self) -> bool:
        """Materialize every completed invite-wall claim, then carry one due row as far as Slack
        allows. True when a row was claimed, so a busy queue drains without waiting."""
        await self._materialize()
        delivery = await self._claim()
        if delivery is None:
            return False
        renewal = asyncio.create_task(self._renew_lease(delivery.onboard_claim_id))
        try:
            await self._advance(delivery)
        except _LeaseLost:
            logger.warning("slack_connect.lease.lost claim=%s", delivery.onboard_claim_id)
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
        return True

    async def _materialize(self) -> None:
        """One replica materializes at a time, and no single claim can poison the batch.

        `MATERIALIZE` is one `INSERT ... SELECT`, so any unique violation rolls back every row it
        was inserting rather than only the offender — and because the select re-enumerates every
        eligible claim each cycle, one bad row stalls delivery for everyone forever. So the
        conflict clause arbitrates *all* unique constraints, not only the primary key. That trades a
        loud failure for a skipped row, which is the accepted trade for a readable name: the name
        carries no workspace id, so two customers sharing a domain label derive one name and one of
        them is skipped rather than aborting everyone else's batch. The advisory lock stops two
        replicas racing on the same insert, which the primary-key arbiter alone did not cover."""
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(f"select pg_advisory_xact_lock(hashtext('{TABLE}'))")
                await connection.execute(MATERIALIZE)

    async def _claim(self) -> _Delivery | None:
        row = await self.pool.fetchrow(CLAIM, self.worker_id, LEASE)
        if row is None:
            return None
        return _Delivery(
            onboard_claim_id=row["onboard_claim_id"],
            email=row["email"],
            channel_name=row["channel_name"],
            channel_id=row["channel_id"],
            slack_invitation_id=row["slack_invitation_id"],
            invite_attempted_at=row["invite_attempted_at"],
            attempts=int(row["attempts"]),
        )

    async def _renew_lease(self, onboard_claim_id: UUID) -> None:
        """Hold the lease across an in-flight Slack call. Losing the compare-and-set is silent — the
        delivery path's own writes discover it and abandon — but a renewal that cannot *reach* the
        database is reported and retried on the next tick rather than ending the task. A dead
        renewal would let the lease lapse mid-delivery, and a second replica claiming the row while
        this one's `inviteShared` is still in flight is how a duplicate invitation gets sent: it
        would find no live invite to reconcile against yet. The caller gathers this task with
        `return_exceptions=True`, so an escaping fault would leave no trace at all."""
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            try:
                await self.pool.execute(
                    f"update {TABLE} set claim_expires_at = now() + $3::interval,"
                    "  updated_at = now()"
                    " where onboard_claim_id = $1 and worker_id = $2",
                    onboard_claim_id,
                    self.worker_id,
                    LEASE,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("slack_connect.lease.renew_failed claim=%s", onboard_claim_id)

    async def _advance(self, delivery: _Delivery) -> None:
        try:
            await self._verify_team()
            channel_id = delivery.channel_id or await self._open_channel(delivery)
            invitation_id = delivery.slack_invitation_id or await self._invite(delivery, channel_id)
        except SlackTransientError as error:
            await self._reschedule(delivery, error)
            return
        except SlackTerminalError as error:
            await self._fail(delivery, error)
            return
        except _LeaseLost:
            raise
        except Exception as unexpected:
            logger.exception("slack_connect.unexpected claim=%s", delivery.onboard_claim_id)
            await self._fail(delivery, unexpected)
            return
        await self._write(
            delivery.onboard_claim_id,
            f"state = '{STATE_DELIVERED}', delivered_at = now(), worker_id = null,"
            " claim_expires_at = null, next_attempt_at = null, last_error = null",
        )
        logger.info(
            "slack_connect.delivered claim=%s channel=%s invitation=%s",
            delivery.onboard_claim_id,
            channel_id,
            invitation_id,
        )

    async def _verify_team(self) -> None:
        """Every delivery re-proves the token's workspace before anything is mutated. A memo would
        buy one Slack call per new customer and cost the guarantee: a token rotated to another
        workspace would keep delivering on a replica's stale word until that replica restarted."""
        team_id = await self.slack.team_id()
        if team_id != self.team_id:
            raise SlackConnectConfigError(
                f"{BOT_TOKEN_ENV} belongs to team {team_id}, not {self.team_id}"
            )

    async def _open_channel(self, delivery: _Delivery) -> str:
        try:
            channel_id = await self.slack.create_channel(delivery.channel_name)
        except SlackNameTakenError:
            channel_id = await self.slack.channel_id_by_name(delivery.channel_name)
        await self._write(delivery.onboard_claim_id, "channel_id = $3", channel_id)
        return channel_id

    async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None:
        """A row whose previous attempt reached Slack reconciles first: an outgoing invitation names
        itself, and an externally shared channel proves one landed even where the invitation is no
        longer listed. Only a fresh delivery skips these rate-limited reads."""
        if delivery.invite_attempted_at is not None:
            reconciled = await self.slack.outgoing_invite_id(channel_id)
            if reconciled is not None:
                return await self._persist_invitation(delivery, reconciled)
            if await self.slack.is_externally_shared(channel_id):
                return None
            logger.info(
                "slack_connect.reconcile.no_live_invite claim=%s channel=%s",
                delivery.onboard_claim_id,
                channel_id,
            )
        await self._write(delivery.onboard_claim_id, "invite_attempted_at = now()")
        invitation_id = await self.slack.invite_shared(channel_id, delivery.email)
        return await self._persist_invitation(delivery, invitation_id)

    async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str:
        await self._write(delivery.onboard_claim_id, "slack_invitation_id = $3", invitation_id)
        return invitation_id

    async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None:
        if delivery.attempts >= MAX_ATTEMPTS:
            await self._fail(delivery, error)
            return
        delay = error.retry_after or min(
            RETRY_BACKOFF_SECONDS * 2 ** (delivery.attempts - 1), RETRY_BACKOFF_MAX_SECONDS
        )
        await self._write(
            delivery.onboard_claim_id,
            f"state = '{STATE_PENDING}', worker_id = null, claim_expires_at = null,"
            "  next_attempt_at = now() + $3::interval, last_error = $4",
            timedelta(seconds=delay),
            self.slack.redact(str(error)),
        )
        logger.warning(
            "slack_connect.retry claim=%s attempts=%s in=%ss",
            delivery.onboard_claim_id,
            delivery.attempts,
            delay,
        )

    async def _fail(self, delivery: _Delivery, error: Exception) -> None:
        await self._write(
            delivery.onboard_claim_id,
            f"state = '{STATE_FAILED}', worker_id = null, claim_expires_at = null,"
            "  next_attempt_at = null, last_error = $3",
            self.slack.redact(str(error)),
        )
        logger.error(
            "slack_connect.failed claim=%s error=%s",
            delivery.onboard_claim_id,
            self.slack.redact(str(error)),
        )

    async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None:
        owned = await self.pool.fetchval(
            f"update {TABLE} set {assignment}, updated_at = now()"
            " where onboard_claim_id = $1 and worker_id = $2 returning true",
            onboard_claim_id,
            self.worker_id,
            *values,
        )
        if not owned:
            raise _LeaseLost(f"{onboard_claim_id} is no longer leased by {self.worker_id}")


def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None:
    """None when the deploy has not enabled signup Slack Connect invitations. Enabled, the token and
    the expected operator team are required here — at gateway startup — so a half-configured deploy
    never reaches Slack. Garbage in the switch fails loud and never defaults to on."""
    match os.environ.get(ENABLED_ENV, "false").strip().lower():
        case "false" | "0":
            return None
        case "true" | "1":
            return SlackConnectInviter(
                pool=pool,
                slack=SlackConnectClient(bot_token=_require_env(BOT_TOKEN_ENV)),
                team_id=_require_env(TEAM_ID_ENV),
                worker_id=f"{socket.gethostname()}.{os.getpid()}",
            )
        case other:
            raise RuntimeError(f"{ENABLED_ENV}={other!r} is not a boolean (true/false)")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required when {ENABLED_ENV} is true")
    return value
