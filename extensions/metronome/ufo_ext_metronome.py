"""Ship settled ledger usage and daily member counts to Metronome, and set billing up from chat.

The usage job drains, per workspace, the usage-export seam (`ctx.pending_usage_exports`): core
mints one frozen intent per settled ledger-row delta, and each intent becomes one ingest event
whose `transaction_id = "<ledger id>:<amount already shipped>"` — deterministic, so a crash or
unacknowledged delivery re-sends the identical event and Metronome's 34-day dedup absorbs it, and
append-only, so a row that grows after shipping ships a top-up on a later tick instead of
undercounting. Intents are acknowledged only after Metronome accepts the batch. A per-workspace
floor recorded on the first run bounds the initial backfill to `BACKFILL_WINDOW_DAYS`; it never
moves after, so a settled row ships however long it waited.

Every usage event is labelled `byok`: a workspace holding its own key for the provider serving
the model — `anthropic_api_key` (declared here so the standard `request_credentials` chat handoff
can fill it), `bedrock_api_key`, whatever a provider extension declares — pays that provider
directly, so the rate card bills only `byok="false"` usage while everything stays visible. Core
freezes the label into each export intent at mint, resolved through the deploy's model registry —
this module only relays `export.byok` — so a backlog drained after an outage carries the key
state that served it, and a re-send is byte-identical whatever changed since.

Billing is a chat act too. An admin asks, the agent calls `manage_billing`, and the tool hands back
either what the workspace has left or a short-lived Stripe Customer Portal link for saving a payment
method — no callback, no webhook, no billing table. What the tool persists is the workspace's Stripe
Customer id, under a deterministic key so a conflict is reconciled by fetching the customer that
already exists rather than by minting a second one.

Metronome rates what it is sent and never gates anything. A workspace runs on the prepaid balance
core holds; this module reports that balance beside the card, and ships the usage record the ledger
reconciles against. There is no contract, no package, and no plan to activate.

Metronome must never collect. A workspace pays by putting money on its balance through Stripe, and
the same usage priced a second time by a Metronome contract configured to invoice would charge that
workspace twice for one turn — once when the balance was funded, once when the statement went out.
So a contract here carries no billing-provider configuration: it exists to rate and record usage
into a statement a human reads, never to move money. Nothing in this module creates a contract, so
this holds by what an operator sets up; it is the first thing to check when wiring a new account.

One provider detail is pinned here rather than discovered per call: `STRIPE_API_VERSION` fixes the
Stripe API version, so a provider-side default bump can never reshape a response underneath us — the
version Stripe's own SDKs pin is the one taken. The opt-in provider smoke is what validates a change
to it.

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
from ufo.sdk.balance import (
    AutoTopup,
    credit,
    mark_topup_verified,
    read_auto_topup,
    read_balance,
    read_headroom,
    set_auto_topup,
)
from ufo.sdk.bearer import SESSION_COOKIE, verify_token, workspace_claim
from ufo.sdk.context import ExtensionContext
from ufo.sdk.http import JSONResponse, Request, Response
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import CredentialSlot, Manifest, PromptSection, RouteSpec
from ufo.sdk.o11y import log, warn
from ufo.sdk.seats import (
    member_by_email,
    member_is_admin,
    member_workspaces,
)
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "metronome"
VERSION = "0.1.0"
JOB_NAME = "usage_shipper"
JOB_SCHEDULE = "0 * * * * *"
MICRO_USD_PER_USD = 1_000_000
TOPUP_REFUSED_AT_KEY = "topup_refused_at"
TOPUP_ATTEMPT_KEY = "topup_attempt"
# An off-session decline is a standing answer — an expired card, a spent limit, a block — and none
# of that changes because five minutes passed, so a second attempt on the tick buys nothing and
# spends another authorization against a card the issuer is already refusing, which is what card
# networks penalise. The refill waits a day and asks again. It never stops asking: a workspace
# short enough to need a refill is one the balance gate is about to refuse every turn of, including
# the turn that would arrange autopay again, so a stand-down only a member act could clear would
# strand the workspace with no way back.
TOPUP_RETRY_AFTER = timedelta(days=1)
TOPUP_JOB_NAME = "balance_topup"
TOPUP_JOB_SCHEDULE = "0 * * * * *"
METRONOME_API = "https://api.metronome.com"
INGEST_URL = f"{METRONOME_API}/v1/ingest"
METRONOME_BEARER_TOKEN_ENV = "METRONOME_BEARER_TOKEN"
STRIPE_API = "https://api.stripe.com/v1"
STRIPE_API_VERSION = "2026-02-25.clover"
STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_PORTAL_CONFIGURATION_ENV = "STRIPE_BILLING_PORTAL_CONFIGURATION_ID"
EVENT_TYPE = "ufo_usage"
ANTHROPIC_KEY_SLOT = "anthropic_api_key"
BATCH_EVENTS = 100
INGEST_TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 7
FLOOR_KEY = "ship_floor"

BILLING_KEY = "billing"
BILLING_TIMEOUT_SECONDS = 30
PAYMENT_METHOD_UPDATE_FLOW = "payment_method_update"
MANAGE_BILLING_TOOL = "manage_billing"

MANAGE_BILLING_DESCRIPTION = (
    "Read and arrange the workspace's billing. Admin-only. 'status' reports whether a card is on "
    "file and how much balance is left; 'portal' returns a short-lived Stripe link for saving a "
    "payment method and for invoices and billing details; 'autopay' sets automatic refills from "
    "the card already on file, taking the amount to add and the balance to refill below, and "
    "stops them when both are omitted."
)

BILLING_SECTION_NAME = "billing"
BILLING_SECTION_BODY = (
    "The workspace runs on a prepaid balance: turns spend it, and a turn is refused once the "
    "balance reaches the headroom a turn needs to begin, which is at or above zero. A workspace "
    "serving its turns with its own model provider key is the exception — its turns run while the "
    "balance is above zero — and a workspace with no balance at all is not limited by one, which "
    "is what a null balance and reserve mean. When a "
    "workspace admin asks about billing, what they have left, or how to add a card, "
    "call manage_billing with action 'status' and report the balance, the reserve beneath it, "
    "and whether a card is on file. A workspace whose card has already paid a refill keeps working "
    "for a fixed amount past that line, so a low balance there is not the same as being stopped. "
    "Report what status returns rather "
    "than inferring why a turn stopped. For adding or changing a card, or "
    "for invoices, call action 'portal' and give them the "
    "returned portal_url as a link to open. There is no plan to sell and none to activate, so "
    "never offer one or say one is pending. If an admin says they are already on a plan, do not "
    "contradict them — nothing here can see a billing arrangement made before this, so say you "
    "will check with the team. An admin can arrange automatic refills from the card on file: call "
    "action 'autopay' with the amount to add and the balance to refill below, both in whole "
    "dollars, and omit both to stop. A card has to be saved first, because the refill runs with "
    "nobody present. If they ask to add credit as a one-off, say you will pass that to the team."
)

INGEST_TRANSPORT: httpx.AsyncBaseTransport | None = None
BILLING_TRANSPORT: httpx.AsyncBaseTransport | None = None


class MetronomeError(RuntimeError):
    """Metronome answered a non-2xx status — surfaced with status and body so the failed call is
    loud; for a job the next scheduled fire is the retry, and the durable identity on every write
    (ingest `transaction_id`, ingest alias, `uniqueness_key`) absorbs the re-send."""


class StripeError(RuntimeError):
    """Stripe answered a non-2xx status — surfaced with status and body. Nothing is recorded for a
    failed call, so the admin's next attempt or the next job tick starts from the same state."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class _ChargeInFlight(StripeError):
    """An earlier charge under this key is still being decided.

    The tick is shorter than a card authorization can take, so a refill still in flight when the
    next tick fires asks Stripe for the same key again and is told so. That is neither a decline
    nor a fault: the first request is still the one deciding, and the money it moves is credited
    when it answers. Nothing is recorded for it — counting it as a refusal would stand the refill
    down for a day over a card that is in the middle of paying."""


