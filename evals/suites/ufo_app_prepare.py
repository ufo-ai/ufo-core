"""Settle automatic homepage work before an app evaluation starts."""

import asyncio

import sqlalchemy as sa

from ufo.config import Config, load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.context import ScopedStore
from ufo.schema import tables
from ufo.workspace import ws

WEB_EXTENSION = "web"
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
SETTLED_MARKER = "withheld-eval"


async def prepare_app_eval(config: Config) -> None:
    """Settle the product homepage job for every agent in this isolated eval database."""
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
            raise RuntimeError("app eval preparation requires at least one seeded agent")
        for agent in agents:
            with ws(agent.workspace_id):
                await ScopedStore(extension=WEB_EXTENSION).put(
                    f"{HOMEPAGE_SEED_PREFIX}{agent.id}", SETTLED_MARKER
                )
    finally:
        await dispose_db()


def main() -> None:
    """Prepare the app eval database named by the active UFO config."""
    asyncio.run(prepare_app_eval(load_config()))


if __name__ == "__main__":
    main()
