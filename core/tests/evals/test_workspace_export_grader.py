import pytest

from evals.harness.capability import CapabilityOutput, SharedArtifactReference, ToolInvocation
from evals.suites.workspace_export import export_requested, private_delivery


@pytest.mark.parametrize(
    "has_result,is_error,passed", [(True, False, True), (False, False, False), (True, True, False)]
)
async def test_export_routing_grades_the_request_without_waiting_for_an_attachment(
    has_result: bool,
    is_error: bool,
    passed: bool,
) -> None:
    output = CapabilityOutput(
        "Export requested.",
        (
            ToolInvocation(
                "object_action",
                {"kind": "workspace", "action": "export", "input": {}},
                has_result=has_result,
                is_error=is_error,
            ),
        ),
    )
    assert (await export_requested(output)).passed is passed
    assert (await private_delivery(output)).passed is not passed


async def test_a_file_without_the_export_action_does_not_count_as_an_export() -> None:
    output = CapabilityOutput(
        "Export ready.",
        (),
        artifact_references=(
            SharedArtifactReference("workspace-export.tar", "artifacts/file", "sha256:abc", 1024),
        ),
    )
    assert not (await export_requested(output)).passed
    assert not (await private_delivery(output)).passed
