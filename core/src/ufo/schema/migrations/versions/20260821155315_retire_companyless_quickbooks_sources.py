"""retire quickbooks sources that name no company

QBO addresses one company file per request, and no broker holds that company id, so the whole
address is the row's to carry: registration now refuses a quickbooks binding without it. A row
written before that carries none and can never run — every sync fails on the address before it
reaches Intuit. Retiring one mirrors `remove_source`: the row stays as its pages' referent, its
grants go, and its live pages are tombstoned so the page-change consumers reap the derived index
state.
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260821155315"
down_revision: str | None = "20260820095839"
branch_labels: str | None = None
depends_on: str | None = None

SOURCE_BACKEND = "quickbooks"


def upgrade() -> None:
    source = sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("backend", sa.Text()),
        sa.column("config", sa.JSON()),
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
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(source.c.id, source.c.config).where(
            source.c.backend == SOURCE_BACKEND, source.c.removed_at.is_(None)
        )
    ).all()
    companyless = [
        row.id
        for row in rows
        if not (json.loads(row.config) if isinstance(row.config, str) else row.config).get(
            "base_url"
        )
    ]
    if not companyless:
        return
    now = datetime.now(UTC)
    connection.execute(sa.delete(source_grant).where(source_grant.c.source_id.in_(companyless)))
    connection.execute(
        sa.update(page)
        .where(page.c.source_id.in_(companyless), page.c.tombstone.is_(False))
        .values(tombstone=True, updated_at=now)
    )
    connection.execute(
        sa.update(source)
        .where(source.c.id.in_(companyless))
        .values(removed_at=now, claimed_by=None, claim_expires_at=None, updated_at=now)
    )


def downgrade() -> None:
    pass
