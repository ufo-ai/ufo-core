"""memory_item admits the `section` class: the paragraph that opens one band of the wiki.

`memory_0001` fixed the class check at the three classes a row could then be. The section pass
writes a fourth — one live row per `(subject, memory_kind)`, holding what that band of the wiki
amounts to — so the check is restated over four. Widening is what lets the outgoing image's rows
keep landing while the fleet rolls: every class it writes still passes.
"""

from alembic import op

revision: str = "memory_0014"
down_revision: str | None = "memory_0013"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

CLASSES = "item_class in ('fact', 'episodic', 'semantic', 'section')"
PRIOR_CLASSES = "item_class in ('fact', 'episodic', 'semantic')"


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_class", type_="check")
        batch.create_check_constraint("memory_item_class", CLASSES)


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_class", type_="check")
        batch.create_check_constraint("memory_item_class", PRIOR_CLASSES)
