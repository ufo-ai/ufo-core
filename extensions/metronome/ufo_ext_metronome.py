"""Ship settled ledger usage and daily seat counts to Metronome, and drive seat changes from chat.

The usage job drains, per workspace, the usage-export seam (`ctx.pending_usage_exports`): core
mints one frozen intent per settled ledger-row delta, and each intent becomes one ingest event
whose `transaction_id = "<ledger id>:<amount already shipped>"` — deterministic, so a crash or
unacknowledged delivery re-sends the identical event and Metronome's 34-day dedup absorbs it, and
append-only, so a row that grows after shipping ships a top-up on a later tick instead of
undercounting. Intents are acknowledged only after Metronome accepts the batch. A per-workspace
floor recorded on the first run bounds the initial backfill to `BACKFILL_WINDOW_DAYS`; it never
moves after, so a settled row ships however long it waited.

The seat job establishes the workspace's seat limit once (core's `ensure_limit` writes only while
it is NULL) and ships one seat-count snapshot per day under `transaction_id =
"seats:<workspace>:<date>"` — snapshots self-correct on the next day's event, so a lost mark can
never accumulate an undercount. Seat changes are chat acts: the owner asks and the agent calls
`grant_seat`/`revoke_seat`; the rules (the count, the limit, the owner's irrevocable seat) are
core's — this module only decides when to apply them and what to report back.

Every usage event is labelled `byok`: a workspace holding its own key for the provider serving
the model — `anthropic_api_key` (declared here so the standard `request_credentials` chat handoff
can fill it), `bedrock_api_key`, whatever a provider extension declares — pays that provider
directly, so the rate card bills only `byok="false"` usage while everything stays visible. Core
freezes the label into each export intent at mint, resolved through the deploy's model registry —
this module only relays `export.byok` — so a backlog drained after an outage carries the key
state that served it, and a re-send is byte-identical whatever changed since.

Billing setup is a chat act too. The owner asks, the agent calls `manage_billing`, and the tool
hands back a short-lived Stripe Customer Portal link for saving a payment method — no callback, no
webhook, no billing table. What the tool persists is the workspace's provider ids and the package
it intends to buy; the `billing_activation` job turns that intent into a live plan once Stripe
reports a default payment method, and tells the owner once. Every provider write carries a durable
identity — a deterministic key for the Stripe Customer, the workspace UUID as the Metronome
customer's ingest alias (the same id every usage event is stamped with), a stable `uniqueness_key`
for the Contract — so a conflict is reconciled by fetching the object that already exists and a key
is never rotated to get past one. Those identities are also how an object is recognized later: the
workspace's plan is the contract carrying its `uniqueness_key`, never whichever contract the
customer happens to list first, because a customer can hold contracts this workspace never bought.
The plan is a Metronome Contract, so no Stripe Subscription is ever created.

Two provider details are pinned here rather than discovered per call. `STRIPE_API_VERSION` fixes the
Stripe API version, so a provider-side default bump can never reshape a response underneath us — the
version Stripe's own SDKs pin is the one taken. `CONTRACTS_LIST_PATH` is on v2 because Metronome
disabled the v1 contract list ("Please use the v2 endpoint to list contracts") while contract
creation stays on the documented v1 path; both responses carry the identical `id` and
`uniqueness_key` this reads. The opt-in provider smoke is what validates a change to either.

Which Metronome environment receives the events — sandbox or production — is decided entirely by
whose bearer token `METRONOME_BEARER_TOKEN` carries."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.accounting import UsageExport, metered_workspaces
from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import CredentialSlot, Manifest, PromptSection
from ufo.sdk.o11y import log
from ufo.sdk.seats import Seats, SeatSnapshot, member_workspaces, owner_conversation
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "metronome"
VERSION = "0.1.0"
JOB_NAME = "usage_shipper"
JOB_SCHEDULE = "0 * * * * *"
SEAT_JOB_NAME = "seat_shipper"
SEAT_JOB_SCHEDULE = "0 0 * * * *"
METRONOME_API = "https://api.metronome.com"
INGEST_URL = f"{METRONOME_API}/v1/ingest"
METRONOME_BEARER_TOKEN_ENV = "METRONOME_BEARER_TOKEN"
METRONOME_PACKAGE_ALIAS_ENV = "METRONOME_PACKAGE_ALIAS"
CONTRACTS_LIST_PATH = "/v2/contracts/list"
STRIPE_API = "https://api.stripe.com/v1"
STRIPE_API_VERSION = "2026-02-25.clover"
STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_PORTAL_CONFIGURATION_ENV = "STRIPE_BILLING_PORTAL_CONFIGURATION_ID"
EVENT_TYPE = "ufo_usage"
ANTHROPIC_KEY_SLOT = "anthropic_api_key"
SEAT_EVENT_TYPE = "ufo_seats"
SEAT_LIMIT_DEFAULT = 25
INCLUDED_SEATS_DEFAULT = 5
SEAT_APPROVAL_JOB_NAME = "seat_approvals"
SEAT_APPROVAL_JOB_SCHEDULE = "30 * * * * *"
SEAT_APPROVAL_KEY_PREFIX = "seat_approval_asked/"
SEAT_APPROVAL_PROMPT = (
    "[seat approval request] {email} joined the workspace but every included seat is taken "
    "({seated} seated, {included} included in the plan). Ask the workspace owner to decide with "
    "the ask_user tool, question 'Grant {email} a seat? It bills as overage on the invoice.' and "
    "options 'Grant the seat' and 'Decline'. When they choose Grant, call grant_seat with that "
    "email and confirm the overage; when they Decline, confirm and take no action — the member "
    "stays unseated."
)
SEAT_SHIPPED_KEY = "seats_shipped_date"
BATCH_EVENTS = 100
INGEST_TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 7
FLOOR_KEY = "ship_floor"

BILLING_JOB_NAME = "billing_activation"
BILLING_JOB_SCHEDULE = "45 * * * * *"
BILLING_KEY = "billing"
BILLING_TIMEOUT_SECONDS = 30
STRIPE_BILLING_PROVIDER = "stripe"
STRIPE_COLLECTION_METHOD = "charge_automatically"
STRIPE_DELIVERY_METHOD = "direct_to_billing_provider"
PAYMENT_METHOD_UPDATE_FLOW = "payment_method_update"
BILLING_ACTIVE_PROMPT = (
    "[billing activated] The workspace's payment method is saved and the {package} plan is live. "
    "Tell the workspace owner in one short line, and mention they can ask you for billing status "
    "or the billing portal whenever they want."
)

GRANT_SEAT_TOOL = "grant_seat"
REVOKE_SEAT_TOOL = "revoke_seat"
LIST_SEATS_TOOL = "list_seats"
MANAGE_BILLING_TOOL = "manage_billing"

GRANT_SEAT_DESCRIPTION = (
    "Grant a workspace seat to a member by email so the agent answers them. Owner-only. A seat "
    "beyond the plan's included allowance bills as overage on the invoice — say so when the "
    "owner approves one. Fails when the hard seat limit is reached; raising that is not a chat "
    "act, contact us."
)
REVOKE_SEAT_DESCRIPTION = (
    "Revoke a member's seat by email. Owner-only; the owner's own seat cannot be revoked. The "
    "member's next message is refused immediately, and a running turn of theirs holds at its "
    "next model round."
)
LIST_SEATS_DESCRIPTION = "Show the workspace's seat limit and who holds a seat."
MANAGE_BILLING_DESCRIPTION = (
    "Set up or inspect the workspace's billing plan. Owner-only, and only in the owner's own "
    "private conversation. 'setup' returns a short-lived Stripe link for saving a payment method "
    "and records the plan to activate once it is saved; 'status' reports the card and the plan as "
    "the providers currently hold them; 'portal' returns a fresh link for invoices, payment "
    "methods, and billing details. Give the returned portal_url to the owner as a link."
)

SEATS_SECTION_NAME = "seats"
SEATS_SECTION_BODY = (
    "Seats gate who this agent answers. A newly joined member is seated automatically while an "
    "included seat is open; beyond the included allowance they stay unseated, their messages are "
    "refused, and the owner receives a seat approval request — if the owner approves, call "
    "grant_seat with the member's email and note the seat bills as overage; if they decline, do "
    "nothing. Only the workspace owner can change seats (grant_seat / revoke_seat); list_seats "
    "shows the limit, the included allowance, billed overage seats, and who holds one."
)

BILLING_SECTION_NAME = "billing"
BILLING_SECTION_BODY = (
    "Billing belongs to the workspace owner and is discussed only in their own private "
    "conversation. When the owner asks to set up billing, add a card, or start a plan — including "
    "the 'Set up billing' choice that ends hosted onboarding — call manage_billing with action "
    "'setup' and give them the returned portal_url as a link to open. Say that the plan goes live "
    "shortly after they save a card and that you will tell them here when it does; never claim it "
    "is active before the tool reports it. 'status' reports whether a card is on file and whether "
    "the plan is live; 'portal' returns a fresh link for invoices, payment methods, and billing "
    "details. Never show a billing link to anyone but the owner."
)

INGEST_TRANSPORT: httpx.AsyncBaseTransport | None = None
BILLING_TRANSPORT: httpx.AsyncBaseTransport | None = None


class MetronomeError(RuntimeError):
    """Metronome answered a non-2xx status — surfaced with status and body so the failed call is
    loud; for a job the next scheduled fire is the retry, and the durable identity on every write
    (ingest `transaction_id`, ingest alias, `uniqueness_key`) absorbs the re-send."""


class MetronomeConflict(MetronomeError):
    """Metronome answered 409: the ingest alias or `uniqueness_key` this call tried to claim is
    already held. The caller reconciles by fetching the object that holds it — never by rotating
    the key."""


class StripeError(RuntimeError):
    """Stripe answered a non-2xx status — surfaced with status and body. Nothing is recorded for a
    failed call, so the owner's next attempt or the next job tick starts from the same state."""


