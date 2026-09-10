"""Every page-derived memory row carries its digest — RFC 0047 change 4, second half, the data
steps.

`body_digest` stays nullable: a row without one is a paragraph or summary a wiki writer produced,
outside the key-based dedup, and every reader dedups by body. The serving image is `memory_0023`'s
code, which never touches `memory_source` and never deletes or rebinds a legacy row. The release
before it wrote links with every derivation, and under it dropping `memory_source` would break every
derivation for the whole roll, so this revision refuses to run while the newest `memory_source` link
is later than the newest digest-bearing `memory_item` row — that release wrote after this code last
did: deploy 80147652e, let it roll, then deploy this. An empty database passes.

The first statement is `LOCK TABLE memory_item IN EXCLUSIVE MODE` (a no-op on SQLite, whose single
writer is the lock): live pods queue their writes behind the data steps instead of racing them,
reads continue, and because the transaction holds nothing before the lock and carries no DDL, no
cycle can form — the DDL is `memory_0025`, a revision and a transaction of its own.

A legacy row — page-derived, `body_digest NULL` — is adopted in place where its own slot
`(workspace, subject, item_class, digest, page)` holds no row: it keeps its id, so the chunks the
index holds under that id stay its own and nothing is embedded again. Each of its links to another
page whose slot holds no row becomes a page-local copy carrying `retired_at`, `as_of`,
`created_at`, the link's revision and source, and no embedding digest; the copy insert does nothing
on a slot already held, so a rerun after a Job died halfway is clean. A legacy row whose own slot a
fresh row already holds is deleted; its chunks stay in the index as orphans until a prune — recall
never serves them, since `_enrich` reads rows by id, and they duplicate the fresh row's chunks word
for word — and their count is bounded by the pages derived again during one roll, an accepted
residual. Then `memory_0022`'s `unindexed_pages_drain` marker insert runs again for the mirror rows
the release before wrote during its roll in a workspace whose walk had already ended.
"""

import hashlib
from datetime import UTC, datetime
from itertools import batched

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.sdk.ids import uuid7

revision: str = "memory_0024"
down_revision: str | None = "memory_0023"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