@dataclass(frozen=True)
class UsageShipper:
    """Drain one workspace's pending usage exports to Metronome's ingest API. Each pass reads a
    batch of frozen delta intents, POSTs them, then acknowledges — strictly in that order, so a
    crash between POST and ack re-sends byte-identical events and Metronome deduplicates.

    The alias is reconciled once a pass, and only once a pass has something to send. This job ticks
    every minute for every workspace, so reconciling ahead of the batch read would spend one
    customer API call per workspace per minute on a fleet that is mostly idle — and the throttling
    that earns raises here, which holds the usage of the workspaces that do have some. The alias is
    still confirmed before anything is ingested under it.

    The `transport` field is the httpx testability seam; production leaves it None."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        """The token is read before the export seam is touched, and that order is load-bearing:
        `pending_usage_exports` mints and commits the delta intents it returns. Reading the seam
        first would have an unkeyed deploy mint intents on every tick against a floor that rolls
        forward with the clock, and a later key would then find months of them pending, since a
        pending read filters on acknowledgement and never on the floor. So an unkeyed deploy that
        meters usage fails this job loudly, which is what a missing setting deserves."""
        token = _require_env(METRONOME_BEARER_TOKEN_ENV)
        floor = await self._floor()
        reconciled = False
        while True:
            exports = await self.ctx.pending_usage_exports(floor, BATCH_EVENTS)
            if not exports:
                return
            self._note_usage_aging_out(exports)
            if not reconciled:
                await _ensure_metronome_customer(self.ctx, token, self.transport)
                reconciled = True
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

    def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None:
        """Say so when held usage has aged past the provider's backdating window.

        Holding a batch rather than acking it unconfirmed delays the usage, which is right — but the
        delay is not free forever. The provider backdates only `BACKFILL_WINDOW_DAYS`, so a backlog
        held longer than that becomes unbillable, and the hold quietly turns into the loss it was
        meant to prevent. Nothing here can recover that usage; what it can do is stop it being
        silent, so an operator sees the window closing while there is time to fix the cause."""
        oldest = min(
            export.occurred_at.replace(tzinfo=UTC)
            if export.occurred_at.tzinfo is None
            else export.occurred_at
            for export in exports
        )
        if oldest >= datetime.now(UTC) - timedelta(days=BACKFILL_WINDOW_DAYS):
            return
        warn(
            "metronome.usage_past_backdating_window",
            workspace_id=str(self.ctx.store.workspace_id),
            oldest=_rfc3339(oldest),
            held=len(exports),
        )

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


class BillingConfig(BaseModel):
    """The three settings the billing workflow cannot run without, read and validated once at the
    entry to a tool call or a tick — before any provider object exists, so a half-configured deploy
    can never leave a Stripe Customer behind and then fail on the portal configuration. Every
    missing name is reported at once rather than one per attempt. The usage shipper reads
    only the bearer token directly: a deploy that meters usage without selling a plan must keep
    shipping, so they never depend on this."""

    model_config = ConfigDict(frozen=True)

    stripe_secret_key: str
    stripe_portal_configuration_id: str
    metronome_bearer_token: str

    @classmethod
    def from_env(cls) -> "BillingConfig":
        found = {
            "stripe_secret_key": os.environ.get(STRIPE_SECRET_KEY_ENV),
            "stripe_portal_configuration_id": os.environ.get(STRIPE_PORTAL_CONFIGURATION_ENV),
            "metronome_bearer_token": os.environ.get(METRONOME_BEARER_TOKEN_ENV),
        }
        missing = [
            name
            for name, field in (
                (STRIPE_SECRET_KEY_ENV, "stripe_secret_key"),
                (STRIPE_PORTAL_CONFIGURATION_ENV, "stripe_portal_configuration_id"),
                (METRONOME_BEARER_TOKEN_ENV, "metronome_bearer_token"),
            )
            if not found[field]
        ]
        if missing:
            raise RuntimeError(f"billing requires {', '.join(missing)}")
        return cls.model_validate(found)


class BillingRecord(BaseModel):
    """One workspace's billing provisioning state, as the extension store holds it. Written when a
    Stripe Customer first exists, and the durable identity every later call resolves against.

    Pydantic ignores keys it does not declare, so a stored record carrying more than this still
    validates and still yields its customer."""

    stripe_customer_id: str


async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None:
    stored = await ctx.store.get(BILLING_KEY)
    return None if stored is None else BillingRecord.model_validate(stored)


class ManageBillingInput(BaseModel):
    action: Literal["status", "portal", "autopay"] = Field(
        description=(
            "status: report the card on file and the workspace's remaining balance. portal: return "
            "a link for saving a payment method, and for invoices and billing details. autopay: "
            "set or stop automatic refills from the card already on file."
        )
    )
    autopay_dollars: int | None = Field(
        default=None,
        description="For autopay: how much to add each time, in whole US dollars. Omit along with "
        "autopay_below_dollars to stop refilling.",
    )
    autopay_below_dollars: int | None = Field(
        default=None,
        description="For autopay: refill once the balance falls to this many US dollars.",
    )
    user_description: str = Field(
        description="What you are doing with their billing, in plain language for the activity "
        "timeline."
    )


async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult:
    ext = await _admin_billing(ctx)
    config = BillingConfig.from_env()
    match args.action:
        case "status":
            return await _billing_status(ext, config)
        case "portal":
            return await _billing_portal(ext, config)
        case "autopay":
            return await _billing_autopay(ext, config, args)


async def _billing_autopay(
    ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput
) -> ToolResult:
    """Arrange or stop automatic refills. Both figures are given together or neither is, and a
    workspace with no card cannot arrange one — the refill runs with nobody present, so the card
    has to be there before it is promised rather than at the moment it is needed."""
    if (args.autopay_dollars is None) != (args.autopay_below_dollars is None):
        raise ValueError("autopay needs both an amount and a balance to refill below, or neither")
    dollars, below_dollars = args.autopay_dollars, args.autopay_below_dollars
    if dollars is None or below_dollars is None:
        amount, below = None, None
    else:
        record = await _billing_record(ext)
        if record is None or (
            await _default_payment_method(config, record.stripe_customer_id, BILLING_TRANSPORT)
            is None
        ):
            raise ValueError("save a payment method before arranging automatic refills")
        amount, below = dollars * MICRO_USD_PER_USD, below_dollars * MICRO_USD_PER_USD
    async with ext.transaction() as connection:
        if not await set_auto_topup(connection, ext.store.workspace_id, amount, below):
            raise ValueError("this workspace has no balance to refill")
    marked = await ext.store.get(TOPUP_ATTEMPT_KEY)
    await ext.store.delete(TOPUP_REFUSED_AT_KEY)
    await ext.store.put(TOPUP_ATTEMPT_KEY, str((int(marked) if isinstance(marked, str) else 0) + 1))
    log(
        "metronome.autopay_set",
        workspace_id=str(ext.store.workspace_id),
        micro_usd=amount,
        below_micro_usd=below,
    )
    return _text_result({"autopay_micro_usd": amount, "autopay_below_micro_usd": below})


async def _admin_billing(ctx: ToolContext) -> ExtensionContext:
    if ctx.speaker_member_id is None:
        raise ValueError("billing requires a speaking member")
    if not await ctx.speaker_is_admin():
        raise ValueError("only a workspace admin can manage billing")
    assert ctx.ext is not None
    return ctx.ext


async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult:
    """What the workspace can spend, and whether a card is on file to add to it. The card is read
    from Stripe rather than from our record, so it answers what the provider holds now; the balance
    is core's, and a workspace that has never been credited has none.

    The reserve is reported beside the balance because a turn is refused once the balance reaches
    it, so the balance alone names money the gate will not spend. Neither is a spendable figure:
    what entry actually asks for scales with the turns already running, so a single number here
    would name a line no gate holds."""
    async with ext.transaction() as connection:
        balance = await read_balance(connection, ext.store.workspace_id)
    record = await _billing_record(ext)
    paid = record is not None and (
        await _default_payment_method(config, record.stripe_customer_id, BILLING_TRANSPORT)
        is not None
    )
    return _text_result(
        {
            "payment_method_on_file": paid,
            "balance_micro_usd": None if balance is None else balance.balance_micro_usd,
            "reserve_micro_usd": None if balance is None else balance.reserve_micro_usd,
            "granted_micro_usd": None if balance is None else balance.granted_micro_usd,
            "charged_micro_usd": None if balance is None else balance.charged_micro_usd,
        }
    )


async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult:
    """A fresh Stripe portal session — payment methods, invoices, billing details.

    The link is what saves a card, so this resolves the workspace's Stripe Customer rather than
    demanding a prior setup: `_stripe_customer` is idempotent on a deterministic key, so the first
    admin to ask for the portal creates it and every later ask reuses it. The record is written only
    when it does not exist, which keeps the customer id stable for the reads beside it."""
    workspace_id = ext.store.workspace_id
    record = await _billing_record(ext)
    if record is None:
        record = BillingRecord(
            stripe_customer_id=await _stripe_customer(config, workspace_id, BILLING_TRANSPORT)
        )
        await ext.store.put(BILLING_KEY, record.model_dump(mode="json"))
    url = await _portal_session(
        config,
        record.stripe_customer_id,
        None,
        BILLING_TRANSPORT,
        _billing_screen(ext.public_base_url),
    )
    log("metronome.billing_portal", workspace_id=str(workspace_id))
    return _text_result({"portal_url": url, "stripe_customer_id": record.stripe_customer_id})


def _text_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


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
    return_url: str | None = None,
) -> str:
    """A short-lived Customer Portal URL under the deploy's portal configuration — the configuration
    is what keeps subscription mutation out of the member's hands. `flow` narrows the session to one
    task (payment-method update at setup); None opens the full management portal.

    `return_url` is where Stripe sends the member when they are done. Without it they are left at
    the provider with no way back, and the card they just saved is known only to Stripe: the deploy
    learns of it whenever something next reads the provider. Sending them to the workspace's own
    billing screen closes that gap at the moment it opens, because that screen reads the card from
    the provider and is reachable even while the balance refuses every turn."""
    data = {
        "customer": customer_id,
        "configuration": config.stripe_portal_configuration_id,
    }
    if return_url is not None:
        data["return_url"] = return_url
    if flow is not None:
        data["flow_data[type]"] = flow
    session = await _stripe(config, "POST", "/billing_portal/sessions", transport, data=data)
    return _as_str(session.get("url"), "stripe portal url")


async def _default_payment_method(
    config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None
) -> str | None:
    """The customer's default payment method, or None. The portal's payment-method-update flow sets
    exactly this field, so it is the provider's own answer to 'has the workspace paid', never a flag
    of ours — and a charge has to name it: a PaymentIntent confirm reads `payment_method` from the
    request and never the customer's invoice default, so a charge that omits it has no card to
    take."""
    customer = await _stripe(config, "GET", f"/customers/{customer_id}", transport)
    match customer.get("invoice_settings"):
        case {"default_payment_method": str() as method}:
            return method
    return None


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
        raise StripeError(
            f"stripe {path} failed ({response.status_code}): {response.text}",
            response.status_code,
        )
    return response.json()


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
    if not response.is_success:
        raise MetronomeError(f"metronome {path} failed ({response.status_code}): {response.text}")
    return response.json()


def _as_str(value: object, field: str) -> str:
    match value:
        case str() if value:
            return value
    raise ValueError(f"provider response carried no {field}")


async def _ensure_metronome_customer(
    ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None
) -> None:
    """Make sure a live Metronome customer carries this workspace's UUID as an ingest alias.

    Every usage event is stamped `customer_id = <workspace uuid>`, and Metronome resolves
    that through the alias — with no customer holding it, each event is accepted and attributed to
    nobody, so metering stops with nothing to see. The alias is read every tick rather than
    remembered: a customer archived, or a bearer token moved to another account, leaves a stored id
    pointing at nothing while `_ingest` keeps answering 2xx and the exports keep being acked, which
    is the same silent loss with a cache in front of it.

    Every way of not confirming the alias raises, and the caller ships nothing: ingest answers 2xx
    whether or not the alias resolves, so shipping past an unconfirmed alias and acking the exports
    destroys that usage rather than delaying it. A token that cannot read customers cannot confirm
    anything, so it holds the backlog instead of draining it into nowhere.

    A conflict means something already holds the alias, which the read that just answered None
    could not see. Re-reading separates the two causes: a live customer means the other shipper
    created it in the gap, and this tick lost a harmless race; still nothing means the holder is
    archived or otherwise invisible to this token, and every event stamped with it is dropped.

    The alias is the durable identity, so the create carries no idempotency key: a key caches its
    response for a day, which would freeze a transient failure long after the cause was gone."""
    alias = str(ctx.store.workspace_id)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=BILLING_TIMEOUT_SECONDS, transport=transport) as http:
        if await _customer_by_alias(http, headers, alias) is not None:
            return
        created = await http.post(
            f"{METRONOME_API}/v1/customers",
            json={"name": f"ufo workspace {alias}", "ingest_aliases": [alias]},
            headers=headers,
        )
        if created.status_code == HTTPStatus.CONFLICT:
            if await _customer_by_alias(http, headers, alias) is not None:
                log("metronome.customer_alias_held", workspace_id=alias)
                return
            raise MetronomeError(
                f"metronome ingest alias {alias} is held by a customer this token cannot read; "
                "usage shipped under it would be attributed to nobody"
            )
        if created.status_code in _CUSTOMER_SCOPE_DENIED:
            raise _CustomerScopeDenied(
                f"metronome customer create denied ({created.status_code}): this token cannot "
                "confirm the ingest alias, so usage under it cannot be shipped"
            )
        if not created.is_success:
            raise MetronomeError(
                f"metronome customer create failed ({created.status_code}): {created.text}"
            )
    log("metronome.customer_created", workspace_id=alias)


class _CustomerScopeDenied(MetronomeError):
    """The token may ingest but not read or write customers."""


_CUSTOMER_SCOPE_DENIED = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


async def _customer_by_alias(
    http: httpx.AsyncClient, headers: dict[str, str], alias: str
) -> str | None:
    """The live customer holding this ingest alias, or None. Archived customers keep the alias but
    are absent from this read, which is why a create can still conflict after it answers None."""
    found = await http.get(
        f"{METRONOME_API}/v1/customers", params={"ingest_alias": alias}, headers=headers
    )
    if found.status_code in _CUSTOMER_SCOPE_DENIED:
        raise _CustomerScopeDenied(f"metronome customer scope denied ({found.status_code})")
    if not found.is_success:
        raise MetronomeError(
            f"metronome customer lookup failed ({found.status_code}): {found.text}"
        )
    match found.json().get("data"):
        case [{"id": str() as existing}, *_]:
            return existing
    return None


@dataclass(frozen=True)
class BalanceTopup:
    """Refill one workspace's balance from the card it saved.

    Core decides the workspace is short; this decides how it pays. The charge is off-session
    because no member is present when a balance runs down, which is the whole point of arranging it
    in advance.

    The credit is keyed on the payment intent, so a redelivered tick that finds the charge already
    made credits nothing a second time — the money moved once and the balance records it once. The
    charge itself carries the same key as its idempotency header, so Stripe collapses a retry of a
    request that never returned rather than taking the money twice."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        """Refill once the balance is short, and slow down against a card that has said no.

        The tick is every minute because a single turn can outspend a longer one: the interval is
        the window a workspace has to survive on its own between crossing its refill line and the
        money landing, and core's grace is sized against it.

        A refusal holds the next attempt for a day, and arranging autopay again releases it sooner —
        an issuer's refusal repeated on the tick is an unbounded retry against something that
        already answered, which earns nothing and costs standing with the card network. The wait
        always lapses: the balance gate refuses the very turn that would re-arrange autopay, so a
        card that recovers on its own has to be enough."""
        workspace_id = self.ctx.store.workspace_id
        async with self.ctx.transaction() as connection:
            wanted = await read_auto_topup(connection, workspace_id)
        if wanted is None:
            return
        config = BillingConfig.from_env()
        record = await _billing_record(self.ctx)
        if record is None:
            warn("metronome.topup_without_customer", workspace_id=str(workspace_id))
            return
        method = await _default_payment_method(config, record.stripe_customer_id, self.transport)
        if method is None:
            warn("metronome.topup_without_card", workspace_id=str(workspace_id))
            return
        async with self.ctx.transaction() as connection:
            settled = await read_balance(connection, workspace_id)
        charged_so_far = 0 if settled is None else settled.charged_micro_usd
        refused = await self.ctx.store.get(TOPUP_REFUSED_AT_KEY)
        marked = await self.ctx.store.get(TOPUP_ATTEMPT_KEY)
        attempt = int(marked) if isinstance(marked, str) else 0
        if (
            isinstance(refused, str)
            and datetime.now(UTC) - datetime.fromisoformat(refused) < TOPUP_RETRY_AFTER
        ):
            warn(
                "metronome.topup_waiting",
                workspace_id=str(workspace_id),
                refused_at=refused,
            )
            return
        try:
            intent = await self._charge(
                config,
                record.stripe_customer_id,
                method,
                wanted,
                workspace_id,
                f"{charged_so_far}:{attempt}",
            )
        except _ChargeInFlight:
            warn("metronome.topup_in_flight", workspace_id=str(workspace_id))
            return
        if intent is None:
            await self.ctx.store.put(TOPUP_REFUSED_AT_KEY, datetime.now(UTC).isoformat())
            await self.ctx.store.put(TOPUP_ATTEMPT_KEY, str(attempt + 1))
            return
        await self.ctx.store.delete(TOPUP_REFUSED_AT_KEY)
        async with self.ctx.transaction() as connection:
            added = await credit(
                connection,
                workspace_id,
                wanted.amount_micro_usd,
                wanted.amount_micro_usd,
                f"stripe/{intent}",
            )
            await mark_topup_verified(connection, workspace_id)
        if added:
            log(
                "metronome.topped_up",
                workspace_id=str(workspace_id),
                micro_usd=wanted.amount_micro_usd,
                payment_intent=intent,
            )

    async def _charge(
        self,
        config: BillingConfig,
        customer_id: str,
        payment_method: str,
        wanted: AutoTopup,
        workspace_id: UUID,
        attempt: str,
    ) -> str | None:
        """The payment intent id once the money has actually moved, or None when the card refused.

        A decline is the card's answer, not a fault of ours: it is reported and the balance is left
        alone. The caller counts it and stands the refill down, so a refused card is asked once and
        then left alone until an admin arranges the refill again — not re-authorized on every tick
        for as long as the balance stays short.

        The idempotency key names the attempt: what the workspace has been charged to date, and a
        counter that only ever moves forward. Stripe holds a key for a day, so both halves are
        needed. The charged total alone would make the second refill a workspace genuinely needed
        replay the first intent and credit nothing. The counter advances on a refusal and on an
        admin arranging the refill again, so the retry after a card is fixed is a new charge rather
        than a replay of the refusal — while a request that never returned advances nothing, so
        retrying it repeats rather than charging twice."""
        cents = wanted.amount_micro_usd // 10_000
        try:
            intent = await _stripe(
                config,
                "POST",
                "/payment_intents",
                self.transport,
                data={
                    "amount": str(cents),
                    "currency": "usd",
                    "customer": customer_id,
                    "payment_method": payment_method,
                    "confirm": "true",
                    "off_session": "true",
                    "description": f"ufo balance top-up for workspace {workspace_id}",
                    "metadata[workspace_id]": str(workspace_id),
                },
                idempotency_key=f"ufo-topup:{workspace_id}:{attempt}",
            )
        except StripeError as refused:
            if refused.status == HTTPStatus.CONFLICT:
                raise _ChargeInFlight(str(refused), refused.status) from refused
            if refused.status != HTTPStatus.PAYMENT_REQUIRED:
                raise
            warn("metronome.topup_declined", workspace_id=str(workspace_id), status="402")
            return None
        match intent:
            case {"id": str() as intent_id, "status": "succeeded"}:
                return intent_id
        warn(
            "metronome.topup_declined",
            workspace_id=str(workspace_id),
            status=str(intent.get("status")),
        )
        return None


async def _top_up(ctx: ExtensionContext) -> None:
    await BalanceTopup(ctx=ctx, transport=BILLING_TRANSPORT).run()


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


BILLING_ROUTE_PATH = "billing"
BILLING_SCREEN_PATH = "/surface/web#/workspace/usage"


def _billing_screen(public_base_url: str | None) -> str | None:
    """Where Stripe returns the member once they are done, or None on a deploy with no public base.

    None is not a failure: the session is still created and still saves a card. It only means the
    member is left at the provider rather than back on the screen that states what the workspace
    has left."""
    if not public_base_url:
        return None
    return f"{public_base_url.rstrip('/')}{BILLING_SCREEN_PATH}"


def _billing_request_workspace(request: Request) -> UUID | None:
    """The workspace a billing request belongs to, read from the member's session cookie.

    Core binds whatever this returns and refuses the request outright when it returns None, so this
    is the only thing standing between the page and the open internet."""
    return workspace_claim(request.cookies.get(SESSION_COOKIE, ""))


async def _billing_projection(ext: ExtensionContext, request: Request) -> Response:
    """What the workspace has left, what stops it, and whether a card is on file.

    This reads; it never charges and never changes a rule. It exists because the acts that fix
    billing are chat acts, and a workspace out of credit refuses the very turns that would carry
    them — so the one screen that explains why the agent stopped has to sit off the turn path
    entirely. Nothing here is reachable from a stopped workspace by any other route.

    The session is verified against the workspace core bound from the same cookie, and the address
    it proves is resolved to a member of that workspace and no other. An address is not a member
    anywhere in particular, so resolving it first and checking the workspace after would read
    another workspace's member on the way."""
    email = verify_token(request.cookies.get(SESSION_COOKIE, ""), ext.store.workspace_id)
    if email is None:
        return JSONResponse({"error": "sign in to read billing"}, status_code=401)
    async with ext.transaction() as connection:
        member_id = await member_by_email(connection, ext.store.workspace_id, email)
        if member_id is None or not await member_is_admin(
            connection, ext.store.workspace_id, member_id
        ):
            return JSONResponse({"error": "only a workspace admin can read billing"}, 403)
        headroom = await read_headroom(connection, ext.store.workspace_id)
        balance = await read_balance(connection, ext.store.workspace_id)
    if headroom is None or balance is None:
        return JSONResponse({"limited": False})
    config = BillingConfig.from_env()
    record = await _billing_record(ext)
    card = record is not None and (
        await _default_payment_method(config, record.stripe_customer_id, BILLING_TRANSPORT)
        is not None
    )
    return JSONResponse(
        {
            "limited": True,
            "balance_micro_usd": headroom.balance_micro_usd,
            "reserve_micro_usd": headroom.reserve_micro_usd,
            "grace_micro_usd": headroom.grace_micro_usd,
            "refused_below_micro_usd": headroom.reserve_micro_usd - headroom.grace_micro_usd,
            "granted_micro_usd": balance.granted_micro_usd,
            "charged_micro_usd": balance.charged_micro_usd,
            "card_on_file": card,
        }
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(MANAGE_BILLING_TOOL_DEF,),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=_ship,
                candidates=metered_workspaces(),
            ),
            JobSpec(
                name=TOPUP_JOB_NAME,
                schedule=TOPUP_JOB_SCHEDULE,
                handler=_top_up,
                candidates=member_workspaces(),
            ),
        ),
        routes=(
            RouteSpec(
                method="GET",
                path=BILLING_ROUTE_PATH,
                handler=_billing_projection,
                identify=_billing_request_workspace,
            ),
        ),
        prompt_sections=(PromptSection(name=BILLING_SECTION_NAME, body=BILLING_SECTION_BODY),),
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
