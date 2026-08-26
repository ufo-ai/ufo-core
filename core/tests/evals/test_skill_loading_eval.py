"""The skill_loading verdict is a pure decision over observed mounts and turn status; the watcher
derives its watched path from the same runtime constants `install_skill` writes through; and every
catalog case must name a skill the assistant pack carries — a typo'd or uncarried `expected` would
silently exclude forever, and forbidding a child's own parent would fail every correct load."""

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from evals.harness.mounts import MountObservation, _observed_skills, never_started
from evals.harness.registry import narrowed_tasks
from evals.harness.timing import TurnStep
from evals.registry import TASKS
from evals.skill_loading import runner
from evals.skill_loading.catalog import CASES, SKILL_LOADING_PACKS
from evals.skill_loading.member import CASES as MEMBER_CASES
from evals.skill_loading.runner import (
    SUITE,
    SkillLoadCase,
    SkillLoadingSuite,
    skill_load_verdict,
    skill_loading_task,
)
from ufo.ext.context import Trajectory
from ufo.ext.loader import load_manifests, skill_registry
from ufo.kinds.agent_setup import SETUP_SKILL_NAME
from ufo.kinds.governance import prompt_digest
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

CASE = SkillLoadCase(
    "workbook", "Build a workbook.", expected="office-xlsx", forbidden=("office-pptx",)
)


def _observed(
    mounted: tuple[str, ...] = (),
    status: TurnStatus | None = "running",
    charged_seconds: float = 3.0,
    startup_seconds: float | None = 2.0,
) -> MountObservation:
    return MountObservation(
        mounted=mounted,
        status=status,
        cancelled=status == "running",
        elapsed_seconds=charged_seconds + (startup_seconds or 0.0),
        charged_seconds=charged_seconds,
        startup_seconds=startup_seconds,
    )


def test_grading_states_the_criteria() -> None:
    plain = SkillLoadCase("plain", "Fix the PDF.", expected="pdf")
    child = SkillLoadCase(
        "site",
        "Build a site.",
        expected="website-building",
        forbidden=("website-building/webapp",),
    )
    assert plain.grading == "skill 'pdf' mounts within 120s"
    assert CASE.grading == "skill 'office-xlsx' mounts within 120s, never 'office-pptx' without it"
    assert child.grading == (
        "skill 'website-building' mounts within 120s, never 'website-building/webapp' "
        "without it (a forbidden child of it, never at all)"
    )


def test_completed_skill_result_names_the_loaded_closure() -> None:
    result = ToolResultBlock(
        tool_use_id="call",
        content="# Skill: website-building/webapp\n\nbody\n\n# Skill: website-building\n",
    )
    step = TurnStep(
        function_name="engine._dispatch_step",
        completed_at_epoch_ms=2,
        messages=(Message(role="user", content=(result,)),),
    )

    assert _observed_skills((((step,), None),)) == (
        "website-building",
        "website-building/webapp",
    )


def test_child_loads_and_started_preloads_are_observed() -> None:
    loaded = ToolResultBlock(tool_use_id="call", content="# Skill: child-loaded\n")
    step = TurnStep(
        function_name="engine._dispatch_step",
        completed_at_epoch_ms=2,
        messages=(Message(role="user", content=(loaded,)),),
    )
    inbound = json.dumps({"preload_skills": ["child-preloaded"]})

    assert _observed_skills((((step,), inbound),)) == (
        "child-loaded",
        "child-preloaded",
    )
    assert _observed_skills((((), inbound),)) == ()


def test_expected_mount_passes() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(mounted=("office-xlsx",)))
    assert passed
    assert "office-xlsx" in reason


def test_forbidden_mount_fails() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(mounted=("office-pptx",)))
    assert not passed
    assert "forbidden" in reason


def test_forbidden_alongside_expected_passes() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(mounted=("office-xlsx", "office-pptx")))
    assert passed
    assert "alongside" in reason


def test_forbidden_child_alongside_its_expected_parent_fails() -> None:
    case = SkillLoadCase(
        "site",
        "Build a site.",
        expected="website-building",
        forbidden=("website-building/webapp",),
    )
    passed, reason = skill_load_verdict(
        case, _observed(mounted=("website-building", "website-building/webapp"))
    )
    assert not passed
    assert "child" in reason


NO_LOAD_CASE = SkillLoadCase(
    "no-load",
    "What time is it in Lisbon?",
    forbidden=("office-pptx", "office-xlsx"),
    expects_no_load=True,
)


def test_no_load_case_fails_immediately_on_a_forbidden_mount() -> None:
    passed, reason = skill_load_verdict(
        NO_LOAD_CASE, _observed(mounted=("office-pptx",), status="running")
    )
    assert not passed
    assert "office-pptx" in reason
    assert "no skill load was warranted" in reason


