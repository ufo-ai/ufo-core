from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.sandbox_cli import CASES, sandbox_cli_scorer


def _call(
    name: str,
    command: str = "",
    *,
    succeeded: bool = True,
    result: str | None = None,
) -> ToolInvocation:
    return ToolInvocation(
        name,
        {"command": command} if command else {},
        result=result or ("done" if succeeded else "failed"),
        has_result=True,
        is_error=not succeeded,
    )


async def test_ufo_llm_scorer_accepts_success_or_a_provider_error() -> None:
    grader = sandbox_cli_scorer("llm")
    assert (
        await grader(
            CapabilityOutput(
                "SECOND MODEL READY",
                (_call("bash", "ufo llm 'Reply with SECOND MODEL READY'"),),
            )
        )
    ).passed
    assert (
        await grader(
            CapabilityOutput(
                "The provider rejected the key.",
                (
                    _call(
                        "bash",
                        "ufo llm 'Reply with SECOND MODEL READY'",
                        succeeded=False,
                        result="ufo llm: provider returned 401",
                    ),
                ),
            )
        )
    ).passed
    assert not (
        await grader(
            CapabilityOutput(
                "SECOND MODEL READY",
                (_call("bash", "printf 'SECOND MODEL READY'"),),
            )
        )
    ).passed


async def test_ufo_tool_scorer_requires_list_describe_and_json_call_without_direct_tool() -> None:
    grader = sandbox_cli_scorer("tool")
    valid = CapabilityOutput(
        "2",
        (
            _call("bash", "ufo tool --list"),
            _call("bash", "ufo tool object_list --describe"),
            _call("bash", 'printf \'{"kind":"agent"}\' | ufo tool object_list'),
        ),
    )
    assert (await grader(valid)).passed
    described_then_failed = CapabilityOutput(
        "2",
        (
            _call("bash", "ufo tool --list"),
            _call(
                "bash",
                "ufo tool object_list --describe && ufo tool object_list",
                succeeded=False,
                result='{"input_schema":{}}\n{"ok":false}',
            ),
            _call("bash", 'printf \'{"kind":"agent"}\' | ufo tool object_list'),
        ),
    )
    assert (await grader(described_then_failed)).passed
    assert not (await grader(CapabilityOutput(valid.response, valid.calls[1:]))).passed
    assert not (
        await grader(CapabilityOutput(valid.response, (*valid.calls, _call("object_list"))))
    ).passed


def test_sandbox_cli_cases_cover_both_commands() -> None:
    assert [case.name for case in CASES] == ["sandbox-ufo-llm", "sandbox-ufo-tool"]
