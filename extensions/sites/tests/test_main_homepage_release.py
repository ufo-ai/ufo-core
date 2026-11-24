"""The sweep that releases a main agent's homepage once the chat app becomes that agent.

It stands in for what a migration cannot state: a workspace is adopted whenever its own next turn or
job runs, so the release has to name it then rather than at the moment the deploy migrated. Each
test drives the real candidate read and the real handler against the real database on both dialects
the fixture parametrizes."""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_sites.main_homepage import (
    RELEASED_KEY,
    release_main_homepage,
    released_visibility,
    unreleased_main_homepage_workspaces,
)
from ufo_ext_sites.manifest import NAME
from ufo_ext_sites.store import hosted_site

from ufo.db import workspace_tx
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

CHAT_EXTENSION = "app_chat"
CHAT_DECLARED = "chat"


async def _seed_agent(
    workspace_id: UUID,
    name: str,
    *,
    main: bool,
    visibility: str = "workspace",
    adopted: bool = False,
) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be brief",
                model="claude-opus-5-5",
                is_main=main,
                visibility=visibility,
                provisioned_by=CHAT_EXTENSION if adopted else None,
                provisioned_name=CHAT_DECLARED if adopted else None,
                provisioned_version="0.2.0" if adopted else None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _seed_workspace(
    *, adopted: bool = True, visibility: str = "workspace"
) -> tuple[UUID, UUID]:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    agent_id = await _seed_agent(
        workspace_id, "chat", main=True, visibility=visibility, adopted=adopted
    )
    return workspace_id, agent_id


async def _seed_site(
    workspace_id: UUID, name: str, *, visibility: str, port: int, bound: UUID | None
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(hosted_site).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                name=name,
                port=port,
                visibility=visibility,
                creator_member_id=uuid4(),
                generation=uuid4(),
                deploy_generation=1,
                homepage_agent_id=bound,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _sites(workspace_id: UUID) -> dict[str, sa.Row]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(hosted_site).where(hosted_site.c.workspace_id == workspace_id)
            )
        ).all()
    return {row.name: row for row in rows}


def test_a_released_row_resumes_at_the_narrower_of_the_two_levels() -> None:
    assert released_visibility("public", "workspace") == "workspace"
    assert released_visibility("private", "workspace") == "private"
    assert released_visibility("workspace", "private") == "private"
    assert released_visibility("workspace", "workspace") == "workspace"


async def test_the_sweep_releases_the_bound_page_and_leaves_the_rest_where_they_stand(
    db: None,
) -> None:
    workspace_id, agent_id = await _seed_workspace()
    with ws(workspace_id):
        other = await _seed_agent(workspace_id, "narrow", main=False)
        await _seed_site(workspace_id, "main-home", visibility="public", port=8000, bound=agent_id)
        await _seed_site(workspace_id, "narrow-home", visibility="private", port=8001, bound=other)
        await _seed_site(workspace_id, "scratch", visibility="private", port=8002, bound=None)
        before = await _sites(workspace_id)

    assert workspace_id in await unreleased_main_homepage_workspaces(NAME)()

    with ws(workspace_id):
        await release_main_homepage(context_for(NAME, frozenset()))
        after = await _sites(workspace_id)
        marker = await context_for(NAME, frozenset()).store.get(RELEASED_KEY)

    assert after["main-home"].homepage_agent_id is None
    assert after["main-home"].visibility == "workspace"
    assert after["main-home"].generation != before["main-home"].generation
    assert after["narrow-home"].homepage_agent_id == other
    assert after["narrow-home"].visibility == "private"
    assert after["narrow-home"].generation == before["narrow-home"].generation
    assert after["scratch"].generation == before["scratch"].generation
    assert marker == str(agent_id)
    assert workspace_id not in await unreleased_main_homepage_workspaces(NAME)()


async def test_the_release_does_not_widen_a_private_page_to_the_workspace(db: None) -> None:
    """The dormant column is what its creator last stated, so a page released from a workspace-
    visible agent's binding stays private."""
    workspace_id, agent_id = await _seed_workspace()
    with ws(workspace_id):
        await _seed_site(workspace_id, "main-home", visibility="private", port=8000, bound=agent_id)
        await release_main_homepage(context_for(NAME, frozenset()))
        after = await _sites(workspace_id)
    assert after["main-home"].homepage_agent_id is None
    assert after["main-home"].visibility == "private"


async def test_a_workspace_the_provision_has_not_reached_keeps_its_page(db: None) -> None:
    workspace_id, agent_id = await _seed_workspace(adopted=False)
    with ws(workspace_id):
        await _seed_site(
            workspace_id, "main-home", visibility="workspace", port=8000, bound=agent_id
        )
        await release_main_homepage(context_for(NAME, frozenset()))
        after = await _sites(workspace_id)
        marker = await context_for(NAME, frozenset()).store.get(RELEASED_KEY)
    assert after["main-home"].homepage_agent_id == agent_id
    assert marker is None
    assert workspace_id not in await unreleased_main_homepage_workspaces(NAME)()


async def test_a_page_a_member_binds_after_the_release_is_never_swept(db: None) -> None:
    """The release happens once per workspace."""
    workspace_id, agent_id = await _seed_workspace()
    with ws(workspace_id):
        await _seed_site(
            workspace_id, "main-home", visibility="workspace", port=8000, bound=agent_id
        )
        await release_main_homepage(context_for(NAME, frozenset()))
        await _seed_site(workspace_id, "asked-for", visibility="private", port=8001, bound=agent_id)

    assert workspace_id not in await unreleased_main_homepage_workspaces(NAME)()

    with ws(workspace_id):
        after = await _sites(workspace_id)
    assert after["asked-for"].homepage_agent_id == agent_id
    assert after["asked-for"].visibility == "private"
