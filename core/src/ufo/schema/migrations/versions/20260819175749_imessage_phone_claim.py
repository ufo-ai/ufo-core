import sqlalchemy as sa
from alembic import op

revision: str = "20260819175749"
down_revision: str | None = "0113"
branch_labels: str | None = None
depends_on: str | None = None

IMESSAGE_EXTENSION = "imessage"
OPT_IN_CLAIM_PREFIX = "opt-in-claim:"
OPT_IN_RECEIPT_PREFIX = "opt-in-receipt:"


def upgrade() -> None:
    ext_store = sa.table(
        "ext_store",
        sa.column("extension", sa.Text()),
        sa.column("key", sa.Text()),
    )
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == IMESSAGE_EXTENSION,
            sa.or_(
                ext_store.c.key.startswith(OPT_IN_CLAIM_PREFIX, autoescape=True),
                ext_store.c.key.startswith(OPT_IN_RECEIPT_PREFIX, autoescape=True),
            ),
        )
    )


def downgrade() -> None:
    pass
