"""Authoritative YC guidance and bounded Bookface searches as workspace sources."""

import asyncio
import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.sdk.context import CredentialSlotUnset
from ufo.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_yc.cli import YC_CREDENTIALS_SLOT, YcRunner

YC_SOURCE_BACKEND = "yc"
YC_SOURCE_PAGE_LIMIT = 200
YC_SOURCE_REFRESH_SECONDS = 3600
YC_SOURCE_FETCH_TIMEOUT_SECONDS = 240
YC_SOURCE_MAX_PAGE_BYTES = 1024 * 1024
YC_SOURCE_TRUNCATED = "\n\n[Content truncated at the YC source size bound.]"
YC_SEARCH_MAX_RESULTS = 1000
YC_SEARCH_MAX_RESULTS_LIMIT = 5000
YcGuidanceCollection = Literal["user_manuals", "startup_library"]
YC_GUIDANCE_COLLECTIONS: tuple[YcGuidanceCollection, ...] = (
    "user_manuals",
    "startup_library",
)
YC_SEARCH_COLLECTIONS = (
    "companies",
    "founders",
    "investors",
    "deals",
    "meetups",
    "forum",
    "launches",
    "alumni_groups",
    "jobs",
)

YcCollection = Literal[
    "user_manuals",
    "startup_library",
    "companies",
    "founders",
    "investors",
    "deals",
    "meetups",
    "forum",
    "launches",
    "alumni_groups",
    "jobs",
]
YcSearchCollection = Literal[
    "companies",
    "founders",
    "investors",
    "deals",
    "meetups",
    "forum",
    "launches",
    "alumni_groups",
    "jobs",
]


class YcSourceConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    collection: YcCollection
    query: str | None = Field(default=None, min_length=2, max_length=500)
    max_results: int | None = Field(default=None, ge=1, le=YC_SEARCH_MAX_RESULTS_LIMIT)
    refresh_seconds: int = Field(default=YC_SOURCE_REFRESH_SECONDS, ge=60, le=24 * 60 * 60)

    @model_validator(mode="after")
    def validate_collection(self) -> "YcSourceConfig":
        if self.collection in YC_GUIDANCE_COLLECTIONS and self.query is not None:
            raise ValueError("YC guidance collections do not accept a query")
        if self.collection in YC_GUIDANCE_COLLECTIONS and self.max_results is not None:
            raise ValueError("YC guidance collections do not accept a result bound")
        if self.collection in YC_SEARCH_COLLECTIONS and self.query is None:
            raise ValueError("YC directory collections require a query")
        if self.collection in YC_SEARCH_COLLECTIONS and self.max_results is None:
            self.max_results = YC_SEARCH_MAX_RESULTS
        return self


class YcIndexInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    entity: YcSearchCollection
    query: str = Field(min_length=2, max_length=500)
    max_results: int = Field(default=YC_SEARCH_MAX_RESULTS, ge=1, le=YC_SEARCH_MAX_RESULTS_LIMIT)
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description shown in the activity timeline.",
    )


class YcSourceCursor(BaseModel):
    synced_at: datetime


class YcSearchResult(BaseModel):
    status: Literal["success"]
    count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    csv_results: str


class YcToolResult(BaseModel):
    name: str
    result: YcSearchResult


class YcSearchRow(BaseModel):
    record_id: str = Field(alias="displayed_attributes.id", min_length=1)
    link: str = Field(alias="displayed_attributes.link", min_length=1)
    body: str = Field(alias="displayed_attributes.body")
    description: str = Field(default="", alias="displayed_attributes.description")
    categories: str = Field(default="", alias="displayed_attributes.categories")


