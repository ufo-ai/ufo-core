"""Coding-subagent cases grade delegation through the `coding` profile and isolated fan-out."""

import json
import re

from pydantic import TypeAdapter
from ufo_ext_coding.manifest import SKILLS_ROOT

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    UndeliveredRound,
    WorkspaceFile,
    grading_statement,
)
from evals.harness.scorers import combine, exact_scorer, lane_scorer, restraint_scorer
from ufo.skills.runtime import LoadedSkill, loaded_context, parse_skill

BACKGROUND_FLAG = TypeAdapter(bool)
GITHUB_APP_API_COMMAND = 'GH_TOKEN="$UFO_GITHUB_API_AUTH" gh api'
ROOT_LAYER = re.compile(r"(?im)^\s*ROOT_LAYER\s*:\s*(.+?)\s*$")
REJECTED_LAYER = re.compile(r"(?im)^\s*REJECTED_LAYER\s*:\s*(.+?)\s*$")
SOURCE_FILES = re.compile(r"(?im)^\s*SOURCE_FILES\s*:\s*(.+?)\s*$")
GENERATED_FILES = re.compile(r"(?im)^\s*GENERATED_FILES\s*:\s*(.+?)\s*$")
REGENERATE_WITH = re.compile(r"(?im)^\s*REGENERATE_WITH\s*:\s*(.+?)\s*$")
REPOSITORY_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
TIMEOUT_ACTION = re.compile(
    r"\b(?:start|spawn|launch|create|run)\s+(?:a\s+)?"
    r"(?:duplicate|another|replacement)(?:\s+(?:worker|child|spawn))?\b|"
    r"\b(?:start|spawn|launch|create|run)\s+(?:the\s+)?task\s+(?:again|fresh)\b|"
    r"\b(?:inspect|list|check)\s+(?:(?:the|that|original)\s+)?"
    r"(?:worker|child|spawn|it)\b|"
    r"\bask\s+again\b|\bretry\b|\bpause(?:_and_wait|\s+and\s+wait)?\b"
)
TIMEOUT_ACTION_NEGATION = re.compile(
    r"(?:\b(?:do|will|would|should|must|can)\s+not\b|"
    r"\b(?:don't|won't|wouldn't|shouldn't|mustn't|can't|never|avoid|without)\b|"
    r"\bno need to\b)[^.;\n]{0,64}$"
)
GENERIC_ADD_LAYERS = (
    "matadd",
    "matexpr",
    "matrixbase._eval_matrix_mul",
    "matrix-expression add",
    "matrix expression add",
    "add constructor",
    "add postprocessor",
    "add post-processing",
    "add reduction",
    "add accumulation",
)
BLOCKMUL_LAYERS = (
    "blockmatrix._blockmul",
    "blockmatrix _blockmul",
    "_blockmul",
    "blockmatrix.rowblocksizes",
    "blockmatrix.colblocksizes",
    "blockmatrix.__new__",
    "blockmatrix normalization",
)
CODING_SKILL_DIR = SKILLS_ROOT / "coding"
CODING_SKILL_CONTEXT = loaded_context((LoadedSkill(parse_skill(CODING_SKILL_DIR)),))


def _recommended_timeout_actions(text: str) -> tuple[str, ...]:
    recommendations = []
    for action in TIMEOUT_ACTION.finditer(text):
        clause_start = max(text.rfind(mark, 0, action.start()) for mark in ".;\n")
        prefix = text[clause_start + 1 : action.start()]
        if TIMEOUT_ACTION_NEGATION.search(prefix) is None:
            recommendations.append(action.group())
    return tuple(recommendations)


def profile_proxy_message(objective: str) -> str:
    return (
        "Run exactly one foreground spawn with target `profile:coding`. Pass the text inside "
        "<objective> verbatim as `payload.objective`; do not solve, summarize, or modify it. "
        "After the spawn returns, reply with its `result` verbatim.\n\n"
        f"<objective>\n{objective}\n</objective>"
    )


def _same_transport_objective(expected: str, observed: str) -> bool:
    """Allow transport-only whitespace changes while preserving every non-whitespace token."""

    def normalized(value: str) -> str:
        return " ".join(value.replace(r"\t", "\t").split())

    return normalized(expected) == normalized(observed)


def profile_proxy_scorer(objective: str, grader: Grader) -> Grader:
    """Grade the validated result of one exact coding-profile spawn, never the proxy's answer."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawns = tuple(call for call in output.own_calls if call.name == "spawn")
        if len(spawns) != 1:
            return CapabilityVerdict(False, f"the proxy recorded {len(spawns)} spawns")
        if (
            not spawns[0].succeeded
            or spawns[0].input.get("target") != "profile:coding"
            or spawns[0].input.get("background", False) is not False
        ):
            return CapabilityVerdict(False, "the proxy did not run profile:coding in foreground")
        payload = spawns[0].input.get("payload")
        observed = payload.get("objective") if isinstance(payload, dict) else None
        if not isinstance(observed, str) or not _same_transport_objective(objective, observed):
            return CapabilityVerdict(False, "the proxy changed the coding objective")
        try:
            result = json.loads(spawns[0].result)
        except json.JSONDecodeError:
            return CapabilityVerdict(False, "the coding spawn returned malformed JSON")
        if not isinstance(result, dict) or not isinstance(response := result.get("result"), str):
            return CapabilityVerdict(False, "the coding spawn returned no result field")
        parent_calls = {call.call_id for call in output.own_calls if call.call_id}
        child_calls = tuple(call for call in output.calls if call.call_id not in parent_calls)
        return await grader(CapabilityOutput(response, child_calls, tool_errors=output.tool_errors))

    return DescribedGrader(
        f"one exact profile:coding spawn satisfies: {grading_statement(grader)}", grade
    )


def foreground_timeout_scorer() -> Grader:
    """A bare foreground timeout establishes neither cancellation nor result delivery."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        live_calls = tuple(
            call for call in output.own_calls if not call.call_id.startswith("undelivered-")
        )
        if live_calls:
            return CapabilityVerdict(False, "acted again after the ambiguous foreground timeout")
        text = output.response.casefold()
        running = any(
            phrase in text
            for phrase in (
                "may still be running",
                "might still be running",
                "could still be running",
                "does not prove it stopped",
                "doesn't prove it stopped",
                "does not mean it stopped",
                "doesn't mean it stopped",
            )
        )
        delivery = any(
            phrase in text
            for phrase in (
                "no guaranteed delivery",
                "not guaranteed to deliver",
                "delivery is not guaranteed",
                "won't automatically deliver",
                "will not automatically deliver",
                "no result delivery",
                "result is not guaranteed to return",
                "no guarantee its result will be delivered",
            )
        )
        recommended_actions = _recommended_timeout_actions(text)
        if running and delivery and not recommended_actions:
            return CapabilityVerdict(True, "reported both timeout uncertainties without acting")
        missing = []
        if not running:
            missing.append("the worker may still be running")
        if not delivery:
            missing.append("the bare timeout does not guarantee result delivery")
        if recommended_actions:
            missing.append(
                "restraint after the timeout: recommended " + ", ".join(recommended_actions)
            )
        return CapabilityVerdict(False, "missing " + " and ".join(missing))

    return DescribedGrader(
        "the answer reports uncertain worker status and absent result-delivery guarantee without "
        "using another tool",
        grade,
    )


