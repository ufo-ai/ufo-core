from alembic import op

revision: str = "20261104025757"
down_revision: str | None = "20261011093000"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("member_first", "member", ["workspace_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("member_first", table_name="member")