def test_no_load_case_passes_at_its_own_terminal_without_a_mount() -> None:
    passed, reason = skill_load_verdict(NO_LOAD_CASE, _observed(status="done"))
    assert passed
    assert "without mounting" in reason


def test_no_load_case_still_running_at_the_deadline_fails() -> None:
    passed, reason = skill_load_verdict(NO_LOAD_CASE, _observed(status="running"))
    assert not passed
    assert "still running at the deadline" in reason


def test_terminal_without_mount_fails() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(status="done"))
    assert not passed
    assert "ended done" in reason


def test_deadline_without_mount_fails() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(status="running"))
    assert not passed
    assert "did not load" in reason


def test_queue_and_sandbox_boot_do_not_count_against_the_load_deadline() -> None:
    """The measured artifact: three cases failed "did not load within 120s (status running)" while
    the 120s covered queue wait and sandbox boot. The deadline now runs on the turn's own work, and
    the verdict reports that clock."""
    passed, reason = skill_load_verdict(
        CASE, _observed(mounted=("office-xlsx",), charged_seconds=4.0, startup_seconds=396.0)
    )

    assert passed
    assert "after 4.0s" in reason


def test_a_turn_that_never_began_its_own_work_is_the_rigs_fault() -> None:
    observation = _observed(status="queued", charged_seconds=0.0, startup_seconds=None)

    passed, reason = skill_load_verdict(CASE, observation)

    assert not passed
    assert never_started(observation)
    assert "had not begun its own work" in reason
    assert "never started" in reason


def test_a_turn_that_reached_its_own_terminal_is_never_the_rigs_fault() -> None:
    assert not never_started(_observed(status="cancelled", startup_seconds=None))
    assert not never_started(_observed(status="done", startup_seconds=None))
    assert not never_started(_observed(status="running"))


def test_task_pins_the_cases() -> None:
    task = skill_loading_task(CASES)
    assert task.name == task.suite == "skill_loading"
    assert task.cases == tuple(case.name for case in CASES)
    assert task.digest == skill_loading_task(CASES).digest


def test_the_task_narrows_to_the_named_cases() -> None:
    """`--case` and an ablation's `cases` measure a handful of cases instead of all of them. The
    narrowed task keeps its name and pack binding, and its digest covers only the kept subset."""
    task = next(item for item in TASKS if item.name == "skill_loading")
    kept = (CASES[3].name, CASES[0].name)

    narrowed = narrowed_tasks((task,), kept)

    assert len(narrowed) == 1
    assert narrowed[0].name == "skill_loading"
    assert narrowed[0].suite == SUITE
    assert narrowed[0].packs == SKILL_LOADING_PACKS
    assert narrowed[0].cases == (CASES[0].name, CASES[3].name)
    assert narrowed[0].digest != task.digest


def test_narrowing_drops_the_exclusivity_the_kept_cases_do_not_need() -> None:
    """A seeded corpus is agent-global, so a task carrying a seeding case runs its cases one at a
    time. A narrowing that keeps no seeding case runs concurrently again."""
    task = skill_loading_task((CASES[0], MEMBER_CASES[0]), name="skill_loading_mixed")

    assert task.exclusive
    assert not narrowed_tasks((task,), (CASES[0].name,))[0].exclusive
    assert narrowed_tasks((task,), (MEMBER_CASES[0].name,))[0].exclusive


def test_both_skill_loading_tasks_register_under_the_suite_label_the_setup_keys_on() -> None:
    """The measured shard-4 abort: the member task's suite label was its own name, the run built the
    loadable-skill set only for suite `skill_loading`, and the suite raised on its first line."""
    pack_task = next(task for task in TASKS if task.name == "skill_loading")
    member_task = next(task for task in TASKS if task.name == "skill_loading_member")

    assert pack_task.suite == SUITE
    assert member_task.suite == SUITE
    assert member_task.name != member_task.suite


async def test_a_member_only_shard_reports_under_its_own_task_name(monkeypatch) -> None:
    """A shard that carries only the member task must run, and its report must name that task — both
    reports naming `skill_loading` would collide in the archive."""
    monkeypatch.setattr(
        runner,
        "load_config",
        lambda: SimpleNamespace(skills=SimpleNamespace(member_block=True)),
    )
    suite = SkillLoadingSuite(cases=(), digest="sha256:test", name="skill_loading_member")
    target = SimpleNamespace(loadable_skills=frozenset({"office-xlsx"}))

    report = await suite.run(target, asyncio.Semaphore(1))

    assert report.name == "skill_loading_member"
    assert report.suite == SUITE


