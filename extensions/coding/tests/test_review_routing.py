import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from ufo_ext_coding.github_app import GIT_SLOT
from ufo_ext_coding.review_checkout import CodeReviewFinding, CodeReviewOutput
from ufo_ext_coding.review_publish import UNFINISHED_NOTICE, CodeReviewWorkflow
from ufo_ext_coding.review_routing import (
    GITHUB_PROVIDER,
    PULL_REQUEST_STREAM,
    ConfigureReviewInboxInput,
    ReviewTarget,
    StopReviewInboxInput,
    configure_review_inbox,
    drop_dead_review_bindings,
    record_review_conversation,
    review_inbox,
    review_run,
    review_run_for,
    route_review_pages,
    stop_review_inbox,
    workspaces_with_review_bindings,
)

from ufo.agent_scope import agent
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.schema import tables
from ufo.schema.records import (
    SUBAGENT_SURFACE,
    Agent,
    IncompleteReason,
    TerminalFrame,
    TerminalStatus,
    Turn,
)
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.sdk.jobs import owner_candidates
from ufo.sdk.manifest import HookContext, PageChangeBatch
from ufo.sdk.sources import ConnectorSourceConfig, PageChange, binding_name
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.tools import ToolContext
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.tools.context import SpawnResult, SubagentStatus
from ufo.workspace import ws

MODEL = "claude-opus-4-8"
ACCOUNT = "account-one"
SOURCE_NAME = binding_name(GITHUB_PROVIDER, ACCOUNT, None)
NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
REVIEW_WITH_DEFECT = CodeReviewOutput(
    findings=(
        CodeReviewFinding(
            path="core/other.py",
            line=7,
            title="A defect of the comparison nobody asked about",
            trigger="any request",
            failure="the wrong pull request is described",
            impact="materially incorrect result or state for a supported workflow",
        ),
    )
)


@dataclass(frozen=True)
class Workspace:
    id: UUID
    owner_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID
    source_id: UUID


@dataclass
class StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        return None


async def _workspace() -> Workspace:
    workspace_id = uuid4()
    owner_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": owner_id,
                    "workspace_id": workspace_id,
                    "email": "owner@example.com",
                    "is_admin": True,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": member_id,
                    "workspace_id": workspace_id,
                    "email": "member@example.com",
                    "is_admin": False,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model=MODEL,
                is_main=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=owner_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with ws(workspace_id), agent(agent_id):
        source_id = await context_for("coding", frozenset()).register_source(
            GITHUB_PROVIDER,
            ConnectorSourceConfig(account=ACCOUNT, stream=PULL_REQUEST_STREAM),
            subject=SHARED_SUBJECT,
            owner_member_id=owner_id,
            agent_id=agent_id,
        )
    return Workspace(
        workspace_id,
        owner_id,
        member_id,
        agent_id,
        conversation_id,
        source_id,
    )


def _tool_context(
    state: Workspace, *, speaker_id: UUID | None = None, conversation_id: UUID | None = None
) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.id,
            conversation_id=conversation_id or state.conversation_id,
            agent_id=state.agent_id,
            seq=1,
            status="running",
            inbound="review pull requests from this source",
            created_at=NOW,
        ),
        agent=Agent(prompt="p", model=MODEL),
        spawn=None,
        speaker_member_id=speaker_id or state.owner_id,
        audience=SHARED_AUDIENCE,
        artifact_token_secret="",
        ext=context_for("coding", frozenset()),
    )


def _change(
    state: Workspace,
    *,
    revision: int,
    base: str = "b" * 40,
    head: str = "a" * 40,
    draft: bool = False,
    status: str = "open",
    source_id: UUID | None = None,
    repository: str = "metalcraftai/ufo",
) -> PageChange:
    record = json.dumps(
        {
            "repo_full_name": repository,
            "number": 1237,
            "state": status,
            "draft": draft,
            "base": {"sha": base, "label": "metalcraftai:main"},
            "head": {"sha": head, "label": "metalcraftai:feature"},
            "updated_at": NOW.isoformat(),
        }
    )
    body = f"# github pull_requests: PR #1237\n\n{record}"
    return PageChange(
        page_id=uuid4(),
        source_id=source_id or state.source_id,
        subject=SHARED_SUBJECT,
        stream=PULL_REQUEST_STREAM,
        title="PR #1237",
        body=body,
        digest="sha256:test",
        revision=revision,
        tombstone=False,
        created_at=NOW,
        as_of=NOW,
        changed_at=NOW,
    )


