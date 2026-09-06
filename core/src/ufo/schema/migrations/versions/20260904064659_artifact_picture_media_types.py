"""shared webp pictures and matroska clips carry the types the listing draws them by

`artifact_media_type` asked `mimetypes` at share time, and the interpreter's own map names neither
`.webp` nor `.mkv`, so those shares landed as `application/octet-stream` — filed under Other, and
never drawn inline, because the preview link is minted only where the declared type matches the one
the blob key names. The derivation now answers those suffixes from its own table; this re-derives
the rows already written. Only the fallback rows move: a row whose type was guessed came off a host
whose registry names the suffix, and that guess is exactly what the new table says.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260904064659"
down_revision: str | None = "20260903234430"
branch_labels: str | None = None
depends_on: str | None = None

FALLBACK_MEDIA_TYPE = "application/octet-stream"
ARTIFACT_MEDIA_TYPES = {".mkv": "video/x-matroska", ".webp": "image/webp"}


def upgrade() -> None:
    shared_artifact = sa.table(
        "shared_artifact", sa.column("filename", sa.Text()), sa.column("media_type", sa.Text())
    )
    for suffix, media_type in ARTIFACT_MEDIA_TYPES.items():
        op.execute(
            shared_artifact.update()
            .where(shared_artifact.c.media_type == FALLBACK_MEDIA_TYPE)
            .where(sa.func.lower(shared_artifact.c.filename).like(f"%{suffix}"))
            .values(media_type=media_type)
        )


def downgrade() -> None:
    shared_artifact = sa.table(
        "shared_artifact", sa.column("filename", sa.Text()), sa.column("media_type", sa.Text())
    )
    for media_type in set(ARTIFACT_MEDIA_TYPES.values()):
        op.execute(
            shared_artifact.update()
            .where(shared_artifact.c.media_type == media_type)
            .values(media_type=FALLBACK_MEDIA_TYPE)
        )
