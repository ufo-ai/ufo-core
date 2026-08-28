import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    annotation_state_scorer,
    derived_state_scorer,
    discovery_boundary_scorer,
    middleware_override_scorer,
    precedence_scorer,
    pyreverse_consumer_scorer,
    transform_state_scorer,
    validation_scope_scorer,
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
            "Keep the private Linker branch in inspector.py and add a direct regression test. "
            "Writer output stays unchanged. Add no new module-level helper.",
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
            "autoclass :members: discovery is the first wrong boundary. Pass self.object and "
            "membername to getdoc. Teach util.inspect.isclassmethod and getdoc to search each "
            "class __dict__ through getmro and unwrap the classmethod __func__. Keep "
            "PropertyDocumenter as the renderer. Its direct-only patch is incomplete. Test that "
            "autoclass includes prop2 and that direct autoproperty still renders it.",
            True,
        ),
        (
            "PropertyDocumenter already renders prop2 when invoked directly, so only add another "
            "direct autoproperty test.",
            False,
        ),
        (
            "Rejected partial boundary: do not add specialized unwrap or extra compensation in "
            "PropertyDocumenter. Add focused public regression coverage in "
            "tests/test_ext_autodoc_autoclass.py, all through public entry points. Use "
            "do_autodoc(..., 'class', ...) and assert prop2 output. Pass self.object and "
            "membername to getdoc, search each class __dict__ through getmro, unwrap __func__, "
            "and keep PropertyDocumenter as the render consumer.",
            True,
        ),
        (
            "Autoclass member discovery is the public collection boundary. Pass self.object and "
            "membername to getdoc, which walks the MRO and unwraps __func__. Keep "
            "PropertyDocumenter as the renderer and reject its direct-only patch. Add focused "
            "public regression coverage that autoclass includes the property.",
            True,
        ),
    ),
)
async def test_discovery_boundary_reaches_the_public_collection(result: str, passed: bool) -> None:
    verdict = await discovery_boundary_scorer(public_discovery=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Keep the proven autoclass discovery path unchanged. In PropertyDocumenter, add the "
            ":classmethod: option from add_directive_header and test direct autoproperty output.",
            True,
        ),
        (
            "Change getdoc and getmro discovery even though autoclass membership already passes.",
            False,
        ),
        (
            "Keep discovery unchanged. In PropertyDocumenter.add_directive_header, add the "
            ":classmethod: option. Add a direct regression test in tests/test_ext_autodoc.py "
            "with do_autodoc(app, 'property', ...) and assert the rendered header.",
            True,
        ),
    ),
)
async def test_discovery_boundary_neighbor_stays_at_the_direct_renderer(
    result: str, passed: bool
) -> None:
    verdict = await discovery_boundary_scorer(public_discovery=False)(output_for(result))

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
            "Walk C.__mro__ forward from most specific to least specific. Read each class's "
            "direct pytestmark from __dict__, which gives c, a, b. In store_mark, copy only the "
            "decorated class's directly declared pytestmark from __dict__. Add a regression test.",
            True,
        ),
        (
            "Traverse obj.__mro__ from the subclass through its bases. Read direct pytestmark "
            "from each __dict__ for c, a, b. store_mark copies only direct marks from the "
            "target class's __dict__. Add a regression test.",
            True,
        ),
        (
            "Walk C.__mro__ from most specific to least specific and read each class's direct "
            "pytestmark from __dict__, preserving c, a, b. Make store_mark copy and update only "
            "obj.__dict__.get('pytestmark', []). Add a regression test.",
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


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Add an EarthLocationAttribute as ITRS.location. observed_to_itrs produces it, and "
            "both directions of the CIRS and TETE transform edges carry it to and from ITRS. "
            "Add public round-trip tests at the same and different locations and different "
            "obstimes.",
            True,
        ),
        (
            "Keep location local to observed_to_itrs and test the direct AltAz conversion.",
            False,
        ),
        (
            "Add EarthLocationAttribute as ITRS.location. observed_to_itrs produces it, and both "
            "directions of the CIRS and TETE transform edges carry it to and from ITRS. Add a "
            "public round trip where location A is observed at location B, covering the "
            "location-to-location branch, and another with a different obstime.",
            True,
        ),
    ),
)
async def test_stateful_transform_requires_an_owner_and_every_consumer(
    result: str, passed: bool
) -> None:
    verdict = await transform_state_scorer(stateful=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Keep Pixel and Normalized stateless with no frame attributes. Change only the two "
            "local conversion functions and add a Pixel-to-Normalized-to-Pixel round-trip test.",
            True,
        ),
        (
            "Add location and obstime to Pixel, then edit every transform.",
            False,
        ),
    ),
)
async def test_stateless_transform_neighbor_stays_local(result: str, passed: bool) -> None:
    verdict = await transform_state_scorer(stateful=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "_replace is the first wrong operation because it preserves stale _dims. Recompute "
            "dimensions from the remaining variables with _replace_with_new_dims. Test the sole "
            "coordinate as the last owner: the empty result has no dimensions and drops x.",
            True,
        ),
        (
            "Keep _replace and add an assertion to DataVariables.__len__.",
            False,
        ),
        (
            "_replace preserves stale _dims. Reconstruct dimensions from the remaining variables "
            "with _replace_with_new_dims. Test removing the sole coordinate as the last owner: "
            "_dims == {}, ds.dims and ds.sizes are empty, and ds.identical(Dataset()).",
            True,
        ),
    ),
)
async def test_derived_state_recomputes_after_the_last_owner(result: str, passed: bool) -> None:
    verdict = await derived_state_scorer(surviving_owner=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Recompute dimensions from surviving variables. The value data variable still owns "
            "x, so keep x; when no variable references it, the last-owner neighbor removes it.",
            True,
        ),
        (
            "Recompute dimensions from surviving variables. The value data variable keeps x "
            "(size 2) because value still carries it, and drops x when no surviving variable "
            "carries it.",
            True,
        ),
        (
            "Recompute dimensions from surviving variables. The value data variable keeps x, "
            "while the last-owner neighbor removes x. A length clamp invents state and is not "
            "the repair.",
            True,
        ),
        ("Always delete x when reset_index drops its coordinate.", False),
    ),
)
async def test_derived_state_neighbor_retains_a_surviving_owner(result: str, passed: bool) -> None:
    verdict = await derived_state_scorer(surviving_owner=True)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "Run the new direct test for the infer_node TypeAlias rule and the nearest writer "
            "consumer regression. Stop there when both pass. Skip the full core suite. Skip the "
            "ten-file project-wide sweep.",
            True,
        ),
        (
            "Run the full core suite, then all ten inference test files to establish a baseline.",
            False,
        ),
        (
            "Run only the new direct test and ignore the nearest public consumer.",
            False,
        ),
        (
            "Validation boundary: focused tests only.\n\n"
            "Tests to run, and why each one:\n"
            "1. The new direct inference test for `infer_node()` on a `TypeAlias` expression — "
            "it is the only test that exercises the changed branch and proves the declared type "
            "replaces `None`.\n"
            "2. The nearest existing test file for the changed inference module, whole file — "
            "it holds the sibling expression cases that share the same code path, so it catches "
            "a regression in the `None` cases the change must keep.\n"
            "3. The one writer test that renders the inferred type — it is the nearest public "
            "consumer and proves the new value flows through the render output.\n\n"
            "Tests not to run: the full core suite (600 s) and the ten-file inference sweep "
            "(900 s). The change is one function; the sweep crosses unrelated packages and "
            "returns no signal about it. Do not baseline-compare the whole suite.\n\n"
            "Stop condition: items 1 to 3 pass. Then write the requested patch and stop. If a "
            "focused test is blocked by a missing test or build dependency, make one repair "
            "attempt; if it still blocks, probe the behavior at source or runtime level without "
            "the dependency, record the blocker in the result, and finish.",
            True,
        ),
        (
            "Validation boundary: focused only. Run the direct inference test for `infer_node()` "
            "on a `TypeAlias`, the nearest inference test file, and the nearest public consumer "
            "writer test. Stop condition: those tests pass. The full core suite and ten-file "
            "inference sweep are both out of scope. Do not widen to the core suite.",
            True,
        ),
        (
            "Run the direct inference test for `infer_node()` on a `TypeAlias` and the nearest "
            "public consumer. Stop condition: both pass. Do not run the full core suite or the "
            "ten-file inference sweep; both cross unrelated packages.",
            True,
        ),
        (
            "Run the direct inference test for `infer_node()` on a `TypeAlias` and the nearest "
            "public consumer. Stop condition: both pass. Skip both wide runs. The 600-second core "
            "suite and 900-second ten-file inference sweep add no coverage.",
            True,
        ),
        (
            "Run the direct inference test for `infer_node()` on a `TypeAlias` and the nearest "
            "public consumer. Stop condition: both pass. Skip the 900-second ten-file inference "
            "sweep and the 600-second core suite.",
            True,
        ),
    ),
)
async def test_local_validation_scope_stops_at_the_nearest_regression(
    result: str, passed: bool
) -> None:
    verdict = await validation_scope_scorer(cross_cutting=False)(output_for(result))

    assert verdict.passed is passed


