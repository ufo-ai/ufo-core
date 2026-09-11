"""the streams join the trigger key

The outgoing image names the four-column key as the conflict target of every trigger it writes, so
a trigger apply on it is refused for the length of the roll. Every read selects columns that image
still finds, so conversations keep waking.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0005"
down_revision: str | None = "sources_0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

KEY = "source_trigger_conversation"
COLUMNS = ("workspace_id", "conversation_id", "connection_id", "resource")


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(sa.Column("streams", sa.Text(), nullable=False, server_default=""))
        batch.drop_constraint(KEY, type_="unique")
        batch.create_unique_constraint(KEY, [*COLUMNS, "streams"])


def downgrade() -> None:
    """A narrowed trigger has no shape here, and two rows the wide key told apart would collide."""
    trigger = sa.table("source_trigger", sa.column("streams", sa.Text()))
    op.execute(sa.delete(trigger).where(trigger.c.streams != ""))
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_constraint(KEY, type_="unique")
        batch.create_unique_constraint(KEY, list(COLUMNS))
        batch.drop_column("streams")