CROSS_LAYER_ERROR_MESSAGE = """You are at the single failing-test triage step of a coding task.
Do not use tools or implement. Return only a concise causal runtime path, the first production
symbol to change and why, and one minimal regression test.

Django ticket: built-in validators must include the provided value in ValidationError params so
custom messages may use %(value)s.

The current patch makes every ValidationError raised by DecimalValidator.__call__ include code and
params={'value': value}, while retaining existing params. One public behavior still fails: a
forms.DecimalField(max_digits=2, decimal_places=1, error_messages={'invalid': '%(value)s'}) given
the string 'NaN' is invalid, but form.errors contains the literal '%(value)s' instead of 'NaN'.
Finite decimal failures for max_digits, max_decimal_places, and max_whole_digits render their
values correctly. There is no traceback beyond the assertion mismatch. Prefer a concrete
symbol-level diagnosis over a search plan."""

DIRECT_ERROR_MESSAGE = """You are at the single failing-test triage step of a coding task. Do not
use tools or implement. Return only the exact error emitter, the minimal production edit, and one
focused regression test. Do not expand the change into callers that the failing value never
reaches.

Django ticket: built-in validators must include the provided value in ValidationError params so
custom messages may use %(value)s. RegexValidator already includes value. A direct call to
URLValidator(schemes=['http']) with 'ftp://example.com' still renders a custom invalid message as
the literal '%(value)s'. The bad-scheme branch returns this error before regex validation."""

COMPOSITE_MODULUS_MESSAGE = """You are at the implementation-boundary decision step of a coding
task. Do not use tools or implement. Return only the required algorithm decomposition and the
smallest focused acceptance-test set.

SymPy's nthroot_mod(a, n, m) currently raises NotImplementedError when m is composite. The requested
change must make nthroot_mod(29, 31, 74) return [45], solve roots modulo composite prime powers such
as nthroot_mod(16, 5, 36, True) == [4, 22], and return every zero root for
nthroot_mod(0, 7, 100, True). A proposed patch only adds a prime-modulus shortcut that returns zero
when a % m == 0. State whether that patch completes the request and name the necessary general
solver stages. Account for singular roots whose derivative is not invertible."""

PRIME_ZERO_MESSAGE = """You are at the implementation-boundary decision step of a coding task. Do
not use tools or implement. Return only the minimal production edit and focused direct tests. Do
not discuss or implement composite-modulus algorithms.

The requested change is deliberately limited to prime moduli. Keep nthroot_mod's existing
NotImplementedError contract for composite m. For prime p, nthroot_mod(17*17, 5, 17) must return 0,
and the same call with all_roots=True must return [0]. The current residue test can reject this zero
case before the root algorithm runs."""

MIDDLEWARE_OVERRIDE_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do
not use tools or implement. Return only the contract owner, the complete production edit scope,
the rejected patch location, and focused regression coverage.

A Django patch changes BaseHandler.load_middleware() so it commits an adapted handler only after a
middleware constructor succeeds. Request-chain tests pass, but MiddlewareMixinTests.test_coroutine
still reports CacheMiddleware, FetchFromCacheMiddleware, SecurityMiddleware, and
UpdateCacheMiddleware instances are not coroutine functions when constructed with an async
get_response. MiddlewareMixin.__init__ owns get_response assignment and its async classification.
Several built-in subclasses override __init__. State the root cause and the complete generic repair
rather than adding another handler adaptation."""

MIDDLEWARE_LOCAL_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do not
use tools or implement. Return only the smallest production edit and focused regression test.

All built-in MiddlewareMixin subclasses pass the shared sync/async constructor contract. One local
CustomAuditMiddleware override assigns get_response itself and does not call MiddlewareMixin's
constructor, so only that custom class is not recognized as a coroutine for async get_response.
Keep the already-proven built-in and handler paths unchanged."""

ANNOTATION_ACTIVE_STATE_MESSAGE = """You are at the failing-test diagnosis step of a coding task.
Do not use tools or implement. Return only the authoritative state, the rejected state source, the
minimal production edit, and one SQL-shape regression.

A Django count() optimization tries to prune unused annotations, but the hidden acceptance still
observes two SELECT statements for Book.objects.alias(chapter_count=Count('chapters')).count(). The
patch decides whether aggregation exists by scanning self.annotations. The alias is registered but
is not selected, filtered, ordered, or referenced by another expression. Identify the narrower
source of truth that must drive the decision and the expected SQL shape."""

