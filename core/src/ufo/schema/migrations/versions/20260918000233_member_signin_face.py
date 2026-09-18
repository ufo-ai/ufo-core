"""member sign-in face: the identity provider that verified a member's address may name them, say
what they go by, and say where their picture is.

`given_name` is written in the same act as `display_name` and governed by its source;
`signin_photo_url` is the address the seat records and a job fetches. Both source checks admit
`signin` beside the member, Slack, and gravatar. The outgoing image writes only the three it knows,
which the widened checks still admit, and never reads the new columns.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260918000233"
down_revision: str | None = "20260917214959"
branch_labels: str | None = None
depends_on: str | None = None

SOURCES = "('member', 'signin', 'slack', 'gravatar')"
PRIOR_SOURCES = "('member', 'slack', 'gravatar')"


def upgrade() -> None:
    op.add_column("member", sa.Column("given_name", sa.Text(), nullable=True))
    op.add_column("member", sa.Column("signin_photo_url", sa.Text(), nullable=True))
    _admit(SOURCES)


def downgrade() -> None:
    _admit(PRIOR_SOURCES)
    op.drop_column("member", "signin_photo_url")
    op.drop_column("member", "given_name")


def _admit(sources: str) -> None:
    with op.batch_alter_table("member") as batch:
        batch.drop_constraint("member_display_name_source", type_="check")
        batch.drop_constraint("member_photo_source", type_="check")
        batch.create_check_constraint(
            "member_display_name_source",
            f"display_name_source is null or display_name_source in {sources}",
        )
        batch.create_check_constraint(
            "member_photo_source", f"photo_source is null or photo_source in {sources}"
        )
