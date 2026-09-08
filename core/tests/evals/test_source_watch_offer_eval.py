import yaml

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.source_watch_offer import (
    PR,
    SECOND_PR,
    watches,
    watches_each,
    watches_nothing,
)

WATCHES = watches(PR)
WATCHES_EACH = watches_each(PR, SECOND_PR)
WATCHES_NOTHING = watches_nothing()


def _apply(kind: str, spec: dict[str, str], *, landed: bool = True) -> ToolInvocation:
    manifest = yaml.safe_dump({"kind": kind, "name": "github-ee65f064-abc-1234abcd", "spec": spec})
    return ToolInvocation(
        name="object_apply",
        input={"manifest": manifest},
        result="created" if landed else "a source_trigger is named for the pair it is",
        has_result=True,
        is_error=not landed,
    )


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response="Watching it here.", calls=calls)


async def test_a_narrowed_apply_under_any_spelling_of_the_link_passes() -> None:
    spelled = "https://github.com/MetalCraftAI/ufo/pull/3112/files"
    verdict = await WATCHES(
        _output(_apply("source_trigger", {"source": "github-ee65f064", "resource": spelled}))
    )
    assert verdict.passed, verdict.reason


async def test_an_apply_that_did_not_land_fails() -> None:
    verdict = await WATCHES(
        _output(
            _apply("source_trigger", {"source": "github-ee65f064", "resource": PR}, landed=False)
        )
    )
    assert not verdict.passed
    assert "did not land" in verdict.reason


async def test_a_whole_source_trigger_is_not_the_watch_asked_for() -> None:
    whole = _output(_apply("source_trigger", {"source": "github-ee65f064"}))
    assert not (await WATCHES(whole)).passed
    restraint = await WATCHES_NOTHING(whole)
    assert not restraint.passed
    assert "the whole source" in restraint.reason


async def test_another_pull_request_is_not_this_one() -> None:
    other = "https://github.com/metalcraftai/ufo/pull/3113"
    verdict = await WATCHES(
        _output(_apply("source_trigger", {"source": "github-ee65f064", "resource": other}))
    )
    assert not verdict.passed


async def test_two_links_asked_for_need_a_landed_trigger_each() -> None:
    first = _apply("source_trigger", {"source": "github-ee65f064", "resource": PR})
    second = _apply("source_trigger", {"source": "github-ee65f064", "resource": SECOND_PR})
    one = await WATCHES_EACH(_output(first))
    assert not one.passed
    assert SECOND_PR in one.reason
    assert PR not in one.reason
    refused = _apply(
        "source_trigger", {"source": "github-ee65f064", "resource": SECOND_PR}, landed=False
    )
    assert not (await WATCHES_EACH(_output(first, refused))).passed
    both = await WATCHES_EACH(_output(first, second))
    assert both.passed
    assert both.evidence == {"explains": 0, "applies": 2}


async def test_a_verdict_carries_the_reads_the_agent_paid_first() -> None:
    explained = _output(
        ToolInvocation(name="object_explain", input={"kind": "source_trigger"}, has_result=True),
        _apply("source_trigger", {"source": "github-ee65f064", "resource": PR}),
    )
    verdict = await WATCHES(explained)
    assert verdict.passed
    assert verdict.evidence == {"explains": 1, "applies": 1}
    direct = await WATCHES(
        _output(_apply("source_trigger", {"source": "github-ee65f064", "resource": PR}))
    )
    assert direct.evidence == {"explains": 0, "applies": 1}
    assert (await WATCHES_NOTHING(_output())).evidence == {"explains": 0, "applies": 0}


async def test_applying_nothing_or_another_kind_is_restraint() -> None:
    assert (await WATCHES_NOTHING(_output())).passed
    other_kind = _output(
        _apply("source", {"provider": "github", "account_id": "metalcraftai"}),
        ToolInvocation(name="bash", input={"command": "cat pr.txt"}, has_result=True),
    )
    assert (await WATCHES_NOTHING(other_kind)).passed
    assert not (await WATCHES(other_kind)).passed