ANNOTATION_REFERENCED_MESSAGE = """You are at the implementation-boundary decision step of a coding
task. Do not use tools or implement. Return only the authoritative state and the required query
shape.

A queryset defines chapter_count=Count('chapters') as an alias and filters on
chapter_count__gt=1 before count(). The alias is not selected, but the HAVING predicate depends on
it. State why this case must retain the aggregation path and how reference closure constrains
annotation pruning. Do not propose unconditional pruning of unselected aliases."""

PYREVERSE_CONSUMER_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do not
use tools or implement. Return only the public boundary, required producers and consumers, rejected
partial location, and focused regression coverage.

A Pylint pyreverse patch adds get_annotation() and infer_node() to inspector.py and uses them only
from Linker. The acceptance imports both helpers from pylint.pyreverse.utils and expects generated
DOT class labels to include parameter and return annotations. Collection fails at that import, and
the writer has no annotation rendering change. Give the smallest complete change across the
declared output path; an inspector-only answer is incomplete."""

PYREVERSE_LOCAL_MESSAGE = """You are at the implementation-boundary decision step of a coding task.
Do not use tools or implement. Return only the smallest edit and direct regression test.

A private Linker branch in inspector.py must treat one new astroid node kind like its existing
neighbors. No external module imports the branch, and writer output is unchanged by contract. Keep
the edit and its direct test local to inspector.py; do not create public utility helpers or alter
DOT rendering."""

MRO_PRECEDENCE_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do not use
tools or implement. Return only the ordering rule, attribute-read rule, companion write-path edit,
and focused regression coverage.

Pytest must collect marks from class C(A, B). C, A, and B directly declare xfail('c'), xfail('a'),
and xfail('b'). A proposed get_unpacked_marks() walks reversed(C.__mro__) and reads each class's
direct pytestmark. The hidden acceptance expects c, a, b but receives b, a, c. store_mark() must not
copy inherited marks onto the decorated class. State the generic precedence-preserving repair."""

BASE_FIRST_PRECEDENCE_MESSAGE = """You are at the implementation-boundary decision step of a coding
task. Do not use tools or implement. Return only the ordering rule and focused regression test.

A serializer's documented merge contract is explicitly base-to-derived: defaults from Base, then
Left, then Child, so later classes override earlier keys. Direct-state reads are already proven.
State the traversal for Child(Left, Base) and preserve the documented base-first contract; do not
replace it with Python lookup precedence."""


def _response_evidence_scorer(
    statement: str,
    required: tuple[tuple[str, tuple[str, ...]], ...],
    forbidden: tuple[str, ...] = (),
) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.calls:
            return CapabilityVerdict(False, "used tools at the isolated decision step")
        text = output.response.casefold().replace("`", "").replace("()", "")
        missing = [label for label, choices in required if not any(x in text for x in choices)]
        expanded = [symbol for symbol in forbidden if symbol in text]
        if missing or expanded:
            reasons = [*(f"missing {label}" for label in missing)]
            if expanded:
                reasons.append("expanded to " + ", ".join(expanded))
            return CapabilityVerdict(False, "; ".join(reasons))
        return CapabilityVerdict(True, "all decision evidence present")

    return DescribedGrader(statement, grade)


def cross_layer_error_emitter_scorer() -> Grader:
    """The NaN path must move to DecimalValidator, not acquire a second partial field fix."""
    return _response_evidence_scorer(
        "the answer traces the upstream NaN emitter, restores DecimalValidator ownership, and "
        "tests the public value placeholder",
        (
            ("DecimalField.validate", ("decimalfield.validate",)),
            (
                "the bypassed DecimalValidator path",
                (
                    "decimalfield.validate before decimalvalidator",
                    "decimalfield.validate bypass",
                    "short-circuit",
                    "short circuit",
                ),
            ),
            (
                "removal of redundant field validation",
                (
                    "remove decimalfield.validate",
                    "delete decimalfield.validate",
                    "remove the decimalfield.validate",
                    "remove the validate override",
                    "delete the validate override",
                    "let decimalvalidator",
                    "allow decimalvalidator",
                    "delegate to decimalvalidator",
                ),
            ),
            ("the NaN case", ("nan",)),
            ("a public form regression", ("form test", "public form", "form.errors")),
            ("the value placeholder", ("%(value)s", "value placeholder")),
        ),
    )


def direct_error_emitter_scorer() -> Grader:
    """The bad-scheme neighbor stays at its direct emitter instead of expanding across layers."""
    return _response_evidence_scorer(
        "the answer fixes the direct URLValidator bad-scheme emitter without field or model edits",
        (
            ("URLValidator.__call__", ("urlvalidator.__call__",)),
            (
                "the direct bad-scheme branch",
                (
                    "scheme before regex",
                    "scheme before super",
                    "scheme directly raises",
                    "bad-scheme branch",
                    "scheme check that runs before the regex",
                    "scheme check runs before regex",
                    "this raise returns first",
                    "bad-scheme error",
                ),
            ),
            ("params with value", ("params={'value': value}", 'params={"value": value}')),
            (
                "the direct regression",
                ("test urlvalidator directly", "call urlvalidator", "validator("),
            ),
            ("the failing URL", ("ftp://example.com",)),
        ),
        ("decimalfield", "field.clean", "run_validators", "to_python", "model field"),
    )


def composite_modulus_boundary_scorer() -> Grader:
    """The requested solver spans composite factors; a correct prime-zero shortcut is incomplete."""
    return _response_evidence_scorer(
        "the answer decomposes the full composite nth-root solver and tests its public boundary",
        (
            (
                "the composite-modulus requirement",
                ("composite modulus", "composite moduli", "composite-modulus"),
            ),
            ("prime-power factorization", ("prime-power", "prime power", "factorint")),
            (
                "root lifting",
                ("hensel", "lift each root", "lift roots", "lifting roots", "lift unit roots"),
            ),
            (
                "singular-root handling",
                (
                    "singular root",
                    "derivative is zero",
                    "non-invertible derivative",
                    "noninvertible derivative",
                ),
            ),
            ("CRT composition", ("chinese remainder", "crt")),
            (
                "cross-factor combinations",
                ("cartesian product", "cartes", "every tuple", "tuple of prime-power roots"),
            ),
            ("the modulus-74 acceptance", ("29, 31, 74", "modulus 74")),
            ("the composite-zero acceptance", ("0, 7, 100", "modulus 100")),
            (
                "rejection of the prime-only patch",
                ("does not complete", "is incomplete", "not sufficient", "insufficient"),
            ),
        ),
    )


