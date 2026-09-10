import re

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from ufo_ext_memory.store import mem_page, memory_item, memory_source

from ufo.db import apply_migrations
from ufo.schema import tables

RFC_0046_TABLES = frozenset({"source", "page", "memory_item", "memory_source", "mem_page"})
PARTITION = re.compile(r".+_p\d{2}")


def _declared() -> sa.MetaData:
    metadata = sa.MetaData()
    for table in (*tables.metadata.tables.values(), memory_item, memory_source, mem_page):
        table.to_metadata(metadata)
    return metadata


def _rfc_0046_names(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    return type_ != "table" or name in RFC_0046_TABLES


def _rfc_0046_objects(
    obj: sa.schema.SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Postgres lists a foreign key onto a partitioned table once per partition beside the key
    itself; those rows are the key's implementation, not a second key."""
    if type_ == "table":
        return name in RFC_0046_TABLES
    if type_ == "foreign_key_constraint" and reflected:
        return PARTITION.fullmatch(obj.referred_table.name) is None
    return True


def test_rfc_0046_tables_are_what_their_metadata_declares(database_url: str) -> None:
    """The migrated `source`, `page` and memory tables carry exactly the columns, keys, indexes and
    defaults `tables.py` and the memory store declare: no content-id column or index outlives
    unit D, and nothing the metadata names is missing. Postgres only — the deploy's dialect, and the
    one alembic compares exactly."""
    if database_url.startswith("sqlite"):
        pytest.skip("compare_metadata is exact on Postgres, the deploy's dialect")
    apply_migrations(database_url)
    engine = sa.create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_name": _rfc_0046_names,
                    "include_object": _rfc_0046_objects,
                },
            )
            differences = compare_metadata(context, _declared())
    finally:
        engine.dispose()
    assert differences == []
