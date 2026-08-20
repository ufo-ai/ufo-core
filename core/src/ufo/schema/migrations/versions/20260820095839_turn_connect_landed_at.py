"""stamp the turn whose connect request landed

The control a reply leaves for the member settles into the account it made, and what says a request
landed is the request's own turn rather than any account the member happens to hold: two accounts on
one provider, and a reconnect begun in another conversation, are otherwise indistinguishable from
the connect this reply asked for.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260820095839"
down_revision: str | None = "20260820052830"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("connect_landed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "connect_landed_at")
