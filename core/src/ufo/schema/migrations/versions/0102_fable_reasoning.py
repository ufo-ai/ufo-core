"""fable requires reasoning"""

import sqlalchemy as sa
from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | None = None
depends_on: str | None = None
FABLE_MODELS = ("claude-fable-5", "anthropic.claude-fable-5")
FABLE_REASONING_UPDATE = sa.text(
    "update agent set reasoning = 'low' "
    "where model in ('claude-fable-5', 'anthropic.claude-fable-5') "
    "and reasoning = 'off'"
)


def upgrade() -> None:
    op.execute(FABLE_REASONING_UPDATE)


def downgrade() -> None:
    pass
