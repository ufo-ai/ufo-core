import sqlalchemy as sa
from alembic import op

revision: str = "0100"
down_revision: str | None = "0099"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "exa"
CREDENTIAL_SLOT = "exa_api_key"


def upgrade() -> None:
    ext_store = sa.table("ext_store", sa.column("extension", sa.Text()))
    credential = sa.table("credential", sa.column("slot", sa.Text()))
    connection = op.get_bind()
    connection.execute(sa.delete(ext_store).where(ext_store.c.extension == EXTENSION))
    connection.execute(sa.delete(credential).where(credential.c.slot == CREDENTIAL_SLOT))


def downgrade() -> None:
    pass