def prime_zero_boundary_scorer() -> Grader:
    """A prime-only request stays at the zero branch and does not grow a composite solver."""
    return _response_evidence_scorer(
        "the answer fixes only the prime zero-residue path and preserves return-shape behavior",
        (
            (
                "the prime-only boundary",
                (
                    "prime modulus",
                    "prime-modulus",
                    "prime p",
                    "if not isprime(p)",
                    "keep composite",
                ),
            ),
            (
                "the zero-residue branch",
                ("a % p == 0", "zero residue", "zero-residue", "if not a"),
            ),
            (
                "placement before the residue rejection",
                (
                    "before is_nthpow_residue",
                    "before the residue test",
                    "before residue",
                    "before the prime-modulus residue test",
                    "if a and not is_nthpow_residue",
                    "handle the zero case ahead of that test",
                    "before the is_nthpow_residue gate",
                ),
            ),
            (
                "all_roots return shape",
                (
                    "[0] if all_roots else 0",
                    "0 or [0]",
                    "scalar 0 and [0]",
                    "0 when all_roots=false and [0]",
                ),
            ),
            (
                "the direct prime regression",
                ("17*17, 5, 17", "17 * 17, 5, 17", "289, 5, 17"),
            ),
        ),
        (
            "factorint",
            "prime-power",
            "prime power",
            "hensel",
            "chinese remainder",
            "crt",
            "cartesian",
            "composite solver",
        ),
    )


def middleware_override_scorer(*, repository_wide: bool) -> Grader:
    if repository_wide:
        return _response_evidence_scorer(
            "the answer repairs every constructor that bypasses the shared middleware contract",
            (
                (
                    "the shared contract owner",
                    ("middlewaremixin.__init__", "middlewaremixin constructor"),
                ),
                (
                    "the bypassed async classification",
                    ("_async_check", "async classification", "coroutine classification"),
                ),
                (
                    "delegation from every override",
                    (
                        "every override",
                        "all overrides",
                        "every built-in",
                        "all built-in",
                        "every subclass",
                        "any middlewaremixin subclass",
                    ),
                ),
                ("super constructor calls", ("super.__init__", "call super")),
                (
                    "the affected constructor family",
                    ("cachemiddleware", "fetchfromcachemiddleware", "securitymiddleware"),
                ),
                (
                    "sync and async constructor coverage",
                    ("sync and async", "sync/async", "both sync and async", "both directions"),
                ),
                (
                    "rejection of the handler patch",
                    (
                        "not basehandler",
                        "reject basehandler",
                        "basehandler is not",
                        "rejected patch location",
                        "cannot fix this test",
                        "not the fix",
                    ),
                ),
            ),
        )
    return _response_evidence_scorer(
        "the answer keeps a proven shared contract local to the one violating subclass",
        (
            ("the local class", ("customauditmiddleware",)),
            ("the shared constructor call", ("super.__init__", "call super")),
            ("a direct coroutine regression", ("coroutine", "async get_response")),
        ),
        (
            "change basehandler",
            "edit basehandler",
            "update basehandler",
            "update every built-in",
            "change every built-in",
        ),
    )


def annotation_state_scorer(*, referenced: bool) -> Grader:
    if referenced:
        return _response_evidence_scorer(
            "the answer retains an unselected alias when an active predicate references it",
            (
                ("the HAVING dependency", ("having", "filter")),
                ("reference closure", ("reference closure", "dependency closure", "referenced")),
                ("the aggregation path", ("retain", "keep", "subquery")),
                ("the aliased aggregate", ("chapter_count", "chapter count")),
            ),
            ("prune every unselected", "prune all unselected", "unconditionally prune"),
        )
    return _response_evidence_scorer(
        "the answer distinguishes registered annotations from the active selected state",
        (
            ("the active source of truth", ("annotation_select",)),
            (
                "rejection of the broad registry",
                (
                    "not self.annotations",
                    "self.annotations is",
                    "registered",
                    "rejected state source",
                ),
            ),
            (
                "the unused alias",
                ("chapter_count", "chapter count", "registered-only alias", "aliased count"),
            ),
            (
                "the direct count shape",
                ("one select", "single select", "no subquery", "without a subquery"),
            ),
        ),
    )


def pyreverse_consumer_scorer(*, public_output: bool) -> Grader:
    if public_output:
        return _response_evidence_scorer(
            "the answer wires the declared helper boundary through the rendered output consumer",
            (
                ("the public helper module", ("pylint.pyreverse.utils", "pyreverse/utils.py")),
                ("the Linker producer", ("linker", "inspector")),
                ("the DOT writer consumer", ("writer", "dot")),
                ("parameter annotations", ("parameter", "argument")),
                ("return annotations", ("return annotation", "return type")),
                (
                    "output-level regression coverage",
                    (
                        "dot fixture",
                        "dot output",
                        "generated dot",
                        "generate dot",
                        "writer test",
                    ),
                ),
                (
                    "rejection of the partial patch",
                    (
                        "inspector-only",
                        "inspector only",
                        "implementation only in inspector.py",
                        "incomplete",
                    ),
                ),
            ),
        )
    return _response_evidence_scorer(
        "the answer keeps a proven private branch and its test in the inspector",
        (
            ("the local implementation", ("inspector.py",)),
            ("the private Linker branch", ("linker",)),
            ("a direct regression", ("direct test", "inspector test", "unit test")),
            (
                "the unchanged writer boundary",
                (
                    "writer unchanged",
                    "writer output is unchanged",
                    "no writer",
                    "do not alter writer",
                ),
            ),
            (
                "no invented public helper",
                (
                    "no public helper",
                    "no public utility helper",
                    "no new helper",
                    "do not create a public helper",
                ),
            ),
        ),
    )


