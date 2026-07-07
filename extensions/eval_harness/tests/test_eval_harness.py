"""The eval harness end-to-end proof: a capability case runs as a real turn via ctx.invoke, and its
answer + tool trajectory are reconstructed from the durable transcript and graded.

The scoped context, the durable transcript, and the trajectory corpus are the real dependencies; the
only stand-in is the turn worker — a StubWorker that plays the DBOS worker by landing the terminal
turn row and the transcript the agent would have produced, then returns the turn id. The target's
real work — invoke, reconstruct, grade — is what the tests assert, read back through the corpus."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_eval_harness import manifest as eh
from ufo_ext_eval_harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    ToolInvocation,
    run_capability_case,
)
from ufo_ext_eval_harness.harness import EvalCaseResult, EvalReport
from ufo_ext_eval_harness.judge import rubric_pass
from ufo_ext_eval_harness.report_html import render_report_html
from ufo_ext_eval_harness.scorers import (
    WEB_TOOLS,
    required_tools_scorer,
    restraint_scorer,
)
from ufo_ext_eval_harness.target import InProcessTarget, capability_output

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import Trajectory, context_for
from ufo.ext.loader import load_manifests
from ufo.loop.transcript import Transcript
from ufo.models.interface import Message, ToolResultBlock, ToolUseBlock
from ufo.schema import tables
from ufo.transcript import Conversation

MODEL = "claude-opus-4-8"
PROMPT = "You are a helpful assistant."


def _research_transcript() -> tuple[Message, ...]:
    return (
        Message(role="user", content="find the record then remember it"),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="s1", name="search_web", input={"query": "record"}),),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="s1", content="the record is 2:00:35"),),
        ),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="m1", name="memory_update", input={"text": "record 2:00:35"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="m1", content="saved"),),
        ),
        Message(role="assistant", content="Done — found it and remembered it for the team."),
    )


@dataclass
class StubWorker:
    blob: FilesystemBlobStore
    workspace_id: UUID
    transcript: tuple[Message, ...]

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID:
        turn_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=self.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound=message,
                    terminal={"status": "done", "text": "Done.", "model": MODEL},
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await Transcript(blob=self.blob, conversation_id=conversation_id).write(
            Conversation(seq=2, messages=self.transcript)
        )
        return turn_id


@dataclass
class DbConversations:
    workspace_id: UUID

    async def open(self, case_name: str) -> UUID:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    surface="eval",
                    queue_key=str(conversation_id),
                    member_id=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return conversation_id


@dataclass
class CorpusOutcome:
    ctx: object

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        for trajectory in await self.ctx.trajectories():
            if trajectory.conversation_id == conversation_id:
                return trajectory
        return None


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt=PROMPT,
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


def _context(workspace_id: UUID, blob: FilesystemBlobStore, invoker: StubWorker):
    return context_for(
        workspace_id,
        eh.NAME,
        frozenset(),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        blob=blob,
        invoker=invoker,
    )


async def test_capability_case_runs_through_invoke_and_scores_the_trajectory(
    db: None, tmp_path
) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = StubWorker(blob, workspace_id, _research_transcript())
    ctx = _context(workspace_id, blob, worker)
    target = InProcessTarget(
        ctx=ctx,
        agent_id=agent_id,
        conversations=DbConversations(workspace_id),
        outcome=CorpusOutcome(ctx),
    )
    case = CapabilityCase(
        "research-then-save",
        "find the record then remember it",
        required_tools_scorer(("search_web", "memory_update"), (("search_web", "memory_update"),)),
    )

    result = await run_capability_case(case, target)

    assert result.passed
    assert result.evidence["tools"] == ["search_web", "memory_update"]
    assert result.evidence["response"] == "Done — found it and remembered it for the team."


async def test_capability_case_fails_when_a_required_tool_is_absent(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = StubWorker(blob, workspace_id, _research_transcript())
    ctx = _context(workspace_id, blob, worker)
    target = InProcessTarget(
        ctx=ctx,
        agent_id=agent_id,
        conversations=DbConversations(workspace_id),
        outcome=CorpusOutcome(ctx),
    )
    case = CapabilityCase(
        "needs-fetch",
        "find the record then remember it",
        required_tools_scorer(("fetch_url",)),
    )

    result = await run_capability_case(case, target)

    assert not result.passed
    assert "fetch_url" in result.reason


async def test_web_dependent_case_behind_an_infra_outage_is_excluded_not_passed(
    db: None, tmp_path
) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    transcript = (
        Message(role="user", content="search the web for the record"),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="s1", name="search_web", input={"query": "record"}),),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(
                    tool_use_id="s1",
                    content="upstream 429 rate limit from the search provider",
                    is_error=True,
                ),
            ),
        ),
        Message(role="assistant", content="I could not reach the web."),
    )
    worker = StubWorker(blob, workspace_id, transcript)
    ctx = _context(workspace_id, blob, worker)
    target = InProcessTarget(
        ctx=ctx,
        agent_id=agent_id,
        conversations=DbConversations(workspace_id),
        outcome=CorpusOutcome(ctx),
    )
    case = CapabilityCase(
        "web-record",
        "search the web for the record",
        required_tools_scorer(("fetch_url",)),
        web_dependent=True,
    )

    result = await run_capability_case(case, target)

    assert result.excluded
    assert not result.passed
    assert result.evidence["infraExcluded"] is True
    assert "infra-excluded" in result.reason


async def test_required_tools_scorer_enforces_order() -> None:
    output = CapabilityOutput(
        "answer",
        (
            ToolInvocation("memory_update", {}),
            ToolInvocation("search_web", {}),
        ),
    )
    verdict = await required_tools_scorer(
        ("search_web", "memory_update"), (("search_web", "memory_update"),)
    )(output)
    assert not verdict.passed


async def test_restraint_scorer_flags_an_unnecessary_web_call() -> None:
    used = CapabilityOutput("Paris", (ToolInvocation("search_web", {"query": "capital"}),))
    clean = CapabilityOutput("Paris", ())
    assert not (await restraint_scorer(WEB_TOOLS)(used)).passed
    assert (await restraint_scorer(WEB_TOOLS)(clean)).passed


def test_capability_output_reconstructs_calls_and_errors() -> None:
    messages = (
        Message(role="user", content="do it"),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="b1", name="bash", input={"command": "ls"}),),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="b1", content="boom", is_error=True),),
        ),
        Message(role="assistant", content="I could not."),
    )
    output = capability_output(messages)
    assert output.tools == ("bash",)
    assert output.calls[0].result == "boom"
    assert output.tool_errors == ("boom",)
    assert output.response == "I could not."


@dataclass
class StubJudge:
    passes: list[bool]

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        return json.dumps({"items": [{"passed": p, "reason": "r"} for p in self.passes]})


async def test_rubric_pass_scripted_matches_quoted_terms() -> None:
    rubric = ["the answer names 'churn'", "the answer names 'billing'"]
    ok, _ = await rubric_pass("inst", "churn dropped after the billing rework", rubric, None)
    assert ok
    bad, reason = await rubric_pass("inst", "nothing relevant", rubric, None)
    assert not bad
    assert "churn" in reason


async def test_rubric_pass_uses_the_injected_judge() -> None:
    rubric = ["c1", "c2"]
    ok, _ = await rubric_pass("inst", "answer", rubric, StubJudge([True, True]))
    assert ok
    bad, _ = await rubric_pass("inst", "answer", rubric, StubJudge([True, False]))
    assert not bad


def test_report_html_renders_pass_fail_and_excluded() -> None:
    report = EvalReport(
        "tool_calling",
        "capability",
        "sha256:abc",
        (
            EvalCaseResult("won", True, "ok", {"response": "hi", "tools": ["search_web"]}),
            EvalCaseResult("lost", False, "did not call: fetch_url", {"response": "", "tools": []}),
            EvalCaseResult(
                "web", False, "infra-excluded (web unavailable): 429", {}, excluded=True
            ),
        ),
    )
    html = render_report_html(report).decode()
    assert "tool_calling" in html
    assert "did not call: fetch_url" in html
    assert "PASS" in html and "FAIL" in html
    assert "EXCLUDED" in html
    assert "1 excluded" in html


def test_report_pass_rate_ignores_excluded_and_suite_fails_on_a_real_failure() -> None:
    report = EvalReport(
        "s",
        "capability",
        "sha256:abc",
        (
            EvalCaseResult("won", True, "ok", {}),
            EvalCaseResult("lost", False, "bad", {}),
            EvalCaseResult("web", False, "infra-excluded", {"infraExcluded": True}, excluded=True),
        ),
    )
    assert report.pass_rate == 0.5
    assert report.passed is False
    payload = report.to_json()
    assert payload["passRate"] == 0.5
    assert payload["passed"] is False
    assert payload["excludedCount"] == 1
    assert [case["excluded"] for case in payload["cases"]] == [False, False, True]


def test_report_of_all_scored_passing_with_an_excluded_case_passes() -> None:
    report = EvalReport(
        "s",
        "capability",
        "sha256:abc",
        (
            EvalCaseResult("won", True, "ok", {}),
            EvalCaseResult("web", False, "infra-excluded", {"infraExcluded": True}, excluded=True),
        ),
    )
    assert report.pass_rate == 1.0
    assert report.passed is True
    assert report.to_json()["excludedCount"] == 1


def test_report_of_only_excluded_cases_is_not_a_pass() -> None:
    report = EvalReport(
        "s",
        "capability",
        "sha256:abc",
        (EvalCaseResult("web", False, "infra-excluded", {}, excluded=True),),
    )
    assert report.pass_rate == 0.0
    assert report.passed is False
    assert report.to_json()["excludedCount"] == 1


def test_manifest_is_discovered() -> None:
    found = next((m for m in load_manifests() if m.name == eh.NAME), None)
    assert found is not None, "eval_harness extension not discovered — run `uv sync`"
    assert found.version == eh.VERSION
