"""GitHub connection routing. One connection covers private clone, push, `gh`, and the API, and
`connect_account` with `provider: github` is the one handoff: unconnected, the agent starts it and
offers no other route to the files; connected, it answers from the connection and offers no second
one."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_pipedream.client import CONNECTORS

from evals.driver import EVAL_SURFACE
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
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

GITHUB_PROVIDER = "github"
GITHUB_ACCOUNT_ID = "eval-github-account"
GITHUB_HOST = CONNECTORS[GITHUB_PROVIDER].host
GITHUB_HANDOFF: tuple[str, JsonObject] = ("connect_account", {"provider": GITHUB_PROVIDER})
OTHER_ROUTES = ("spawn", "fetch_url", "call_external_tool")


def github_state(*, connected: bool) -> CapabilitySeed:
    """Every GitHub connection the workspace holds is disconnected; with `connected`, one shared
    `github` connection is then recorded for the workspace's first member."""

    async def seed(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        workspace = ws_current()
        async with workspace_tx() as connection:
            connections = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.id,
                        tables.connection.c.account_id,
                        tables.connection.c.owner_member_id,
                    ).where(
                        tables.connection.c.workspace_id == workspace.workspace_id,
                        tables.connection.c.provider == GITHUB_PROVIDER,
                    )
                )
            ).all()
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace.workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
        if any(row.account_id != GITHUB_ACCOUNT_ID for row in connections):
            raise RuntimeError(
                "github_connections requires a disposable workspace without GitHub accounts"
            )
        with agent(agent_id):
            grants = GrantStore()
            for row in connections:
                await grants.disconnect(row.id, actor_member_id=row.owner_member_id)
        if not connected:
            return
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace.workspace_id,
                    agent_id=agent_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-github-state:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with agent(agent_id):
            await GrantStore().record(
                provider=GITHUB_PROVIDER,
                account_id=GITHUB_ACCOUNT_ID,
                host=GITHUB_HOST,
                grantor_member_id=member_id,
                conversation_id=conversation_id,
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
        digest_tag="github-connections:not-connected-setup:action",
        seed=github_state(connected=False),
    ),
    CapabilityCase(
        "github-not-connected-asked-if-connected",
        "Is GitHub connected for this workspace? I need you to clone a private repo and push a "
        "branch.",
        attempted_tools_scorer((GITHUB_HANDOFF,), OTHER_ROUTES, ()),
        digest_tag="github-connections:not-connected-asked-if-connected:action",
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
        digest_tag="github-connections:connected-asked-if-connected:answer",
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
        digest_tag="github-connections:connected-private-repo-issue:action",
        seed=github_state(connected=True),
    ),
)
