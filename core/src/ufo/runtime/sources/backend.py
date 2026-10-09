"""The adapter that puts a connector on the core source seam.

A connector speaks in streams and async page generators; the source seam speaks in one `SyncResult`
per run. `ConnectorBackend` bridges them: one `source` row is one stream of one connection, so
`fetch` resolves that connection's `Credential` through the runner's auth proxy, drives the
connector's one stream, and renders each record into a recallable `Page` — one record the page
model rejects, or one
that carries no value for its stream's declared `primary_key`, is dropped, warned, and counted onto
the result's `dropped` rather than failing the run (`_page`).
A full-collection stream (`delete_missing`) returns as an authoritative `snapshot` so the driver
tombstones records that vanished; an incremental stream returns `snapshot=False`, advances a
provider checkpoint, and names any provider-reported removals in `deletes`. A row
whose config pins a `backfill_after` hands that instant to `fetch_page` beside the spec — never on
it, so the spec holds only connector declarations and nothing on it invites a per-run recomputation.

An incremental run lands `MAX_RECORDS_PER_RUN` records and then stops at the first checkpoint
advance — immediately for a stream with per-page checkpoints or none at all (tier 2 resumes by
position), at the partition boundary for a checkpoint-less stretch like a `none` partition
mid-flight, so a partition larger than the cap completes once instead of restarting forever. The
overrun is bounded twice: by the partition, and by a hard ceiling of `CAP_OVERRUN_FACTOR` times the
cap — a provider whose pagination never advances the checkpoint (a self-referential page cursor, a
single unordered partition beyond any reasonable size) ends the run there with a
`source_sync.cap_overrun` warning and a tier-2 positional envelope over the stuck cursor — even a
pathological provider makes positional progress rather than spinning a worker forever. A
full-history backfill thus lands as a bounded run per sync interval instead of one unbounded
fetch, and two tiers guarantee it makes progress.

A provider rate limit ends an incremental run at its last complete checkpoint — a native cursor, or
a tier-2 envelope past the last record it read. Its pages return for commit with the provider delay.
A full snapshot instead retries the current request: it cannot yield without losing the complete
enumeration its tombstones need.

A `delete_missing` stream is exempt from the cap: it returns
an authoritative full-collection `snapshot` the driver tombstones against, and tombstone
correctness requires the complete enumeration — a capped snapshot would either livelock
delete-detection or tombstone live records it never reached, so a full-snapshot stream keeps
single-run enumeration. Tier 1: a connector that yields native checkpoints
(`StreamPage.next_cursor`, e.g. GitHub's per-repo watermark map) resumes a capped run from the last
one, stored verbatim. Tier 2, for the connectors that yield none: the adapter itself stores a
skip-count envelope as the cursor — `{"ufo_backfill": {"origin", "skip", "watermark"}}`,
`json.dumps(sort_keys=True)`, owned and parsed here alone. A cursor that parses as a JSON dict
carrying the reserved `ufo_backfill` key is an envelope; anything else (a plain watermark, a
connector's own JSON map) is opaque and reaches `fetch_page` untouched. A capped run with no native
checkpoint stores `{origin, skip: records consumed so far, watermark}`; the next run re-drives
`fetch_page` from `origin` (never the watermark — the connector must reproduce the same record
sequence for the skip count to be sound), discards the first `skip` records, lands the rest, and
advances the count. The envelope dissolves to the provider checkpoint once a run finally exhausts
the stream.

`snapshot = delete_missing`: only a full-snapshot stream tombstones, and it is never capped, so its
run always enumerates the whole collection. An incremental (tier-1/tier-2) run never snapshots.
Trades: tier 2 re-fetches the skipped prefix over HTTP each slice; the skip count
assumes the connector enumerates in a stable order between runs, and an edit between runs that
reorders the enumeration moves the count off its boundary. The connector owns incremental
filtering and checkpoint ordering. A cursor-less stream re-walks fully regardless.

The credential the proxy hands back is a broker's proxying transport (the secret never leaves the
broker) or a member-added key read host-side from the credential store (the direct/BYOK backend) —
either way it is used in-process by the sync job in the jobs role and NEVER reaches the sandbox or
agent surface, which is the invariant this preserves. Keep the `Credential` out of any structured
log. The manifest registers one backend per connector in the registry."""