@dataclass(frozen=True)
class UsageShipper:
    """Drain one workspace's pending usage exports to Metronome's ingest API. Each pass reads a
    batch of frozen delta intents, POSTs them, then acknowledges — strictly in that order, so a
    crash between POST and ack re-sends byte-identical events and Metronome deduplicates. The
    `transport` field is the httpx testability seam; production leaves it None."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        token = _require_env(METRONOME_BEARER_TOKEN_ENV)
        floor = await self._floor()
        while True:
            exports = await self.ctx.pending_usage_exports(floor, BATCH_EVENTS)
            if not exports:
                return
            await _ingest(token, self._events(exports), self.transport)
            log(
                "metronome.shipped",
                count=len(exports),
                workspace_id=str(self.ctx.store.workspace_id),
            )
            await self.ctx.ack_usage_exports(exports)
            if len(exports) < BATCH_EVENTS:
                return

    async def _floor(self) -> datetime:
        """The fixed per-workspace shipping floor: usage settled before it never ships. Recorded
        once on the first run — bounding the initial backfill inside Metronome's backdating
        window — and never moved after, so a row left unsent through an outage or an unkeyed
        deploy is picked up whenever shipping resumes, never aged out."""
        stored = await self.ctx.store.get(FLOOR_KEY)
        if stored is None:
            floor = datetime.now(UTC) - timedelta(days=BACKFILL_WINDOW_DAYS)
            await self.ctx.store.put(FLOOR_KEY, floor.isoformat())
            return floor
        return datetime.fromisoformat(str(stored))

    def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]:
        customer_id = str(self.ctx.store.workspace_id)
        return [
            {
                "transaction_id": f"{export.ledger_id}:{export.from_amount}",
                "customer_id": customer_id,
                "event_type": EVENT_TYPE,
                "timestamp": _rfc3339(export.occurred_at),
                "properties": {
                    "dimension": export.dimension,
                    "model": export.model,
                    "amount": str(export.amount),
                    "priced_micro_usd": str(export.priced_micro_usd),
                    "price_digest": export.price_digest or "",
                    "turn_id": str(export.turn_id) if export.turn_id else "",
                    "byok": "true" if export.byok else "false",
                },
            }
            for export in exports
        ]


async def _ship(ctx: ExtensionContext) -> None:
    await UsageShipper(ctx=ctx, transport=INGEST_TRANSPORT).run()


@dataclass(frozen=True)
class SeatShipper:
    """Establish the workspace's seat limit and ship one seat-count snapshot per day. The
    transaction_id is the workspace-day, so a retry after a failed POST re-sends within
    Metronome's keep-first dedup, and the next day's snapshot corrects whatever a stale first
    event froze — snapshots never accumulate an undercount."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        token = _require_env(METRONOME_BEARER_TOKEN_ENV)
        today = datetime.now(UTC).date().isoformat()
        if await self.ctx.store.get(SEAT_SHIPPED_KEY) == today:
            return
        workspace_id = self.ctx.store.workspace_id
        async with self.ctx.transaction() as connection:
            await Seats(workspace_id).ensure_limit(connection, SEAT_LIMIT_DEFAULT)
            await Seats(workspace_id).ensure_included(connection, INCLUDED_SEATS_DEFAULT)
            snapshot = await Seats(workspace_id).snapshot(connection)
        await _ingest(token, [self._event(snapshot, today)], self.transport)
        log(
            "metronome.seats_shipped",
            workspace_id=str(workspace_id),
            seat_count=snapshot.seated,
        )
        await self.ctx.store.put(SEAT_SHIPPED_KEY, today)

    def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]:
        workspace_id = str(self.ctx.store.workspace_id)
        return {
            "transaction_id": f"seats:{workspace_id}:{today}",
            "customer_id": workspace_id,
            "event_type": SEAT_EVENT_TYPE,
            "timestamp": _rfc3339(datetime.now(UTC)),
            "properties": {
                "seat_count": str(snapshot.seated),
                "seat_limit": "" if snapshot.limit is None else str(snapshot.limit),
            },
        }


