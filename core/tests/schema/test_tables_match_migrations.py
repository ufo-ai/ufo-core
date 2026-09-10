import re

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from ufo_ext_memory.store import mem_page, memory_item

from ufo.db import apply_migrations
from ufo.schema import tables

PARTITION = re.compile(r".+_p\d{2}")


def _declared() -> sa.MetaData:
    metadata = sa.MetaData()
    for table in (*tables.metadata.tables.values(), memory_item, mem_page):
        table.to_metadata(metadata)
    return metadata


DECLARED = frozenset(_declared().tables)


def _declared_names(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    return type_ != "table" or name in DECLARED


def _declared_objects(
    obj: sa.schema.SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Postgres lists a foreign key onto a partitioned table once per partition beside the key
    itself; those rows are the key's implementation, not a second key."""
    if type_ == "table":
        return name in DECLARED
    if type_ == "foreign_key_constraint" and reflected:
        return PARTITION.fullmatch(obj.referred_table.name) is None
    return True


def test_declared_tables_are_what_their_migrations_built(database_url: str) -> None:
    """Every table `tables.py` and the memory store declare carries exactly the columns, keys,
    indexes and defaults the migration chain builds. Postgres only: the deploy's dialect, and the
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
                    "include_name": _declared_names,
                    "include_object": _declared_objects,
                },
            )
            differences = compare_metadata(context, _declared())
    finally:
        engine.dispose()
    assert differences == []
