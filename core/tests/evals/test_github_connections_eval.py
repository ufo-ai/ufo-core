"""The GitHub routing suite is bound to the GitHub the eval workspace actually offers."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_eval_env.manifest import ACCOUNT_ID, GITHUB_HOST, GITHUB_PROVIDER

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import TASKS
from evals.suites.github_connections import CASES, GITHUB_HANDOFF, github_state
from evals.suites.source_watch_offer import GITHUB as SOURCE_WATCH_PROVIDER
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables

BY_NAME = {case.name: case for case in CASES}


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response="", calls=calls, own_calls=calls)


def test_the_handoff_names_the_provider_the_eval_pack_ships() -> None:
    """`assistant_eval` drops the real brokers, so the broker's `github` is in no catalogue a case
    of this suite ever sees. Asserting it cost four cases a night to "did not attempt:
    connect_account matching {'provider': 'github'}" while the agent correctly reached for the one
    connector the workspace showed it."""
    assert GITHUB_HANDOFF == ("connect_account", {"provider": GITHUB_PROVIDER})
    assert GITHUB_PROVIDER == "eval_github"
    assert GITHUB_HOST.endswith(".evalenv.test")


async def test_the_handoff_case_accepts_the_eval_github_connect() -> None:
    case = BY_NAME["github-not-connected-asked-if-connected"]

    handed = await case.grader(
        _output(ToolInvocation("connect_account", {"provider": GITHUB_PROVIDER}, "{}", True))
    )
    brokered = await case.grader(
        _output(ToolInvocation("connect_account", {"provider": "github"}, "{}", True))
    )

    assert handed.passed, handed.reason
    assert not brokered.passed


def test_the_suite_is_exclusive_so_it_settles_a_workspace_others_share() -> None:
    """`source_watch_offer` records a GitHub connection of its own on the shard's one workspace.
    While the two shared a provider and this suite refused to run beside it, its seed raised and
    took the whole report with it — no `github_connections` row on the 2026-09-10 or 2026-09-11
    sweeps."""
    task = next(task for task in TASKS if task.name == "github_connections")

    assert task.exclusive
    assert SOURCE_WATCH_PROVIDER != GITHUB_PROVIDER


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_the_seed_settles_a_workspace_another_suites_connection_reached(
    db: None, tmp_path
) -> None:
    """A shard runs its suites on one workspace. The unconnected state clears whatever GitHub
    connections it finds, whichever account they name; the connected state only adds, because
    `coding_subagent` seeds it from the concurrent wave where another suite's turn may be reading
    the same account. Seeding the connected state twice settles rather than duplicates."""
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="evals@localhost",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=uuid4(),
                workspace_id=workspace_id,
                provider=GITHUB_PROVIDER,
                account_id="a-stranger",
                host=GITHUB_HOST,
                owner_member_id=member_id,
                shared=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        await github_state(connected=True)(workspace_id, agent_id, blob)
        assert await _accounts(workspace_id) == {"a-stranger", ACCOUNT_ID}

        await github_state(connected=True)(workspace_id, agent_id, blob)
        assert await _accounts(workspace_id) == {"a-stranger", ACCOUNT_ID}

        await github_state(connected=False)(workspace_id, agent_id, blob)
        assert await _accounts(workspace_id) == set()

        await github_state(connected=True)(workspace_id, agent_id, blob)
        assert await _accounts(workspace_id) == {ACCOUNT_ID}


async def _accounts(workspace_id) -> set[str]:
    async with workspace_tx() as connection:
        return set(
            (
                await connection.execute(
                    sa.select(tables.connection.c.account_id).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.provider == GITHUB_PROVIDER,
                    )
                )
            )
            .scalars()
            .all()
        )
