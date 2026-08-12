"""index member email for fleet sign-in"""

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("member_email", "member", ["email"])


def downgrade() -> None:
    op.drop_index("member_email", table_name="member")
