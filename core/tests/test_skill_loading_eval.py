"""The skill_loading verdict is a pure decision over observed mounts and turn status; the watcher
derives its watched path from the same runtime constants `mount_skill` writes through; and every
catalog case must name a skill the assistant pack carries — a typo'd or uncarried `expected` would
silently exclude forever, and forbidding a child's own parent would fail every correct load."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from evals.harness.mounts import MountObservation
from evals.skill_loading.catalog import CASES
from evals.skill_loading.member import CASES as MEMBER_CASES
from evals.skill_loading.runner import (
    SkillLoadCase,
    SkillLoadingSuite,
    skill_load_verdict,
    skill_loading_task,
)
from ufo.agent_setup import SETUP_SKILL_NAME
from ufo.ext.context import Trajectory
from ufo.ext.loader import load_manifests, skill_registry
from ufo.governance import prompt_digest
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

CASE = SkillLoadCase(
    "workbook", "Build a workbook.", expected="office-xlsx", forbidden=("office-pptx",)
)


def _observed(
    mounted: tuple[str, ...] = (), status: TurnStatus | None = "running"
) -> MountObservation:
    return MountObservation(
        mounted=mounted, status=status, cancelled=status == "running", elapsed_seconds=3.0
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


def test_task_pins_the_cases() -> None:
    task = skill_loading_task(CASES)
    assert task.name == task.suite == "skill_loading"
    assert task.cases == tuple(case.name for case in CASES)
    assert task.digest == skill_loading_task(CASES).digest


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


def test_every_catalog_skill_is_loadable_by_the_target() -> None:
    """Every skill a case names is one the target actually carries — a case naming a skill the pack
    does not ship can only ever fail. A case that expects no load names none, and is held to its
    forbidden set alone."""
    carried = set(skill_registry(load_manifests("assistant")).by_name) | {SETUP_SKILL_NAME}
    for case in CASES:
        if not case.expects_no_load:
            assert case.expected in carried, (case.name, case.expected)
        missing = [name for name in case.forbidden if name not in carried]
        assert not missing, (case.name, missing)


def test_no_case_forbids_its_expected_or_its_parent() -> None:
    for case in CASES:
        assert case.expected not in case.forbidden, case.name
        parent, _, _ = case.expected.rpartition("/")
        if parent:
            assert parent not in case.forbidden, case.name
