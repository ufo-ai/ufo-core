from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_slack.manifest import SLACK_INSTALLED_LINE
from ufo_ext_slack.manifest import manifest as slack_manifest

from evals.harness.capability import CapabilityCase, CapabilityOutput, ToolInvocation
from evals.suites import surface_setup
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_workspace_facts
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables

COLD = {
    "slack-workspace-install": ("slack", "slack_connect"),
    "slack-own-app-manifest": ("slack", "slack_app_manifest"),
    "slack-channel-lookup": ("slack", "slack_channels"),
    "imessage-phone": ("imessage", "imessage_connect"),
}
WARM = {f"{name}-known": pair for name, pair in COLD.items()}
NEGATIVE = {
    "slack-status-question",
    "slack-installed-status-question",
    "slack-personal-account",
    "imessage-line-question",
}


def _dispatched(surface: str, action: str) -> ToolInvocation:
    return ToolInvocation(
        name="object_action",
        input={"kind": "surface", "name": surface, "action": action, "input": {}},
        result="{}",
        has_result=True,
    )


def _read(surface: str) -> ToolInvocation:
    return ToolInvocation(
        name="object_get", input={"kind": "surface", "name": surface}, result="", has_result=True
    )


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response="", calls=calls)


def _case(name: str) -> CapabilityCase:
    return next(case for case in surface_setup.CASES if case.name == name)


async def test_cold_and_warm_cases_grade_the_dispatched_surface_action() -> None:
    assert set(COLD) | set(WARM) | NEGATIVE == {case.name for case in surface_setup.CASES}
    for name, (surface, action) in (COLD | WARM).items():
        case = _case(name)
        assert (await case.grader(_output(_dispatched(surface, action)))).passed
        assert not (await case.grader(_output(ToolInvocation(name=action, input={})))).passed
        other = "web" if surface == "slack" else "slack"
        assert not (await case.grader(_output(_dispatched(other, action)))).passed
        beside_a_neighbor = _output(
            _dispatched(surface, action),
            ToolInvocation(name="connect_account", input={"provider": surface}),
        )
        assert not (await case.grader(beside_a_neighbor)).passed


async def test_a_warm_case_seeds_the_surface_read_and_refuses_rediscovery() -> None:
    for name, (surface, action) in WARM.items():
        case = _case(name)
        assert case.prior_messages
        [seeded] = case.undelivered
        assert seeded.tool == "object_get"
        assert seeded.input == {"kind": "surface", "name": surface}
        listed = yaml.safe_load(seeded.result)
        assert listed["name"] == surface
        assert action in {view["name"] for view in listed["actions"]}
        assert all(view["call"]["name"] == surface for view in listed["actions"])
        rediscovered = _output(
            ToolInvocation(name="object_explain", input={"kind": "surface"}),
            _dispatched(surface, action),
        )
        assert not (await case.grader(rediscovered)).passed
        reread = _output(_read(surface), _dispatched(surface, action))
        assert (await case.grader(reread)).passed
    for name in COLD:
        assert not _case(name).prior_messages and not _case(name).undelivered


async def test_the_status_question_takes_either_read_and_no_sibling() -> None:
    case = _case("slack-status-question")
    assert (await case.grader(_output(_dispatched("slack", "slack_connect")))).passed
    assert (await case.grader(_output(_read("slack")))).passed
    assert not (await case.grader(_output())).passed
    assert not (
        await case.grader(_output(_read("slack"), _dispatched("slack", "slack_channels")))
    ).passed
    assert not (await case.grader(_output(_dispatched("slack", "slack_app_manifest")))).passed


async def test_the_negatives_fail_on_the_surface_action_they_guard() -> None:
    personal = _case("slack-personal-account")
    assert (await personal.grader(_output())).passed
    assert (
        await personal.grader(
            _output(ToolInvocation(name="connect_account", input={"provider": "slack"}))
        )
    ).passed
    assert not (await personal.grader(_output(_dispatched("slack", "slack_connect")))).passed
    assert not (await personal.grader(_output(_dispatched("slack", "slack_channels")))).passed

    line = _case("imessage-line-question")
    assert (await line.grader(_output(_read("imessage")))).passed
    assert not (await line.grader(_output(_dispatched("imessage", "imessage_connect")))).passed


def test_briefs_are_authored_and_name_no_action() -> None:
    actions = {action for _surface, action in COLD.values()}
    for case in surface_setup.CASES:
        spoken = " ".join((case.message, *case.prior_messages)).casefold()
        assert not any(action in spoken for action in actions)
        assert "object_action" not in spoken
    assert len({case.digest_tag for case in surface_setup.CASES}) == len(surface_setup.CASES)
    assert all(case.digest_tag.startswith("surface-setup:") for case in surface_setup.CASES)


async def test_the_installed_status_case_seeds_a_live_install_and_takes_it_away(
    db: None, tmp_path: Path
) -> None:
    """The one case in this suite that runs against an installed workspace. The state it needs is a
    live install, not a bound row: the row alone is what `slack_connect` reports as `pending`, and
    the prompt states the Slack capability only where Slack has actually reached this deploy — so
    the seed is read back through the declared fact itself. The cleanup removes all of it, so the
    cold cases that follow still meet a workspace with nothing installed, which is what makes them
    cold."""
    case = _case("slack-installed-status-question")
    assert case.seed is not None and case.cleanup is not None
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="you are the assistant",
                model="anthropic/claude-sonnet-5",
                reasoning="off",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    with ws(workspace_id):
        await case.seed(workspace_id, agent_id, blob)
        assert await _installed(workspace_id) == surface_setup.SLACK_INSTALLATION_ID
        seeded = await turn_workspace_facts((slack_manifest(),), store, audience=SHARED_AUDIENCE)
        await case.cleanup(workspace_id, agent_id, blob)
        assert await _installed(workspace_id) is None
        cleaned = await turn_workspace_facts((slack_manifest(),), store, audience=SHARED_AUDIENCE)
    assert (seeded, cleaned) == ((SLACK_INSTALLED_LINE,), ())


async def _installed(workspace_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.surface_installation.c.installation_id).where(
                    tables.surface_installation.c.workspace_id == workspace_id,
                    tables.surface_installation.c.surface == surface_setup.SURFACE_SLACK,
                )
            )
        ).scalar_one_or_none()


async def test_the_seed_refuses_a_workspace_that_holds_someone_elses_slack(db: None) -> None:
    """The seed removes the installation row, both secret slots, the identity and marker blobs, and
    the mirror. Against a workspace that really runs Slack that is an OAuth-minted token and a
    member-supplied secret destroyed, and an install that has to be made again. A foreign install
    stops the run instead, and the cleanup refuses the same state, so a cleanup firing after a
    refused seed deletes nothing it was refused."""
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="you are the assistant",
                model="anthropic/claude-sonnet-5",
                reasoning="off",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface=surface_setup.SURFACE_SLACK,
                installation_id="team:TREALTEAM",
                agent_id=agent_id,
                routes_ingress=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=Path()))
    with ws(workspace_id):
        for act in (surface_setup._bind_slack_install, surface_setup._drop_slack_install):
            with pytest.raises(RuntimeError, match="disposable workspace without Slack state"):
                await act(workspace_id, agent_id, blob)
        async with workspace_tx() as connection:
            held = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
    assert held == "team:TREALTEAM"