class YcDirectoryRow(BaseModel):
    record_id: str = Field(min_length=1)
    link: str = Field(min_length=1)
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class YcSource:
    runner: YcRunner
    config_model: ClassVar[type[YcSourceConfig]] = YcSourceConfig

    async def fetch(
        self, config: YcSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        now = datetime.now(UTC)
        if cursor is not None:
            last = YcSourceCursor.model_validate_json(cursor).synced_at
            if now < last + timedelta(seconds=config.refresh_seconds):
                return SyncResult(pages=(), next_cursor=cursor)
        try:
            async with asyncio.timeout(YC_SOURCE_FETCH_TIMEOUT_SECONDS):
                pages = await self._fetch_collection(config, auth)
        except CredentialSlotUnset as error:
            raise StreamSkipped("YC is not connected") from error
        return SyncResult(
            pages=pages,
            next_cursor=YcSourceCursor(synced_at=now).model_dump_json(),
            snapshot=True,
        )

    async def _fetch_collection(self, config: YcSourceConfig, auth: SourceAuth) -> tuple[Page, ...]:
        pages: list[Page] = []
        page = 0
        total = 1
        guidance = config.collection in YC_GUIDANCE_COLLECTIONS
        max_results = config.max_results
        while page * YC_SOURCE_PAGE_LIMIT < total and (
            max_results is None or page * YC_SOURCE_PAGE_LIMIT < max_results
        ):
            limit = YC_SOURCE_PAGE_LIMIT
            request: dict[str, object] = {"limit": limit, "page": page}
            if guidance:
                request["filters"] = {"agent_searchable": "true"}
            else:
                request["query"] = config.query
            arguments = json.dumps(
                request,
                separators=(",", ":"),
            )
            raw = await self.runner.run(
                (
                    "tools",
                    "run",
                    f"search.{config.collection}",
                    "--input",
                    arguments,
                    "--json",
                ),
                session=f"ufo-source-{auth.workspace_id}",
            )
            expected_name = f"search.{config.collection}"
            envelope = YcToolResult.model_validate_json(raw)
            if envelope.name != expected_name:
                raise ValueError(
                    f"YC search returned {envelope.name!r}, expected {expected_name!r}"
                )
            result = envelope.result
            found = self._pages(config.collection, result.csv_results)
            if len(found) != result.count:
                raise ValueError(
                    f"YC search reported {result.count} rows but returned {len(found)}"
                )
            if result.count > limit:
                raise ValueError(
                    f"YC search returned {result.count} rows for a {limit}-row request"
                )
            if result.total_count < result.count:
                raise ValueError(
                    f"YC search total {result.total_count} is below page count {result.count}"
                )
            total = result.total_count
            if max_results is None:
                pages.extend(found)
            else:
                pages.extend(found[: max_results - len(pages)])
            page += 1
        return tuple(pages)

    def _pages(self, collection: str, body: str) -> tuple[Page, ...]:
        if collection in YC_GUIDANCE_COLLECTIONS:
            return self._guidance_pages(collection, body)
        return self._directory_pages(collection, body)

    def _guidance_pages(self, collection: str, body: str) -> tuple[Page, ...]:
        pages: list[Page] = []
        for raw in csv.DictReader(io.StringIO(body)):
            row = YcSearchRow.model_validate(raw)
            rendered = self._bounded(
                "\n\n".join(
                    part for part in (row.link, row.description, row.body, row.categories) if part
                )
            )
            pages.append(
                Page(
                    source_ref=f"{collection}/{row.record_id}",
                    digest="sha256:" + hashlib.sha256(rendered.encode()).hexdigest(),
                    body=rendered,
                )
            )
        return tuple(pages)

    def _directory_pages(self, collection: str, body: str) -> tuple[Page, ...]:
        pages: list[Page] = []
        for raw in csv.DictReader(io.StringIO(body)):
            row = YcDirectoryRow.model_validate(
                {
                    "record_id": raw.get("id"),
                    "link": raw.get("link"),
                    "attributes": tuple(
                        (name, value)
                        for name, value in raw.items()
                        if name not in ("id", "link") and value
                    ),
                }
            )
            rendered = self._bounded(
                "\n\n".join(
                    (
                        row.link,
                        "\n".join(f"{name}: {value}" for name, value in row.attributes),
                    )
                )
            )
            pages.append(
                Page(
                    source_ref=f"{collection}/{row.record_id}",
                    digest="sha256:" + hashlib.sha256(rendered.encode()).hexdigest(),
                    body=rendered,
                )
            )
        return tuple(pages)

    def _bounded(self, body: str) -> str:
        encoded = body.encode()
        if len(encoded) <= YC_SOURCE_MAX_PAGE_BYTES:
            return body
        prefix = encoded[: YC_SOURCE_MAX_PAGE_BYTES - len(YC_SOURCE_TRUNCATED.encode())]
        return prefix.decode("utf-8", "ignore") + YC_SOURCE_TRUNCATED


async def yc_index(ctx: ToolContext, args: YcIndexInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("yc_index dispatched without the YC extension context")
    await ctx.ext.credentials.get(YC_CREDENTIALS_SLOT)
    await ctx.ext.register_source(
        YC_SOURCE_BACKEND,
        YcSourceConfig(
            collection=args.entity,
            query=args.query,
            max_results=args.max_results,
        ),
        subject=SHARED_SUBJECT,
        owner_member_id=None,
    )
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Syncing up to {args.max_results} {args.entity} results for {args.query!r} "
                    "into shared memory. Repeating this request is idempotent."
                )
            ),
        )
    )