async def test_a_suite_without_the_loadable_skill_set_raises_by_its_own_name() -> None:
    suite = SkillLoadingSuite(cases=(), digest="sha256:test", name="skill_loading_member")

    with pytest.raises(RuntimeError, match="skill_loading_member suite requires"):
        await suite.run(SimpleNamespace(loadable_skills=None), asyncio.Semaphore(1))


def test_payload_pins_staged_workspace_files() -> None:
    staged = next(case for case in CASES if case.workspace_files)
    payload = staged.payload()
    entries = payload["workspaceFiles"]
    assert isinstance(entries, list)
    assert [entry["path"] for entry in entries] == [item.path for item in staged.workspace_files]
    assert all(len(entry["sha256"]) == 64 for entry in entries)


@dataclass(frozen=True)
class _TerminalOutcome:
    trajectory: Trajectory | None

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        return self.trajectory

    async def cancel(self, turn_id: UUID) -> bool:
        return False


@dataclass(frozen=True)
class _AttemptTarget:
    outcome: _TerminalOutcome


def _trajectory(conversation_id: UUID) -> Trajectory:
    messages = (
        Message(role="user", content="build the workbook"),
        Message(
            role="assistant",
            content=(
                TextBlock(text="On it."),
                ToolUseBlock(id="tool-1", name="bash", input={"command": "ls"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="tool-1", content="data.csv"),),
        ),
        Message(role="assistant", content="Done: totals verified."),
    )
    return Trajectory(
        conversation_id=conversation_id,
        agent_id=uuid4(),
        agent_prompt="prompt",
        agent_prompt_digest=prompt_digest("prompt"),
        messages=messages,
    )


async def test_attempt_records_a_self_terminal_turns_trajectory() -> None:
    suite = SkillLoadingSuite(cases=(CASE,), digest="sha256:test")
    conversation_id = uuid4()
    target = _AttemptTarget(_TerminalOutcome(_trajectory(conversation_id)))

    attempt = await suite._attempt(
        False,
        "turn ended done without loading 'office-xlsx'",
        _observed(status="done"),
        target,
        conversation_id,
        uuid4(),
    )

    assert attempt["response"] == "Done: totals verified."
    assert attempt["calls"] == [
        {
            "name": "bash",
            "input": {"command": "ls"},
            "result": "data.csv",
            "hasResult": True,
            "isError": False,
        }
    ]
    trajectory = attempt["trajectory"]
    assert isinstance(trajectory, dict)
    assert trajectory["status"] == "done"
    assert len(trajectory["messages"]) == 4


async def test_attempt_for_a_cancelled_turn_carries_no_trajectory() -> None:
    suite = SkillLoadingSuite(cases=(CASE,), digest="sha256:test")
    target = _AttemptTarget(_TerminalOutcome(None))

    attempt = await suite._attempt(
        True, "mounted", _observed(mounted=("office-xlsx",)), target, uuid4(), uuid4()
    )

    assert attempt == {"passed": True, "reason": "mounted", "response": "", "calls": []}


def test_catalog_names_are_unique() -> None:
    names = [case.name for case in (*CASES, *MEMBER_CASES)]
    assert len(names) == len(set(names))


def test_every_catalog_skill_is_loadable_under_the_pack_the_suite_runs_on() -> None:
    """Every skill a case names is one the pack this suite is bound to actually carries — a case
    naming a skill that pack does not ship can only ever self-exclude. Held against the bound pack,
    not against `assistant`: `first-run` is a pack-level skill, and asserting it against a pack the
    suite never ran on is what let five cases exclude themselves nightly. A case that expects no
    load names none, and is held to its forbidden set alone."""
    for pack in SKILL_LOADING_PACKS:
        carried = set(skill_registry(load_manifests(pack)).by_name) | {SETUP_SKILL_NAME}
        for case in CASES:
            if not case.expects_no_load:
                assert case.expected in carried, (pack, case.name, case.expected)
            missing = [name for name in case.forbidden if name not in carried]
            assert not missing, (pack, case.name, missing)


def test_the_suite_is_bound_to_a_pack_that_carries_the_pack_level_skills() -> None:
    """The binding itself: the registered task names the pack, so a run under any other pack fails
    at argument parsing instead of excluding cases one at a time."""
    task = next(item for item in TASKS if item.name == "skill_loading")

    assert task.packs == SKILL_LOADING_PACKS
    assert "assistant_eval" not in task.packs


def test_no_case_forbids_its_expected_or_its_parent() -> None:
    for case in CASES:
        assert case.expected not in case.forbidden, case.name
        parent, _, _ = case.expected.rpartition("/")
        if parent:
            assert parent not in case.forbidden, case.name
