"""repoint agents off bedrock ids mantle does not serve"""

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | None = None
depends_on: str | None = None

SERVED_REPLACEMENTS = {
    "anthropic.claude-opus-4-6-v1": "anthropic.claude-opus-5",
    "anthropic.claude-sonnet-4-6": "anthropic.claude-sonnet-5",
}

AGENT = sa.table("agent", sa.column("model", sa.Text))


def upgrade() -> None:
    for dropped, served in SERVED_REPLACEMENTS.items():
        op.execute(AGENT.update().where(AGENT.c.model == dropped).values(model=served))


def downgrade() -> None:
    pass
