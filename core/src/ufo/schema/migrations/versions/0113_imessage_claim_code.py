import sqlalchemy as sa
from alembic import op

revision: str = "0113"
down_revision: str | None = "0112"
branch_labels: str | None = None
depends_on: str | None = None

IMESSAGE_EXTENSION = "imessage"
CLAIM_KEY_PREFIX = "claim:"
CONFIRMATION_REPLY_PREFIX = "confirmation-reply:"


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
                ext_store.c.key.startswith(CLAIM_KEY_PREFIX, autoescape=True),
                ext_store.c.key.startswith(CONFIRMATION_REPLY_PREFIX, autoescape=True),
            ),
        )
    )


def downgrade() -> None:
    pass
