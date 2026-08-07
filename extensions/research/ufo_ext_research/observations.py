"""Durable retrieved-source observations keyed to the conversation that requested them."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.context import ExtensionContext
from ufo.sdk.manifest import (
    ConversationSlotContext,
    ConversationSlotProvider,
    ConversationSource,
    SourcesSlotPayload,
)
from ufo.sdk.search import FetchedPage, SearchHit

SOURCE_LIMIT = 100
TITLE_MAX_CHARS = 500
SNIPPET_MAX_CHARS = 2_000
DATE_MAX_CHARS = 40

_metadata = sa.MetaData()
source_observation = sa.Table(
    "research_source_observation",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, primary_key=True),
    sa.Column("conversation_id", sa.Uuid, primary_key=True),
    sa.Column("url_digest", sa.String(64), primary_key=True),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("turn_id", sa.Uuid, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("snippet", sa.Text, nullable=False),
    sa.Column("published_date", sa.Text, nullable=True),
    sa.Column("rank", sa.Integer, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class RetrievedSource:
    url: str
    title: str
    snippet: str
    published_date: str | None


def _bounded(value: str, limit: int) -> str:
    return value[:limit]


async def record_sources(
    ext: ExtensionContext,
    conversation_id: UUID,
    turn_id: UUID,
    sources: tuple[RetrievedSource, ...],
) -> None:
    if not sources:
        return
    async with ext.transaction() as connection:
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        observed_at = datetime.now(UTC)
        for rank, source in enumerate(sources):
            try:
                safe = ConversationSource(
                    url=source.url,
                    title=_bounded(source.title, TITLE_MAX_CHARS),
                    snippet=_bounded(source.snippet, SNIPPET_MAX_CHARS),
                    published_date=None
                    if source.published_date is None
                    else _bounded(source.published_date, DATE_MAX_CHARS),
                )
            except ValidationError:
                continue
            values = {
                "workspace_id": ext.store.workspace_id,
                "conversation_id": conversation_id,
                "url_digest": sha256(safe.url.encode()).hexdigest(),
                "url": safe.url,
                "turn_id": turn_id,
                "title": safe.title,
                "snippet": safe.snippet,
                "published_date": safe.published_date,
                "rank": rank,
            }
            await connection.execute(
                insert(source_observation)
                .values(**values, created_at=observed_at, updated_at=observed_at)
                .on_conflict_do_update(
                    index_elements=[
                        source_observation.c.workspace_id,
                        source_observation.c.conversation_id,
                        source_observation.c.url_digest,
                    ],
                    set_={
                        "url": safe.url,
                        "turn_id": turn_id,
                        "title": safe.title,
                        "snippet": safe.snippet,
                        "published_date": safe.published_date,
                        "rank": rank,
                        "updated_at": observed_at,
                    },
                )
            )
        retained = (
            sa.select(source_observation.c.url_digest)
            .where(
                source_observation.c.workspace_id == ext.store.workspace_id,
                source_observation.c.conversation_id == conversation_id,
            )
            .order_by(
                source_observation.c.updated_at.desc(),
                source_observation.c.rank,
                source_observation.c.url_digest,
            )
            .limit(SOURCE_LIMIT + 1)
        )
        await connection.execute(
            sa.delete(source_observation).where(
                source_observation.c.workspace_id == ext.store.workspace_id,
                source_observation.c.conversation_id == conversation_id,
                source_observation.c.url_digest.not_in(retained),
            )
        )


async def record_search_hits(
    ext: ExtensionContext,
    conversation_id: UUID,
    turn_id: UUID,
    hits: tuple[SearchHit, ...],
) -> None:
    await record_sources(
        ext,
        conversation_id,
        turn_id,
        tuple(
            RetrievedSource(
                url=hit.url,
                title=hit.title,
                snippet=hit.text,
                published_date=hit.published_date,
            )
            for hit in hits
        ),
    )


async def record_fetched_page(
    ext: ExtensionContext,
    conversation_id: UUID,
    turn_id: UUID,
    page: FetchedPage,
) -> None:
    await record_sources(
        ext,
        conversation_id,
        turn_id,
        (
            RetrievedSource(
                url=page.url,
                title=page.url,
                snippet=page.summary or page.text,
                published_date=None,
            ),
        ),
    )


async def _source_count(ctx: ConversationSlotContext) -> int | None:
    async with ctx.ext.transaction() as connection:
        count = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(source_observation)
            .where(
                source_observation.c.workspace_id == ctx.ext.store.workspace_id,
                source_observation.c.conversation_id == ctx.conversation_id,
            )
        )
    return None if not count else min(int(count), SOURCE_LIMIT)


async def _read_sources(ctx: ConversationSlotContext) -> SourcesSlotPayload:
    async with ctx.ext.transaction() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(source_observation)
                    .where(
                        source_observation.c.workspace_id == ctx.ext.store.workspace_id,
                        source_observation.c.conversation_id == ctx.conversation_id,
                    )
                    .order_by(source_observation.c.updated_at.desc(), source_observation.c.rank)
                    .limit(SOURCE_LIMIT + 1)
                )
            )
            .mappings()
            .all()
        )
    return SourcesSlotPayload(
        sources=tuple(
            ConversationSource(
                url=row["url"],
                title=row["title"],
                snippet=row["snippet"],
                published_date=row["published_date"],
            )
            for row in rows[:SOURCE_LIMIT]
        ),
        truncated=len(rows) > SOURCE_LIMIT,
    )


SOURCES_SLOT = ConversationSlotProvider(
    id="sources",
    label="Sources",
    icon="link",
    content=SourcesSlotPayload,
    summarize=_source_count,
    read=_read_sources,
)
