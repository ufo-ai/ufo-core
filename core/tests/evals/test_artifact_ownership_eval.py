from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites import artifact_ownership


def _listing(filters: dict[str, object], *, succeeded: bool = True) -> ToolInvocation:
    return ToolInvocation(
        "object_list",
        {"kind": "artifact", "filters": filters},
        "{}",
        has_result=succeeded,
        is_error=not succeeded,
    )


async def test_the_artifact_listing_grader_requires_a_correct_result() -> None:
    valid = CapabilityOutput(
        artifact_ownership.MINE_FILENAME,
        (_listing({}),),
    )
    no_listing = CapabilityOutput(
        artifact_ownership.MINE_FILENAME,
        (),
    )
    leaked = CapabilityOutput(
        f"{artifact_ownership.MINE_FILENAME}\n{artifact_ownership.THEIRS_FILENAME}",
        (_listing({"mine": True}),),
    )

    assert (await artifact_ownership._graded_mine_listing(valid)).passed
    assert not (await artifact_ownership._graded_mine_listing(no_listing)).passed
    assert not (await artifact_ownership._graded_mine_listing(leaked)).passed


async def test_the_mine_meaning_grader_requires_artifact_definition_inspection() -> None:
    inspected = CapabilityOutput(
        "Files I shared.",
        (ToolInvocation("object_explain", {"kind": "artifact"}, "{}", has_result=True),),
    )
    skipped = CapabilityOutput("Files I shared.", ())

    assert (await artifact_ownership._graded_mine_meaning(inspected)).passed
    assert not (await artifact_ownership._graded_mine_meaning(skipped)).passed