import json
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.harness.o11y import warn
from ufo.runtime.access.connectors import Credential, GrantUnusable
from ufo.runtime.sources.connector import (
    Connector,
    FieldValue,
    ParentPages,
    StreamPage,
    StreamSpec,
    get_path,
    no_parents,
)
from ufo.runtime.sources.rest import ProviderRateLimited
from ufo.runtime.sources.sync import (
    Page,
    SourceAuth,
    SourceRowConfig,
    StreamSkipped,
    SyncResult,
    normalize_page_timestamp,
    validation_fault,
)

MAX_RECORDS_PER_RUN = 5_000
CAP_OVERRUN_FACTOR = 4
BACKFILL_KEY = "ufo_backfill"


class _BackfillEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str | None
    skip: int
    watermark: str | None


class ConnectorSourceConfig(SourceRowConfig):
    """Which stream one connector source row syncs. The account it authenticates as and the tenant
    URL it dials are the connection's, so the row names neither: `stream` is the whole of what this
    row is, within the connection it hangs off. The backend never reads a raw token — it asks the
    proxy for a `Credential`.

    `backfill_days` is how far back this row's first sync reaches — the connection's window where it
    names one, else the stream's declared `backfill_window_days` — and `backfill_after` is that
    resolved against the instant of registration, replayed by every run so a `CursorExpired` reset
    refetches the same window. Both are None where the stream declares no window. `backfill_after`
    alone is `resolved`, since concurrent registrations of one `backfill_days` differ by
    microseconds."""

    non_identity_fields: ClassVar[frozenset[str]] = frozenset({"backfill_days", "backfill_after"})
    resolved_fields: ClassVar[frozenset[str]] = frozenset({"backfill_after"})

    stream: str
    backfill_days: int | None = None
    backfill_after: datetime | None = None


