"""mem_page workspace_id"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0004"
down_revision: str | None = "memory_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0008"


def upgrade() -> None:
    op.add_column("mem_page", sa.Column("workspace_id", sa.Uuid(), nullable=True))
    # A database built after RFC 0046 unit D has no `page.id` to join on, and no mirror row to fill.
    if "id" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("page")}:
        op.execute(
            "update mem_page set workspace_id = "
            "(select workspace_id from page where page.id = mem_page.page_id)"
        )
    with op.batch_alter_table("mem_page") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "mem_page_workspace_id_fkey",
            "workspace",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("mem_page") as batch:
        batch.drop_constraint("mem_page_workspace_id_fkey", type_="foreignkey")
        batch.drop_column("workspace_id")
