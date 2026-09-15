"""`turn.fired_by_provider`: the connector provider whose feed fired a turn, beside the object that
fired it — the brand a member's view draws over the words. Admission writes it from here on; a
scheduled fire and every turn already landed carry none.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260914223548"
down_revision: str | None = "20260914122142"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("fired_by_provider", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "fired_by_provider")
