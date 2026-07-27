"""order page changes at the database boundary"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | None = None
depends_on: str | None = None


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    page = sa.table(
        "page",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("revision", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    ext_store = sa.table(
        "ext_store",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("extension", sa.Text()),
        sa.column("key", sa.Text()),
        sa.column("value", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return page, ext_store


def _backfill_page_revisions(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            with ranked as (
                select id, row_number() over (
                    partition by workspace_id order by updated_at, id
                ) as revision
                from page
            )
            update page
            set revision = (
                select ranked.revision from ranked where ranked.id = page.id
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            update workspace
            set page_revision = coalesce((
                select max(page.revision)
                from page
                where page.workspace_id = workspace.id
            ), 0)
            """
        )
    )


def _translate_page_change_cursors(connection: sa.Connection) -> None:
    page, ext_store = _tables()
    cursors = (
        connection.execute(
            sa.select(
                ext_store.c.workspace_id,
                ext_store.c.extension,
                ext_store.c.key,
                ext_store.c.value,
            ).where(ext_store.c.key.startswith("page_change_cursor:", autoescape=True))
        )
        .mappings()
        .all()
    )
    for cursor in cursors:
        raw = cursor["value"]
        if not isinstance(raw, str):
            raise ValueError(f"page cursor must be a string, got {raw!r}")
        boundary, separator, raw_page_id = raw.partition("|")
        if not separator:
            raise ValueError(f"invalid page cursor {raw!r}")
        try:
            page_id = UUID(raw_page_id)
            position = datetime.fromisoformat(boundary)
        except ValueError as error:
            raise ValueError(f"invalid page cursor {raw!r}") from error
        translated = (
            connection.execute(
                sa.select(page.c.id, page.c.revision, page.c.updated_at)
                .where(page.c.workspace_id == cursor["workspace_id"])
                .where(
                    sa.or_(
                        page.c.updated_at < position,
                        sa.and_(page.c.updated_at == position, page.c.id <= page_id),
                    )
                )
                .order_by(page.c.updated_at.desc(), page.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        match_cursor = sa.and_(
            ext_store.c.workspace_id == cursor["workspace_id"],
            ext_store.c.extension == cursor["extension"],
            ext_store.c.key == cursor["key"],
        )
        if translated is None:
            connection.execute(sa.delete(ext_store).where(match_cursor))
            continue
        connection.execute(
            sa.update(ext_store)
            .where(match_cursor)
            .values(
                value=f"{translated['revision']}|{translated['id']}",
                updated_at=sa.func.now(),
            )
        )


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("page_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "page",
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    _backfill_page_revisions(op.get_bind())
    _translate_page_change_cursors(op.get_bind())
    op.drop_index("page_feed", table_name="page")
    op.create_index("page_feed", "page", ["workspace_id", "revision", "id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            create function assign_page_revision() returns trigger as $$
            begin
                if tg_op = 'INSERT'
                   or row(new.digest, new.body_ref, new.subject, new.tombstone)
                      is distinct from
                      row(old.digest, old.body_ref, old.subject, old.tombstone)
                then
                    update workspace
                    set page_revision = page_revision + 1
                    where id = new.workspace_id
                    returning page_revision into new.revision;
                end if;
                return new;
            end;
            $$ language plpgsql
            """
        )
        op.execute(
            """
            create trigger page_assign_revision
            before insert or update of digest, body_ref, subject, tombstone on page
            for each row execute function assign_page_revision()
            """
        )
        return
    op.execute(
        """
        create trigger page_assign_revision_insert
        after insert on page
        begin
            update workspace
            set page_revision = page_revision + 1
            where id = new.workspace_id;
            update page
            set revision = (
                select page_revision from workspace where id = new.workspace_id
            )
            where id = new.id;
        end
        """
    )
    op.execute(
        """
        create trigger page_assign_revision_update
        after update of digest, body_ref, subject, tombstone on page
        when new.digest is not old.digest
          or new.body_ref is not old.body_ref
          or new.subject is not old.subject
          or new.tombstone is not old.tombstone
        begin
            update workspace
            set page_revision = page_revision + 1
            where id = new.workspace_id;
            update page
            set revision = (
                select page_revision from workspace where id = new.workspace_id
            )
            where id = new.id;
        end
        """
    )


def downgrade() -> None:
    _, ext_store = _tables()
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.key.startswith("page_change_cursor:", autoescape=True)
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("drop trigger page_assign_revision on page")
        op.execute("drop function assign_page_revision()")
    else:
        op.execute("drop trigger page_assign_revision_update")
        op.execute("drop trigger page_assign_revision_insert")
    op.drop_index("page_feed", table_name="page")
    op.create_index("page_feed", "page", ["workspace_id", "updated_at", "id"])
    op.drop_column("page", "revision")
    op.drop_column("workspace", "page_revision")
