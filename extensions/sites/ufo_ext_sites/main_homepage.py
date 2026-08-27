"""Release the page a workspace's main agent holds as its homepage, once, when the chat app becomes
that agent.

The chat app's page ships in the deploy's own bundle and is served row-less, so the main agent needs
no `hosted_site` row of its own. But the homepage read answers a bound row before it reads that
bundle, and the seed sweep bound a page to every agent it ever settled — main among them — so a
workspace carrying that seeded page draws it instead of the chat screen.

The release is a sweep here rather than a statement in a migration because a migration cannot reach
the workspace it has to reach. The core revision adopts the main agent only where the app's own
agent row already stood; every other workspace is adopted by `AgentProvisioning` on its next turn or
job, in core, where this table is out of reach — and a workspace an outgoing pod onboards and seeds
during a roll is adopted after the migration has run. A sweep names each of them whenever they are
adopted, however late, and its candidate read drains as it goes: a workspace leaves the set on the
release itself.

It releases once per workspace and records that it has. The main agent keeps the member-facing tool
set, so a member may ask it for a homepage of their own after this release; a standing rule would
take that page away on the next tick, where the marker leaves it exactly where the member put it.

The released row's own visibility column resumes as the binding clears, and the release caps it at
the audience the bound page already had — the agent's level, which stops at `workspace`. The column
is dormant while bound, so a `public` value left on it would publish the page to a viewer with no
session, and the agent's level written over a narrower column would disclose the page to the
workspace. A sweep has no speaker, so it performs no disclosure a member did not make."""

from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo_ext_sites.store import (
    VISIBILITY_LEVELS,
    HostedSites,
    Visibility,
    hosted_site,
    visibility_level,
)

RELEASE_JOB_NAME = "release_main_homepage"

"""Every five minutes, the cadence the homepage seed sweep runs on: the two answer the same state
from opposite ends, and a workspace adopted between two ticks waits one of them."""
RELEASE_JOB_SCHEDULE = "0 */5 * * * *"
RELEASED_KEY = "main-homepage-released"
CHAT_EXTENSION = "app_chat"
CHAT_DECLARED = "chat"

_agent = sa.table(
    "agent",
    sa.column("id", sa.Uuid),
    sa.column("workspace_id", sa.Uuid),
    sa.column("is_main", sa.Boolean),
    sa.column("visibility", sa.Text),
    sa.column("provisioned_by", sa.Text),
    sa.column("provisioned_name", sa.Text),
)
_ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid),
    sa.column("extension", sa.Text),
    sa.column("key", sa.Text),
)


def _the_chat_main_agent() -> sa.ColumnElement[bool]:
    """The one row this release is about: the workspace's main agent, once the chat app's provision
    has landed on it. Before that the agent's page is still its own, and releasing the binding would
    leave the member's Home tab empty until the adopting pass runs."""
    return sa.and_(
        _agent.c.is_main.is_(True),
        _agent.c.provisioned_by == CHAT_EXTENSION,
        _agent.c.provisioned_name == CHAT_DECLARED,
    )


def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates:
    """The candidate seam this sweep declares: the workspaces holding a page bound to a main agent
    the chat app has taken over, that this sweep has not released yet. A workspace leaves the set on
    the release — the row it named is no longer bound — and the marker keeps it out once a member
    binds a page of their own, so the settled fleet costs the tick one read."""

    def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]:
        released = sa.select(sa.literal(1)).where(
            _ext_store.c.workspace_id == hosted_site.c.workspace_id,
            _ext_store.c.extension == extension,
            _ext_store.c.key == RELEASED_KEY,
        )
        return (
            sa.select(hosted_site.c.workspace_id)
            .select_from(hosted_site.join(_agent, _agent.c.id == hosted_site.c.homepage_agent_id))
            .where(_the_chat_main_agent(), ~sa.exists(released))
            .group_by(hosted_site.c.workspace_id)
        )

    return owner_candidates(with_a_bound_main_homepage)


def released_visibility(site: str, agent: str) -> Visibility:
    """The level a released row resumes at: the narrower of its own dormant column and the agent
    whose binding gated it. Neither end may widen the other — the column is what its creator last
    stated, and the agent's level is the audience the page actually had while it was bound."""
    return min(visibility_level(site), visibility_level(agent), key=VISIBILITY_LEVELS.index)


async def release_main_homepage(ctx: ExtensionContext) -> None:
    """Release the main agent's homepage binding and record the workspace as released.

    The marker is written after the release, so a crash between the two leaves the workspace in the
    candidate set: the next tick finds the row already unbound, releases nothing, and marks it."""
    async with ctx.transaction() as connection:
        main = (
            await connection.execute(
                sa.select(_agent.c.id, _agent.c.visibility).where(
                    _agent.c.workspace_id == ctx.store.workspace_id, _the_chat_main_agent()
                )
            )
        ).one_or_none()
    if main is None:
        return
    sites = HostedSites(ctx.store.workspace_id, ctx.transaction)
    bound = await sites.homepage(main.id)
    if bound is not None:
        await sites.release_homepage(
            bound.conversation_id,
            bound.name,
            released_visibility(bound.visibility, main.visibility),
        )
    await ctx.store.put(RELEASED_KEY, str(main.id))
