"""the element pack's own mark is the icon a new agent row defaults to"""

import sqlalchemy as sa
from alembic import op

revision: str = "0112"
down_revision: str | None = "0111"
branch_labels: str | None = None
depends_on: str | None = None

ELEMENT_DEFAULT = "propylon"
TABLER_DEFAULT = "robot"


def _default(icon: str) -> None:
    with op.batch_alter_table("agent") as batch:
        batch.alter_column(
            "icon",
            existing_type=sa.Text(),
            existing_nullable=False,
            server_default=sa.text(f"'{icon}'"),
        )


def upgrade() -> None:
    """Only the default moves. Every existing row keeps the slug it holds: the portal still draws a
    tabler mark for any name outside its own pack, so a row carrying an old slug still draws, and
    re-stamping rows is a separate operational step run when it is wanted (see
    `scripts/agent_icon_element_pack.sql`)."""
    _default(ELEMENT_DEFAULT)


def downgrade() -> None:
    _default(TABLER_DEFAULT)
