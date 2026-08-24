import sqlalchemy as sa
from alembic import op

revision: str = "20260823202415"
down_revision: str | None = "20260820015537"
branch_labels: str | None = None
depends_on: str | None = None

ARCHIVED_NAME_STATE = (
    "(archived_at is null and archived_name is null) "
    "or (archived_at is not null and archived_name is not null)"
)


def upgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.add_column(sa.Column("archived_name", sa.Text, nullable=True))
    bind = op.get_bind()
    archived = bind.execute(
        sa.text("select id, name from agent where archived_at is not null")
    ).all()
    for row in archived:
        bind.execute(
            sa.text(
                "update agent set name = :internal_name, archived_name = :archived_name "
                "where id = :id"
            ),
            {
                "id": row.id,
                "internal_name": f"~archived-{row.id}",
                "archived_name": row.name,
            },
        )
    with op.batch_alter_table("agent") as batch:
        batch.create_check_constraint("agent_archived_name_state", ARCHIVED_NAME_STATE)


def downgrade() -> None:
    pass