async def _activate(state: Workspace) -> str:
    with ws(state.id), agent(state.agent_id):
        result = await configure_review_inbox(
            _tool_context(state),
            ConfigureReviewInboxInput(
                source=SOURCE_NAME, user_description="Configuring automatic code review."
            ),
        )
    return result.content[0].text


def _hook_context(state: Workspace) -> object:
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=state.id,
    )
    return context_for("coding", frozenset(), invoker=invoker)


async def _route(state: Workspace, ext: object, *changes: PageChange) -> None:
    with ws(state.id):
        await route_review_pages(HookContext(ext=ext, payload=PageChangeBatch(changes=changes)))


async def _turns(state: Workspace) -> list[sa.RowMapping]:
    """Every review turn: the ones the router opened a conversation for, never the conversation the
    admin configured the source in."""
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn)
                    .where(
                        tables.turn.c.workspace_id == state.id,
                        tables.turn.c.conversation_id != state.conversation_id,
                    )
                    .order_by(tables.turn.c.created_at)
                )
            ).mappings()
        )


async def _reviewer_conversation(state: Workspace) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=state.id,
                agent_id=state.agent_id,
                surface=SUBAGENT_SURFACE,
                queue_key=uuid4().hex,
                member_id=state.owner_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return conversation_id


async def test_activation_baselines_existing_pages_and_wakes_exact_inbox(db: None) -> None:
    state = await _workspace()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=uuid4(),
                workspace_id=state.id,
                source_id=state.source_id,
                digest="sha256:baseline",
                body_ref="sources/baseline",
                stream=PULL_REQUEST_STREAM,
                title="Existing PR",
                subject=SHARED_SUBJECT,
                revision=7,
                tombstone=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    assert "revision 1" in await _activate(state)
    ext = _hook_context(state)
    await _route(state, ext, _change(state, revision=1))
    assert await _turns(state) == []

    await _route(state, ext, _change(state, revision=2))
    [turn] = await _turns(state)
    assert "Repository: metalcraftai/ufo" in turn["inbound"]
    assert "Pull request: 1237" in turn["inbound"]
    assert f"Base SHA: {'b' * 40}" in turn["inbound"]
    assert f"Head SHA: {'a' * 40}" in turn["inbound"]
    assert "Spawn exactly one `code_review` subagent in the background" in turn["inbound"]
    assert "end the turn without publishing" in turn["inbound"]
    assert "call `publish_code_review`" in turn["inbound"]
    assert "subagent id named by that message" in turn["inbound"]
    async with workspace_tx() as connection:
        inbox = (
            (
                await connection.execute(
                    sa.select(review_inbox).where(review_inbox.c.workspace_id == state.id)
                )
            )
            .mappings()
            .one()
        )
        run = (
            (
                await connection.execute(
                    sa.select(review_run).where(review_run.c.workspace_id == state.id)
                )
            )
            .mappings()
            .one()
        )
    assert inbox["agent_id"] == state.agent_id
    assert inbox["baseline_revision"] == 1
    assert run["turn_id"] == turn["id"]
    assert str(run["run_id"]) in turn["inbound"]
    with ws(state.id):
        stored = await review_run_for(_hook_context(state), run["run_id"], turn["conversation_id"])
        wrong_conversation = await review_run_for(_hook_context(state), run["run_id"], uuid4())
    assert stored is not None
    assert stored.repository == "metalcraftai/ufo"
    assert stored.conversation_id == turn["conversation_id"]
    assert turn["conversation_id"] != state.conversation_id
    assert wrong_conversation is None


