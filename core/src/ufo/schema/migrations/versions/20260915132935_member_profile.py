"""member profile: the name a member is drawn under and the picture beside it.

The name and the photo each carry the source that wrote them, so a prefill job fills what a member
has not set and never overwrites what they have. Every stored picture is re-encoded to one square
WebP, so the bytes need no media type beside them: they live in the workspace blob store under the
member's own key, and `photo_digest` is the ETag the portal's read route serves and the address a
replaced picture gets.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260915132935"
down_revision: str | None = "20260915181110"
branch_labels: str | None = None
depends_on: str | None = None

SOURCES = "('member', 'slack', 'gravatar')"


def upgrade() -> None:
    op.add_column("member", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("member", sa.Column("display_name_source", sa.Text(), nullable=True))
    op.add_column("member", sa.Column("photo_digest", sa.Text(), nullable=True))
    op.add_column("member", sa.Column("photo_source", sa.Text(), nullable=True))
    with op.batch_alter_table("member") as batch:
        batch.create_check_constraint(
            "member_display_name_source",
            f"display_name_source is null or display_name_source in {SOURCES}",
        )
        batch.create_check_constraint(
            "member_photo_source", f"photo_source is null or photo_source in {SOURCES}"
        )


def downgrade() -> None:
    with op.batch_alter_table("member") as batch:
        batch.drop_constraint("member_photo_source", type_="check")
        batch.drop_constraint("member_display_name_source", type_="check")
    op.drop_column("member", "photo_source")
    op.drop_column("member", "photo_digest")
    op.drop_column("member", "display_name_source")
    op.drop_column("member", "display_name")
