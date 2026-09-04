import json

from ufo.runtime.tools.context import (
    FAILURE_APPLIED_MAX,
    FAILURE_PROVIDER_MAX_CHARS,
    FAILURE_STREAM_MAX_CHARS,
    NO_REASON_NOTICE,
)
from ufo.sdk.tools import AppliedEffect, CommandDiagnostics, ToolFailure


def _payload(failure: ToolFailure) -> dict[str, object]:
    result = failure.result()
    assert result.is_error is True
    return json.loads(result.content[0].text)


def test_a_failure_with_no_reason_says_so_rather_than_returning_an_empty_summary() -> None:
    for blank in ("", "   ", "\n\t"):
        assert _payload(ToolFailure(operation="op", summary=blank))["summary"] == NO_REASON_NOTICE
    assert _payload(ToolFailure(operation="op", summary=" a port is taken "))["summary"] == (
        "a port is taken"
    )


def test_a_failure_keeps_the_exit_code_both_streams_and_the_expiry_apart() -> None:
    """A command running `timeout` exits 124 exactly as a carrier-stopped one does, so the expiry
    cannot be read off the code — and a toolchain writes its reason to stdout as readily as to
    stderr, so neither stream stands in for the other."""
    payload = _payload(
        ToolFailure(
            operation="npm run build",
            summary="the build failed",
            command=CommandDiagnostics(
                exit_code=124,
                stdout="webpack said",
                stderr="and so did node",
                timed_out_after_s=600,
            ),
        )
    )
    assert payload["command"] == {
        "exit_code": 124,
        "stdout": "webpack said",
        "stderr": "and so did node",
        "timed_out_after_s": 600,
    }


def test_a_failure_carries_each_applied_effect_by_its_identity_and_state() -> None:
    payload = _payload(
        ToolFailure(
            operation="take_independent_steps",
            summary="step 3 would not start",
            applied=(
                AppliedEffect(kind="step", identity="turn-a", state="running: draft the copy"),
                AppliedEffect(kind="step", identity="turn-b", state="running: size the market"),
            ),
        )
    )
    assert payload["applied"] == [
        {"kind": "step", "identity": "turn-a", "state": "running: draft the copy"},
        {"kind": "step", "identity": "turn-b", "state": "running: size the market"},
    ]


def test_a_failure_clips_every_unbounded_field_rather_than_refusing_it() -> None:
    """A diagnostic that raises on its own size replaces the reason with a complaint about the
    reason, which is the failure the contract exists to prevent — so each field clips."""
    stream = "x" * (FAILURE_STREAM_MAX_CHARS + 500)
    provider = "y" * (FAILURE_PROVIDER_MAX_CHARS + 500)
    payload = _payload(
        ToolFailure(
            operation="op",
            summary="failed",
            applied=tuple(
                AppliedEffect(kind="k", identity=str(index), state="s")
                for index in range(FAILURE_APPLIED_MAX + 10)
            ),
            command=CommandDiagnostics(exit_code=1, stdout=stream, stderr=stream),
            provider=provider,
        )
    )
    command = payload["command"]
    assert isinstance(command, dict)
    for cut in (command["stdout"], command["stderr"]):
        assert isinstance(cut, str)
        assert cut.startswith("x" * FAILURE_STREAM_MAX_CHARS)
        assert cut.endswith(f"\n…[truncated 500 of {len(stream)} chars]")
    assert isinstance(payload["provider"], str)
    assert payload["provider"].endswith(f"\n…[truncated 500 of {len(provider)} chars]")
    assert isinstance(payload["applied"], list)
    assert len(payload["applied"]) == FAILURE_APPLIED_MAX


def test_a_failure_omits_the_diagnostics_it_does_not_have() -> None:
    payload = _payload(ToolFailure(operation="op", summary="failed"))
    assert payload == {"operation": "op", "summary": "failed", "applied": []}


def test_a_failure_from_a_third_party_is_walled_as_untrusted_when_the_caller_says_so() -> None:
    failure = ToolFailure(operation="server.tool", summary="refused")
    assert failure.result().untrusted is False
    assert failure.result(untrusted=True).untrusted is True
