"""carry each agent's own statement of what it is for

One sentence a member reads before anything else on an app's page. It is nullable because every
row this workspace already holds was made without one, and the extension that shipped a row fills
its own on the next provisioning pass; an agent a member built states one when they say what it is
for.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260825023542"
down_revision: str | None = "20260824141446"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("agent", sa.Column("purpose", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent", "purpose")