async def _ship_seats(ctx: ExtensionContext) -> None:
    await SeatShipper(ctx=ctx, transport=INGEST_TRANSPORT).run()


@dataclass(frozen=True)
class SeatApprovals:
    """Turn every never-asked unseated member into one approval request in the owner's own
    conversation: an internal turn tells the owner who needs a seat and that granting bills as
    overage, and the owner's reply drives grant_seat — chat-native consent, no new surface. The
    ask is marked per member only after the invoke lands, so a fire without an owner conversation
    retries next tick, and a granted or declined member is never re-asked (the mark is the ask,
    not the answer; revoke_seat marks too, so an explicitly unseated member is a decision, not a
    request). Asks fire only while the included allowance is exhausted — an unseated member with
    a silent seat still open is the owner's own doing, never a request."""

    ctx: ExtensionContext

    async def run(self) -> None:
        async with self.ctx.transaction() as connection:
            snapshot = await Seats(self.ctx.store.workspace_id).snapshot(connection)
        if snapshot.included is None or snapshot.seated < snapshot.included:
            return
        pending = [entry for entry in snapshot.members if not entry.seated]
        for entry in pending:
            marker = f"{SEAT_APPROVAL_KEY_PREFIX}{entry.email.strip().lower()}"
            if await self.ctx.store.get(marker) is not None:
                continue
            async with self.ctx.transaction() as connection:
                venue = await owner_conversation(connection, self.ctx.store.workspace_id)
            if venue is None:
                return
            conversation_id, agent_id = venue
            await self.ctx.invoke(
                conversation_id,
                agent_id,
                SEAT_APPROVAL_PROMPT.format(
                    email=entry.email,
                    seated=snapshot.seated,
                    included=snapshot.included,
                ),
                idempotency_key=f"seat-approval:{entry.email}",
            )
            await self.ctx.store.put(marker, datetime.now(UTC).isoformat())


