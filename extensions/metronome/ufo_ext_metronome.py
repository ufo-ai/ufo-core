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

Which Metronome environment receives the events — sandbox or production — is decided entirely by
whose bearer token `METRONOME_BEARER_TOKEN` carries."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from ufo.sdk.accounting import UsageExport, metered_workspaces
from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import CredentialSlot, Manifest, PromptSection
from ufo.sdk.o11y import log
from ufo.sdk.seats import Seats, SeatSnapshot, member_workspaces
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "metronome"
VERSION = "0.1.0"
JOB_NAME = "usage_shipper"
JOB_SCHEDULE = "0 * * * * *"
SEAT_JOB_NAME = "seat_shipper"
SEAT_JOB_SCHEDULE = "0 0 * * * *"
INGEST_URL = "https://api.metronome.com/v1/ingest"
METRONOME_BEARER_TOKEN_ENV = "METRONOME_BEARER_TOKEN"
EVENT_TYPE = "ufo_usage"
ANTHROPIC_KEY_SLOT = "anthropic_api_key"
SEAT_EVENT_TYPE = "ufo_seats"
SEAT_LIMIT_DEFAULT = 25
SEAT_SHIPPED_KEY = "seats_shipped_date"
BATCH_EVENTS = 100
INGEST_TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 7
FLOOR_KEY = "ship_floor"

GRANT_SEAT_TOOL = "grant_seat"
REVOKE_SEAT_TOOL = "revoke_seat"
LIST_SEATS_TOOL = "list_seats"

GRANT_SEAT_DESCRIPTION = (
    "Grant a workspace seat to a member by email so the agent answers them. Owner-only. Fails "
    "when every seat is taken — revoke one first; raising the limit is not a chat act, contact "
    "us for that."
)
REVOKE_SEAT_DESCRIPTION = (
    "Revoke a member's seat by email. Owner-only; the owner's own seat cannot be revoked. The "
    "member's next message is refused immediately, and a running turn of theirs holds at its "
    "next model round."
)
LIST_SEATS_DESCRIPTION = "Show the workspace's seat limit and who holds a seat."

SEATS_SECTION_NAME = "seats"
SEATS_SECTION_BODY = (
    "Seats gate who this agent answers: members hold seats up to the workspace's seat limit, and "
    "an unseated member's messages are refused automatically with a pointer to the owner. A newly "
    "joined member is seated automatically while a seat is open. Only the workspace owner can "
    "change seats: when the owner asks, call grant_seat or revoke_seat with the member's email; "
    "call list_seats to show current standing."
)

INGEST_TRANSPORT: httpx.AsyncBaseTransport | None = None


class MetronomeError(RuntimeError):
    """Metronome answered a non-2xx status — surfaced with status and body so the failed tick is
    loud; the next scheduled fire is the retry, and dedup absorbs the re-send."""


@dataclass(frozen=True)
class UsageShipper:
    """Drain one workspace's pending usage exports to Metronome's ingest API. Each pass reads a
    batch of frozen delta intents, POSTs them, then acknowledges — strictly in that order, so a
    crash between POST and ack re-sends byte-identical events and Metronome deduplicates. The
    `transport` field is the httpx testability seam; production leaves it None."""

    ctx: ExtensionContext
    transport: httpx.AsyncBaseTransport | None = None

    async def run(self) -> None:
        token = _require_token()
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
        token = _require_token()
        today = datetime.now(UTC).date().isoformat()
        if await self.ctx.store.get(SEAT_SHIPPED_KEY) == today:
            return
        workspace_id = self.ctx.store.workspace_id
        async with self.ctx.transaction() as connection:
            await Seats(workspace_id).ensure_limit(connection, SEAT_LIMIT_DEFAULT)
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


class GrantSeatInput(BaseModel):
    email: str = Field(description="Email of the workspace member to seat.")


class RevokeSeatInput(BaseModel):
    email: str = Field(description="Email of the seated member to unseat.")


class ListSeatsInput(BaseModel):
    pass


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
    return _snapshot_result(snapshot)


async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult:
    assert ctx.ext is not None
    async with ctx.ext.transaction() as connection:
        snapshot = await Seats(ctx.turn.workspace_id).snapshot(connection)
    return _snapshot_result(snapshot)


async def _owner_seats(ctx: ToolContext) -> Seats:
    if ctx.speaker_member_id is None:
        raise ValueError("seat changes require a speaking member")
    if not await ctx.speaker_is_owner():
        raise ValueError("only the workspace owner can change seats")
    return Seats(ctx.turn.workspace_id)


def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult:
    payload = {
        "seat_limit": snapshot.limit,
        "seated": snapshot.seated,
        "members": [
            {"email": entry.email, "seated": entry.seated, "owner": entry.owner}
            for entry in snapshot.members
        ],
    }
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


def _require_token() -> str:
    token = os.environ.get(METRONOME_BEARER_TOKEN_ENV)
    if not token:
        raise RuntimeError(
            f"{METRONOME_BEARER_TOKEN_ENV} is not set; the metronome extension requires it"
        )
    return token


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
        tools=(GRANT_SEAT_TOOL_DEF, REVOKE_SEAT_TOOL_DEF, LIST_SEATS_TOOL_DEF),
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
        ),
        prompt_sections=(PromptSection(name=SEATS_SECTION_NAME, body=SEATS_SECTION_BODY),),
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
