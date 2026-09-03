"""shared yaml, toml and typescript files carry the text types the page previews

`artifact_media_type` asked `mimetypes` at share time, and the interpreter's own map names no
yaml, toml or typescript while a laptop's registry calls a `.ts` a video stream, so those shares
landed as `application/octet-stream` or `video/mp2t` — an extension label on the shelf, no inline
rendering. The derivation now answers those suffixes from its own table; this re-derives the rows
already written. A row with one of the suffixes moves whatever a registry guessed for it — the
suffix names exactly what the table would say, and a guess was wrong for `.ts` by construction —
unless it already holds a `text/*` type: those bytes are already readable and already filed as a
document, and a retype would take a member context record from the file's characters back to a
placeholder.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260902223926"
down_revision: str | None = "20260901111145"
branch_labels: str | None = None
depends_on: str | None = None

FALLBACK_MEDIA_TYPE = "application/octet-stream"
TEXT_MEDIA_PREFIX = "text/"
ARTIFACT_MEDIA_TYPES = {
    ".toml": "application/toml",
    ".ts": "application/typescript",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def upgrade() -> None:
    shared_artifact = sa.table(
        "shared_artifact", sa.column("filename", sa.Text()), sa.column("media_type", sa.Text())
    )
    for suffix, media_type in ARTIFACT_MEDIA_TYPES.items():
        op.execute(
            shared_artifact.update()
            .where(shared_artifact.c.media_type != media_type)
            .where(
                sa.not_(sa.func.lower(shared_artifact.c.media_type).like(TEXT_MEDIA_PREFIX + "%"))
            )
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