async def _ask_seat_approvals(ctx: ExtensionContext) -> None:
    await SeatApprovals(ctx=ctx).run()


class BillingConfig(BaseModel):
    """The four settings the billing workflow cannot run without, read and validated once at the
    entry to a tool call or a tick — before any provider object exists, so a half-configured deploy
    can never leave a Stripe Customer behind and then fail on the portal configuration. Every
    missing name is reported at once rather than one per attempt. The usage and seat shippers read
    only the bearer token directly: a deploy that meters usage without selling a plan must keep
    shipping, so they never depend on this."""

    model_config = ConfigDict(frozen=True)

    stripe_secret_key: str
    stripe_portal_configuration_id: str
    metronome_bearer_token: str
    metronome_package_alias: str

    @classmethod
    def from_env(cls) -> "BillingConfig":
        found = {
            "stripe_secret_key": os.environ.get(STRIPE_SECRET_KEY_ENV),
            "stripe_portal_configuration_id": os.environ.get(STRIPE_PORTAL_CONFIGURATION_ENV),
            "metronome_bearer_token": os.environ.get(METRONOME_BEARER_TOKEN_ENV),
            "metronome_package_alias": os.environ.get(METRONOME_PACKAGE_ALIAS_ENV),
        }
        missing = [
            name
            for name, field in (
                (STRIPE_SECRET_KEY_ENV, "stripe_secret_key"),
                (STRIPE_PORTAL_CONFIGURATION_ENV, "stripe_portal_configuration_id"),
                (METRONOME_BEARER_TOKEN_ENV, "metronome_bearer_token"),
                (METRONOME_PACKAGE_ALIAS_ENV, "metronome_package_alias"),
            )
            if not found[field]
        ]
        if missing:
            raise RuntimeError(f"billing requires {', '.join(missing)}")
        return cls.model_validate(found)


class BillingRecord(BaseModel):
    """One workspace's billing provisioning state, as the extension store holds it. Written by the
    `setup` tool the moment a Stripe Customer exists — before the owner is handed the portal link —
    and completed by the activation job. The provider ids are the durable identities every later
    call resolves against; `package_alias` and `contract_starting_at` are the intent captured at
    setup, so neither a package the deploy renames nor the passage of time changes what a pending
    workspace was promised — and every contract-create retry, however far apart, sends
    byte-identical parameters. A record with no `activated_at` is the job's pending work."""

    stripe_customer_id: str
    package_alias: str
    contract_starting_at: datetime
    metronome_customer_id: str | None = None
    metronome_contract_id: str | None = None
    activated_at: datetime | None = None


