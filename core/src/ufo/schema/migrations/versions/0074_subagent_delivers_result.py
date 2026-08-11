"""record whether a delegated child owes its parent a result, and whether that result arrived

A spawn either awaits its child inside the calling turn or it does not, and only the caller knows
which — so the choice is written onto the child at admission. One tri-state column answers that and
what became of it: null is a turn that hands nothing back (not a delegated child, or one awaited
inline), `pending` is a delegated child still owing its parent, `delivered` is one whose arrival is
posted. A `delivered_at` beside a boolean would state the same fact twice, and a row reading
`awaited, delivered a moment ago` is representable in that pair with no reader able to say which
end is wrong.

The partial index carries exactly the outstanding rows, so the sweep that finds what a child's own
execution could not deliver probes a handful of rows rather than walking every turn the deploy has
ever run.

Existing rows are awaited children by construction — every spawn before this either blocked on its
terminal or was collected by a wait — so the column lands null.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | None = "0072"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("result_delivery", sa.Text(), nullable=True))
    with op.batch_alter_table("turn") as batch:
        batch.create_check_constraint(
            "turn_result_delivery", "result_delivery in ('pending', 'delivered')"
        )
    op.create_index(
        "turn_result_pending",
        "turn",
        ["workspace_id"],
        postgresql_where=sa.text("result_delivery = 'pending'"),
        sqlite_where=sa.text("result_delivery = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("turn_result_pending", "turn")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_result_delivery", type_="check")
        batch.drop_column("result_delivery")
