"""The memory extension's two object kinds: `memory`, stored memory items readable by durable id,
and `profile`, what the workspace knows about each of its members.

`memory_search` finds and returns refs; that kind is what a ref opens — the item's body and recall
inputs beside its provenance links: `created_from` names the synced page a derivation distilled it
from, `superseded_by` the item that stands in its place, `overtaken_by` the item that says what is
true now about the thing it names. Search excludes superseded items,
so the link is the recovery path when an old id arrives through a stale reference; `get` resolves
any visible row while `list` shows only live ones — a row superseded, or one the page pass retired,
is off the listing the wiki reads and still open by its id. Reads follow the caller's audience;
foreign rooms are sealed from shared memory, and a signed-in member reads the same rows in the
portal on the subjects their own conversation carries. `memory_update` stays the write path, and
there is no delete: index chunks are derived by jobs and no cleanup path exists for one item's
chunks.

`profile` is the People band: one row per member, named by their member id, carrying the role and
the current focus the People pass wrote. It reads on the same rule the shared half of `memory` does
— a reader whose subjects carry the workspace-shared one reads every entry, and a foreign room
reads none — because every entry is written from the shared facts alone, so a profile says exactly
what its reader could already read in the Facts band. The pass is the write path; apply and delete
refuse."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, get_args
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.accounting import MEMORY_SERVICE
from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.context import (
    ExtensionContext,
    JsonValue,
    PageState,
    SourceReader,
    agent_current,
)
from ufo.sdk.objects import (
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.tools import ToolContext
from ufo_ext_memory.client import ItemClass, Memory, MemoryApi, MemoryKind, PageSource, Reach
from ufo_ext_memory.condenser import memory_profile
from ufo_ext_memory.store import (
    OVERVIEW,
    SECTION,
    SEMANTIC,
    _aware,
    clip_to_word,
    memory_item,
    one_row_per_statement,
)

MEMORY_KIND = "memory"
PROFILE_KIND = "profile"
PAGE_OBJECT_KIND = "page"
CREATED_FROM: Final = "created_from"
MEMORY_UPDATE_REFUSAL = "memories are recorded through memory_update, never applied"
MEMORY_UNDELETABLE = (
    "memories cannot be deleted — consolidation supersedes them and recall drops superseded items"
)
PROFILE_WRITE_REFUSAL = (
    "member profiles are written by the People pass from the workspace's shared facts"
)
PROFILE_LIST_MAX = 500
SUMMARY_MAX = 120
TEXT_MAX = 2000
"""How much of an item's body a listing row carries as `text`. A consolidated summary is a
paragraph a band renders whole, so the bound sits far past one; a row stays lightweight, so the
bound exists."""
MEMORY_LIST_MAX = 500
CONDENSED_CLASSES = frozenset({SEMANTIC, SECTION, OVERVIEW})


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(description="The stored memory text.")
    subject: str = Field(description="The exact visibility audience.")
    item_class: str = Field(description="fact, episodic, semantic, section, or overview.")
    memory_kind: str = Field(
        description="Recency-decay kind: fact, preference, decision, event, or task."
    )
    confidence: int = Field(description="Confidence 1-10; scales a fact's decayed recall rank.")
    source_ref: str | None = Field(
        description="Free-form note on what produced the memory, when the writer gave one."
    )
    as_of: str | None = Field(description="When the source information was current, ISO-8601.")


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("memory objects dispatched without their ExtensionContext")
    return ext


def _stamp(written: datetime) -> str:
    """SQLite holds no offset on a `timezone=True` column and Postgres does."""
    return _aware(written).isoformat()


def _row(
    name: str,
    body: str,
    subject: str,
    item_class: str,
    memory_kind: str,
    written: datetime | None,
    page_id: UUID | None,
    pages: Mapping[UUID, PageState],
) -> ObjectRow:
    state = None if page_id is None else pages.get(page_id)
    return ObjectRow(
        name=name,
        summary=clip_to_word(body, SUMMARY_MAX),
        fields={
            "subject": subject,
            "item_class": item_class,
            "memory_kind": memory_kind,
            "text": clip_to_word(body, TEXT_MAX),
            "written": None if written is None else _stamp(written),
            "created_from_page_id": None if page_id is None else str(page_id),
            "created_from_page_title": None if state is None else state.title,
            "created_from_page_stream": None if state is None else state.stream,
        },
    )


def _own_page(memory: Memory) -> UUID | None:
    """The page a memory was drawn from; a condensed memory's pages are the sources of the memories
    it stands for, not its own."""
    if memory.item_class in CONDENSED_CLASSES:
        return None
    return next(
        (source.page_id for source in memory.sources if isinstance(source, PageSource)), None
    )


def _reach_of(reader: SourceReader) -> Reach:
    return Reach(agent_id=reader.agent_id, member_id=reader.requesting_member_id)


def _member_reader(member_id: UUID) -> SourceReader:
    """The same three a turn's `source_reader` carries, taken from the portal read."""
    return SourceReader(
        agent_id=agent_current().agent_id,
        requesting_member_id=member_id,
        subjects=audience_subjects(conversation_audience(member_id)),
    )