async def test_only_new_open_ready_comparisons_wake_once(db: None) -> None:
    state = await _workspace()
    await _activate(state)
    ext = _hook_context(state)
    draft = _change(state, revision=1, draft=True)
    await _route(state, ext, draft)
    await _route(state, ext, _change(state, revision=2))
    await _route(state, ext, _change(state, revision=3))
    await _route(state, ext, _change(state, revision=4, head="c" * 40, status="closed"))
    await _route(state, ext, _change(state, revision=5, head="c" * 40))
    await _route(state, ext, _change(state, revision=6, source_id=uuid4()))

    turns = await _turns(state)
    assert len(turns) == 2
    assert len({turn["conversation_id"] for turn in turns}) == 2
    async with workspace_tx() as connection:
        runs = (
            (
                await connection.execute(
                    sa.select(review_run)
                    .where(review_run.c.workspace_id == state.id)
                    .order_by(review_run.c.head_sha)
                )
            )
            .mappings()
            .all()
        )
    assert [run["head_sha"] for run in runs] == ["a" * 40, "c" * 40]
    assert {run["turn_id"] for run in runs} == {turn["id"] for turn in turns}
    assert {run["conversation_id"] for run in runs} == {turn["conversation_id"] for turn in turns}


async def test_the_conversation_that_ran_the_review_is_recorded_on_its_run(db: None) -> None:
    state = await _workspace()
    await _activate(state)
    await _route(state, _hook_context(state), _change(state, revision=1))
    reviewer = await _reviewer_conversation(state)
    target = ReviewTarget(
        repository="metalcraftai/ufo",
        pull_request_number=1237,
        base_sha="b" * 40,
        head_sha="a" * 40,
    )
    async with workspace_tx() as connection:
        run_id, review_conversation = (
            await connection.execute(
                sa.select(review_run.c.run_id, review_run.c.conversation_id).where(
                    review_run.c.workspace_id == state.id
                )
            )
        ).one()

    with ws(state.id):
        ordered = await review_run_for(_hook_context(state), run_id, review_conversation)
        await record_review_conversation(_hook_context(state), target, reviewer)
        reviewed = await review_run_for(_hook_context(state), run_id, review_conversation)
        await record_review_conversation(
            _hook_context(state),
            ReviewTarget(
                repository="metalcraftai/ufo",
                pull_request_number=1237,
                base_sha="b" * 40,
                head_sha="c" * 40,
            ),
            uuid4(),
        )
        unmatched = await review_run_for(_hook_context(state), run_id, review_conversation)

    assert ordered is not None
    assert ordered.conversation_id == review_conversation
    assert ordered.review_conversation_id is None
    assert reviewed is not None
    assert reviewed.review_conversation_id == reviewer
    assert unmatched is not None
    assert unmatched.review_conversation_id == reviewer


@pytest.mark.parametrize(
    ("terminal_status", "incomplete_reason", "has_output", "records_checkout", "conclusion"),
    (
        ("done", None, True, True, "success"),
        ("done", "round_budget", True, True, "action_required"),
        ("done", None, True, False, "action_required"),
        ("done", None, False, True, "action_required"),
        ("failed", None, False, True, "action_required"),
    ),
)
async def test_publish_code_review_reads_the_exact_child_outcome(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: TerminalStatus,
    incomplete_reason: IncompleteReason | None,
    has_output: bool,
    records_checkout: bool,
    conclusion: str,
) -> None:
    state = await _workspace()
    await _activate(state)
    await _route(state, _hook_context(state), _change(state, revision=1))
    reviewer = await _reviewer_conversation(state)
    target = ReviewTarget(
        repository="metalcraftai/ufo",
        pull_request_number=1237,
        base_sha="b" * 40,
        head_sha="a" * 40,
    )
    async with workspace_tx() as connection:
        run_id, review_conversation = (
            await connection.execute(
                sa.select(review_run.c.run_id, review_run.c.conversation_id).where(
                    review_run.c.workspace_id == state.id
                )
            )
        ).one()
    ext = context_for("coding", frozenset({GIT_SLOT}))

    subagent_id = uuid4()

    class ResultControl:
        async def result(self, turn_id: UUID) -> SpawnResult:
            assert turn_id == subagent_id
            if records_checkout:
                await record_review_conversation(ext, target, reviewer)
            terminal = TerminalFrame(
                status=terminal_status, text="{}", incomplete_reason=incomplete_reason
            )
            return SpawnResult(
                turn_id=turn_id,
                conversation_id=reviewer,
                output=CodeReviewOutput() if has_output else None,
                terminal=terminal,
                untrusted=True,
            )

        async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
            raise AssertionError(turn_ids)

        async def cancel(self, turn_id: UUID) -> SubagentStatus:
            raise AssertionError(turn_id)

        async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus:
            raise AssertionError((turn_id, text, dedup_key))

    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(201, json={"id": 91})

    monkeypatch.setenv(GIT_SLOT.upper(), "secret")
    context = replace(
        _tool_context(state, conversation_id=review_conversation),
        subagents=ResultControl(),
        ext=ext,
        public_base_url="https://app.example.com",
    )
    with ws(state.id):
        await CodeReviewWorkflow(context, httpx.MockTransport(github)).run(run_id, subagent_id)

    body = json.loads(requests[1].content)
    assert body["conclusion"] == conclusion
    if conclusion == "action_required":
        assert body["output"]["summary"].startswith(UNFINISHED_NOTICE)
    else:
        assert body["output"]["summary"].startswith("No severe defect found.")
    assert ("details_url" in body) is records_checkout


