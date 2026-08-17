"""the provider usage and serving key decision that priced each token row"""

import sqlalchemy as sa
from alembic import op

revision: str = "0101"
down_revision: str | None = "0100"
branch_labels: str | None = None
depends_on: str | None = None


TOKEN_DIMENSIONS = "('tokens', 'sandbox_tokens')"


def upgrade() -> None:
    op.add_column(
        "ledger",
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ledger",
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ledger",
        sa.Column(
            "cache_write_5m_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "ledger",
        sa.Column(
            "cache_write_30m_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "ledger",
        sa.Column(
            "cache_write_1h_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("ledger", sa.Column("byok", sa.Boolean, nullable=True))
    op.add_column(
        "ledger",
        sa.Column("token_classes_complete", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            f"update ledger set input_tokens = prompt_tokens - cache_read_tokens, "
            f"output_tokens = amount - prompt_tokens where dimension in {TOKEN_DIMENSIONS}"
        )
    )
    op.execute(
        sa.text(
            "update ledger set byok = (select byok from turn "
            "where turn.id = ledger.turn_id) where dimension = 'tokens' "
            "and turn_id is not null "
            "and (select byok from turn where turn.id = ledger.turn_id) is not null"
        )
    )
    with op.batch_alter_table("ledger") as batch:
        batch.create_check_constraint(
            "ledger_token_classes_nonnegative",
            "input_tokens >= 0 and output_tokens >= 0 and cache_read_tokens >= 0 "
            "and cache_write_5m_tokens >= 0 and cache_write_30m_tokens >= 0 "
            "and cache_write_1h_tokens >= 0",
        )
        batch.create_check_constraint(
            "ledger_token_classes_total",
            f"dimension not in {TOKEN_DIMENSIONS} or not token_classes_complete or "
            "amount = input_tokens + output_tokens "
            "+ cache_read_tokens + cache_write_5m_tokens + cache_write_30m_tokens "
            "+ cache_write_1h_tokens",
        )
        batch.create_check_constraint(
            "ledger_prompt_classes_total",
            f"dimension not in {TOKEN_DIMENSIONS} or not token_classes_complete or "
            "prompt_tokens = input_tokens "
            "+ cache_read_tokens + cache_write_5m_tokens + cache_write_30m_tokens "
            "+ cache_write_1h_tokens",
        )
        batch.create_check_constraint("ledger_byok_dimension", "not byok or dimension = 'tokens'")


def downgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.drop_constraint("ledger_byok_dimension", type_="check")
        batch.drop_constraint("ledger_prompt_classes_total", type_="check")
        batch.drop_constraint("ledger_token_classes_total", type_="check")
        batch.drop_constraint("ledger_token_classes_nonnegative", type_="check")
    op.drop_column("ledger", "byok")
    op.drop_column("ledger", "token_classes_complete")
    op.drop_column("ledger", "cache_write_1h_tokens")
    op.drop_column("ledger", "cache_write_30m_tokens")
    op.drop_column("ledger", "cache_write_5m_tokens")
    op.drop_column("ledger", "output_tokens")
    op.drop_column("ledger", "input_tokens")
