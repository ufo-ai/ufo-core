"""`page.parent_fields`: the record fields a page's children template their request paths from.

A stream's partitions are its parent stream's landed records, so a child composes its path out of
fields the parent page carries. Null is the absence of a projection — a page nothing asks anything
of, which is most of them — and is not a record a fan-out reads; a projection that answered none of
the fields asked of it is an empty object, and raises where it is read.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260912192000"
down_revision: str | None = "20260912030644"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("page", sa.Column("parent_fields", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("page", "parent_fields")
