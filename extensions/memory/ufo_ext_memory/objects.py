"""The `memory` object kind: stored memory items readable by durable id.

`memory_search` finds and returns refs; this kind is what a ref opens — the item's body and recall
inputs beside its provenance links: `created_from` names the synced page a derivation distilled it
from, `superseded_by` the item consolidation replaced it with. Search excludes superseded items,
so the link is the recovery path when an old id arrives through a stale reference; `get` resolves
any visible row while `list` shows only live ones. Reads are scoped to the caller's own subjects —
shared memory plus, in a member conversation, that member's private space. `memory_update` stays
the write path, and there is no delete: index chunks are derived by jobs and no cleanup path
exists for one item's chunks."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.objects import (
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
from ufo.sdk.tools import ToolContext
from ufo_ext_memory.store import memory_item, recall_subjects

MEMORY_KIND = "memory"
PAGE_OBJECT_KIND = "page"
MEMORY_UPDATE_REFUSAL = "memories are recorded through memory_update, never applied"
MEMORY_UNDELETABLE = (
    "memories cannot be deleted — consolidation supersedes them and recall drops superseded items"
)
SUMMARY_MAX = 120
MEMORY_LIST_MAX = 500


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(description="The stored memory text.")
    subject: str = Field(description="Visibility subject: 'shared' or 'member:<id>'.")
    item_class: str = Field(description="fact, episodic, or semantic.")
    memory_kind: str = Field(
        description="Recency-decay kind: fact, preference, decision, event, or task."
    )
    confidence: int = Field(description="Confidence 1-10; scales a fact's decayed recall rank.")
    source_ref: str | None = Field(
        description="Free-form note on what produced the memory, when the writer gave one."
    )
    as_of: str | None = Field(description="When the source information was current, ISO-8601.")


def _require_ext(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError("memory objects dispatched without their ExtensionContext")
    return ctx.ext


@dataclass(frozen=True)
class MemoryObjects:
    """Read-only handlers over the extension's own `memory_item` rows under the caller's subjects:
    list shows live (non-superseded) items newest first, get resolves any visible row — including a
    superseded one, whose `superseded_by` link names its replacement. Both mutations refuse."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        ext = _require_ext(ctx)
        subjects = recall_subjects(ctx.audience)
        async with ext.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.body,
                            memory_item.c.subject,
                            memory_item.c.item_class,
                            memory_item.c.memory_kind,
                            memory_item.c.created_from_page_id,
                            memory_item.c.created_from_page_revision,
                        )
                        .where(
                            memory_item.c.workspace_id == ext.store.workspace_id,
                            memory_item.c.subject.in_(subjects),
                            memory_item.c.superseded_by.is_(None),
                        )
                        .order_by(memory_item.c.created_at.desc(), memory_item.c.id)
                        .limit(MEMORY_LIST_MAX)
                    )
                )
                .mappings()
                .all()
            )
        page_ids = tuple(
            row["created_from_page_id"] for row in rows if row["created_from_page_id"] is not None
        )
        current = await ext.page_states(page_ids)
        return object_page(
            tuple(
                ObjectRow(
                    name=str(row["id"]),
                    summary=row["body"][:SUMMARY_MAX],
                    fields={
                        "subject": row["subject"],
                        "item_class": row["item_class"],
                        "memory_kind": row["memory_kind"],
                    },
                )
                for row in rows
                if row["created_from_page_id"] is None
                or (
                    (state := current.get(row["created_from_page_id"])) is not None
                    and state.subject == row["subject"]
                    and state.revision == row["created_from_page_revision"]
                    and state.subject in subjects
                )
            ),
            query,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None:
        try:
            item_id = UUID(name)
        except ValueError:
            return None
        ext = _require_ext(ctx)
        subjects = recall_subjects(ctx.audience)
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
        if row["created_from_page_id"] is not None:
            state = (await ext.page_states((row["created_from_page_id"],))).get(
                row["created_from_page_id"]
            )
            if (
                state is None
                or state.subject != row["subject"]
                or state.revision != row["created_from_page_revision"]
                or state.subject not in subjects
            ):
                return None
        links: list[ObjectLink] = []
        if row["created_from_page_id"] is not None:
            links.append(
                ObjectLink(
                    relation="created_from",
                    target=ObjectRef(kind=PAGE_OBJECT_KIND, name=str(row["created_from_page_id"])),
                )
            )
        if row["superseded_by"] is not None:
            links.append(
                ObjectLink(
                    relation="superseded_by",
                    target=ObjectRef(kind=MEMORY_KIND, name=str(row["superseded_by"])),
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

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None
    ) -> None:
        raise VerbNotSupported(MEMORY_UPDATE_REFUSAL)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        raise VerbNotSupported(MEMORY_UNDELETABLE)


MEMORY_OBJECT = ObjectKind(
    name=MEMORY_KIND,
    description=(
        "A stored memory item, readable by the id a memory_search ref carries. Read-only: "
        "memory_update records memories, consolidation supersedes them."
    ),
    guidance=(
        "Open a memory_search ref here to read the full item: its body, recall inputs, and "
        "links — `created_from` names the synced page the item was distilled from (object_get "
        "it for the source document), and `superseded_by` names the item that replaced it. "
        "Search excludes superseded items, so reaching one through an old reference means "
        "follow `superseded_by` to the current statement before relying on it. Listing shows "
        "live items newest first (filter subject, item_class, or memory_kind); search, not "
        "listing, is how memory is recalled. Reads cover shared memory plus the conversation "
        "member's own. Apply and delete are refused — memory_update is the write path, and "
        "consolidation is how an item ends."
    ),
    spec_model=MemorySpec,
    store=MemoryObjects(),
    list_fields=frozenset({"subject", "item_class", "memory_kind"}),
)
