from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

MOMENT = datetime(2026, 8, 1, tzinfo=UTC)
FALLBACK = "application/octet-stream"
PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _retyped(
    path: Path, before: str, after: str, shared: tuple[tuple[str, str], ...]
) -> dict[str, str]:
    """The `filename -> media_type` a schema at `before` holds after `shared` rows are written into
    it and it is upgraded to `after`."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, before)

    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    conversation_id, turn_id = uuid4(), uuid4()
    engine = sa.create_engine(f"sqlite:///{path}")
    metadata = sa.MetaData()
    metadata.reflect(
        engine, only=("workspace", "member", "agent", "conversation", "turn", "shared_artifact")
    )

    def stored(value: UUID) -> str:
        return value.hex

    with engine.connect() as connection:
        connection.execute(
            metadata.tables["workspace"]
            .insert()
            .values(id=stored(workspace_id), created_at=MOMENT, updated_at=MOMENT)
        )
        connection.execute(
            metadata.tables["member"]
            .insert()
            .values(
                id=stored(member_id),
                workspace_id=stored(workspace_id),
                email="member@example.com",
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            metadata.tables["agent"]
            .insert()
            .values(
                id=stored(agent_id),
                workspace_id=stored(workspace_id),
                name="assistant",
                prompt="p",
                model="m",
                internet_access_allowed=True,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            metadata.tables["conversation"]
            .insert()
            .values(
                id=stored(conversation_id),
                workspace_id=stored(workspace_id),
                agent_id=stored(agent_id),
                surface="web",
                queue_key="seed",
                audience="shared",
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            metadata.tables["turn"]
            .insert()
            .values(
                id=stored(turn_id),
                workspace_id=stored(workspace_id),
                conversation_id=stored(conversation_id),
                agent_id=stored(agent_id),
                seq=1,
                status="done",
                inbound="seed",
                admission_source="member",
                terminal='{"kind": "reply"}',
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        for filename, media_type in shared:
            connection.execute(
                metadata.tables["shared_artifact"]
                .insert()
                .values(
                    id=stored(uuid4()),
                    turn_id=stored(turn_id),
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=stored(workspace_id),
                    filename=filename,
                    media_type=media_type,
                    size_bytes=3,
                    created_at=MOMENT,
                    updated_at=MOMENT,
                )
            )
        connection.commit()

    command.upgrade(config, after)

    with engine.connect() as connection:
        return dict(
            connection.execute(
                sa.select(
                    metadata.tables["shared_artifact"].c.filename,
                    metadata.tables["shared_artifact"].c.media_type,
                )
            ).all()
        )


def test_media_type_backfill_retypes_only_the_fallback_rows_by_suffix(tmp_path: Path) -> None:
    retyped = _retyped(
        tmp_path / "artifact-media.db",
        "0088",
        "0089",
        (
            ("deck.pptx", FALLBACK),
            ("Cased.PPTX", FALLBACK),
            ("fix.patch", FALLBACK),
            ("unknown.bin", FALLBACK),
            ("guessed.pptx", PRESENTATION),
            ("report.pdf", "application/pdf"),
        ),
    )
    assert retyped == {
        "deck.pptx": PRESENTATION,
        "Cased.PPTX": PRESENTATION,
        "fix.patch": "text/x-patch",
        "unknown.bin": FALLBACK,
        "guessed.pptx": PRESENTATION,
        "report.pdf": "application/pdf",
    }


def test_text_media_type_backfill_retypes_every_row_by_suffix(tmp_path: Path) -> None:
    """A yaml, toml or typescript row moves whatever a registry guessed for it — the fallback the
    hosted image wrote, the video type a laptop's registry gives a `.ts`, another's `x-yaml` — and
    a row whose suffix the table does not name keeps its type, `.tsx` included."""
    retyped = _retyped(
        tmp_path / "artifact-text-media.db",
        "20260901111145",
        "20260902223926",
        (
            ("compose.yaml", FALLBACK),
            ("Cased.YML", FALLBACK),
            ("pyproject.toml", FALLBACK),
            ("app.ts", "video/mp2t"),
            ("types.d.ts", FALLBACK),
            ("laptop.yaml", "application/x-yaml"),
            ("App.tsx", FALLBACK),
            ("unknown.bin", FALLBACK),
            ("notes.txt", "text/plain"),
        ),
    )
    assert retyped == {
        "compose.yaml": "application/yaml",
        "Cased.YML": "application/yaml",
        "pyproject.toml": "application/toml",
        "app.ts": "application/typescript",
        "types.d.ts": "application/typescript",
        "laptop.yaml": "application/yaml",
        "App.tsx": FALLBACK,
        "unknown.bin": FALLBACK,
        "notes.txt": "text/plain",
    }