memory_item = sa.table(
    "memory_item",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("subject", sa.Text()),
    sa.column("body", sa.Text()),
    sa.column("body_digest", sa.Text()),
    sa.column("item_class", sa.Text()),
    sa.column("memory_kind", sa.Text()),
    sa.column("confidence", sa.Integer()),
    sa.column("source_ref", sa.Text()),
    sa.column("created_from_page_uid", sa.Uuid()),
    sa.column("created_from_page_revision", sa.BigInteger()),
    sa.column("source_uid", sa.Uuid()),
    sa.column("as_of", sa.DateTime(timezone=True)),
    sa.column("superseded_by", sa.Uuid()),
    sa.column("retired_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
memory_source = sa.table(
    "memory_source",
    sa.column("memory_item_id", sa.Uuid()),
    sa.column("source_uid", sa.Uuid()),
    sa.column("page_uid", sa.Uuid()),
    sa.column("revision", sa.BigInteger()),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
mem_page = sa.table(
    "mem_page", sa.column("workspace_id", sa.Uuid()), sa.column("page_uid", sa.Uuid())
)
page = sa.table(
    "page",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("uid", sa.Uuid()),
    sa.column("indexed", sa.Boolean()),
)
COPIED = (
    "workspace_id",
    "subject",
    "body",
    "item_class",
    "memory_kind",
    "confidence",
    "source_ref",
    "as_of",
    "superseded_by",
    "retired_at",
    "created_at",
    "updated_at",
)
BATCH = 1000
EXTENSION = "memory"
MARKER_KEY = "unindexed_pages_drain"
LOCK = "lock table memory_item in exclusive mode"
ORDER = (
    "the serving image still writes memory_source links: deploy 80147652e, let it roll, then "
    "deploy this release"
)
LEGACY = sa.and_(
    memory_item.c.created_from_page_uid.is_not(None), memory_item.c.body_digest.is_(None)
)


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _refuse_under_the_previous_release(connection: sa.Connection) -> None:
    latest_link = connection.execute(sa.select(sa.func.max(memory_source.c.updated_at))).scalar()
    latest_digested = connection.execute(
        sa.select(sa.func.max(memory_item.c.created_at)).where(
            memory_item.c.body_digest.is_not(None)
        )
    ).scalar()
    if latest_link is not None and (latest_digested is None or latest_link > latest_digested):
        raise RuntimeError(ORDER)


def _slot_holder(page_uid: sa.ColumnElement[object]) -> sa.ColumnElement[bool]:
    holder = memory_item.alias("holder")
    return (
        sa.select(sa.literal(1))
        .select_from(holder)
        .where(
            holder.c.id != memory_item.c.id,
            holder.c.workspace_id == memory_item.c.workspace_id,
            holder.c.subject == memory_item.c.subject,
            holder.c.item_class == memory_item.c.item_class,
            holder.c.body == memory_item.c.body,
            holder.c.created_from_page_uid == page_uid,
            holder.c.body_digest.is_not(None),
        )
        .exists()
    )


def _copy_foreign_links(connection: sa.Connection) -> None:
    links = (
        connection.execute(
            sa.select(
                memory_source.c.page_uid,
                memory_source.c.revision,
                memory_source.c.source_uid,
                *(memory_item.c[name] for name in COPIED),
            )
            .join(memory_item, memory_item.c.id == memory_source.c.memory_item_id)
            .where(
                LEGACY,
                memory_source.c.page_uid != memory_item.c.created_from_page_uid,
                ~_slot_holder(memory_source.c.page_uid),
            )
            .order_by(memory_source.c.memory_item_id, memory_source.c.page_uid)
        )
        .mappings()
        .all()
    )
    copies = [
        {
            "id": uuid7(),
            "body_digest": _digest(link["body"]),
            "created_from_page_uid": link["page_uid"],
            "created_from_page_revision": link["revision"],
            "source_uid": link["source_uid"],
            **{name: link[name] for name in COPIED},
        }
        for link in links
    ]
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    statement = insert(memory_item).on_conflict_do_nothing(
        index_elements=[
            "workspace_id",
            "subject",
            "item_class",
            "body_digest",
            "created_from_page_uid",
        ],
        index_where=memory_item.c.created_from_page_uid.is_not(None),
    )
    for batch in batched(copies, BATCH):
        connection.execute(statement, list(batch))


def _adopt_legacy_rows(connection: sa.Connection) -> None:
    adoptable = sa.and_(LEGACY, ~_slot_holder(memory_item.c.created_from_page_uid))
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.update(memory_item)
            .where(adoptable)
            .values(body_digest=sa.text("encode(sha256(convert_to(body, 'UTF8')), 'hex')"))
        )
        return
    for row in connection.execute(
        sa.select(memory_item.c.id, memory_item.c.body).where(adoptable)
    ).all():
        connection.execute(
            sa.update(memory_item)
            .where(memory_item.c.id == row.id)
            .values(body_digest=_digest(row.body))
        )


def _mark_unindexed_workspaces(connection: sa.Connection) -> None:
    marked = (
        sa.select(mem_page.c.workspace_id)
        .join(
            page,
            sa.and_(
                page.c.workspace_id == mem_page.c.workspace_id,
                page.c.uid == mem_page.c.page_uid,
            ),
        )
        .where(page.c.indexed.is_(False))
        .distinct()
    )
    workspace_ids = connection.execute(marked).scalars().all()
    if not workspace_ids:
        return
    now = datetime.now(UTC)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    connection.execute(
        insert(ext_store).on_conflict_do_nothing(
            index_elements=["workspace_id", "extension", "key"]
        ),
        [
            {
                "workspace_id": workspace_id,
                "extension": EXTENSION,
                "key": MARKER_KEY,
                "value": {"after": None},
                "created_at": now,
                "updated_at": now,
            }
            for workspace_id in workspace_ids
        ],
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(LOCK))
    _refuse_under_the_previous_release(connection)
    _copy_foreign_links(connection)
    _adopt_legacy_rows(connection)
    connection.execute(sa.delete(memory_item).where(LEGACY))
    _mark_unindexed_workspaces(connection)


def downgrade() -> None:
    pass
