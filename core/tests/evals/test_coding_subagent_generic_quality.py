import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    annotation_state_scorer,
    middleware_override_scorer,
    precedence_scorer,
    pyreverse_consumer_scorer,
)


def output_for(result: str, calls: tuple[ToolInvocation, ...] = ()) -> CapabilityOutput:
    return CapabilityOutput(result, calls)


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "MiddlewareMixin.__init__ owns _async_check. Update every built-in __init__ override, "
            "including CacheMiddleware, FetchFromCacheMiddleware, and SecurityMiddleware, to "
            "call super().__init__(get_response). Test all built-in constructors with both sync "
            "and async get_response. BaseHandler is not the repair location.",
            True,
        ),
        (
            "MiddlewareMixin constructor owns async classification. Every subclass override, "
            "including CacheMiddleware, FetchFromCacheMiddleware, and SecurityMiddleware, must "
            "call super().__init__(get_response). Test both directions. BaseHandler is not the "
            "fix.",
            True,
        ),
        (
            "Keep the BaseHandler adaptation and add one CacheMiddleware test.",
            False,
        ),
        (
            "Call super().__init__(get_response) in CacheMiddleware only and test async behavior.",
            False,
        ),
    ),
)
async def test_middleware_contract_requires_every_bypassing_override(
    result: str, passed: bool
) -> None:
    verdict = await middleware_override_scorer(repository_wide=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Change CustomAuditMiddleware to call super().__init__(get_response). Add a direct "
            "unit test with async get_response and assert it is a coroutine. Do not touch "
            "BaseHandler or any built-in middleware.",
            True,
        ),
        (
            "Update CustomAuditMiddleware and every built-in CacheMiddleware and "
            "SecurityMiddleware constructor plus BaseHandler.",
            False,
        ),
    ),
)
async def test_middleware_contract_neighbor_stays_local(result: str, passed: bool) -> None:
    verdict = await middleware_override_scorer(repository_wide=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Use annotation_select as the active source of truth. self.annotations is the broad "
            "registered alias registry, so chapter_count must not force wrapping. Assert a single "
            "SELECT with no subquery.",
            True,
        ),
        (
            "Authoritative state: annotation_select. Rejected state source: self.annotations. "
            "The registered-only alias must not force a subquery; assert one SELECT.",
            True,
        ),
        ("Keep scanning self.annotations and remove chapter_count after compilation.", False),
    ),
)
async def test_annotation_state_uses_the_active_selection(result: str, passed: bool) -> None:
    verdict = await annotation_state_scorer(referenced=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "The HAVING filter references chapter_count. Retain the aggregate and its subquery "
            "through the dependency closure even though it is not selected.",
            True,
        ),
        ("Prune every unselected chapter_count alias before the filter is compiled.", False),
    ),
)
async def test_annotation_state_neighbor_retains_referenced_aliases(
    result: str, passed: bool
) -> None:
    verdict = await annotation_state_scorer(referenced=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Move the helpers to pylint.pyreverse.utils, use them from Linker in inspector and "
            "from the DOT writer. Render argument and return type annotations. Add direct helper "
            "tests and a writer test against generated DOT output. The inspector-only patch is "
            "incomplete.",
            True,
        ),
        (
            "Export the helpers from pylint.pyreverse.utils. Linker produces parameter and return "
            "annotations, and the DOT writer consumes them. An implementation only in "
            "inspector.py fails the public boundary. Generate DOT and assert both annotations.",
            True,
        ),
        ("Keep both helpers in inspector.py and add Linker unit tests.", False),
    ),
)
async def test_pyreverse_boundary_requires_the_output_consumer(result: str, passed: bool) -> None:
    verdict = await pyreverse_consumer_scorer(public_output=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Keep the private Linker branch in inspector.py and add a direct inspector unit test. "
            "Do not alter writer output. Do not create a public helper.",
            True,
        ),
        (
            "Keep the private Linker branch in inspector.py and add a direct unit test. Writer "
            "output is unchanged, with no public utility helper.",
            True,
        ),
        (
            "Create a public helper in pyreverse.utils and update writer DOT rendering.",
            False,
        ),
    ),
)
async def test_pyreverse_boundary_neighbor_stays_private(result: str, passed: bool) -> None:
    verdict = await pyreverse_consumer_scorer(public_output=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Walk obj.__mro__ without reversed and read direct pytestmark from each __dict__, so "
            "the order is c, a, b. store_mark reads direct marks with consider_mro=False. Test "
            "local plus both base marks and the local-only path.",
            True,
        ),
        (
            "Walk obj.__mro__ and drop the reversal. Read direct pytestmark from __dict__ for "
            "c, a, b. store_mark uses consider_mro=False. Add a regression test.",
            True,
        ),
        (
            "Walk reversed(C.__mro__) and read each direct pytestmark, producing b, a, c.",
            False,
        ),
    ),
)
async def test_mro_precedence_follows_python_lookup_order(result: str, passed: bool) -> None:
    verdict = await precedence_scorer(python_mro=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Use reversed MRO for Base, Left, Child. Later derived values override earlier base "
            "values. Add a regression test for the documented base-first merge. Reject Python "
            "lookup precedence in Child, Left, Base order.",
            True,
        ),
        (
            "Use Python lookup precedence: Child, Left, Base, and add a regression test.",
            False,
        ),
    ),
)
async def test_precedence_neighbor_preserves_an_explicit_base_first_contract(
    result: str, passed: bool
) -> None:
    verdict = await precedence_scorer(python_mro=False)(output_for(result))

    assert verdict.passed is passed


async def test_generic_quality_scorers_reject_tool_use() -> None:
    output = output_for(
        "Use annotation_select instead of self.annotations and assert one SELECT.",
        (ToolInvocation("bash", {}),),
    )

    verdict = await annotation_state_scorer(referenced=False)(output)

    assert not verdict.passed
    assert verdict.reason == "used tools at the isolated decision step"