@dataclass(frozen=True)
class BillingActivation:
    """Turn a workspace's saved payment method into a live Metronome plan.

    Each step commits before the next runs, so a tick that dies part-way resumes exactly where it
    stopped rather than redoing provider writes: no default payment method leaves the record
    untouched and pending, a created Metronome customer is recorded (and recovered by ingest alias
    if the record was lost), and the contract's stable uniqueness key makes a duplicate create a
    409 the next tick reconciles by reading the contract that already exists. The owner is told
    once, and the activation mark lands only after that turn is admitted — so a workspace whose
    owner has no conversation yet stays pending instead of going quiet."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        record = await _billing_record(self.ctx)
        if record is None or record.activated_at is not None:
            return
        config = BillingConfig.from_env()
        if not await _has_default_payment_method(config, record.stripe_customer_id, self.transport):
            return
        workspace_id = str(self.ctx.store.workspace_id)
        customer_id = record.metronome_customer_id
        if customer_id is None:
            customer_id = await _metronome_customer(
                config, workspace_id, record.stripe_customer_id, self.transport
            )
            record = await self._store(
                record.model_copy(update={"metronome_customer_id": customer_id})
            )
        if record.metronome_contract_id is None:
            contract_id = await _metronome_contract(
                config,
                customer_id,
                record,
                _contract_key(self.ctx.store.workspace_id),
                self.transport,
            )
            record = await self._store(
                record.model_copy(update={"metronome_contract_id": contract_id})
            )
        await self._notify(record)

    async def _store(self, record: BillingRecord) -> BillingRecord:
        await self.ctx.store.put(BILLING_KEY, record.model_dump(mode="json"))
        return record

    async def _notify(self, record: BillingRecord) -> None:
        async with self.ctx.transaction() as connection:
            venue = await owner_conversation(connection, self.ctx.store.workspace_id)
        if venue is None:
            return
        conversation_id, agent_id = venue
        workspace_id = self.ctx.store.workspace_id
        await self.ctx.invoke(
            conversation_id,
            agent_id,
            BILLING_ACTIVE_PROMPT.format(package=record.package_alias),
            idempotency_key=f"billing-active:{workspace_id}",
        )
        await self._store(record.model_copy(update={"activated_at": datetime.now(UTC)}))
        log(
            "metronome.billing_activated",
            workspace_id=str(workspace_id),
            contract_id=record.metronome_contract_id,
        )


async def _activate_billing(ctx: ExtensionContext) -> None:
    await BillingActivation(ctx=ctx, transport=BILLING_TRANSPORT).run()


async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None:
    stored = await ctx.store.get(BILLING_KEY)
    return None if stored is None else BillingRecord.model_validate(stored)


def _contract_key(workspace_id: UUID) -> str:
    """The workspace's permanent contract identity: the `uniqueness_key` Metronome stores on the
    Contract we create, and the only field a later read identifies it by. Never rotated — a conflict
    on it means our contract already exists, not that we need a different key."""
    return f"ufo-contract:{workspace_id}"


class GrantSeatInput(BaseModel):
    email: str = Field(description="Email of the workspace member to seat.")


class RevokeSeatInput(BaseModel):
    email: str = Field(description="Email of the seated member to unseat.")


class ListSeatsInput(BaseModel):
    pass


class ManageBillingInput(BaseModel):
    action: Literal["setup", "status", "portal"] = Field(
        description=(
            "setup: start payment setup and return a link for saving a card. status: report the "
            "card and plan the providers currently hold. portal: return a link for invoices, "
            "payment methods, and billing details."
        )
    )


async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult:
    seats = await _owner_seats(ctx)
    assert ctx.ext is not None
    async with ctx.ext.transaction() as connection:
        await seats.grant(connection, args.email)
        snapshot = await seats.snapshot(connection)
    return _snapshot_result(snapshot)


async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult:
    seats = await _owner_seats(ctx)
    assert ctx.ext is not None
    async with ctx.ext.transaction() as connection:
        await seats.revoke(connection, args.email)
        snapshot = await seats.snapshot(connection)
    await ctx.ext.store.put(
        f"{SEAT_APPROVAL_KEY_PREFIX}{args.email.strip().lower()}",
        datetime.now(UTC).isoformat(),
    )
    return _snapshot_result(snapshot)


async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult:
    assert ctx.ext is not None
    async with ctx.ext.transaction() as connection:
        snapshot = await Seats(ctx.turn.workspace_id).snapshot(connection)
    return _snapshot_result(snapshot)


async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult:
    ext = await _owner_billing(ctx)
    config = BillingConfig.from_env()
    match args.action:
        case "setup":
            return await _billing_setup(ext, config)
        case "status":
            return await _billing_status(ext, config)
        case "portal":
            return await _billing_portal(ext, config)


async def _owner_billing(ctx: ToolContext) -> ExtensionContext:
    """Billing is the owner's act and never leaves their private conversation: a speakerless turn, a
    teammate, or a shared channel is refused here — before any provider call, so a refused caller
    cannot even cause a Stripe write."""
    if ctx.speaker_member_id is None:
        raise ValueError("billing requires a speaking member")
    if ctx.audience_member_id != ctx.speaker_member_id:
        raise ValueError("billing requires the speaker's private conversation")
    if not await ctx.speaker_is_owner():
        raise ValueError("only the workspace owner can manage billing")
    assert ctx.ext is not None
    return ctx.ext


async def _billing_setup(ext: ExtensionContext, config: BillingConfig) -> ToolResult:
    """Resolve the workspace's one Stripe Customer, then hand back a portal link that does exactly
    one thing: save a payment method.

    The record is written once — only when it does not exist yet — and that write lands before the
    link is returned, so the activation job owns the follow-through by the time the owner opens it.
    A later setup has nothing to add and must not write: putting a re-read record back would let a
    setup overlapping the job revert the provider ids that job had just recorded. What the first
    setup captures is what every later provisioning attempt replays: the package, and a contract
    start truncated to the hour, because a package contract must begin on an hour boundary and
    Metronome rejects microsecond precision outright."""
    workspace_id = ext.store.workspace_id
    record = await _billing_record(ext)
    if record is None:
        record = BillingRecord(
            stripe_customer_id=await _stripe_customer(config, workspace_id, BILLING_TRANSPORT),
            package_alias=config.metronome_package_alias,
            contract_starting_at=datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
        )
        await ext.store.put(BILLING_KEY, record.model_dump(mode="json"))
    url = await _portal_session(
        config, record.stripe_customer_id, PAYMENT_METHOD_UPDATE_FLOW, BILLING_TRANSPORT
    )
    log(
        "metronome.billing_setup",
        workspace_id=str(workspace_id),
        customer_id=record.stripe_customer_id,
    )
    return _text_result(
        {
            "portal_url": url,
            "stripe_customer_id": record.stripe_customer_id,
            "package": record.package_alias,
        }
    )


async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult:
    """Current provider truth, not our record: Stripe says whether a card is on file and Metronome
    says whether this workspace's own contract exists. The record supplies only the ids to ask
    about — a contract on the customer that is not ours never counts as this workspace's plan."""
    record = await _billing_record(ext)
    if record is None:
        return _text_result({"configured": False})
    paid = await _has_default_payment_method(config, record.stripe_customer_id, BILLING_TRANSPORT)
    contract_id = (
        None
        if record.metronome_customer_id is None
        else await _contract_for(
            config,
            record.metronome_customer_id,
            _contract_key(ext.store.workspace_id),
            BILLING_TRANSPORT,
        )
    )
    return _text_result(
        {
            "configured": True,
            "stripe_customer_id": record.stripe_customer_id,
            "payment_method_on_file": paid,
            "package": record.package_alias,
            "metronome_customer_id": record.metronome_customer_id,
            "metronome_contract_id": contract_id,
            "plan_active": contract_id is not None,
        }
    )


