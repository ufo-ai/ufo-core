"""A quickbooks source that names no company is retired; one that names its own is left alone.

QBO addresses one company file per request and no broker holds that company id, so a row without the
whole address can never run — registration refuses one now, and the rows written before it are what
this migration takes. A retired source keeps its row (its pages carry the foreign key) but is marked
removed with its claim cleared so the sync driver never picks it up again, its grants are gone, and
its live pages are tombstoned at a fresh page revision so the page-change consumers reap the derived
index state. The row that pins its company, and every neighbouring backend's row, must survive
untouched."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.subjects import SHARED_SUBJECT

NOW = datetime(2026, 8, 21, tzinfo=UTC)
REVISION = "20260821155315"
DOWN_REVISION = "20260820095839"
COMPANY_BASE_URL = "https://quickbooks.api.intuit.com/v3/company/9130347596"


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def _seed(path: Path) -> tuple[Config, dict[str, UUID]]:
    """Three sources as they stand the moment before the migration: a quickbooks row auto-registered
    with no address, a quickbooks row the member registered against its company, and a folder row
    that has nothing to do with either. Each holds a grant and an indexed page."""
    config = _config(path)
    command.upgrade(config, DOWN_REVISION)
    ids = {
        "workspace": uuid4(),
        "agent": uuid4(),
        "companyless": uuid4(),
        "pinned": uuid4(),
        "other": uuid4(),
    }
    rows = (
        (ids["companyless"], "quickbooks", {"account": "ca_1", "stream": "invoices"}),
        (
            ids["pinned"],
            "quickbooks",
            {"account": "ca_1", "stream": "customers", "base_url": COMPANY_BASE_URL},
        ),
        (ids["other"], "folder", {"path": "/feeds"}),
    )
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
        for source, backend, config_json in rows:
            connection.execute(
                sa.text(
                    "insert into source (id, workspace_id, backend, config, subject, "
                    "owner_member_id, cursor, claimed_by, claim_expires_at, next_sync_at, "
                    "consecutive_errors, removed_at, created_at, updated_at) "
                    "values (:id, :ws, :backend, :config, :subject, null, 'c', 'runner-1', :now, "
                    ":now, 0, null, :now, :now)"
                ),
                {
                    "id": source.hex,
                    "ws": ids["workspace"].hex,
                    "backend": backend,
                    "config": json.dumps(config_json),
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
            connection.execute(
                sa.text(
                    "insert into page (id, workspace_id, source_id, digest, body_ref, stream, "
                    "title, subject, tombstone, created_at, updated_at) "
                    "values (:id, :ws, :source, 'd', 'b', 's', 't', :subject, false, :now, :now)"
                ),
                {
                    "id": source.hex,
                    "ws": ids["workspace"].hex,
                    "source": source.hex,
                    "subject": SHARED_SUBJECT,
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()
    return config, ids


def test_a_quickbooks_source_naming_no_company_is_retired(tmp_path: Path) -> None:
    database = tmp_path / "quickbooks-company.db"
    config, ids = _seed(database)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        revision_before = {
            UUID(row.id): row.revision
            for row in connection.execute(sa.text("select id, revision from page")).all()
        }

    command.upgrade(config, REVISION)

    with engine.connect() as connection:
        sources = {
            UUID(row.id): row
            for row in connection.execute(
                sa.text("select id, removed_at, claimed_by, claim_expires_at from source")
            ).all()
        }
        grants = {
            UUID(source_id)
            for source_id in connection.execute(
                sa.text("select source_id from source_grant")
            ).scalars()
        }
        pages = {
            UUID(row.id): row
            for row in connection.execute(sa.text("select id, tombstone, revision from page")).all()
        }
    engine.dispose()

    retired = sources[ids["companyless"]]
    assert retired.removed_at is not None
    assert retired.claimed_by is None and retired.claim_expires_at is None
    assert ids["companyless"] not in grants
    assert pages[ids["companyless"]].tombstone
    assert pages[ids["companyless"]].revision > revision_before[ids["companyless"]]

    for kept in (ids["pinned"], ids["other"]):
        assert sources[kept].removed_at is None
        assert sources[kept].claimed_by == "runner-1"
        assert kept in grants
        assert not pages[kept].tombstone
        assert pages[kept].revision == revision_before[kept]
