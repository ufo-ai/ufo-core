"""`turn.fired_by_*`: what admitted a turn on its own — the kind, name, and title of the scheduled
task or source trigger whose fire it is — the columns the `run` kind lists work from.

Admission writes them from here on. The scheduled fires already landed are backfilled off their
admission key, which names the task they fired for; a trigger's key names no trigger, so its runs
list from the next fire. The index is partial over fired turns, so it holds the runs and not the
member turns around them.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260910194926"
down_revision: str | None = "20260910101300"
branch_labels: str | None = None
depends_on: str | None = None

SCHEDULED_TASK_KIND = "scheduled_task"
FIRED_INDEX = "turn_fired"

turn = sa.table(
    "turn",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("admission_source", sa.Text()),
    sa.column("idempotency_key", sa.Text()),
    sa.column("fired_by_kind", sa.Text()),
    sa.column("fired_by_name", sa.Text()),
    sa.column("fired_by_title", sa.Text()),
)
scheduled_task = sa.table(
    "scheduled_task",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("name", sa.Text()),
)


def upgrade() -> None:
    op.add_column("turn", sa.Column("fired_by_kind", sa.Text(), nullable=True))
    op.add_column("turn", sa.Column("fired_by_name", sa.Text(), nullable=True))
    op.add_column("turn", sa.Column("fired_by_title", sa.Text(), nullable=True))
    bind = op.get_bind()
    tasks = bind.execute(
        sa.select(scheduled_task.c.id, scheduled_task.c.workspace_id, scheduled_task.c.name)
    ).all()
    for task in tasks:
        bind.execute(
            sa.update(turn)
            .where(
                turn.c.workspace_id == task.workspace_id,
                turn.c.admission_source == "scheduled",
                turn.c.idempotency_key.startswith(f"{task.id}:"),
            )
            .values(
                fired_by_kind=SCHEDULED_TASK_KIND,
                fired_by_name=task.name,
                fired_by_title=task.name,
            )
        )
    columns = ["workspace_id", "agent_id", "created_at"]
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                FIRED_INDEX,
                "turn",
                columns,
                postgresql_where=sa.text("fired_by_kind is not null"),
                postgresql_concurrently=True,
            )
    else:
        op.create_index(
            FIRED_INDEX, "turn", columns, sqlite_where=sa.text("fired_by_kind is not null")
        )


def downgrade() -> None:
    op.drop_index(FIRED_INDEX, table_name="turn")
    op.drop_column("turn", "fired_by_title")
    op.drop_column("turn", "fired_by_name")
    op.drop_column("turn", "fired_by_kind")