@pytest.mark.parametrize(
    ("result", "passed"),
    (
        (
            "The shared InferenceResult protocol is a cross-cutting public contract used by all "
            "adapters. Add a focused contract regression, test every direct consumer, then run "
            "the full project-wide suite because the wire shape changes across all packages.",
            True,
        ),
        (
            "Run one local serializer unit test and stop; the shared protocol needs no consumer "
            "or integration coverage.",
            False,
        ),
        (
            "The shared protocol is public and serialized. Add a focused contract test and test "
            "every public writer, but do not run the full repository suite.",
            False,
        ),
        (
            "The shared protocol is a cross-package public and serialized contract. Add a "
            "focused contract test, cover every adapter and all direct callers, then run the "
            "full repository suite for indirect consumers.",
            True,
        ),
        (
            "The changed field is serialized and public across packages. Add a focused contract "
            "test, the test file for each analyzer adapter and each public writer, and the full "
            "repository suite for indirect consumers.",
            True,
        ),
        (
            "Validation boundary: the whole repository suite plus a repository-wide type check — "
            "not per-package focused runs. The changed field is public and serialized, so "
            "consumers can reach it through string keys, fixtures, and golden files that no "
            "import graph or grep shows.\n\n"
            "Tests to run, and why each one:\n"
            "- The focused contract test on the new serialized field — proves the wire shape the "
            "protocol now emits.\n"
            "- Every test file next to a changed implementation (each analyzer adapter, each "
            "public writer) — proves each implementation still satisfies the protocol.\n"
            "- Every test file next to a changed direct consumer — proves each reader parses the "
            "new field.\n"
            "- The full repository suite, once — catches indirect consumers: serialized fixtures, "
            "golden/snapshot files, and string-keyed field access.\n"
            "- The repository-wide type check — catches signature drift in packages with no test "
            "coverage.\n\n"
            "Stop condition: the focused contract test passes, and the full suite plus type check "
            "show no failure that is absent on the base commit. Record any failure that also "
            "reproduces on the base commit as pre-existing and stop; do not repair it in this "
            "patch. Make at most one attempt to install a missing test or build dependency; if it "
            "still blocks, record the blocker, probe the affected behavior directly at runtime, "
            "and stop.",
            True,
        ),
        (
            "Validation boundary: the full repository suite. The changed serialized field on a "
            "shared protocol crosses package lines, so indirect readers can break on its wire "
            "shape. Run the focused contract test, the test file nearest each analyzer adapter, "
            "and the test file nearest each public writer and each direct consumer touched by "
            "the patch, then the full repository suite. Stop when all pass.",
            True,
        ),
    ),
)
async def test_cross_cutting_validation_scope_covers_every_consumer(
    result: str, passed: bool
) -> None:
    verdict = await validation_scope_scorer(cross_cutting=True)(output_for(result))

    assert verdict.passed is passed


