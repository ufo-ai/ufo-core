import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from ufo_testsupport.plugin import drop_postgres_database, reset_postgres_database

from ufo.db import MIGRATIONS_DIR, workspace_tx
from ufo.runtime.billing.accounting import UNGATED_LEDGER
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.accounting import (
    GIB_MONTHS_DIMENSION,
    MEMORY_SERVICE,
    PAGES_DIMENSION,
    SEARCHES_DIMENSION,
    SOURCES_SERVICE,
    WRITES_DIMENSION,
)

KNOWLEDGE_UNITS = "20261008071529"
LEDGER_CHECKS = ("ledger_dimension", "ledger_service_dimension", "ledger_byok_dimension")
SERVICE_INSERT = sa.text(
    "insert into ledger (id, workspace_id, service, dimension, amount, priced_micro_usd, model, "
    "created_at, updated_at) values (:id, :workspace_id, :service, :dimension, 1, 0, '', :now, "
    ":now)"
).bindparams(
    sa.bindparam("id", type_=sa.Uuid()),
    sa.bindparam("workspace_id", type_=sa.Uuid()),
    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _record(resource_id: str) -> dict[str, object]:
    return {
        "token_id": None,
        "session_id": None,
        "labels": {},
        "resource_id": resource_id,
        "attempt": "flush-1",
        "occurred_at": datetime.now(UTC),
        "byok": False,
        "price_micro_usd": 7,
        "price_digest": "sha256:card",
    }


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_memory_and_sources_units_book_through_record_usage() -> None:
    workspace_id = await _workspace()
    context = context_for("core", frozenset(), ledger=UNGATED_LEDGER)
    units = (
        (MEMORY_SERVICE, WRITES_DIMENSION, "ufo"),
        (MEMORY_SERVICE, SEARCHES_DIMENSION, "ufo"),
        (SOURCES_SERVICE, PAGES_DIMENSION, None),
        (SOURCES_SERVICE, GIB_MONTHS_DIMENSION, None),
    )
    with ws(workspace_id):
        written = [
            await context.record_usage(service, unit, backend, 3, **_record(f"{service}-{unit}"))
            for service, unit, backend in units
        ]
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.service, tables.ledger.c.dimension, tables.ledger.c.backend
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    assert written == [True] * 4
    assert {tuple(row) for row in rows} == set(units)


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_a_unit_of_another_service_is_refused() -> None:
    workspace_id = await _workspace()
    context = context_for("core", frozenset(), ledger=UNGATED_LEDGER)
    refused = pytest.raises(ValueError, match=r"^The memory service meters no pages\.$")
    with ws(workspace_id), refused:
        await context.record_usage(MEMORY_SERVICE, PAGES_DIMENSION, "ufo", 3, **_record("refused"))
    async with workspace_tx() as connection:
        booked = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert booked == 0
    with pytest.raises(sa.exc.IntegrityError, match="ledger_service_dimension"):
        async with workspace_tx() as connection:
            await connection.execute(
                SERVICE_INSERT,
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "service": MEMORY_SERVICE,
                    "dimension": PAGES_DIMENSION,
                    "now": datetime.now(UTC),
                },
            )


@pytest.fixture
def scratch_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "ledger.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    name = f"{source.database}_knowledge_units_{uuid4().hex[:8]}"
    asyncio.run(reset_postgres_database(name))
    try:
        yield (
            source.set(database=name).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=name).render_as_string(
                hide_password=False
            ),
        )
    finally:
        asyncio.run(drop_postgres_database(name))


def _ledger_catalog(connection: sa.Connection) -> dict[str, str]:
    checks = {
        check["name"]: check["sqltext"]
        for check in sa.inspect(connection).get_check_constraints("ledger")
        if check["name"] in LEDGER_CHECKS
    }
    if connection.dialect.name == "postgresql":
        return checks
    return checks | dict(
        connection.execute(
            sa.text(
                "select name, sql from sqlite_master "
                "where tbl_name = 'ledger' and type in ('index', 'trigger') and sql is not null"
            )
        ).all()
    )


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
def test_the_revision_round_trips(scratch_urls: tuple[str, str]) -> None:
    migration_url, sync_url = scratch_urls
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    parent = ScriptDirectory.from_config(config).get_revision(KNOWLEDGE_UNITS).down_revision
    assert isinstance(parent, str)
    command.upgrade(config, parent)
    gib_id, workspace_id, now = uuid4(), uuid4(), datetime.now(UTC)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            SERVICE_INSERT,
            {
                "id": gib_id,
                "workspace_id": workspace_id,
                "service": "proxy",
                "dimension": "gib",
                "now": now,
            },
        )
        connection.commit()
        before = _ledger_catalog(connection)
    engine.dispose()
    command.upgrade(config, KNOWLEDGE_UNITS)
    with engine.connect() as connection:
        upgraded = _ledger_catalog(connection)
        kept = connection.execute(
            sa.select(tables.ledger.c.service, tables.ledger.c.dimension).where(
                tables.ledger.c.id == gib_id
            )
        ).one()
        knowledge_id = uuid4()
        connection.execute(
            SERVICE_INSERT,
            {
                "id": knowledge_id,
                "workspace_id": workspace_id,
                "service": SOURCES_SERVICE,
                "dimension": GIB_MONTHS_DIMENSION,
                "now": now,
            },
        )
        connection.execute(sa.delete(tables.ledger).where(tables.ledger.c.id == knowledge_id))
        connection.commit()
    engine.dispose()
    command.downgrade(config, parent)
    with engine.connect() as connection:
        downgraded = _ledger_catalog(connection)
        with pytest.raises(sa.exc.IntegrityError, match="ledger_"):
            connection.execute(
                SERVICE_INSERT,
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "service": MEMORY_SERVICE,
                    "dimension": WRITES_DIMENSION,
                    "now": now,
                },
            )
    engine.dispose()
    assert tuple(kept) == ("proxy", "gib")
    assert upgraded != before
    assert upgraded.keys() == before.keys()
    assert all(
        f"'{unit}'" in "".join(upgraded.values())
        for unit in (WRITES_DIMENSION, SEARCHES_DIMENSION, PAGES_DIMENSION, GIB_MONTHS_DIMENSION)
    )
    assert downgraded == before
