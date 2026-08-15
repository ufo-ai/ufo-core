"""shared files the hosted registry could not name carry their real media type

`artifact_media_type` asked `mimetypes` at share time, and the hosted image carries no mime
registry, so every office document, patch, and diff landed as `application/octet-stream` — filed
under Other, with no inline rendering. The derivation now answers those suffixes from its own
table; this re-derives the rows already written. Only the fallback rows move: a row whose type was
guessed is already right, and the suffix names exactly what the new table would say.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | None = None
depends_on: str | None = None

FALLBACK_MEDIA_TYPE = "application/octet-stream"
ARTIFACT_MEDIA_TYPES = {
    ".diff": "text/x-patch",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".patch": "text/x-patch",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


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