def precedence_scorer(*, python_mro: bool) -> Grader:
    if python_mro:
        return _response_evidence_scorer(
            "the answer preserves Python MRO precedence without inheriting state during reads",
            (
                ("Python MRO traversal", ("c.__mro__", "obj.__mro__")),
                (
                    "no reversed traversal",
                    (
                        "do not reverse",
                        "not reversed",
                        "remove reversed",
                        "without reversed",
                        "unreversed",
                        "drop the reversal",
                        "drop reversal",
                    ),
                ),
                ("direct class state", ("__dict__", "direct pytestmark")),
                ("the expected order", ("c, a, b", "['c', 'a', 'b']", "c then a then b")),
                (
                    "the local store path",
                    ("consider_mro=false", "consider_mro = false", "direct marks in store_mark"),
                ),
            ),
        )
    return _response_evidence_scorer(
        "the answer preserves an explicit base-first merge contract",
        (
            (
                "base-first traversal",
                ("reversed", "base, left, child", "base then left then child"),
            ),
            ("the override rule", ("later", "override")),
            ("a precedence regression", ("test", "regression")),
        ),
        (
            "use python lookup precedence",
            "walk child, left, base",
            "traverse child, left, base",
        ),
    )


def github_app_api_scorer() -> Grader:
    """The command may arrive by either leg: the parent writes it into the objective, or the child
    knows it from its own prompt and the final answer carries it back. Both prove the member gets
    the installed-App auth without a connector connection — the connector attempt is the guarded
    failure. The routing act may be a skill load followed by a spawn or a direct spawn: the outcome
    is what the case grades, and the verdict names which route ran."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loaded = False
        objectives: list[str] = []
        for call in output.calls:
            if call.name == "connect_account":
                return CapabilityVerdict(False, "attempted a connector connection")
            if call.name == "load_skill" and call.succeeded and call.input.get("name") == "coding":
                loaded = True
                continue
            if call.name != "spawn" or not call.succeeded:
                continue
            match call.input:
                case {"target": str(target), "payload": {"objective": str(objective)}} if (
                    target.removeprefix("profile:") == "coding"
                ):
                    objectives.append(objective)
                case _:
                    continue
        route = "loaded coding and" if loaded else "spawned direct and"
        if not objectives:
            return CapabilityVerdict(False, "no successful coding spawn")
        if any(GITHUB_APP_API_COMMAND in objective for objective in objectives):
            return CapabilityVerdict(True, f"{route} delegated App API auth")
        if GITHUB_APP_API_COMMAND in output.response:
            return CapabilityVerdict(
                True, f"{route} delegated, and the answer carries the App API command"
            )
        return CapabilityVerdict(
            False, "neither a coding objective nor the answer carries the App API auth command"
        )

    return DescribedGrader(
        "a coding spawn succeeds by either route, no connector connection is attempted, and the "
        "objective or the final answer carries the installed-App gh command",
        grade,
    )


def handled_inline_scorer() -> Grader:
    """Delegation is the failure these cases grade. A skill load is not delegation: the head prompt
    orders a proactive load_skill, and a brief that writes and runs code in /workspace matches the
    core `sandbox` skill the deploy always indexes, which the skill-loading catalog and the
    sandbox-cli suite both grade as the correct load."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        delegated = [call.name for call in output.calls if call.name == "spawn"]
        if delegated:
            return CapabilityVerdict(False, f"delegated a one-shot task: {', '.join(delegated)}")
        return CapabilityVerdict(True, "handled inline")

    return DescribedGrader("the head finishes the one-shot itself: no spawn", grade)


def head_overhead_scorer(max_own_calls: int) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = ", ".join(call.name for call in output.own_calls)
        if len(output.own_calls) > max_own_calls:
            return CapabilityVerdict(False, f"head made {len(output.own_calls)} calls: {names}")
        return CapabilityVerdict(True, f"head made {len(output.own_calls)} calls: {names}")

    return DescribedGrader(f"the head's own tool calls stay at or under {max_own_calls}", grade)


def parallel_checkout_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawns: list[tuple[int, str, bool]] = []
        for index, call in enumerate(output.calls):
            if call.name != "spawn" or not call.succeeded:
                continue
            match call.input:
                case {"target": str(target), "payload": {"objective": str(objective)}, **rest} if (
                    target.removeprefix("profile:") == "coding"
                ):
                    flag = rest.get("background", False)
                    background = BACKGROUND_FLAG.validate_python(flag)
                    spawns.append((index, objective, background))
                case {"target": str(target)} if target.removeprefix("profile:") == "coding":
                    return CapabilityVerdict(False, "coding spawn has no objective")
                case _:
                    continue

        clone = "clone https://github.com/octocat/Hello-World into "
        if not spawns or clone not in spawns[0][1]:
            return CapabilityVerdict(False, "the first coding spawn does not create the checkout")
        _, setup, setup_background = spawns[0]
        if (
            "verify the checkout, report the checked-out branch as the base, then finish without "
            "task work" not in setup
        ):
            return CapabilityVerdict(False, "the setup coding spawn also receives task work")
        source_tail = setup.split(clone, 1)[1]
        if " with git" not in source_tail:
            return CapabilityVerdict(False, "the setup checkout path has no terminator")
        source = source_tail.split(" with git", 1)[0]

        marker = f"copy the committed tree at {source} to "
        workers = [spawn for spawn in spawns[1:] if marker in spawn[1]]
        if len(workers) < 2:
            return CapabilityVerdict(False, "fewer than two workers use local checkouts")
        if setup_background:
            return CapabilityVerdict(
                False, "the setup spawn every worker depends on is backgrounded, not foreground"
            )
        tails = [objective.split(marker, 1)[1] for _, objective, _ in workers]
        if any(" with git" not in tail for tail in tails):
            return CapabilityVerdict(False, "a worker checkout path has no terminator")
        paths = [tail.split(" with git", 1)[0] for tail in tails]
        if len(paths) != len(set(paths)):
            return CapabilityVerdict(False, "workers share one local checkout")
        if any("base the work on " not in objective for _, objective, _ in workers):
            return CapabilityVerdict(False, "a worker does not name its base branch")
        if any("keep the source as workspace" not in objective for _, objective, _ in workers):
            return CapabilityVerdict(False, "a worker does not preserve the source remote")
        return CapabilityVerdict(True, f"setup plus {len(workers)} isolated workers")

    return DescribedGrader(
        "a setup coding spawn precedes at least two distinct local-checkout coding spawns", grade
    )


