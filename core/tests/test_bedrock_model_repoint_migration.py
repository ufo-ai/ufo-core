import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
import ufo_ext_bedrock as bedrock
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

MIGRATION = MIGRATIONS_DIR / "versions" / "0064_repoint_dropped_bedrock_models.py"
DROPPED = ("anthropic.claude-opus-4-6-v1", "anthropic.claude-sonnet-4-6")
UNTOUCHED = (
    "anthropic.claude-opus-5",
    "anthropic.claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "auto",
)


def _replacements() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("migration_0064", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replacements: dict[str, str] = module.SERVED_REPLACEMENTS
    return replacements


def test_0064_maps_the_dropped_ids_onto_served_bedrock_specs() -> None:
    """The repoint rests on each target being a served Bedrock spec: a target core serves instead
    would resolve through a different key, stranding a Bedrock-only deploy at `client_for` and
    flipping a BYOK workspace onto the platform rate card. The key set is stated here because
    `0064:12-13` is the only place repo-wide that names the dropped ids."""
    served = {spec.id: spec for spec in bedrock.manifest().models}

    assert set(_replacements()) == set(DROPPED)
    assert set(_replacements().values()) <= set(served)
    assert {served[target].key_slot for target in _replacements().values()} == {
        bedrock.BEDROCK_KEY_SLOT
    }


def test_0064_repoints_only_the_dropped_bedrock_ids(tmp_path: Path) -> None:
    """A stored id no registry serves raises at turn setup, before intent dispatch, so the agent
    loses the portal's model-change form too. 0064 repoints each dropped id to its served
    same-provider successor and must not touch core's same-numbered direct-Anthropic slugs, which
    are distinct ids still served."""
    replacements = _replacements()
    database_path = tmp_path / "bedrock-repoint.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "0063")

    workspace_id = uuid4()
    moment = datetime(2026, 7, 30, tzinfo=UTC)
    sync_url = f"sqlite:///{database_path}"
    seeded = (*replacements, *UNTOUCHED)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": workspace_id.hex, "moment": moment},
        )
        for index, model in enumerate(seeded):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, created_at, updated_at) "
                    "values (:id, :workspace, :name, 'you are helpful', :model, :moment, :moment)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id.hex,
                    "name": f"agent-{index}",
                    "model": model,
                    "moment": moment,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "0064")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        models = [
            row[0] for row in connection.execute(sa.text("select model from agent order by name"))
        ]
    engine.dispose()

    assert models == [*replacements.values(), *UNTOUCHED]
