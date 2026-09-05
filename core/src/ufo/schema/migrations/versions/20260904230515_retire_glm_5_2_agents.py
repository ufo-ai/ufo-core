"""glm-5.2 is no longer served"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260904230515"
down_revision: str | None = "20260903020306"
branch_labels: str | None = None
depends_on: str | None = None
RETIRED_MODEL_UPDATE = sa.text(
    "update agent set model = 'z-ai/glm-5.3', "
    "reasoning = case when reasoning = 'off' then 'low' else reasoning end "
    "where model = 'z-ai/glm-5.2'"
)
"""glm-5.2 could disable reasoning and glm-5.3 cannot, so a row carried across with `off` intact
would be a pair `_known_model` refuses — every later apply of that agent raises, including the one
a member would make to repair it. `off` becomes the replacement's own declared minimum, exactly as
0103 did for fable."""


def upgrade() -> None:
    op.execute(RETIRED_MODEL_UPDATE)


def downgrade() -> None:
    pass
