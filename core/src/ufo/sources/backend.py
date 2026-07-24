"""The adapter that puts a connector on the core source seam.

A connector speaks in streams and async page generators; the source seam speaks in one `SyncResult`
per run. `ConnectorBackend` bridges them: one `source` row is one (account, stream), so `fetch`
resolves the account's `Credential` through the runner's auth proxy, drives the connector's one
stream, and renders each record into a recallable `Page`. A full-collection stream
(`delete_missing`) returns as an authoritative `snapshot` so the driver tombstones records that
vanished; an incremental stream returns `snapshot=False`, advances a watermark over its
`cursor_field`, and names any provider-reported removals in `deletes`.

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
advances the count. The envelope dissolves to a plain watermark cursor once a run finally exhausts
the stream.

`snapshot = delete_missing`: only a full-snapshot stream tombstones, and it is never capped, so its
run always enumerates the whole collection. An incremental (tier-1/tier-2) run never snapshots.
Trades: tier 2 re-fetches the skipped prefix over HTTP each slice; the skip count
assumes the connector enumerates in a stable order between runs — drift self-heals, since updated
or created rows carry fresh `cursor_field` values the next incremental pass catches, and a
cursor-less stream re-walks fully regardless.

The credential the proxy hands back is a broker's proxying transport (the secret never leaves the
broker) or a member-added key read host-side from the credential store (the direct/BYOK backend) —
either way it is used in-process by the sync job in the jobs role and NEVER reaches the sandbox or
agent surface, which is the invariant this preserves. Keep the `Credential` out of any structured
log. The manifest registers one backend per connector in the registry."""

import hashlib
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.o11y import warn
from ufo.sources.connector import Connector, StreamPage, StreamSpec
from ufo.sources.sync import Page, SourceAuth, SyncResult, normalize_page_timestamp

MAX_RECORDS_PER_RUN = 5_000
CAP_OVERRUN_FACTOR = 4
BACKFILL_KEY = "ufo_backfill"


class _BackfillEnvelope(BaseModel):
    """The tier-2 resume state the adapter round-trips through a source's cursor when a capped run
    yielded no native checkpoint: the connector cursor the slice re-drives from, the record count to
    discard off its front, and the watermark folded over what has landed so far. Persisted in the
    source row's cursor, so a validated model that rejects anything beyond its three fields."""

    model_config = ConfigDict(extra="forbid")

    origin: str | None
    skip: int
    watermark: str | None


class ConnectorSourceConfig(BaseModel):
    """Which account + stream one connector source row syncs. `account` is the handle the auth proxy
    resolves the credential for (a broker connected-account id under Composio, a label under the
    direct backend, whose key is keyed by the provider name); `stream` is the connector stream this
    row pulls. `base_url` overrides the connector's host for a per-tenant provider (Freshdesk's
    `https://<account>.freshdesk.com`, Zendesk's `<subdomain>.zendesk.com`), whose connector class
    leaves `base_url` empty; it is part of the config the `source_row_id` hashes, so two tenants of
    the same provider settle on distinct rows. The backend never reads a raw token — it asks the
    proxy for a `Credential`."""

    account: str
    stream: str
    base_url: str | None = None


@dataclass(frozen=True)
class ConnectorBackend:
    """Drives one connector's one stream per run onto the source seam. See the module docstring."""

    connector: Connector
    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        if auth.auth_proxy is None:
            raise RuntimeError(
                f"connector source {self.connector.name!r} needs an auth proxy but none is wired "
                "(install the provider's broker extension, or set [connectors] auth_backend)"
            )
        credential = await auth.auth_proxy.credential(
            auth.workspace_id, self.connector.name, config.account
        )
        stream = self._stream(config.stream)
        base_url = config.base_url or self.connector.base_url
        if not base_url:
            raise RuntimeError(
                f"connector source {self.connector.name!r} resolved no base_url: it is a "
                "per-tenant provider (its connector class leaves base_url empty) and the source "
                "row set no base_url — a misconfigured source fails its run rather than dial an "
                "empty host"
            )
        envelope = None if stream.delete_missing else self._decode_cursor(cursor)
        if envelope is None:
            origin, skip_target, watermark = cursor, 0, cursor
        else:
            origin, skip_target, watermark = envelope.origin, envelope.skip, envelope.watermark
        pages: list[Page] = []
        deletes: list[str] = []
        page_cursor: str | None = None
        consumed = 0
        skipped = 0
        over = False
        cap_checkpoint: str | None = None
        stream_pages = self.connector.fetch_page(
            stream, cursor=origin, credential=credential, base_url=base_url
        )
        try:
            async for page in stream_pages:
                records = page.records if isinstance(page, StreamPage) else page
                for record in records:
                    consumed += 1
                    if skipped < skip_target:
                        skipped += 1
                        continue
                    pages.append(self._page(stream, record))
                    if stream.cursor_field:
                        watermark = _max_str(watermark, record.get(stream.cursor_field))
                if isinstance(page, StreamPage):
                    deletes.extend(f"{stream.name}/{external_id}" for external_id in page.deletes)
                    if page.next_cursor:
                        page_cursor = page.next_cursor
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
                    )
        finally:
            if isinstance(stream_pages, AsyncGenerator):
                await stream_pages.aclose()
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
        )

    def _stream(self, name: str) -> StreamSpec:
        for stream in self.connector.streams():
            if stream.name == name:
                return stream
        raise ValueError(f"connector {self.connector.name!r} has no stream {name!r}")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> "_BackfillEnvelope | None":
        """A stored cursor as its `_BackfillEnvelope` when it is one, else None — meaning the
        cursor is opaque connector state (a plain watermark, a connector's own JSON map) that
        passes through to `fetch_page` untouched. Only a JSON object carrying the reserved
        `ufo_backfill` key is the adapter's envelope; a malformed one raises, since the adapter is
        its sole writer."""
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

    def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page:
        """One provider record as a recallable page: the connector's rendered body, keyed by
        `stream.name/<primary key>` so a re-fetch of an unchanged record, an upsert, and a `deletes`
        entry all settle on the same page."""
        ref = _record_ref(stream, record)
        title, body = self.connector.render(record, stream)
        created_at = _optional_string(record, "created_at")
        raw_updated_at = record.get("updated_at")
        updated_at: str | None
        if isinstance(raw_updated_at, str):
            updated_at = raw_updated_at
        elif raw_updated_at is not None:
            raise ValueError("record field 'updated_at' must be a string")
        elif stream.cursor_field:
            candidate = _optional_string(record, stream.cursor_field)
            if candidate is None:
                updated_at = None
            else:
                try:
                    updated_at = normalize_page_timestamp(candidate)
                except ValueError:
                    updated_at = None
        else:
            updated_at = None
        return Page(
            source_ref=f"{stream.name}/{ref}",
            digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
            body=body,
            stream=stream.name,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
        )


def _optional_string(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"record field {field!r} must be a string")


def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str:
    value = record.get(stream.primary_key)
    if isinstance(value, (str, int)):
        return str(value)
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def _max_str(current: str | None, value: Any) -> str | None:
    if not isinstance(value, str):
        return current
    if current is None or value > current:
        return value
    return current
