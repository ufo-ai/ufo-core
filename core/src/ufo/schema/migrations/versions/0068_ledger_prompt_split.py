"""ledger prompt-versus-cache split alongside the token total"""

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for column in ("prompt_tokens", "cache_read_tokens"):
        op.add_column(
            "ledger",
            sa.Column(column, sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    for column in ("prompt_tokens", "cache_read_tokens"):
        op.drop_column("ledger", column)