async def test_generic_quality_scorers_accept_observed_semantic_evidence() -> None:
    middleware = await middleware_override_scorer(repository_wide=True)(
        output_for(
            "MiddlewareMixin.__init__ owns _async_check. Sweep the remaining MiddlewareMixin "
            "subclasses, including CacheMiddleware, FetchFromCacheMiddleware, and "
            "SecurityMiddleware, so each calls super.__init__. The async cases must classify as "
            "coroutines and sync get_response must remain sync. BaseHandler is not the fix."
        )
    )
    delegated_middleware = await middleware_override_scorer(repository_wide=True)(
        output_for(
            "MiddlewareMixin.__init__ owns classification. Every override, including "
            "CacheMiddleware, FetchFromCacheMiddleware, and SecurityMiddleware, must call "
            "super.__init__. Without delegation, the instance is never marked as a coroutine. "
            "Test sync and async constructors. BaseHandler is not the fix."
        )
    )
    annotation = await annotation_state_scorer(referenced=False)(
        output_for(
            "Use annotation_select. self.annotations is the registered registry, so "
            "chapter_count does not force wrapping. The expected result is one statement."
        )
    )
    public_pyreverse = await pyreverse_consumer_scorer(public_output=True)(
        output_for(
            "Move the helpers to pylint.pyreverse.utils and keep Linker in inspector as the "
            "producer. The DOT writer renders each parameter and appends node.returns. Add a "
            "writer test. An inspector-only patch is incomplete."
        )
    )
    local_pyreverse = await pyreverse_consumer_scorer(public_output=False)(
        output_for(
            "Keep the private Linker branch in inspector.py. Add no helper and leave writer "
            "output unchanged. Add a direct regression test."
        )
    )
    last_owner = await derived_state_scorer(surviving_owner=False)(
        output_for(
            "_replace is wrong. Recompute dimensions from surviving variables. Test the last "
            "owner path and assert _dims = {}."
        )
    )
    surviving_owner = await derived_state_scorer(surviving_owner=True)(
        output_for(
            "The value variable is among the surviving variables, so x survives. With no "
            "surviving variable, x disappears."
        )
    )
    validation = await validation_scope_scorer(cross_cutting=False)(
        output_for(
            "Run a direct inference test for infer_node TypeAlias and the nearest writer "
            "consumer. Stop condition: both pass. Skip the 600-second core suite because the "
            "ten-file sweep crosses unrelated packages."
        )
    )
    direct_validation = await validation_scope_scorer(cross_cutting=False)(
        output_for(
            "Run a direct test for the infer_node TypeAlias inference rule and the nearest "
            "public consumer. Stop there when both pass. Do **not** run the 600-second core suite "
            "because it is unrelated. Do **not** run the 900-second ten-file inference sweep."
        )
    )
    stateless = await transform_state_scorer(stateful=False)(
        output_for(
            "Keep Pixel and Normalized local. No value needs an owner between calls. Add a "
            "focused round-trip test."
        )
    )

    assert all(
        verdict.passed
        for verdict in (
            middleware,
            delegated_middleware,
            annotation,
            public_pyreverse,
            local_pyreverse,
            last_owner,
            surviving_owner,
            validation,
            direct_validation,
            stateless,
        )
    )


async def test_generic_quality_scorers_reject_tool_use() -> None:
    output = output_for(
        "Use annotation_select instead of self.annotations and assert one SELECT.",
        (ToolInvocation("bash", {}),),
    )

    verdict = await annotation_state_scorer(referenced=False)(output)

    assert not verdict.passed
    assert verdict.reason == "used tools at the isolated decision step"
