"""`member_invited_at`: the index the per-minute invitation sweep's candidate read rides.

`recently_invited_workspaces` names the workspaces holding an invitation inside its window, under
the one RLS-bypass read and so across the whole fleet. Without this the predicate scans every member
row of every workspace each minute, on the database that serves every turn. Partial on the column
being not null, because an invitation is rare and a seat that was never invited never matches.
"""

from alembic import op

revision: str = "20260916035845"
down_revision: str | None = "20260915132935"
branch_labels: str | None = None
depends_on: str | None = None

INDEX = "member_invited_at"


def upgrade() -> None:
    op.create_index(INDEX, "member", ["invited_at"], postgresql_where="invited_at is not null")


def downgrade() -> None:
    op.drop_index(INDEX, table_name="member")
