"""The skill_loading verdict is a pure decision over observed mounts and turn status; the watched
mount key must agree with the layout `mount_skill` writes; and every catalog case must name a
skill the assistant pack carries — a typo'd or uncarried `expected` would silently exclude
forever, and forbidding a child's own parent would fail every correct load."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from evals.skill_loading.catalog import CASES
from evals.skill_loading.runner import (
    SkillLoadCase,
    SkillLoadingSuite,
    SkillLoadObservation,
    _mount_key,
    skill_load_verdict,
    skill_loading_task,
)
from ufo.ext.context import Trajectory
from ufo.ext.loader import load_manifests, skill_registry
from ufo.governance import prompt_digest
from ufo.sandbox.session import WORKSPACE_DIR
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.skills.runtime import CORE_SKILL_REGISTRY, SKILL_MD

CASE = SkillLoadCase(
    "workbook", "Build a workbook.", expected="office-xlsx", forbidden=("office-pptx",)
)


def _observed(
    mounted: tuple[str, ...] = (), status: TurnStatus | None = "running"
) -> SkillLoadObservation:
    return SkillLoadObservation(
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


def test_terminal_without_mount_fails() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(status="done"))
    assert not passed
    assert "ended done" in reason


def test_deadline_without_mount_fails() -> None:
    passed, reason = skill_load_verdict(CASE, _observed(status="running"))
    assert not passed
    assert "did not load" in reason


def test_mount_key_matches_the_runtime_mount_layout() -> None:
    skill = CORE_SKILL_REGISTRY.named("delegation")
    conversation_id = uuid4()
    mounted_path = f"{skill.mount_root()}/{SKILL_MD}".removeprefix(f"{WORKSPACE_DIR}/")
    assert (
        _mount_key(conversation_id, skill.name)
        == f"conversations/{conversation_id}/workspace/{mounted_path}"
    )


def test_child_skill_mount_key_nests_under_its_parent() -> None:
    conversation_id = uuid4()
    assert _mount_key(conversation_id, "website-building/webapp").endswith(
        ".skills/website-building/webapp/SKILL.md"
    )


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
    names = [case.name for case in CASES]
    assert len(names) == len(set(names))


def test_every_catalog_skill_is_assistant_carried() -> None:
    carried = set(skill_registry(load_manifests("assistant")).by_name)
    for case in CASES:
        assert case.expected in carried, (case.name, case.expected)
        missing = [name for name in case.forbidden if name not in carried]
        assert not missing, (case.name, missing)


def test_no_case_forbids_its_expected_or_its_parent() -> None:
    for case in CASES:
        assert case.expected not in case.forbidden, case.name
        parent, _, _ = case.expected.rpartition("/")
        if parent:
            assert parent not in case.forbidden, case.name