async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult:
    """A fresh management portal session — billing details, payment methods, invoices. Subscription
    mutation is off in the configured portal: the plan is a Metronome Contract, not a Stripe
    Subscription."""
    record = await _billing_record(ext)
    if record is None:
        raise ValueError("billing is not set up for this workspace yet; run setup first")
    url = await _portal_session(config, record.stripe_customer_id, None, BILLING_TRANSPORT)
    return _text_result({"portal_url": url})


async def _owner_seats(ctx: ToolContext) -> Seats:
    if ctx.speaker_member_id is None:
        raise ValueError("seat changes require a speaking member")
    if not await ctx.speaker_is_owner():
        raise ValueError("only the workspace owner can change seats")
    return Seats(ctx.turn.workspace_id)


def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult:
    return _text_result(
        {
            "seat_limit": snapshot.limit,
            "included_seats": snapshot.included,
            "billed_overage_seats": (
                max(0, snapshot.seated - snapshot.included) if snapshot.included is not None else 0
            ),
            "seated": snapshot.seated,
            "members": [
                {"email": entry.email, "seated": entry.seated, "owner": entry.owner}
                for entry in snapshot.members
            ],
        }
    )


def _text_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


GRANT_SEAT_TOOL_DEF = ToolDef(
    name=GRANT_SEAT_TOOL,
    description=GRANT_SEAT_DESCRIPTION,
    input_model=GrantSeatInput,
    handler=grant_seat,
    side_effecting=True,
)
REVOKE_SEAT_TOOL_DEF = ToolDef(
    name=REVOKE_SEAT_TOOL,
    description=REVOKE_SEAT_DESCRIPTION,
    input_model=RevokeSeatInput,
    handler=revoke_seat,
    side_effecting=True,
)
LIST_SEATS_TOOL_DEF = ToolDef(
    name=LIST_SEATS_TOOL,
    description=LIST_SEATS_DESCRIPTION,
    input_model=ListSeatsInput,
    handler=list_seats,
)
MANAGE_BILLING_TOOL_DEF = ToolDef(
    name=MANAGE_BILLING_TOOL,
    description=MANAGE_BILLING_DESCRIPTION,
    input_model=ManageBillingInput,
    handler=manage_billing,
    side_effecting=True,
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; the metronome extension requires it")
    return value


async def _stripe_customer(
    config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None
) -> str:
    """The workspace's one Stripe Customer. The idempotency key is derived from the workspace, so a
    redelivered create settles on the customer the first attempt made instead of minting a second
    one; the workspace id also rides `metadata` so the customer is traceable from Stripe's side."""
    created = await _stripe(
        config,
        "POST",
        "/customers",
        transport,
        data={
            "description": f"ufo workspace {workspace_id}",
            "metadata[workspace_id]": str(workspace_id),
        },
        idempotency_key=f"ufo-stripe-customer:{workspace_id}",
    )
    return _as_str(created.get("id"), "stripe customer id")


async def _portal_session(
    config: BillingConfig,
    customer_id: str,
    flow: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    """A short-lived Customer Portal URL under the deploy's portal configuration — the configuration
    is what keeps subscription mutation out of the member's hands. `flow` narrows the session to one
    task (payment-method update at setup); None opens the full management portal."""
    data = {
        "customer": customer_id,
        "configuration": config.stripe_portal_configuration_id,
    }
    if flow is not None:
        data["flow_data[type]"] = flow
    session = await _stripe(config, "POST", "/billing_portal/sessions", transport, data=data)
    return _as_str(session.get("url"), "stripe portal url")


async def _has_default_payment_method(
    config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None
) -> bool:
    """Whether Stripe holds a default payment method for the customer — the one gate on activation.
    The portal's payment-method-update flow sets exactly this field, so it is the provider's own
    answer to 'has the owner paid', never a flag of ours."""
    customer = await _stripe(config, "GET", f"/customers/{customer_id}", transport)
    match customer.get("invoice_settings"):
        case {"default_payment_method": str()}:
            return True
    return False


async def _stripe(
    config: BillingConfig,
    method: str,
    path: str,
    transport: httpx.AsyncBaseTransport | None,
    data: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    headers = {
        "Authorization": f"Bearer {config.stripe_secret_key}",
        "Stripe-Version": STRIPE_API_VERSION,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=BILLING_TIMEOUT_SECONDS, transport=transport) as http:
        response = await http.request(method, f"{STRIPE_API}{path}", data=data, headers=headers)
    if not response.is_success:
        raise StripeError(f"stripe {path} failed ({response.status_code}): {response.text}")
    return response.json()


async def _metronome_customer(
    config: BillingConfig,
    alias: str,
    stripe_customer_id: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    """The workspace's Metronome customer, carrying the workspace UUID as an ingest alias — the same
    id every usage event is stamped with, so events match the customer they bill. Looked up by that
    alias first and reconciled to it on conflict, so the alias (not a 24-hour idempotency key) is
    the durable identity. Created with the Stripe automatic-collection configuration, so Metronome
    invoices charge the card the owner just saved."""
    existing = await _customer_by_alias(config, alias, transport)
    if existing is not None:
        return existing
    body: dict[str, object] = {
        "name": f"ufo workspace {alias}",
        "ingest_aliases": [alias],
        "customer_billing_provider_configurations": [
            {
                "billing_provider": STRIPE_BILLING_PROVIDER,
                "delivery_method": STRIPE_DELIVERY_METHOD,
                "configuration": {
                    "stripe_customer_id": stripe_customer_id,
                    "stripe_collection_method": STRIPE_COLLECTION_METHOD,
                },
            }
        ],
    }
    try:
        created = await _metronome(
            config,
            "POST",
            "/v1/customers",
            transport,
            body=body,
            idempotency_key=f"ufo-metronome-customer:{alias}",
        )
    except MetronomeConflict:
        reconciled = await _customer_by_alias(config, alias, transport)
        if reconciled is None:
            raise
        return reconciled
    match created.get("data"):
        case {"id": str() as customer_id}:
            return customer_id
    raise MetronomeError(f"metronome customer create returned no id: {created}")


async def _customer_by_alias(
    config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None
) -> str | None:
    found = await _metronome(
        config, "GET", "/v1/customers", transport, params={"ingest_alias": alias}
    )
    match found.get("data"):
        case [{"id": str() as customer_id}, *_]:
            return customer_id
    return None


async def _metronome_contract(
    config: BillingConfig,
    customer_id: str,
    record: BillingRecord,
    uniqueness_key: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    """The workspace's plan: one Contract provisioned from the configured Package, identified for
    all time by `uniqueness_key`. Reading that key back is both the resume path and the
    reconciliation for the 409 a reused key raises. The start comes from the record rather than the
    clock, so every attempt — however far apart — sends identical parameters and can never trip the
    key on a mismatch."""
    existing = await _contract_for(config, customer_id, uniqueness_key, transport)
    if existing is not None:
        return existing
    try:
        created = await _metronome(
            config,
            "POST",
            "/v1/contracts/create",
            transport,
            body={
                "customer_id": customer_id,
                "starting_at": _rfc3339(record.contract_starting_at),
                "package_alias": record.package_alias,
                "uniqueness_key": uniqueness_key,
            },
        )
    except MetronomeConflict:
        reconciled = await _contract_for(config, customer_id, uniqueness_key, transport)
        if reconciled is None:
            raise
        return reconciled
    match created.get("data"):
        case {"id": str() as contract_id}:
            return contract_id
    raise MetronomeError(f"metronome contract create returned no id: {created}")


async def _contract_for(
    config: BillingConfig,
    customer_id: str,
    uniqueness_key: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str | None:
    """This workspace's own live contract on the customer, matched by the `uniqueness_key` we minted
    for it — never by list position. A Metronome customer can carry contracts this workspace never
    asked for (an operator-provisioned trial, a hand-built plan), and adopting one of those would
    report a plan the workspace does not have while suppressing the create that would give it one.
    Archived contracts are absent from this read, so a match is a live plan; no match means ours
    does not exist yet, whatever else the customer holds."""
    listed = await _metronome(
        config, "POST", CONTRACTS_LIST_PATH, transport, body={"customer_id": customer_id}
    )
    match listed.get("data"):
        case [*contracts]:
            for contract in contracts:
                match contract:
                    case {"id": str() as contract_id, "uniqueness_key": key} if (
                        key == uniqueness_key
                    ):
                        return contract_id
    return None


async def _metronome(
    config: BillingConfig,
    method: str,
    path: str,
    transport: httpx.AsyncBaseTransport | None,
    body: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {config.metronome_bearer_token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=BILLING_TIMEOUT_SECONDS, transport=transport) as http:
        response = await http.request(
            method, f"{METRONOME_API}{path}", json=body, params=params, headers=headers
        )
    if response.status_code == HTTPStatus.CONFLICT:
        raise MetronomeConflict(f"metronome {path} conflicted: {response.text}")
    if not response.is_success:
        raise MetronomeError(f"metronome {path} failed ({response.status_code}): {response.text}")
    return response.json()


def _as_str(value: object, field: str) -> str:
    match value:
        case str() if value:
            return value
    raise ValueError(f"provider response carried no {field}")


async def _ingest(
    token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None
) -> None:
    async with httpx.AsyncClient(timeout=INGEST_TIMEOUT_SECONDS, transport=transport) as http:
        response = await http.post(
            INGEST_URL, json=events, headers={"Authorization": f"Bearer {token}"}
        )
    if not response.is_success:
        raise MetronomeError(f"metronome ingest failed ({response.status_code}): {response.text}")


def _rfc3339(moment: datetime) -> str:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.isoformat()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            GRANT_SEAT_TOOL_DEF,
            REVOKE_SEAT_TOOL_DEF,
            LIST_SEATS_TOOL_DEF,
            MANAGE_BILLING_TOOL_DEF,
        ),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=_ship,
                candidates=metered_workspaces(),
            ),
            JobSpec(
                name=SEAT_JOB_NAME,
                schedule=SEAT_JOB_SCHEDULE,
                handler=_ship_seats,
                candidates=member_workspaces(),
            ),
            JobSpec(
                name=SEAT_APPROVAL_JOB_NAME,
                schedule=SEAT_APPROVAL_JOB_SCHEDULE,
                handler=_ask_seat_approvals,
                candidates=member_workspaces(),
            ),
            JobSpec(
                name=BILLING_JOB_NAME,
                schedule=BILLING_JOB_SCHEDULE,
                handler=_activate_billing,
                candidates=member_workspaces(),
            ),
        ),
        prompt_sections=(
            PromptSection(name=SEATS_SECTION_NAME, body=SEATS_SECTION_BODY),
            PromptSection(name=BILLING_SECTION_NAME, body=BILLING_SECTION_BODY),
        ),
        credentials=(
            CredentialSlot(
                name=ANTHROPIC_KEY_SLOT,
                description=(
                    "Workspace's own Anthropic API key (BYOK): model usage is metered for "
                    "visibility but not billed; without it the platform key is used and usage "
                    "bills as pass-through."
                ),
            ),
        ),
    )