async def _publish_fixture(
    state: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    reviewer: UUID,
    output: CodeReviewOutput | None,
) -> tuple[ToolContext, UUID, UUID, list[httpx.Request], httpx.MockTransport]:
    async with workspace_tx() as connection:
        run_id, review_conversation = (
            await connection.execute(
                sa.select(review_run.c.run_id, review_run.c.conversation_id).where(
                    review_run.c.workspace_id == state.id
                )
            )
        ).one()
    subagent_id = uuid4()

    class ResultControl:
        async def result(self, turn_id: UUID) -> SpawnResult:
            return SpawnResult(
                turn_id=turn_id,
                conversation_id=reviewer,
                output=output,
                terminal=TerminalFrame(status="done", text="{}", incomplete_reason=None),
                untrusted=True,
            )

        async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
            raise AssertionError(turn_ids)

        async def cancel(self, turn_id: UUID) -> SubagentStatus:
            raise AssertionError(turn_id)

        async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus:
            raise AssertionError((turn_id, text, dedup_key))

    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(201, json={"id": 91})

    monkeypatch.setenv(GIT_SLOT.upper(), "secret")
    context = replace(
        _tool_context(state, conversation_id=review_conversation),
        subagents=ResultControl(),
        ext=context_for("coding", frozenset({GIT_SLOT})),
        public_base_url="https://app.example.com",
    )
    return context, run_id, subagent_id, requests, httpx.MockTransport(github)


async def test_publish_refuses_a_child_that_reviewed_another_comparison(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _workspace()
    await _activate(state)
    await _route(state, _hook_context(state), _change(state, revision=1))
    reviewed_by = await _reviewer_conversation(state)
    other = await _reviewer_conversation(state)
    with ws(state.id):
        await record_review_conversation(
            context_for("coding", frozenset({GIT_SLOT})),
            ReviewTarget(
                repository="metalcraftai/ufo",
                pull_request_number=1237,
                base_sha="b" * 40,
                head_sha="a" * 40,
            ),
            reviewed_by,
        )
    context, run_id, subagent_id, requests, transport = await _publish_fixture(
        state, monkeypatch, other, REVIEW_WITH_DEFECT
    )
    with ws(state.id), pytest.raises(ValueError, match=str(subagent_id)):
        await CodeReviewWorkflow(context, transport).run(run_id, subagent_id)
    assert requests == []


async def test_publish_carries_no_findings_the_comparison_never_earned(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _workspace()
    await _activate(state)
    await _route(state, _hook_context(state), _change(state, revision=1))
    reviewer = await _reviewer_conversation(state)
    context, run_id, subagent_id, requests, transport = await _publish_fixture(
        state, monkeypatch, reviewer, REVIEW_WITH_DEFECT
    )
    with ws(state.id):
        await CodeReviewWorkflow(context, transport).run(run_id, subagent_id)
    body = json.loads(requests[1].content)
    assert body["conclusion"] == "action_required"
    assert body["output"]["summary"] == UNFINISHED_NOTICE
    assert REVIEW_WITH_DEFECT.findings[0].title not in body["output"]["summary"]


async def test_review_run_refuses_another_workspaces_turn(db: None) -> None:
    first = await _workspace()
    second = await _workspace()
    await _activate(first)
    await _activate(second)
    await _route(first, _hook_context(first), _change(first, revision=1))
    await _route(second, _hook_context(second), _change(second, revision=1))
    [other_turn] = await _turns(second)
    with pytest.raises(IntegrityError):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(review_run)
                .where(review_run.c.workspace_id == first.id)
                .values(turn_id=other_turn["id"])
            )


async def test_review_inbox_requires_an_admin_and_shared_source(db: None) -> None:
    state = await _workspace()
    with ws(state.id), agent(state.agent_id):
        with pytest.raises(ValueError, match="workspace admin"):
            await configure_review_inbox(
                _tool_context(state, speaker_id=state.member_id),
                ConfigureReviewInboxInput(
                    source=SOURCE_NAME, user_description="Configuring automatic code review."
                ),
            )
        await context_for("coding", frozenset()).set_source_subject(
            (state.source_id,), f"member:{state.owner_id}"
        )
        with pytest.raises(ValueError, match="shared pull_requests source"):
            await configure_review_inbox(
                _tool_context(state),
                ConfigureReviewInboxInput(
                    source=SOURCE_NAME, user_description="Configuring automatic code review."
                ),
            )


@pytest.mark.parametrize(
    ("repository", "base"),
    (("metalcraftai/ufo/extra", "b" * 40), ("metalcraftai/ufo", "B" * 40)),
)
async def test_untrusted_page_identity_is_validated_before_wake(
    db: None, repository: str, base: str
) -> None:
    state = await _workspace()
    await _activate(state)
    with pytest.raises(ValueError):
        await _route(
            state,
            _hook_context(state),
            _change(state, revision=1, repository=repository, base=base),
        )
    assert await _turns(state) == []
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(review_run)
                .where(review_run.c.workspace_id == state.id)
            )
        ).scalar_one() == 0


