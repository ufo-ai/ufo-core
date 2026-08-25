"""Settle automatic homepage work before an app evaluation starts."""

import argparse
import asyncio

import sqlalchemy as sa
from ufo_ext_sites.application_builder import APPLICATION_BUILDER_DELEGATION_TOOL

from ufo.config import Config, load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.context import ScopedStore
from ufo.schema import tables
from ufo.workspace import ws

WEB_EXTENSION = "web"
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
SETTLED_MARKER = "withheld-eval"
APP_PARENT_TOOLS = (APPLICATION_BUILDER_DELEGATION_TOOL,)


async def prepare_app_eval(config: Config) -> None:
    """Settle homepage work and limit the app-eval parent to delegation."""
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            agents = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.workspace_id,
                        tables.agent.c.id,
                        tables.agent.c.is_main,
                    ).order_by(tables.agent.c.workspace_id, tables.agent.c.id)
                )
            ).all()
            if not agents:
                raise RuntimeError("app eval preparation requires at least one seeded agent")
            main_agents = tuple(agent for agent in agents if agent.is_main)
            if len(main_agents) != 1:
                raise RuntimeError("app eval preparation requires exactly one main agent")
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == main_agents[0].id)
                .values(tools=list(APP_PARENT_TOOLS))
            )
        for agent in agents:
            with ws(agent.workspace_id):
                await ScopedStore(extension=WEB_EXTENSION).put(
                    f"{HOMEPAGE_SEED_PREFIX}{agent.id}", SETTLED_MARKER
                )
    finally:
        await dispose_db()


async def prepare_creation_eval(config: Config) -> None:
    """Settle existing homepages without changing the creation agent's tools."""
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            agents = (
                await connection.execute(
                    sa.select(tables.agent.c.workspace_id, tables.agent.c.id).order_by(
                        tables.agent.c.workspace_id, tables.agent.c.id
                    )
                )
            ).all()
            if not agents:
                raise RuntimeError("creation eval preparation requires a seeded agent")
        for agent in agents:
            with ws(agent.workspace_id):
                await ScopedStore(extension=WEB_EXTENSION).put(
                    f"{HOMEPAGE_SEED_PREFIX}{agent.id}", SETTLED_MARKER
                )
    finally:
        await dispose_db()


def main() -> None:
    """Prepare the app eval database named by the active UFO config."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--creation", action="store_true")
    args = parser.parse_args()
    prepare = prepare_creation_eval if args.creation else prepare_app_eval
    asyncio.run(prepare(load_config()))


if __name__ == "__main__":
    main()
