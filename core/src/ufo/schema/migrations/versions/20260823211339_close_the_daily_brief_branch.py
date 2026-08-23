"""close the daily brief's revision branch

Sweep's revisions stay in this directory because deployed databases stamped `sweep_0002`, and
alembic resolves every stamped head from the script directory before it runs any DDL — a head whose
files left the tree stops the migrate Job the deploy waits on. Merging that branch into core's line
is what retires it.

The two tables it left stand until a later revision. This revision meets the image it replaces: the
migrate Job completes before the fleet rolls, so the outgoing pods still carry the sweep extension,
whose `pre_tool_use` hook reads `sweep_application` on every scheduled turn that touches
`update_todo_list`, `memory_update`, or `set_homepage`. `pre_tool_use` gates, so a read against a
dropped table would fail closed to a Deny and refuse those turns until the last old pod went away.
"""

revision: str = "20260823211339"
down_revision: tuple[str, str] = ("20260823021954", "sweep_0002")
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """The merge is the whole change: the graph loses a head and the schema keeps its shape."""


def downgrade() -> None:
    """Splitting the branch again would strand the head this revision exists to retire."""