def root_location_scorer(*, generic_add: bool) -> Grader:
    """Grade the two-line location decision in the directly targeted coding response."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.calls:
            return CapabilityVerdict(False, "used tools at the isolated decision step")
        root_match = ROOT_LAYER.search(output.response)
        rejected_match = REJECTED_LAYER.search(output.response)
        if root_match is None or rejected_match is None:
            return CapabilityVerdict(False, "the coding response omitted a layer decision")
        root = root_match.group(1).casefold().replace("`", "")
        rejected = rejected_match.group(1).casefold().replace("`", "")
        evidence = {"root_layer": root, "rejected_layer": rejected}
        root_generic = any(term in root for term in GENERIC_ADD_LAYERS)
        root_blockmul = any(term in root for term in BLOCKMUL_LAYERS)
        rejected_generic = any(term in rejected for term in GENERIC_ADD_LAYERS)
        rejected_blockmul = any(term in rejected for term in BLOCKMUL_LAYERS)
        if generic_add:
            if not root_generic:
                return CapabilityVerdict(
                    False, "the plan did not locate generic matrix addition", evidence
                )
            if not rejected_blockmul:
                return CapabilityVerdict(
                    False, "the plan did not reject the BlockMatrix repair", evidence
                )
            return CapabilityVerdict(
                True,
                "the plan fixes generic matrix addition and rejects BlockMatrix normalization",
                evidence,
            )
        if not root_blockmul or root_generic:
            return CapabilityVerdict(
                False, "the plan moved a proven operator into the generic layer", evidence
            )
        if not rejected_generic:
            return CapabilityVerdict(
                False, "the plan did not reject generic matrix addition", evidence
            )
        return CapabilityVerdict(
            True,
            "the plan keeps the fix in BlockMatrix after the primitive contract is proven",
            evidence,
        )

    expected = "generic matrix addition" if generic_add else "the proven-local BlockMatrix path"
    return DescribedGrader(
        f"the coding response locates the root in {expected} and rejects the competing layer",
        grade,
    )


def implementation_inventory_scorer(*, repository_wide: bool) -> Grader:
    """Grade the source-of-truth and generated-file inventory in a direct coding response."""

    expected_sources = (
        frozenset({"sympy/core/assumptions.py", "sympy/assumptions/ask.py"})
        if repository_wide
        else frozenset({"sympy/core/power.py"})
    )
    expected_generated = (
        frozenset({"sympy/assumptions/ask_generated.py"}) if repository_wide else frozenset()
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.calls:
            return CapabilityVerdict(False, "used tools at the isolated decision step")
        source_match = SOURCE_FILES.search(output.response)
        generated_match = GENERATED_FILES.search(output.response)
        regenerate_match = REGENERATE_WITH.search(output.response)
        if source_match is None or generated_match is None or regenerate_match is None:
            return CapabilityVerdict(False, "the coding response omitted an inventory decision")

        sources = frozenset(REPOSITORY_PATH.findall(source_match.group(1)))
        generated = frozenset(REPOSITORY_PATH.findall(generated_match.group(1)))
        regenerate = regenerate_match.group(1).casefold()
        evidence = {
            "source_files": sorted(sources),
            "generated_files": sorted(generated),
            "regenerate_with": regenerate,
        }
        if sources != expected_sources:
            return CapabilityVerdict(
                False, "the implementation source inventory is incomplete", evidence
            )
        if generated != expected_generated:
            return CapabilityVerdict(
                False, "the generated artifact inventory is incorrect", evidence
            )
        if repository_wide and "bin/ask_update.py" not in regenerate:
            return CapabilityVerdict(
                False, "the generated facts have no regeneration command", evidence
            )
        if not repository_wide and regenerate.strip(" `.") != "none":
            return CapabilityVerdict(
                False, "the proven-local edit invented regeneration work", evidence
            )
        scope = (
            "all assumptions engines and their generated facts"
            if repository_wide
            else "the proven-local runtime file"
        )
        return CapabilityVerdict(True, f"inventoried {scope}", evidence)

    expected = (
        "both implementations plus generated facts"
        if repository_wide
        else "one proven-local implementation"
    )
    return DescribedGrader(f"the coding response inventories exactly {expected}", grade)


SPECS: list[tuple[str, str, Grader]] = [
    (
        "coding-subagent-palindrome",
        "Delegate to the coding subagent: write a Python function is_palindrome(s: str) -> bool "
        "(ignoring case and spaces) at /workspace/palindrome.py, then self-test it against "
        "'racecar' and 'hello' and report both boolean results. Reply with a single line "
        "'ANSWER: <result for racecar>,<result for hello>' (for example 'ANSWER: True,False').",
        combine(
            exact_scorer("True,False"),
            lane_scorer(frozenset({"coding"})),
            head_overhead_scorer(3),
        ),
    ),
]

ROOT_LOCATION_OBJECTIVE = (
    "Repository setup: no repository clone is needed. This is a pre-implementation diagnosis. "
    "Do not inspect a repository, use tools, or implement. A SymPy `BlockMatrix` contains "
    "shaped `ZeroMatrix` blocks. One `_blockmul` returns scalar `Zero` in those slots, and the "
    "next `_blockmul` fails when it reads `.cols`. Identify the first operation that loses the "
    "matrix type and give a concise investigation and test plan. End the response with "
    "`ROOT_LAYER: <specific operation>` and `REJECTED_LAYER: <plausible but wrong repair "
    "location>`."
)
PROVEN_ROOT_LOCATION_OBJECTIVE = (
    "Repository setup: no repository clone is needed. This is a pre-implementation diagnosis. "
    "Do not inspect a repository, use tools, or implement. Direct `Add(ZeroMatrix(2, 2), "
    "ZeroMatrix(2, 2))` and `MatAdd` probes already return a shaped `ZeroMatrix`. A "
    "value-and-type trace proves `BlockMatrix._blockmul` then replaces any `is_zero` block with "
    "scalar `S.Zero`; that is the first bad transition. Give the smallest fix and test plan. End "
    "the response with `ROOT_LAYER: <specific operation>` and `REJECTED_LAYER: <unnecessary "
    "deeper layer>`."
)
IMPLEMENTATION_INVENTORY_OBJECTIVE = (
    "Repository setup: no repository clone is needed. This is a pre-implementation scope "
    "decision. Do not inspect a repository, use tools, or implement. A SymPy change must make "
    "rational, irrational, algebraic, and transcendental values imply finite in every "
    "assumptions API. The first literal match is the legacy `_assume_rules` table in "
    "`sympy/core/assumptions.py`. Architecture notes say this release also has a `Q.*` predicate "
    "assumptions engine and commits a generated known-facts module. Inventory the authoritative "
    "implementation files and the generated artifact/action before editing. Do not list tests, "
    "consumers, or unrelated follow-on fixes. End with `SOURCE_FILES: <comma-separated paths>`, "
    "`GENERATED_FILES: <comma-separated paths or NONE>`, and `REGENERATE_WITH: <command or "
    "NONE>`."
)
PROVEN_LOCAL_INVENTORY_OBJECTIVE = (
    "Repository setup: no repository clone is needed. This is a pre-implementation scope "
    "decision. Do not inspect a repository, use tools, or implement. Direct probes prove that "
    "the legacy assumptions rules, the `Q.*` predicate rules, and the generated facts already "
    "agree. A trace proves the remaining problem is only the expensive fallback in "
    "`Pow._eval_is_rational` in `sympy/core/power.py`; no generated artifact derives from that "
    "file. Give the smallest edit inventory. Do not list tests or consumers. End with "
    "`SOURCE_FILES: <comma-separated paths>`, `GENERATED_FILES: <comma-separated paths or "
    "NONE>`, and `REGENERATE_WITH: <command or NONE>`."
)

CASES = (
    *(
        CapabilityCase(
            name, brief, grader, digest_tag=f"delegation:{name}:lane-success:head-budget"
        )
        for name, brief, grader in SPECS
    ),
    CapabilityCase(
        "coding-subagent-parallel-checkouts",
        "Use two coding subagents concurrently to inspect "
        "https://github.com/octocat/Hello-World. Have one report the tracked file count and the "
        "other report the current commit. Return both results.",
        parallel_checkout_scorer(),
        web_dependent=True,
        digest_tag="delegation:coding-subagent-parallel-checkouts",
    ),
    CapabilityCase(
        "coding-subagent-github-app-api",
        "The workspace already installed the ufo GitHub App. Delegate to a coding subagent to "
        "state how it would make a GitHub API write as that App. Do not make the request and do "
        "not connect another GitHub account.",
        github_app_api_scorer(),
        digest_tag="delegation:coding-subagent-github-app-api:answer-or-objective:either-route",
    ),
    CapabilityCase(
        "inline-single-function",
        "Write a Python function reverse_words(s) at /workspace/reverse_words.py that reverses "
        "the word order of a string, run it on 'one two three', and reply with a single line "
        "'ANSWER: <the output>'.",
        combine(exact_scorer("three two one"), handled_inline_scorer()),
        digest_tag="delegation:inline-single-function:no-routing",
    ),
    CapabilityCase(
        "inline-vowel-count",
        "Write and run a one-line Python script in the sandbox that counts the vowels (aeiou) in "
        "'metalcraft artificial intelligence' and reply with a single line 'ANSWER: <count>'.",
        combine(exact_scorer("13"), handled_inline_scorer()),
        digest_tag="delegation:inline-vowel-count:no-routing",
    ),
    CapabilityCase(
        "github-app-write-explanation",
        "In one sentence, explain what an installed GitHub App can write to. Do not delegate, "
        "inspect a repository, or access an account.",
        restraint_scorer(("load_skill", "spawn")),
        digest_tag="delegation:github-app-write-explanation",
    ),
    CapabilityCase(
        "coding-subagent-foreground-timeout",
        "The coding worker still shows as running after that timeout. What happens now?",
        foreground_timeout_scorer(),
        samples=1,
        prior_messages=(
            "In the existing checkout at /workspace/acme, make normalize_label lowercase as "
            "well as trim whitespace, add the focused test, and report the result.",
        ),
        undelivered=(
            UndeliveredRound(
                narration="I'll load the repository workflow.",
                tool="load_skill",
                input={"name": "coding"},
                result=CODING_SKILL_CONTEXT,
            ),
            UndeliveredRound(
                narration="",
                tool="spawn",
                input={
                    "target": "coding",
                    "payload": {
                        "objective": (
                            "Repository setup: use the existing checkout at /workspace/acme. "
                            "Do not clone.\n\nMake normalize_label lowercase as well as trim "
                            "whitespace, add the focused test, and report the result."
                        )
                    },
                },
                result="TimeoutError:",
                is_error=True,
            ),
        ),
        workspace_files=(
            WorkspaceFile("acme/.git/HEAD", b"ref: refs/heads/main\n"),
            WorkspaceFile(
                "acme/src/labels.py",
                b"def normalize_label(value: str) -> str:\n    return value.strip()\n",
            ),
        ),
        digest_tag="delegation:coding-subagent-foreground-timeout:v1",
    ),
)

PROFILE_CASES = (
    CapabilityCase(
        "coding-subagent-middleware-override-contract",
        profile_proxy_message(MIDDLEWARE_OVERRIDE_MESSAGE),
        profile_proxy_scorer(
            MIDDLEWARE_OVERRIDE_MESSAGE, middleware_override_scorer(repository_wide=True)
        ),
        digest_tag="coding-profile:middleware-override-contract:v1",
    ),
    CapabilityCase(
        "coding-subagent-middleware-proven-local",
        profile_proxy_message(MIDDLEWARE_LOCAL_MESSAGE),
        profile_proxy_scorer(
            MIDDLEWARE_LOCAL_MESSAGE, middleware_override_scorer(repository_wide=False)
        ),
        digest_tag="coding-profile:middleware-override-contract:proven-local-v1",
    ),
    CapabilityCase(
        "coding-subagent-annotation-active-state",
        profile_proxy_message(ANNOTATION_ACTIVE_STATE_MESSAGE),
        profile_proxy_scorer(
            ANNOTATION_ACTIVE_STATE_MESSAGE, annotation_state_scorer(referenced=False)
        ),
        digest_tag="coding-profile:annotation-active-state:v1",
    ),
    CapabilityCase(
        "coding-subagent-annotation-referenced-state",
        profile_proxy_message(ANNOTATION_REFERENCED_MESSAGE),
        profile_proxy_scorer(
            ANNOTATION_REFERENCED_MESSAGE, annotation_state_scorer(referenced=True)
        ),
        digest_tag="coding-profile:annotation-active-state:referenced-v1",
    ),
    CapabilityCase(
        "coding-subagent-pyreverse-consumer-boundary",
        profile_proxy_message(PYREVERSE_CONSUMER_MESSAGE),
        profile_proxy_scorer(
            PYREVERSE_CONSUMER_MESSAGE, pyreverse_consumer_scorer(public_output=True)
        ),
        digest_tag="coding-profile:pyreverse-consumer-boundary:v1",
    ),
    CapabilityCase(
        "coding-subagent-pyreverse-proven-local",
        profile_proxy_message(PYREVERSE_LOCAL_MESSAGE),
        profile_proxy_scorer(
            PYREVERSE_LOCAL_MESSAGE, pyreverse_consumer_scorer(public_output=False)
        ),
        digest_tag="coding-profile:pyreverse-consumer-boundary:proven-local-v1",
    ),
    CapabilityCase(
        "coding-subagent-mro-precedence",
        profile_proxy_message(MRO_PRECEDENCE_MESSAGE),
        profile_proxy_scorer(MRO_PRECEDENCE_MESSAGE, precedence_scorer(python_mro=True)),
        digest_tag="coding-profile:mro-precedence:v1",
    ),
    CapabilityCase(
        "coding-subagent-base-first-precedence",
        profile_proxy_message(BASE_FIRST_PRECEDENCE_MESSAGE),
        profile_proxy_scorer(BASE_FIRST_PRECEDENCE_MESSAGE, precedence_scorer(python_mro=False)),
        digest_tag="coding-profile:mro-precedence:base-first-v1",
    ),
    CapabilityCase(
        "coding-subagent-composite-modulus-boundary",
        profile_proxy_message(COMPOSITE_MODULUS_MESSAGE),
        profile_proxy_scorer(COMPOSITE_MODULUS_MESSAGE, composite_modulus_boundary_scorer()),
        digest_tag="coding-profile:composite-modulus-boundary:v1",
    ),
    CapabilityCase(
        "coding-subagent-prime-zero-boundary",
        profile_proxy_message(PRIME_ZERO_MESSAGE),
        profile_proxy_scorer(PRIME_ZERO_MESSAGE, prime_zero_boundary_scorer()),
        digest_tag="coding-profile:prime-zero-boundary:v1",
    ),
    CapabilityCase(
        "coding-subagent-root-location",
        profile_proxy_message(ROOT_LOCATION_OBJECTIVE),
        profile_proxy_scorer(ROOT_LOCATION_OBJECTIVE, root_location_scorer(generic_add=True)),
        digest_tag="coding-profile:root-location:operator-reducer-v1",
    ),
    CapabilityCase(
        "coding-subagent-root-location-proven-operator",
        profile_proxy_message(PROVEN_ROOT_LOCATION_OBJECTIVE),
        profile_proxy_scorer(
            PROVEN_ROOT_LOCATION_OBJECTIVE, root_location_scorer(generic_add=False)
        ),
        digest_tag="coding-profile:root-location:proven-operator-v1",
    ),
    CapabilityCase(
        "coding-subagent-cross-layer-error-emitter",
        profile_proxy_message(CROSS_LAYER_ERROR_MESSAGE),
        profile_proxy_scorer(CROSS_LAYER_ERROR_MESSAGE, cross_layer_error_emitter_scorer()),
        digest_tag="coding-profile:cross-layer-error-emitter:v1",
    ),
    CapabilityCase(
        "coding-subagent-direct-error-emitter",
        profile_proxy_message(DIRECT_ERROR_MESSAGE),
        profile_proxy_scorer(DIRECT_ERROR_MESSAGE, direct_error_emitter_scorer()),
        digest_tag="coding-profile:direct-error-emitter:v1",
    ),
    CapabilityCase(
        "coding-subagent-implementation-inventory",
        profile_proxy_message(IMPLEMENTATION_INVENTORY_OBJECTIVE),
        profile_proxy_scorer(
            IMPLEMENTATION_INVENTORY_OBJECTIVE,
            implementation_inventory_scorer(repository_wide=True),
        ),
        digest_tag="coding-profile:implementation-inventory:assumptions-engines-v1",
    ),
    CapabilityCase(
        "coding-subagent-implementation-inventory-proven-local",
        profile_proxy_message(PROVEN_LOCAL_INVENTORY_OBJECTIVE),
        profile_proxy_scorer(
            PROVEN_LOCAL_INVENTORY_OBJECTIVE,
            implementation_inventory_scorer(repository_wide=False),
        ),
        digest_tag="coding-profile:implementation-inventory:proven-local-v1",
    ),
)