@dataclass(frozen=True)
class ConnectorBackend:
    """Drives one connector's one stream per run onto the source seam. See the module docstring."""

    connector: Connector
    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    def partitioned(self, config: Mapping[str, object]) -> bool:
        """Whether this row's cursor is a walk's partition map — its stream fans out over parents —
        and so lives in `source.partition_cursor`, which the image before the tree never reads,
        rather than in `source.cursor`, which that image reads as its own watermark. Total over any
        row: a stream the connector no longer declares is not partitioned, so the run that fails on
        it still releases its claim."""
        return any(
            bool(stream.parents)
            for stream in self.connector.streams()
            if stream.name == config.get("stream")
        )

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        credential = await self._credential(config, auth)
        stream = self._stream(config.stream)
        base_url = self._base_url(auth)
        parents = self._parents(stream, auth)
        local_keys = stream.key_scope == "local"
        read_by_children = frozenset(
            field_path
            for spec in self.connector.streams()
            for edge in spec.parents
            if edge.stream == stream.name
            for field_path in edge.reads
        )
        envelope = None if stream.delete_missing else self._decode_cursor(cursor)
        if envelope is None:
            origin, skip_target, watermark = cursor, 0, cursor
        else:
            origin, skip_target, watermark = envelope.origin, envelope.skip, envelope.watermark
        pages: list[Page] = []
        deletes: list[str] = []
        dropped = 0
        page_cursor: str | None = None
        consumed = 0
        skipped = 0
        over = False
        cap_checkpoint: str | None = None
        native_checkpointed = False
        stream_pages = self.connector.fetch_page(
            stream,
            cursor=origin,
            credential=credential,
            base_url=base_url,
            self_user_id=auth.self_user_id,
            backfill_after=config.backfill_after,
            yield_rate_limits=not stream.delete_missing,
            parents=parents,
        )
        try:
            async for page in stream_pages:
                records = page.records if isinstance(page, StreamPage) else page
                scope = page.scope if isinstance(page, StreamPage) else None
                address = f"{stream.name}/{scope}" if scope and local_keys else stream.name
                checkpoint_records: list[dict[str, Any]] = []
                for record in records:
                    consumed += 1
                    if skipped < skip_target:
                        skipped += 1
                        continue
                    checkpoint_records.append(record)
                    page_row = self._page(stream, record, address, read_by_children)
                    if page_row is None:
                        dropped += 1
                    else:
                        pages.append(page_row)
                if isinstance(page, StreamPage):
                    deletes.extend(f"{address}/{external_id}" for external_id in page.deletes)
                    if page.next_cursor is not None:
                        native_checkpointed = True
                        page_cursor = page.next_cursor
                        watermark = page.next_cursor
                if not native_checkpointed and checkpoint_records:
                    watermark = self.connector.checkpoint(stream, checkpoint_records, watermark)
                if not stream.delete_missing and len(pages) >= MAX_RECORDS_PER_RUN:
                    if not over:
                        over = True
                        cap_checkpoint = page_cursor
                    advanced = page_cursor is None or page_cursor != cap_checkpoint
                    ceiling = MAX_RECORDS_PER_RUN * CAP_OVERRUN_FACTOR
                    if not advanced and len(pages) < ceiling:
                        continue
                    if not advanced:
                        warn(
                            "source_sync.cap_overrun",
                            stream=stream.name,
                            consumed=str(consumed),
                        )
                    envelope = _BackfillEnvelope(origin=origin, skip=consumed, watermark=watermark)
                    resume = (
                        page_cursor
                        if advanced and page_cursor is not None
                        else json.dumps({BACKFILL_KEY: envelope.model_dump()}, sort_keys=True)
                    )
                    return SyncResult(
                        pages=tuple(pages),
                        next_cursor=resume,
                        deletes=tuple(deletes),
                        snapshot=False,
                        dropped=dropped,
                        indexed=stream.indexed,
                    )
        except ProviderRateLimited as limited:
            return self._rate_limited_result(
                stream=stream,
                cursor=cursor,
                origin=origin,
                skip_target=skip_target,
                consumed=consumed,
                watermark=watermark,
                page_cursor=page_cursor,
                pages=pages,
                deletes=deletes,
                dropped=dropped,
                retry_after_seconds=limited.retry_after_seconds,
            )
        finally:
            if isinstance(stream_pages, AsyncGenerator):
                await stream_pages.aclose()
        self._warn_missing_cursor(stream, pages, watermark, page_cursor)
        next_cursor = (
            None
            if stream.delete_missing
            else (page_cursor if page_cursor is not None else watermark)
        )
        return SyncResult(
            pages=tuple(pages),
            next_cursor=next_cursor,
            deletes=tuple(deletes),
            snapshot=stream.delete_missing,
            dropped=dropped,
            indexed=stream.indexed,
        )

    def _parents(self, stream: StreamSpec, auth: SourceAuth) -> ParentPages:
        if auth.parents is not None:
            return auth.parents
        if stream.parents:
            raise RuntimeError(
                f"connector source {self.connector.name!r} stream {stream.name!r} fans out over "
                f"{sorted(edge.stream for edge in stream.parents)} and the run threaded no reader "
                "of their landed records"
            )
        return no_parents

    def _warn_missing_cursor(
        self,
        stream: StreamSpec,
        pages: list[Page],
        watermark: str | None,
        page_cursor: str | None,
    ) -> None:
        if stream.cursor_field and pages and watermark is None and page_cursor is None:
            warn(
                "source_sync.cursor_field_absent",
                connector=self.connector.name,
                stream=stream.name,
                cursor_field=stream.cursor_field,
            )

    def _rate_limited_result(
        self,
        *,
        stream: StreamSpec,
        cursor: str | None,
        origin: str | None,
        skip_target: int,
        consumed: int,
        watermark: str | None,
        page_cursor: str | None,
        pages: list[Page],
        deletes: list[str],
        dropped: int,
        retry_after_seconds: float,
    ) -> SyncResult:
        if stream.delete_missing:
            raise RuntimeError(
                f"connector {self.connector.name!r} yielded a protected rate limit for "
                f"stream {stream.name!r}"
            )
        resume = page_cursor
        if resume is None and consumed > skip_target:
            resume = json.dumps(
                {
                    BACKFILL_KEY: _BackfillEnvelope(
                        origin=origin,
                        skip=consumed,
                        watermark=watermark,
                    ).model_dump()
                },
                sort_keys=True,
            )
        return SyncResult(
            pages=tuple(pages),
            next_cursor=cursor if resume is None else resume,
            deletes=tuple(deletes),
            retry_after_seconds=retry_after_seconds,
            dropped=dropped,
            indexed=stream.indexed,
        )

    async def _credential(self, config: ConnectorSourceConfig, auth: SourceAuth) -> Credential:
        if auth.auth_proxy is None:
            raise RuntimeError(
                f"connector source {self.connector.name!r} needs an auth proxy but none is wired "
                "(install the provider's broker extension, or set [connectors] auth_backend)"
            )
        try:
            return await auth.auth_proxy.credential(auth.workspace_id, self.connector.name)
        except GrantUnusable as unusable:
            raise StreamSkipped(
                f"{self.connector.name}: {config.stream!r} {unusable}",
                awaits_grant=unusable.awaits_grant,
            ) from unusable

    def _base_url(self, auth: SourceAuth) -> str:
        base_url = auth.base_url or self.connector.base_url
        if base_url or not self.connector.dials_host:
            return base_url
        raise RuntimeError(
            f"connector source {self.connector.name!r} resolved no base_url: it is a "
            "per-tenant provider (its connector class leaves base_url empty) and its connection "
            "names no tenant URL — a misconfigured source fails its run rather than dial an "
            "empty host"
        )

    def _stream(self, name: str) -> StreamSpec:
        for stream in self.connector.streams():
            if stream.name == name:
                return stream
        raise ValueError(f"connector {self.connector.name!r} has no stream {name!r}")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> "_BackfillEnvelope | None":
        """A malformed envelope raises, since the adapter is its sole writer."""
        if cursor is None:
            return None
        try:
            parsed = json.loads(cursor)
        except ValueError:
            return None
        if not isinstance(parsed, dict) or BACKFILL_KEY not in parsed:
            return None
        try:
            return _BackfillEnvelope.model_validate(parsed[BACKFILL_KEY])
        except ValidationError as error:
            raise RuntimeError(f"malformed {BACKFILL_KEY} cursor envelope: {cursor!r}") from error

    def _page(
        self,
        stream: StreamSpec,
        record: dict[str, Any],
        address: str,
        read_by_children: frozenset[str],
    ) -> Page | None:
        """A record is dropped rather than raised: a raising run commits no page, so one
        unrepresentable record would stall the stream while the provider keeps returning it."""
        identity = self.connector.record_identity(record, stream)
        if identity is None:
            warn(
                "source_sync.unkeyed_record",
                connector=self.connector.name,
                stream=stream.name,
                primary_key=stream.primary_key,
            )
            return None
        ref = self.connector.record_ref(record, stream) or identity
        title, body = self.connector.render(record, stream)
        created_at = _record_timestamp(
            record,
            stream.created_at_field,
            connector=self.connector.name,
            stream=stream.name,
        )
        updated_at = _record_timestamp(
            record,
            stream.updated_at_field,
            connector=self.connector.name,
            stream=stream.name,
        )
        try:
            return Page(
                source_ref=f"{address}/{ref}",
                source_identity=f"{address}/{identity}",
                body=body,
                stream=stream.name,
                title=title,
                created_at=created_at,
                updated_at=updated_at,
                parent_fields=_parent_fields(record, read_by_children),
            )
        except ValidationError as error:
            warn(
                "source_sync.unrepresentable_record",
                connector=self.connector.name,
                stream=stream.name,
                source_ref=f"{address}/{ref}",
                fault=validation_fault(error),
            )
            return None


def _parent_fields(
    record: dict[str, Any], read_by_children: frozenset[str]
) -> dict[str, FieldValue] | None:
    if not read_by_children:
        return None
    projected: dict[str, FieldValue] = {}
    for name in sorted(read_by_children):
        value = record[name] if name in record else get_path(record, name)
        if isinstance(value, (str, int, float, bool)):
            projected[name] = value
    return projected


def _record_timestamp(
    record: dict[str, Any],
    field: str | None,
    *,
    connector: str,
    stream: str,
) -> str | None:
    if field is None:
        return None
    value = record[field] if field in record else get_path(record, field)
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return normalize_page_timestamp(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return normalize_page_timestamp(str(value))
    except ValueError:
        warn(
            "source_sync.malformed_timestamp",
            connector=connector,
            stream=stream,
            field=field,
        )
        return None
    warn(
        "source_sync.malformed_timestamp",
        connector=connector,
        stream=stream,
        field=field,
    )
    return None
