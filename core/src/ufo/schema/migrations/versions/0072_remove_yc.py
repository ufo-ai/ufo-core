"""drop the removed yc extension's durable rows

The extension owns no tables of its own, so its rows are all it leaves: the pending device
authorization in `ext_store`, the shared YC identity in `credential`, and the guidance and directory
sources it registered. Retiring a source stamps `removed_at` and leaves the row as its pages'
referent, deletes its grants, and tombstones its live pages so the page-change consumers reap the
derived index state — the shape the source table carried at this revision.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | None = None
depends_on: str | None = None

EXTENSION = "yc_cli"
CREDENTIAL_SLOT = "yc_cli_credentials"
SOURCE_BACKEND = "yc"


def upgrade() -> None:
    ext_store = sa.table(
        "ext_store",
        sa.column("extension", sa.Text()),
    )
    credential = sa.table(
        "credential",
        sa.column("slot", sa.Text()),
    )
    source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("backend", sa.Text()),
        sa.column("claimed_by", sa.Text()),
        sa.column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.column("removed_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    source_grant = sa.table(
        "source_grant",
        sa.column("source_id", sa.Uuid()),
    )
    page = sa.table(
        "page",
        sa.column("source_id", sa.Uuid()),
        sa.column("tombstone", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    connection = op.get_bind()
    connection.execute(sa.delete(ext_store).where(ext_store.c.extension == EXTENSION))
    connection.execute(sa.delete(credential).where(credential.c.slot == CREDENTIAL_SLOT))
    yc_sources = sa.select(source.c.id).where(source.c.backend == SOURCE_BACKEND)
    connection.execute(
        sa.delete(source_grant).where(source_grant.c.source_id.in_(yc_sources.scalar_subquery()))
    )
    connection.execute(
        sa.update(page)
        .where(
            page.c.source_id.in_(yc_sources.scalar_subquery()),
            page.c.tombstone.is_(False),
        )
        .values(tombstone=True, updated_at=now)
    )
    connection.execute(
        sa.update(source)
        .where(source.c.backend == SOURCE_BACKEND, source.c.removed_at.is_(None))
        .values(removed_at=now, claimed_by=None, claim_expires_at=None, updated_at=now)
    )


def downgrade() -> None:
    pass
