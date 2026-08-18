"""repoint agents at the id anthropic serves fable 5 under"""

import sqlalchemy as sa
from alembic import op

revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | None = None
depends_on: str | None = None

FABLE_SERVED_ID_UPDATE = sa.text(
    "update agent set model = 'claude-5-fable-20260609' where model = 'claude-fable-5'"
)


def upgrade() -> None:
    op.execute(FABLE_SERVED_ID_UPDATE)


def downgrade() -> None:
    pass
