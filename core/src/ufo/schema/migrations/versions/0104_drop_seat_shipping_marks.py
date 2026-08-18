"""drop the day marks the seat shipper left, now that nothing ships seats"""

import sqlalchemy as sa
from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "delete from ext_store where extension = 'metronome' and key = 'seats_shipped_date'"
        )
    )


def downgrade() -> None:
    pass
