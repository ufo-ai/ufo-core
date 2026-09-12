"""`turn_fired_by`: the index the last-fire lookup walks.

`last_fires` asks for the newest turn of each named object of one kind, over the whole workspace
and never one agent, so `turn_fired` — `workspace_id, agent_id, created_at` — leaves it scanning
every fired turn the workspace holds. This one leads on the two columns that lookup filters and
groups by, and carries `created_at` so the group's maximum is read off the index.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260912030644"
down_revision: str | None = "20260911124440"
branch_labels: str | None = None
depends_on: str | None = None

FIRED_BY_INDEX = "turn_fired_by"
COLUMNS = ["workspace_id", "fired_by_kind", "fired_by_name", "created_at"]
WHERE = "fired_by_kind is not null"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                FIRED_BY_INDEX,
                "turn",
                COLUMNS,
                postgresql_where=sa.text(WHERE),
                postgresql_concurrently=True,
            )
        return
    op.create_index(FIRED_BY_INDEX, "turn", COLUMNS, sqlite_where=sa.text(WHERE))


def downgrade() -> None:
    op.drop_index(FIRED_BY_INDEX, table_name="turn")
