"""GitHub connection routing. One connection covers private clone, push, `gh`, and the API, and
`connect_account` with the workspace's GitHub provider is the one handoff: unconnected, the agent
starts it and offers no other route to the files; connected, it answers from the connection and
offers no second one.

The provider is the eval environment's own GitHub (`eval_env`), because that is the only GitHub a
pack these cases run under offers: `assistant_eval` drops the real brokers, so a case naming the
broker's `github` asserts a handoff the catalogue never shows and the agent can never attempt."""

from uuid import UUID

import sqlalchemy as sa
from ufo_ext_eval_env.manifest import ACCOUNT_ID, GITHUB_HOST, GITHUB_PROVIDER

from evals.harness.capability import CapabilityCase, CapabilitySeed
from evals.harness.harness import JsonObject
from evals.harness.scorers import (
    attempted_tools_scorer,
    combine,
    restraint_scorer,
    skill_scorer,
)
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.schema import tables

GITHUB_HANDOFF: tuple[str, JsonObject] = ("connect_account", {"provider": GITHUB_PROVIDER})
OTHER_ROUTES = ("spawn", "fetch_url", "call_external_tool")


def github_state(*, connected: bool) -> CapabilitySeed:
    """`connected` records one shared GitHub connection for the workspace's first member, adding
    to whatever the workspace already holds; the unconnected state removes every GitHub connection
    it finds, whichever account names it.

    Only the removing half touches another suite's state, and only this suite — which is exclusive,
    so it runs alone after the concurrent wave — ever asks for it. `coding_subagent` seeds the
    connected state from the concurrent wave, where `memory_staleness` holds the same account and a
    live turn may be reading it, so that half must add and never delete. `record` reuses the
    member's existing connection, so seeding it twice settles rather than duplicates."""

    async def seed(workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        async with workspace_tx() as connection:
            held = (
                ()
                if connected
                else (
                    (
                        await connection.execute(
                            sa.select(tables.connection.c.id).where(
                                tables.connection.c.workspace_id == workspace_id,
                                tables.connection.c.provider == GITHUB_PROVIDER,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
        with agent(agent_id):
            grants = GrantStore()
            for connection_id in held:
                await grants.disconnect(connection_id, actor_member_id=member_id)
            if connected:
                await grants.record(
                    provider=GITHUB_PROVIDER,
                    account_id=ACCOUNT_ID,
                    host=GITHUB_HOST,
                    grantor_member_id=member_id,
                    shared=True,
                )

    return seed


CASES = (
    CapabilityCase(
        "github-not-connected-setup",
        "I haven't connected GitHub to this workspace yet. Set it up so you can clone our private "
        "repositories, push branches, and open pull requests.",
        combine(
            skill_scorer("coding", "create-skill"),
            attempted_tools_scorer(
                (GITHUB_HANDOFF,), OTHER_ROUTES, (("load_skill", "connect_account"),)
            ),
        ),
        digest_tag=f"github-connections:not-connected-setup:action:{GITHUB_PROVIDER}",
        seed=github_state(connected=False),
    ),
    CapabilityCase(
        "github-not-connected-asked-if-connected",
        "Is GitHub connected for this workspace? I need you to clone a private repo and push a "
        "branch.",
        attempted_tools_scorer((GITHUB_HANDOFF,), OTHER_ROUTES, ()),
        digest_tag=f"github-connections:not-connected-asked-if-connected:action:{GITHUB_PROVIDER}",
        seed=github_state(connected=False),
    ),
    CapabilityCase(
        "github-connected-asked-if-connected",
        "Is GitHub connected for this workspace? Can you clone our private repos and push "
        "branches?",
        restraint_scorer(("connect_account", "spawn")),
        rubric=(
            "The answer states that GitHub is connected, and that private clone, push, and GitHub "
            "API reads and writes all work through that one connection; it offers no further "
            "connection.",
        ),
        digest_tag=f"github-connections:connected-asked-if-connected:answer:{GITHUB_PROVIDER}",
        seed=github_state(connected=True),
    ),
    CapabilityCase(
        "github-connected-private-repo-issue",
        "Fix issue #42 in our private repo github.com/evalco/ledger — the nightly export writes an "
        "empty CSV — and open a pull request. Stop after the first attempted route; do not retry "
        "failed tools or delegate substitute workers.",
        combine(
            skill_scorer("coding", "create-skill"),
            attempted_tools_scorer(
                (("spawn", {}),), ("connect_account", "fetch_url"), (("load_skill", "spawn"),)
            ),
        ),
        digest_tag=f"github-connections:connected-private-repo-issue:action:{GITHUB_PROVIDER}",
        seed=github_state(connected=True),
    ),
)
