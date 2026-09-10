"""One fact from one page is one row — RFC 0047 change 4.

`body_digest` arrives nullable and no existing row is backfilled: through the roll the outgoing
image still writes `INSERT … ON CONFLICT (id) DO UPDATE SET created_from_page_uid = excluded.…` in
`commit` and `UPDATE memory_item SET created_from_page_uid = <surviving link>` in
`supersede_page_facts`, and either would move a digest-bearing row into a slot the new image or a
copy already holds under `memory_item_page_body`; NULLs are distinct under the index, so a row the
outgoing image may rebind can never collide. Every item linked to a page other than its own has
every one of its links, its own page's included, copied into a page-local row carrying the digest
and no embedding digest, so the memory indexer embeds them. A single-link item takes no copy: the
new image's next derivation of its page inserts the fresh row beside it. No legacy row is deleted
in this release — the new image's `supersede_page_facts` deletes digest-bearing rows only, because
the outgoing image keeps rebinding legacy rows and minting links while the fleet rolls, and every
read serves a legacy row and its fresh twin as one statement. The downgrade gives every linkless
page-derived row one link from its own binding, so the restored image can retire the copies. The
follow-up, once no outgoing image remains and every link is final, copies each legacy row's links
whose slot holds no row, `retired_at` included, deletes the legacy rows, backfills the remaining
written rows' digests with a dedup step, makes the column NOT NULL, drops `memory_source`, and
re-runs `memory_0022`'s `unindexed_pages_drain` marker insert: a mirror row the outgoing image
wrote during that release's roll, in a workspace whose walk had already ended, is otherwise never
drained.
"""

import hashlib
from datetime import UTC, datetime
from itertools import batched

import sqlalchemy as sa
from alembic import op

from ufo.sdk.ids import uuid7

revision: str = "memory_0023"
down_revision: str | None = "memory_0022"
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
    sa.column("workspace_id", sa.Uuid()),
    sa.column("memory_item_id", sa.Uuid()),
    sa.column("source_uid", sa.Uuid()),
    sa.column("page_uid", sa.Uuid()),
    sa.column("revision", sa.BigInteger()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
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
INDEXES = (
    (
        "memory_item_page_body",
        ("workspace_id", "subject", "item_class", "body_digest", "created_from_page_uid"),
        "created_from_page_uid is not null",
    ),
    (
        "memory_item_written_body",
        ("workspace_id", "subject", "item_class", "body_digest"),
        "created_from_page_uid is null",
    ),
)


def upgrade() -> None:
    connection = op.get_bind()
    op.add_column("memory_item", sa.Column("body_digest", sa.Text(), nullable=True))
    foreign = memory_source.alias("foreign")
    multi_linked = (
        sa.select(sa.literal(1))
        .where(
            foreign.c.memory_item_id == memory_item.c.id,
            sa.or_(
                memory_item.c.created_from_page_uid.is_(None),
                foreign.c.page_uid != memory_item.c.created_from_page_uid,
            ),
        )
        .exists()
    )
    existing = memory_item.alias("existing")
    already_copied = (
        sa.select(sa.literal(1))
        .select_from(existing)
        .where(
            existing.c.id != memory_item.c.id,
            existing.c.workspace_id == memory_item.c.workspace_id,
            existing.c.subject == memory_item.c.subject,
            existing.c.item_class == memory_item.c.item_class,
            existing.c.body == memory_item.c.body,
            existing.c.created_from_page_uid == memory_source.c.page_uid,
        )
        .exists()
    )
    links = (
        connection.execute(
            sa.select(
                memory_source.c.page_uid,
                memory_source.c.revision,
                memory_source.c.source_uid,
                *(memory_item.c[name] for name in COPIED),
            )
            .join(memory_item, memory_item.c.id == memory_source.c.memory_item_id)
            .where(multi_linked, ~already_copied)
            .order_by(memory_source.c.memory_item_id, memory_source.c.page_uid)
        )
        .mappings()
        .all()
    )
    copies = [
        {
            "id": uuid7(),
            "body_digest": hashlib.sha256(link["body"].encode()).hexdigest(),
            "created_from_page_uid": link["page_uid"],
            "created_from_page_revision": link["revision"],
            "source_uid": link["source_uid"],
            **{name: link[name] for name in COPIED},
        }
        for link in links
    ]
    for batch in batched(copies, BATCH):
        connection.execute(sa.insert(memory_item), list(batch))
    for name, key, where in INDEXES:
        op.create_index(
            name,
            "memory_item",
            list(key),
            unique=True,
            postgresql_where=sa.text(where),
            sqlite_where=sa.text(where),
        )


def downgrade() -> None:
    connection = op.get_bind()
    linked = (
        sa.select(sa.literal(1)).where(memory_source.c.memory_item_id == memory_item.c.id).exists()
    )
    linkless = connection.execute(
        sa.select(
            memory_item.c.workspace_id,
            memory_item.c.id,
            memory_item.c.source_uid,
            memory_item.c.created_from_page_uid,
            memory_item.c.created_from_page_revision,
        )
        .where(memory_item.c.created_from_page_uid.is_not(None), ~linked)
        .order_by(memory_item.c.id)
    ).all()
    now = datetime.now(UTC)
    links = [
        {
            "workspace_id": row.workspace_id,
            "memory_item_id": row.id,
            "source_uid": row.source_uid,
            "page_uid": row.created_from_page_uid,
            "revision": row.created_from_page_revision,
            "created_at": now,
            "updated_at": now,
        }
        for row in linkless
    ]
    for batch in batched(links, BATCH):
        connection.execute(sa.insert(memory_source), list(batch))
    for name, _key, _where in INDEXES:
        op.drop_index(name, table_name="memory_item")
    with op.batch_alter_table("memory_item") as batch_op:
        batch_op.drop_column("body_digest")
