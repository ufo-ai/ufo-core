"""`credential_fulfillment.member_id` stops being written: nothing reads it back.

`CredentialStore.fulfill` was the column's only writer, and the one query against the table selects
`fulfilled_at` alone. The column becomes nullable so a row this release lands can omit it while the
release being replaced still fills it; a later revision drops the column and its composite key onto
`member`. The downgrade refuses while any row carries no member, because the release being replaced
writes the column NOT NULL.

SQLite has no ALTER COLUMN, so the batch rebuilds the table; `copy_from` hands the rebuild the shape
`20260901040105` created.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260910101300"
down_revision: str | None = "20260910114436"
branch_labels: str | None = None
depends_on: str | None = None

CREDENTIAL_FULFILLMENT = sa.Table(
    "credential_fulfillment",
    sa.MetaData(),
    sa.Column("workspace_id", sa.Uuid(), nullable=False),
    sa.Column("request_id", sa.Uuid(), nullable=False),
    sa.Column("slot", sa.Text(), nullable=False),
    sa.Column("member_id", sa.Uuid(), nullable=False),
    sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(
        ["workspace_id", "member_id"],
        ["member.workspace_id", "member.id"],
    ),
    sa.PrimaryKeyConstraint("workspace_id", "request_id", "slot"),
)


def upgrade() -> None:
    with op.batch_alter_table("credential_fulfillment", copy_from=CREDENTIAL_FULFILLMENT) as batch:
        batch.alter_column("member_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    unwritten = op.get_bind().scalar(
        sa.text("select count(*) from credential_fulfillment where member_id is null")
    )
    if unwritten:
        raise RuntimeError(
            f"{unwritten} fulfillments carry no member; the previous release keeps the column "
            "NOT NULL"
        )
    with op.batch_alter_table("credential_fulfillment", copy_from=CREDENTIAL_FULFILLMENT) as batch:
        batch.alter_column("member_id", existing_type=sa.Uuid(), nullable=False)
