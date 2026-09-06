import json
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from ufo_ext_web.surface import HOMEPAGE_TOOLS

from ufo.db import MIGRATIONS_DIR
from ufo.schema import tables

BEFORE = "20260903020306"
REVISION = "20260903234430"
RETIRED = ("action:site:build_ufo_application", "action:site:design_ufo_application")
REPLACEMENT = (
    "spawn",
    "load_skill",
    "share_file",
    "action:site:deploy_website",
    "action:agent:set_homepage",
)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(database_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id = uuid4()
    with engine.connect() as connection:
        connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for name, tools in (
            ("builder", ["bash", *RETIRED]),
            # An allowlist that already held one of the replacements: 20260828010853 writes
            # `deploy_website` beside the builder, so a downgrade removing every replacement takes
            # this agent's own grant with it and nothing records that it was its own.
            ("builder-with-deploy", ["bash", *RETIRED, "action:site:deploy_website"]),
            ("deployer", ["bash", "action:site:deploy_website"]),
            ("plain", ["bash", "read"]),
            ("open", None),
        ):
            connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="auto",
                    is_main=False,
                    tools=tools,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for extension, key, value in (
            ("web", "homepage-seed/one", "settled"),
            ("web", "chat/title", "kept"),
            ("sites", "application-builder/qa-proof/turn", "held"),
            ("sites", "application-wireframe/support-desk", "held"),
            ("sites", "main-homepage-released", "kept"),
        ):
            connection.execute(
                sa.insert(tables.ext_store).values(
                    workspace_id=workspace_id,
                    extension=extension,
                    key=key,
                    value=value,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        connection.commit()
    return engine


def _tools(engine: sa.Engine) -> dict[str, list[str] | None]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select name, tools from agent order by name"))
        return {row.name: None if row.tools is None else json.loads(row.tools) for row in rows}


def _keys(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(tables.ext_store.c.extension, tables.ext_store.c.key).order_by(
                tables.ext_store.c.extension, tables.ext_store.c.key
            )
        )
        return [tuple(row) for row in rows]


def test_a_builder_allowlist_gains_the_actions_that_replace_it(tmp_path: Path) -> None:
    """An allowlist naming only the torn-out actions would withhold the homepage build the
    workspace is owed. What replaces them is what the sweep admits an agent on and what its prompt
    then asks for: an allowlist is the whole naming, so a migrated agent given `deploy_website`
    alone would be admitted and hold no way to spawn the builder, and its three attempts would
    settle it `unbuilt` for ever.

    The old names stay. The image this release replaces dispatches create-application through them
    while the migrate Job has already run, so taking them here refuses the one create path it
    holds."""

    database_path = tmp_path / "drop-application-builder.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, REVISION)

    assert _tools(engine) == {
        "builder": ["bash", *RETIRED, *REPLACEMENT],
        "builder-with-deploy": [
            "bash",
            *RETIRED,
            "action:site:deploy_website",
            *(name for name in REPLACEMENT if name != "action:site:deploy_website"),
        ],
        "deployer": ["bash", "action:site:deploy_website"],
        "open": None,
        "plain": ["bash", "read"],
    }


def test_every_store_key_the_outgoing_image_still_reads_stays(tmp_path: Path) -> None:
    """The migrate Job runs to completion while only the outgoing image serves.
    `application-wireframe/<name>` is written in a design turn and read in a later build turn, so
    dropping it here would set that image building a design the member never approved; dropping
    `homepage-seed/` would hand it a fleet of unmarked agents to seed again. A key is dropped in a
    later revision than the one that stops reading it."""

    database_path = tmp_path / "drop-application-builder-rows.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)
    before = _keys(engine)

    command.upgrade(config, REVISION)

    assert _keys(engine) == before


def test_a_migrated_allowlist_holds_everything_the_sweep_and_its_prompt_ask_for(
    tmp_path: Path,
) -> None:
    """The two ends of one seed: the sweep admits an agent on `HOMEPAGE_TOOLS`, and the turn it
    fires acts with exactly the allowlist this migration wrote."""

    database_path = tmp_path / "drop-application-builder-admits.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, REVISION)

    migrated = _tools(engine)["builder"]
    assert migrated is not None
    assert set(HOMEPAGE_TOOLS) <= set(migrated)


def test_a_rollback_takes_no_grant_the_allowlist_already_held(tmp_path: Path) -> None:
    """The upgrade adds only the names a row lacked, so afterwards an agent given
    `deploy_website` here reads the same as one that held it all along. A downgrade that removed
    every replacement would take the second agent's own grant with it, and nothing records that it
    was its own."""

    database_path = tmp_path / "drop-application-builder-down.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)
    before = _tools(engine)

    command.upgrade(config, REVISION)
    command.downgrade(config, BEFORE)

    after = _tools(engine)
    assert set(after) == set(before)
    for name, held in before.items():
        if held is None:
            assert after[name] is None
            continue
        rolled = after[name]
        assert rolled is not None
        assert set(held) <= set(rolled), name
