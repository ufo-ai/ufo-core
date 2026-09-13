"""Scheduled task and pause connection scope. SQL NULL authorizes no connection."""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

from ufo.sdk.context import CONNECTION_SCOPE_MAX

revision: str = "scheduled_tasks_0002"
down_revision: str | None = "scheduled_tasks_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0085"

task = sa.table(
    "scheduled_task",
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
pause = sa.table("pause", sa.column("connections", sa.JSON(none_as_null=True)))


def upgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.add_column(sa.Column("connections", sa.JSON(none_as_null=True), nullable=True))
    with op.batch_alter_table("pause") as batch:
        batch.add_column(sa.Column("connections", sa.JSON(none_as_null=True), nullable=True))

    joined = task.outerjoin(
        grant,
        sa.and_(
            grant.c.workspace_id == task.c.workspace_id,
            grant.c.agent_id == task.c.agent_id,
        ),
    ).outerjoin(
        connection,
        sa.and_(
            connection.c.workspace_id == task.c.workspace_id,
            connection.c.id == grant.c.connection_id,
            sa.or_(
                connection.c.shared.is_(True),
                connection.c.owner_member_id == task.c.created_by_member_id,
            ),
        ),
    )
    scopes: dict[UUID, list[str]] = {}
    for row in op.get_bind().execute(
        sa.select(task.c.id.label("task_id"), connection.c.id.label("connection_id"))
        .select_from(joined)
        .order_by(task.c.id, connection.c.id)
    ):
        scoped = scopes.setdefault(row.task_id, [])
        if row.connection_id is not None and len(scoped) < CONNECTION_SCOPE_MAX:
            scoped.append(str(row.connection_id))
    if scopes:
        op.get_bind().execute(
            sa.update(task)
            .where(task.c.id == sa.bindparam("task_id"))
            .values(connections=sa.bindparam("connection_scope")),
            [
                {"task_id": task_id, "connection_scope": connection_scope}
                for task_id, connection_scope in scopes.items()
            ],
        )
    op.get_bind().execute(sa.update(pause).values(connections=[]))


def downgrade() -> None:
    with op.batch_alter_table("pause") as batch:
        batch.drop_column("connections")
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_column("connections")