@dataclass(frozen=True)
class MemoryObjects:
    """Read-only handlers over the memories the caller's subjects read — the memory service's, as
    the caller's reach, where the deploy selects it, else the extension's own `memory_item` rows:
    list shows live (non-superseded) items newest first, get resolves any visible row — including a
    superseded one, whose `superseded_by` link names its replacement. Both mutations refuse."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return await self._page(_require_ext(ctx.ext), ctx.source_reader(), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The live memory a signed-in member reads outside a turn: the rows `list` produces, on
        the subjects their own conversation carries — their own and the workspace-shared. Another
        member's private memory and a foreign room's are absent here for everyone, an admin
        included; a shared item distilled from a page the reader may no longer read drops out
        exactly as it does in a turn."""
        return await self._page(_require_ext(ext), _member_reader(member_id), query)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[MemorySpec] | None:
        """One memory item as the portal reads it — the row `list` renders beside the detail `get`
        reads, on the same subjects `member_page` lists under. A superseded item still answers, so
        a stale reference lands on the `superseded_by` link that names its replacement."""
        context = _require_ext(ext)
        reader = _member_reader(member_id)
        detail = await self._item(context, reader, name)
        if detail is None:
            return None
        spec = detail.spec
        page_id = next(
            (UUID(link.target.name) for link in detail.links if link.relation == CREATED_FROM),
            None,
        )
        cited = await context.readable_page_states(() if page_id is None else (page_id,), reader)
        return MemberObject(
            row=_row(
                name,
                spec.body,
                spec.subject,
                spec.item_class,
                spec.memory_kind,
                detail.created_at,
                page_id,
                cited,
            ),
            detail=detail,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None:
        return await self._item(_require_ext(ctx.ext), ctx.source_reader(), name)

    async def _page(
        self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery
    ) -> ObjectPage:
        if ext.cloud_selects(MEMORY_SERVICE):
            return await self._served_page(ext, reader, query)
        subjects = reader.subjects
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.subject,
                        memory_item.c.item_class,
                        memory_item.c.memory_kind,
                        memory_item.c.as_of,
                        memory_item.c.created_at,
                        memory_item.c.created_from_page_uid.label("created_from_page_id"),
                        memory_item.c.created_from_page_revision,
                    )
                    .where(
                        memory_item.c.workspace_id == ext.store.workspace_id,
                        memory_item.c.subject.in_(subjects),
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id)
                    .limit(MEMORY_LIST_MAX)
                )
            ).all()
        page_ids = tuple(
            row.created_from_page_id for row in rows if row.created_from_page_id is not None
        )
        cited = await ext.readable_page_states(page_ids, reader)
        servable = one_row_per_statement(
            row
            for row in rows
            if row.created_from_page_id is None
            or (
                (state := cited.get(row.created_from_page_id)) is not None
                and state.subject == row.subject
                and state.revision == row.created_from_page_revision
                and state.subject in subjects
            )
        )
        return object_page(
            tuple(
                _row(
                    str(row.id),
                    row.body,
                    row.subject,
                    row.item_class,
                    row.memory_kind,
                    row.created_at,
                    row.created_from_page_id,
                    cited,
                )
                for row in servable
            ),
            query,
        )

    async def _served_page(
        self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery
    ) -> ObjectPage:
        item_class = query.filters.get("item_class")
        memory_kind = query.filters.get("memory_kind")
        if (
            not reader.subjects
            or (item_class is not None and item_class not in get_args(ItemClass))
            or (memory_kind is not None and memory_kind not in get_args(MemoryKind))
        ):
            return object_page((), query)
        memories = await MemoryApi(cloud=ext.cloud_api()).newest(
            subjects=reader.subjects,
            item_classes=() if item_class is None else (str(item_class),),
            kinds=() if memory_kind is None else (str(memory_kind),),
            reach=_reach_of(reader),
            total=MEMORY_LIST_MAX,
        )
        cited = await ext.readable_page_states(
            tuple(page_id for memory in memories if (page_id := _own_page(memory)) is not None),
            reader,
        )
        return object_page(
            tuple(
                _row(
                    str(memory.id),
                    memory.body,
                    memory.subject,
                    memory.item_class,
                    memory.kind,
                    memory.created_at,
                    _own_page(memory),
                    cited,
                )
                for memory in memories
            ),
            query,
        )

    async def _item(
        self, ext: ExtensionContext, reader: SourceReader, name: str
    ) -> ObjectDetail[MemorySpec] | None:
        try:
            item_id = UUID(name)
        except ValueError:
            return None
        if ext.cloud_selects(MEMORY_SERVICE):
            return await self._served_item(ext, reader, item_id)
        subjects = reader.subjects
        async with ext.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(memory_item).where(
                            memory_item.c.workspace_id == ext.store.workspace_id,
                            memory_item.c.id == item_id,
                            memory_item.c.subject.in_(subjects),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["created_from_page_uid"] is not None:
            state = (
                await ext.readable_page_states(
                    (row["created_from_page_uid"],),
                    reader,
                )
            ).get(row["created_from_page_uid"])
            if (
                state is None
                or state.subject != row["subject"]
                or state.revision != row["created_from_page_revision"]
                or state.subject not in subjects
            ):
                return None
        links: list[ObjectLink] = []
        if row["created_from_page_uid"] is not None:
            links.append(
                ObjectLink(
                    relation=CREATED_FROM,
                    target=ObjectRef(kind=PAGE_OBJECT_KIND, name=str(row["created_from_page_uid"])),
                )
            )
        if row["superseded_by"] is not None:
            links.append(
                ObjectLink(
                    relation="superseded_by",
                    target=ObjectRef(kind=MEMORY_KIND, name=str(row["superseded_by"])),
                )
            )
        if row["overtaken_by"] is not None:
            links.append(
                ObjectLink(
                    relation="overtaken_by",
                    target=ObjectRef(kind=MEMORY_KIND, name=str(row["overtaken_by"])),
                )
            )
        return ObjectDetail(
            spec=MemorySpec(
                body=row["body"],
                subject=row["subject"],
                item_class=row["item_class"],
                memory_kind=row["memory_kind"],
                confidence=row["confidence"],
                source_ref=row["source_ref"],
                as_of=None if row["as_of"] is None else row["as_of"].isoformat(),
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            links=tuple(links),
        )

    async def _served_item(
        self, ext: ExtensionContext, reader: SourceReader, item_id: UUID
    ) -> ObjectDetail[MemorySpec] | None:
        if not reader.subjects:
            return None
        found = await MemoryApi(cloud=ext.cloud_api()).get(
            item_id, subjects=reader.subjects, reach=_reach_of(reader)
        )
        if found is None:
            return None
        page_id = _own_page(found)
        links: list[ObjectLink] = []
        if page_id is not None:
            links.append(
                ObjectLink(
                    relation=CREATED_FROM,
                    target=ObjectRef(kind=PAGE_OBJECT_KIND, name=str(page_id)),
                )
            )
        if found.superseded_by is not None:
            links.append(
                ObjectLink(
                    relation="superseded_by",
                    target=ObjectRef(kind=MEMORY_KIND, name=str(found.superseded_by)),
                )
            )
        if found.invalidated_by is not None:
            links.append(
                ObjectLink(
                    relation="overtaken_by",
                    target=ObjectRef(kind=MEMORY_KIND, name=str(found.invalidated_by)),
                )
            )
        return ObjectDetail(
            spec=MemorySpec(
                body=found.body,
                subject=found.subject,
                item_class=found.item_class,
                memory_kind=found.kind,
                confidence=found.confidence,
                source_ref=found.source_ref,
                as_of=None if found.as_of is None else found.as_of.isoformat(),
            ),
            created_at=found.created_at,
            updated_at=None,
            links=tuple(links),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: MemorySpec,
        old: MemorySpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(MEMORY_UPDATE_REFUSAL)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(MEMORY_UNDELETABLE)


MEMORY_OBJECT = ObjectKind(
    name=MEMORY_KIND,
    description=(
        "A stored memory item, readable by the id a memory_search ref carries. Read-only: "
        "memory_update records memories, consolidation supersedes them."
    ),
    guidance=(
        "Open a memory_search ref here to read the full item: its body, recall inputs, and "
        "links — pass the `created_from` target unchanged to object_get for the synced page the "
        "item was distilled from, and `superseded_by` names the item that replaced it. "
        "Search excludes superseded items, so reaching one through an old reference means "
        "follow `superseded_by` to the current statement before relying on it. Listing shows "
        "live items newest first (filter subject, item_class, or memory_kind, and order on "
        "`written`, the moment the item was recorded); search, not "
        "listing, is how memory is recalled. Reads follow the conversation audience. Apply and "
        "delete are refused — memory_update is the write path, and "
        "consolidation is how an item ends."
    ),
    spec_model=MemorySpec,
    store=MemoryObjects(),
    list_fields=frozenset(
        {
            "subject",
            "item_class",
            "memory_kind",
            "text",
            "written",
            "created_from_page_id",
            "created_from_page_title",
            "created_from_page_stream",
        }
    ),
)


class ProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(description="What this person does in the workspace, in a short phrase.")
    focus: str = Field(description="What they are carrying now, in one sentence.")


@dataclass(frozen=True)
class ProfileObjects:
    """Read-only handlers over the extension's own `memory_profile` rows. Every entry is written
    from the workspace-shared facts, so the fence is whether the reader carries the shared subject
    at all: a member of the workspace reads the whole People band, a foreign room reads none of it.
    Both mutations refuse."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return await self._page(_require_ext(ctx.ext), ctx.read_subjects, query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The People band a signed-in member reads outside a turn — the rows `list` produces, on
        the subjects their own conversation carries, which is the workspace-shared one and their
        own. No entry widens for an admin, because none of them is narrowed for anyone else."""
        return await self._page(
            _require_ext(ext), audience_subjects(conversation_audience(member_id)), query
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None:
        found = await self._entry(_require_ext(ctx.ext), ctx.read_subjects, name)
        return None if found is None else found.detail

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[ProfileSpec] | None:
        return await self._entry(
            _require_ext(ext), audience_subjects(conversation_audience(member_id)), name
        )

    async def _page(
        self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery
    ) -> ObjectPage:
        if SHARED_SUBJECT not in subjects:
            return object_page((), query)
        async with ext.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        memory_profile.c.member_id,
                        memory_profile.c.role,
                        memory_profile.c.focus,
                        memory_profile.c.written_at,
                    )
                    .where(memory_profile.c.workspace_id == ext.store.workspace_id)
                    .order_by(memory_profile.c.written_at.desc(), memory_profile.c.member_id)
                    .limit(PROFILE_LIST_MAX)
                )
            ).all()
        return object_page(tuple(_profile_row(row) for row in rows), query)

    async def _entry(
        self, ext: ExtensionContext, subjects: frozenset[str], name: str
    ) -> MemberObject[ProfileSpec] | None:
        if SHARED_SUBJECT not in subjects:
            return None
        try:
            member_id = UUID(name)
        except ValueError:
            return None
        async with ext.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        memory_profile.c.member_id,
                        memory_profile.c.role,
                        memory_profile.c.focus,
                        memory_profile.c.written_at,
                    ).where(
                        memory_profile.c.workspace_id == ext.store.workspace_id,
                        memory_profile.c.member_id == member_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        written = _aware(row.written_at)
        return MemberObject(
            row=_profile_row(row),
            detail=ObjectDetail(
                spec=ProfileSpec(role=row.role, focus=row.focus),
                created_at=written,
                updated_at=written,
            ),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ProfileSpec,
        old: ProfileSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(PROFILE_WRITE_REFUSAL)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(PROFILE_WRITE_REFUSAL)


def _profile_row(row: sa.Row) -> ObjectRow:
    return ObjectRow(
        name=str(row.member_id),
        summary=clip_to_word(f"{row.role} — {row.focus}", SUMMARY_MAX),
        fields={
            "member_id": str(row.member_id),
            "role": row.role,
            "focus": row.focus,
            "written": _stamp(row.written_at),
        },
    )


PROFILE_OBJECT = ObjectKind(
    name=PROFILE_KIND,
    description=(
        "What the workspace knows about one of its members: the role they hold and the work they "
        "are carrying now, written nightly from the workspace's shared facts. Read-only."
    ),
    guidance=(
        "The People band's rows, named by member id — the same name the `member` kind's rows "
        "carry, so a profile and a roster row join on it. Each carries member_id, role (a short "
        "phrase), focus (one sentence on what that person is carrying now) and `written`, the "
        "moment the entry was recorded; listing orders on `written`, newest first. An entry exists "
        "only where the workspace's shared facts said something about that person, so a member "
        "with no entry is one nothing shared has been recorded about. Reads follow the "
        "conversation audience and never widen for an admin. Apply and delete are refused — the "
        "People pass is the write path, and what it can say is what memory_update and the synced "
        "sources put in shared memory."
    ),
    spec_model=ProfileSpec,
    store=ProfileObjects(),
    list_fields=frozenset({"member_id", "role", "focus", "written"}),
)
