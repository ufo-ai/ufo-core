import yaml

from evals.harness.capability import CapabilityCase, CapabilityOutput, ToolInvocation
from evals.suites import surface_setup

COLD = {
    "slack-workspace-install": ("slack", "slack_connect"),
    "slack-own-app-manifest": ("slack", "slack_app_manifest"),
    "slack-channel-lookup": ("slack", "slack_channels"),
    "imessage-phone": ("imessage", "imessage_connect"),
}
WARM = {f"{name}-known": pair for name, pair in COLD.items()}
NEGATIVE = {"slack-status-question", "slack-personal-account", "imessage-line-question"}


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
