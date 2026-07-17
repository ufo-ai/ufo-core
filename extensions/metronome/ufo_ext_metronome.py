"""Ship settled ledger usage to Metronome as idempotent delta events.

The recurring job drains, per workspace, the usage-export seam (`ctx.pending_usage_exports`):
core mints one frozen intent per settled ledger-row delta, and each intent becomes one ingest
event whose `transaction_id = "<ledger id>:<amount already shipped>"` — deterministic, so a crash
or unacknowledged delivery re-sends the identical event and Metronome's 34-day dedup absorbs it,
and append-only, so a row that grows after shipping ships a top-up on a later tick instead of
undercounting. Intents are acknowledged only after Metronome accepts the batch. A per-workspace
floor recorded on the first run bounds the initial backfill to `BACKFILL_WINDOW_DAYS`; it never
moves after, so a settled row ships however long it waited. Which Metronome environment receives
the events — sandbox or production — is decided entirely by whose bearer token
`METRONOME_BEARER_TOKEN` carries."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from ufo.sdk.accounting import UsageExport, metered_workspaces
from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import Manifest
from ufo.sdk.o11y import log

NAME = "metronome"
VERSION = "0.1.0"
JOB_NAME = "usage_shipper"
JOB_SCHEDULE = "0 * * * * *"
INGEST_URL = "https://api.metronome.com/v1/ingest"
METRONOME_BEARER_TOKEN_ENV = "METRONOME_BEARER_TOKEN"
EVENT_TYPE = "ufo_usage"
BATCH_EVENTS = 100
INGEST_TIMEOUT_SECONDS = 30
BACKFILL_WINDOW_DAYS = 7
FLOOR_KEY = "ship_floor"

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
        token = os.environ.get(METRONOME_BEARER_TOKEN_ENV)
        if not token:
            raise RuntimeError(
                f"{METRONOME_BEARER_TOKEN_ENV} is not set; the metronome extension requires it"
            )
        floor = await self._floor()
        while True:
            exports = await self.ctx.pending_usage_exports(floor, BATCH_EVENTS)
            if not exports:
                return
            await self._post(token, self._events(exports))
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
                    "byok": "false",
                },
            }
            for export in exports
        ]

    async def _post(self, token: str, events: list[dict[str, object]]) -> None:
        async with httpx.AsyncClient(
            timeout=INGEST_TIMEOUT_SECONDS, transport=self.transport
        ) as http:
            response = await http.post(
                INGEST_URL, json=events, headers={"Authorization": f"Bearer {token}"}
            )
        if not response.is_success:
            raise MetronomeError(
                f"metronome ingest failed ({response.status_code}): {response.text}"
            )
        log("metronome.shipped", count=len(events), workspace_id=str(self.ctx.store.workspace_id))


async def _ship(ctx: ExtensionContext) -> None:
    await UsageShipper(ctx=ctx, transport=INGEST_TRANSPORT).run()


def _rfc3339(moment: datetime) -> str:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.isoformat()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=_ship,
                candidates=metered_workspaces(),
            ),
        ),
    )