async def test_stopping_a_source_ends_review_and_is_an_admins(db: None) -> None:
    """An off switch, so ending automatic review does not mean deleting the feed. Stopping is the
    admin act that binding it was, a source nobody bound reports so rather than failing, and a
    stopped source's later heads open nothing."""
    state = await _workspace()
    await _activate(state)
    ext = _hook_context(state)
    await _route(state, ext, _change(state, revision=1))
    assert len(await _turns(state)) == 1
    with ws(state.id), agent(state.agent_id):
        with pytest.raises(ValueError, match="workspace admin"):
            await stop_review_inbox(
                _tool_context(state, speaker_id=state.member_id),
                StopReviewInboxInput(
                    source=SOURCE_NAME, user_description="Stopping automatic code review."
                ),
            )
        stopped = await stop_review_inbox(
            _tool_context(state),
            StopReviewInboxInput(
                source=SOURCE_NAME, user_description="Stopping automatic code review."
            ),
        )
        again = await stop_review_inbox(
            _tool_context(state),
            StopReviewInboxInput(
                source=SOURCE_NAME, user_description="Stopping automatic code review."
            ),
        )
    await _route(state, ext, _change(state, revision=2, head="c" * 40))
    assert "no longer open a review conversation" in stopped.content[0].text
    assert "was being reviewed" in again.content[0].text
    assert len(await _turns(state)) == 1
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(review_inbox)
                .where(review_inbox.c.workspace_id == state.id)
            )
        ).scalar_one()
    assert rows == 0


async def test_a_binding_whose_source_is_gone_is_dropped_rather_than_left_armed(db: None) -> None:
    """Deleting a source is a soft delete, so nothing cascades to the binding keyed on it. Left
    there, the row is invisible state that re-arms at its old baseline the moment that source is
    registered again — a burst of reviews nobody asked for. The sweep drops it, and it has to be
    a sweep: a removed source produces no page change, so routing never sees the binding to forget.
    A live source's binding is untouched by the same pass."""
    state = await _workspace()
    live = await _workspace()
    await _activate(state)
    await _activate(live)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(removed_at=NOW, updated_at=NOW)
            .where(tables.source.c.id == state.source_id)
        )
    assert state.id in await owner_candidates(workspaces_with_review_bindings)()
    with ws(state.id):
        await drop_dead_review_bindings(_hook_context(state))
    with ws(live.id):
        await drop_dead_review_bindings(_hook_context(live))
    async with workspace_tx() as connection:
        kept = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(review_inbox)
                .where(review_inbox.c.workspace_id == live.id)
            )
        ).scalar_one()
    assert kept == 1
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(review_inbox)
                .where(review_inbox.c.workspace_id == state.id)
            )
        ).scalar_one()
    assert rows == 0
    assert await _turns(state) == []
