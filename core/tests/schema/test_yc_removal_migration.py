"""The yc tear-out takes its rows with it: nothing else ever would.

The extension owned no tables, so 0072's whole job is the durable state it leaves behind — the
pending device authorization keyed under `yc_cli`, the workspace's shared YC credential, and the
sources it registered. A retired source keeps its row (its pages carry the foreign key) but is
marked removed with its claim cleared so the sync driver never picks it up again, its grants are
gone, and its live pages are tombstoned at a fresh page revision so the page-change consumers reap
the derived index state. Every neighbouring row — another extension's cursor, another slot's
credential, another backend's source and pages — must survive untouched."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.runtime.turns.subjects import SHARED_SUBJECT

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(path: Path) -> tuple[Config, dict[str, UUID]]:
    """A workspace as it stands the moment before 0072: a connected YC identity with its pending
    device authorization, a YC source holding an indexed page and a grant, and a folder source
    alongside it that has nothing to do with YC."""
    config = _config(path)
    command.upgrade(config, "0071")
    ids = {
        "workspace": uuid4(),
        "agent": uuid4(),
        "yc_source": uuid4(),
        "other_source": uuid4(),
        "yc_page": uuid4(),
        "other_page": uuid4(),
    }
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": ids["workspace"].hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "internet_access_allowed, created_at, updated_at) "
                "values (:id, :ws, 'ufo', 'p', 'm', true, true, :now, :now)"
            ),
            {"id": ids["agent"].hex, "ws": ids["workspace"].hex, "now": NOW},
        )
        for extension, key in (("yc_cli", "device_authorization"), ("memory", "page_cursor")):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:ws, :extension, :key, '{}', :now, :now)"
                ),
                {
                    "ws": ids["workspace"].hex,
                    "extension": extension,
                    "key": key,
                    "now": NOW,
                },
            )
        for slot in ("yc_cli_credentials", "perplexity_api_key"):
            connection.execute(
                sa.text(
                    "insert into credential "
                    "(workspace_id, slot, ciphertext, created_at, updated_at) "
                    "values (:ws, :slot, :secret, :now, :now)"
                ),
                {
                    "ws": ids["workspace"].hex,
                    "slot": slot,
                    "secret": b"sealed",
                    "now": NOW,
                },
            )
        for source, backend in ((ids["yc_source"], "yc"), (ids["other_source"], "folder")):
            connection.execute(
                sa.text(
                    "insert into source (id, workspace_id, backend, config, subject, "
                    "owner_member_id, cursor, claimed_by, claim_expires_at, next_sync_at, "
                    "consecutive_errors, removed_at, created_at, updated_at) "
                    "values (:id, :ws, :backend, '{}', :subject, null, 'c', 'runner-1', :now, "
                    ":now, 0, null, :now, :now)"
                ),
                {
                    "id": source.hex,
                    "ws": ids["workspace"].hex,
                    "backend": backend,
                    "subject": SHARED_SUBJECT,
                    "now": NOW,
                },
            )
            connection.execute(
                sa.text(
                    "insert into source_grant "
                    "(workspace_id, source_id, agent_id, created_at, updated_at) "
                    "values (:ws, :source, :agent, :now, :now)"
                ),
                {
                    "ws": ids["workspace"].hex,
                    "source": source.hex,
                    "agent": ids["agent"].hex,
                    "now": NOW,
                },
            )
        for page, source in (
            (ids["yc_page"], ids["yc_source"]),
            (ids["other_page"], ids["other_source"]),
        ):
            connection.execute(
                sa.text(
                    "insert into page (id, workspace_id, source_id, digest, body_ref, stream, "
                    "title, subject, tombstone, created_at, updated_at) "
                    "values (:id, :ws, :source, 'd', 'b', 's', 't', :subject, false, :now, :now)"
                ),
                {
                    "id": page.hex,
                    "ws": ids["workspace"].hex,
                    "source": source.hex,
                    "subject": SHARED_SUBJECT,
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()
    return config, ids


def test_removing_yc_takes_its_rows_and_leaves_every_neighbour(tmp_path: Path) -> None:
    config, ids = _seed(tmp_path / "yc-removal.db")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'yc-removal.db'}")
    with engine.connect() as connection:
        before = connection.execute(
            sa.text("select id, revision from page order by revision")
        ).all()
    revision_before = {UUID(row.id): row.revision for row in before}

    command.upgrade(config, "0072")

    with engine.connect() as connection:
        assert connection.execute(sa.text("select extension, key from ext_store")).all() == [
            ("memory", "page_cursor")
        ]
        assert connection.execute(sa.text("select slot from credential")).scalars().all() == [
            "perplexity_api_key"
        ]
        sources = {
            UUID(row.id): row
            for row in connection.execute(
                sa.text("select id, backend, removed_at, claimed_by, claim_expires_at from source")
            ).all()
        }
        grants = connection.execute(sa.text("select source_id from source_grant")).scalars().all()
        pages = {
            UUID(row.id): row
            for row in connection.execute(sa.text("select id, tombstone, revision from page")).all()
        }
    engine.dispose()

    yc_source = sources[ids["yc_source"]]
    assert yc_source.removed_at is not None
    assert yc_source.claimed_by is None and yc_source.claim_expires_at is None
    other_source = sources[ids["other_source"]]
    assert other_source.removed_at is None
    assert other_source.claimed_by == "runner-1"
    assert [UUID(source_id) for source_id in grants] == [ids["other_source"]]
    assert pages[ids["yc_page"]].tombstone
    assert pages[ids["yc_page"]].revision > revision_before[ids["yc_page"]]
    assert not pages[ids["other_page"]].tombstone
    assert pages[ids["other_page"]].revision == revision_before[ids["other_page"]]
