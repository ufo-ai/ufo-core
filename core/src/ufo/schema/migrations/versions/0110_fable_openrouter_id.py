"""repoint agents at the openrouter id that serves fable 5"""

import sqlalchemy as sa
from alembic import op

revision: str = "0110"
down_revision: str | None = "0109"
branch_labels: str | None = None
depends_on: str | None = None

SERVED_ID = "anthropic/claude-fable-5"
STRANDED_IDS = ("claude-fable-5", "claude-5-fable-20260609")
DATED_ID = "claude-5-fable-20260609"
FABLE_OPENROUTER_UPDATE = sa.text(
    "update agent set model = :served where model in :stranded"
).bindparams(sa.bindparam("stranded", expanding=True))
FABLE_OPENROUTER_REVERT = sa.text("update agent set model = :dated where model = :served")


def upgrade() -> None:
    op.execute(FABLE_OPENROUTER_UPDATE.bindparams(served=SERVED_ID, stranded=list(STRANDED_IDS)))


def downgrade() -> None:
    """0108 leaves every fable row on the dated id, so that is the state this returns them to — a
    row that cannot run is still the row the prior revision held, and a downgrade that stranded them
    on an id no revision wrote would be worse than the one this undoes."""
    op.execute(FABLE_OPENROUTER_REVERT.bindparams(dated=DATED_ID, served=SERVED_ID))
