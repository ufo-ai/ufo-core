"""Monitor runtime scope. SQL NULL connections authorize no connection."""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "monitors_0003"
down_revision: str | None = "monitors_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0085"

CONNECTIONS_MAX = 50
POSTGRES_FUNCTION = "monitor_internet_scope"
POSTGRES_TRIGGER = "monitor_internet_scope"

monitor = sa.table(
    "monitor",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("created_by_member_id", sa.Uuid()),
    sa.column("connections", sa.JSON(none_as_null=True)),
)
grant = sa.table(
    "connector_grant",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("agent_id", sa.Uuid()),
    sa.column("connection_id", sa.Uuid()),
)
connection = sa.table(
    "connection",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("owner_member_id", sa.Uuid()),
    sa.column("shared", sa.Boolean()),
)


def upgrade() -> None:
    with op.batch_alter_table("monitor") as batch:
        batch.add_column(sa.Column("connections", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("internet_access", sa.Boolean(), nullable=True))

    op.execute(sa.text("UPDATE monitor SET internet_access = true"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION {POSTGRES_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.internet_access IS NULL THEN
                    SELECT COALESCE(
                        bool_and(
                            COALESCE((runtime_config ->> 'internet_access')::boolean, true)
                        ),
                        false
                    )
                    INTO NEW.internet_access
                    FROM turn
                    WHERE workspace_id = NEW.workspace_id
                      AND conversation_id = NEW.conversation_id
                      AND status = 'running';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {POSTGRES_TRIGGER}
            BEFORE INSERT ON monitor
            FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()
            """
        )
    with op.batch_alter_table("monitor") as batch:
        batch.alter_column("internet_access", nullable=False)

    joined = monitor.outerjoin(
        grant,
        sa.and_(
            grant.c.workspace_id == monitor.c.workspace_id,
            grant.c.agent_id == monitor.c.agent_id,
        ),
    ).outerjoin(
        connection,
        sa.and_(
            connection.c.workspace_id == monitor.c.workspace_id,
            connection.c.id == grant.c.connection_id,
            sa.or_(
                connection.c.shared.is_(True),
                connection.c.owner_member_id == monitor.c.created_by_member_id,
            ),
        ),
    )
    scopes: dict[UUID, list[str]] = {}
    for row in op.get_bind().execute(
        sa.select(monitor.c.id.label("monitor_id"), connection.c.id.label("connection_id"))
        .select_from(joined)
        .order_by(monitor.c.id, connection.c.id)
    ):
        scoped = scopes.setdefault(row.monitor_id, [])
        if row.connection_id is not None and len(scoped) < CONNECTIONS_MAX:
            scoped.append(str(row.connection_id))
    if scopes:
        op.get_bind().execute(
            sa.update(monitor)
            .where(monitor.c.id == sa.bindparam("monitor_id"))
            .values(connections=sa.bindparam("connection_scope")),
            [
                {"monitor_id": monitor_id, "connection_scope": connection_scope}
                for monitor_id, connection_scope in scopes.items()
            ],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER {POSTGRES_TRIGGER} ON monitor")
        op.execute(f"DROP FUNCTION {POSTGRES_FUNCTION}()")
    with op.batch_alter_table("monitor") as batch:
        batch.drop_column("internet_access")
        batch.drop_column("connections")
