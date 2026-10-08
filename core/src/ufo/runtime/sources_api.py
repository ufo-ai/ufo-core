"""The sources service's client, and the page feed core's `page_change` consumers read through it.

`SourcesFeed` replays the bound workspace's page changes from `GET /v1/sources/changes` and maps
each to the core connection its source names in `labels.connection` (`SourceLinks`). A source no
core connection names — one a program connected with its own token — feeds no consumer: its items
are dropped, and the feed reads on so they never hold a consumer's cursor."""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ufo.harness.o11y import log
from ufo.runtime.cloud import CloudApi, CloudApis, CloudRefused
from ufo.runtime.pages import PAGE_FEED_BATCH_MAX, PageBatch, PageChange
from ufo.runtime.workspace import ws_current

SOURCE_CONNECTION_LABEL = "connection"
SOURCE_LINKS_MAX = 65_536
FEED_READS_MAX = 4
NOT_FOUND_STATUS = 404


class WireFeedItem(BaseModel):
    """One page change of `GET /v1/sources/changes`."""

    model_config = ConfigDict(extra="ignore")
    page_id: UUID
    source_id: UUID
    connection_id: UUID
    provider: str
    subject: str
    stream: str
    title: str
    body: str
    digest: str
    revision: int
    tombstone: bool
    indexed: bool
    as_of: AwareDatetime
    created_at: AwareDatetime
    changed_at: AwareDatetime


class ChangesPage(BaseModel):
    """A batch of the feed and where the next read starts; null on an empty batch."""

    model_config = ConfigDict(extra="ignore")
    items: list[WireFeedItem]
    next_cursor: str | None


class SourceOptions(BaseModel):
    """A source's provider options: a repository's `repo` and `branch`, nothing for any other."""

    model_config = ConfigDict(extra="ignore")
    repo: str | None = None
    branch: str | None = None


class Source(BaseModel):
    """One stream of a connection the sources service syncs."""

    model_config = ConfigDict(extra="ignore")
    id: UUID
    connection_id: UUID
    provider: str
    stream: str
    secrets: dict[str, str]
    subject: str
    base_url: str | None
    options: SourceOptions
    backfill_days: int | None
    labels: dict[str, str]
    next_sync_at: AwareDatetime | None
    synced_at: AwareDatetime | None
    consecutive_errors: int
    parked_at: AwareDatetime | None
    parked_reason: str | None
    awaits_grant: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


@dataclass(frozen=True)
class SourcesApi:
    """The sources service's routes, called as one workspace."""

    cloud: CloudApi

    async def changes(self, cursor: str | None, limit: int) -> ChangesPage:
        """The page changes after `cursor`, from the start when it is None."""
        params = () if cursor is None else (("cursor", cursor),)
        return await self.cloud.send(
            "GET",
            "/v1/sources/changes",
            params=(*params, ("limit", str(limit))),
            answer=ChangesPage,
        )

    async def source(self, source_id: UUID) -> Source | None:
        """The source `source_id`, or None when the service holds no such source."""
        try:
            return await self.cloud.send("GET", f"/v1/sources/{source_id}", answer=Source)
        except CloudRefused as refusal:
            if refusal.status == NOT_FOUND_STATUS:
                return None
            raise


@dataclass(frozen=True)
class SourceLink:
    """The core connection a source syncs, and its provider."""

    connection_id: UUID
    provider: str


@dataclass(frozen=True)
class SourceLinks:
    """Each source's core connection, per workspace, as the source's `labels.connection` names it,
    or None for a source that names none. A source's label never changes, so an entry is never
    refreshed; past `SOURCE_LINKS_MAX` entries the oldest leaves."""

    entries: dict[tuple[UUID, UUID], SourceLink | None]

    async def of(
        self, api: SourcesApi, workspace_id: UUID, source_ids: Collection[UUID]
    ) -> dict[UUID, SourceLink]:
        """The links of `source_ids` that name a core connection, every unknown source fetched at
        once."""
        missing = [
            source_id
            for source_id in dict.fromkeys(source_ids)
            if (workspace_id, source_id) not in self.entries
        ]
        fetched = await asyncio.gather(*(api.source(source_id) for source_id in missing))
        for source_id, source in zip(missing, fetched, strict=True):
            link = None if source is None else _link(source)
            if link is None:
                log(
                    "pages.source_unlinked",
                    source_id=str(source_id),
                    found=source is not None,
                )
            self.entries[(workspace_id, source_id)] = link
        links = {
            source_id: link
            for source_id in source_ids
            if (link := self.entries.get((workspace_id, source_id))) is not None
        }
        while len(self.entries) > SOURCE_LINKS_MAX:
            self.entries.pop(next(iter(self.entries)), None)
        return links


def _link(source: Source) -> SourceLink | None:
    label = source.labels.get(SOURCE_CONNECTION_LABEL)
    if label is None:
        return None
    try:
        return SourceLink(connection_id=UUID(label), provider=source.provider)
    except ValueError:
        return None


@dataclass(frozen=True)
class SourcesFeed:
    """The `PageFeed` over the sources service: the bound workspace's changes, each carrying its
    source's core connection. An item whose source names no core connection is dropped; while a
    read keeps nothing and the feed moved on, it reads on, at most `FEED_READS_MAX` reads a call,
    and answers the position it reached so the consumer's cursor passes those items."""

    apis: CloudApis
    links: SourceLinks

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        workspace_id = ws_current().workspace_id
        api = SourcesApi(cloud=self.apis.bound(workspace_id))
        after, moved = cursor, None
        kept: list[PageChange] = []
        for _ in range(FEED_READS_MAX):
            page = await api.changes(after, min(limit, PAGE_FEED_BATCH_MAX))
            links = await self.links.of(api, workspace_id, [item.source_id for item in page.items])
            for item in page.items:
                link = links.get(item.source_id)
                if link is None:
                    log(
                        "pages.unlinked",
                        page_id=str(item.page_id),
                        source_id=str(item.source_id),
                        revision=item.revision,
                    )
                    continue
                kept.append(
                    PageChange(
                        page_id=item.page_id,
                        source_id=item.source_id,
                        connection_id=link.connection_id,
                        provider=item.provider,
                        subject=item.subject,
                        stream=item.stream,
                        title=item.title,
                        body=item.body,
                        digest=item.digest,
                        revision=item.revision,
                        tombstone=item.tombstone,
                        indexed=item.indexed,
                        created_at=item.created_at,
                        as_of=item.as_of,
                        changed_at=item.changed_at,
                    )
                )
            if page.next_cursor is None:
                break
            after = moved = page.next_cursor
            if kept:
                break
        return PageBatch(changes=tuple(kept), next_cursor=moved)
