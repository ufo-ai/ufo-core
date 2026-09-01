"""Record a spawn's immutable request identity separately from its mutable state."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260901073109"
down_revision: str | None = "20260901072400"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("spawn_delivers_result", sa.Boolean(), nullable=True))
    op.add_column("turn", sa.Column("spawn_request_fingerprint", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "spawn_request_fingerprint")
    op.drop_column("turn", "spawn_delivers_result")
