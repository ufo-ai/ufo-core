"""turn parent index"""

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "turn_parent",
        "turn",
        ["parent_turn_id"],
        postgresql_where=sa.text("parent_turn_id is not null"),
        sqlite_where=sa.text("parent_turn_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("turn_parent", table_name="turn")
