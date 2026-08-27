"""memory_item admits the `overview` class: the paragraph that opens a whole wiki page.

`memory_0014` restated the class check over four classes. The overview pass writes a fifth — one
live row per subject, holding where the workspace stands — so the check is restated over five.
Widening is what lets the outgoing image's rows keep landing while the fleet rolls: every class it
writes still passes.
"""

from alembic import op

revision: str = "memory_0015"
down_revision: str | None = "memory_0014"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

CLASSES = "item_class in ('fact', 'episodic', 'semantic', 'section', 'overview')"
PRIOR_CLASSES = "item_class in ('fact', 'episodic', 'semantic', 'section')"


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_class", type_="check")
        batch.create_check_constraint("memory_item_class", CLASSES)


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_class", type_="check")
        batch.create_check_constraint("memory_item_class", PRIOR_CLASSES)
