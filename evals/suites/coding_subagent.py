"""Coding-subagent cases grade delegation through the `coding` profile and isolated fan-out."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
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
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine, exact_scorer, lane_scorer, restraint_scorer
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.runtime.skills.runtime import LoadedSkill, loaded_context, parse_skill
from ufo.schema import tables

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
    "_eval_matrix_mul",
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
STRUCTURED_REVIEW_RESULT: JsonObject = {
    "head_sha": "52b8c52b2575d4eaa4403765de981824fe8c22bd",
    "complete": True,
    "findings": [
        {
            "path": "core/src/ufo/runtime/subagents.py",
            "line": 210,
            "title": "The handoff word limit truncates the required review JSON object",
            "trigger": (
                "The Code app asks a coding reviewer to return a complete finding through finish."
            ),
            "failure": (
                "The shared 20-word instruction conflicts with the required JSON and can truncate "
                "its fields."
            ),
            "impact": "materially incorrect result or state for a supported workflow",
        }
    ],
}
STRUCTURED_REVIEW_OBJECTIVE = (
    "Do not use tools. Return exactly the following complete JSON object through finish, with no "
    "text before or after it. Preserve every field and value: "
    + json.dumps(STRUCTURED_REVIEW_RESULT, separators=(",", ":"))
)
STRUCTURED_AGENT_NAME = "structured-worker"
TASK_REPOSITORY = "dclm"
FALLBACK_REPOSITORY = "source"
TASK_REPOSITORY_FILES = ("README.md", "src/model.py", "tests/test_model.py")
GIT_IDENTITY = ("-c", "user.email=evals@localhost", "-c", "user.name=evals")
EXISTING_CHECKOUT_NO_URL_OBJECTIVE = (
    "Repository setup: use the existing checkout at /workspace/dclm. Do not clone or fetch; "
    "if it is missing, report it.\n\nReport the number of tracked files. Do not change the "
    "checkout. Reply exactly `ANSWER: <count>`."
)
EXISTING_CHECKOUT_URL_FALLBACK_OBJECTIVE = (
    "Repository setup: use the existing checkout at /workspace/dclm, from "
    "file:///workspace/source. If the path is missing, clone file:///workspace/source there "
    "once.\n\nReport the number of tracked files. Do not change the checkout after setup. "
    "Reply exactly `ANSWER: <count>`."
)


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


def profile_proxy_scorer(objective: str, grader: Grader, *, relayed_answer: bool = False) -> Grader:
    """Grade the validated result of one exact coding-profile spawn, never the proxy's answer.

    `relayed_answer` additionally requires the proxy's reply to BE the child result, token for
    token across transport whitespace. A decision case's rubric is judged over the reply, so this
    equality is what makes that judgment a judgment of the child's decision — a proxy that
    paraphrases or summarizes fails here before any criterion is read."""

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
        if relayed_answer and not _same_transport_objective(response, output.response):
            return CapabilityVerdict(False, "the proxy did not relay the child result verbatim")
        parent_calls = {call.call_id for call in output.own_calls if call.call_id}
        child_calls = tuple(call for call in output.calls if call.call_id not in parent_calls)
        return await grader(CapabilityOutput(response, child_calls, tool_errors=output.tool_errors))

    return DescribedGrader(
        f"one exact profile:coding spawn satisfies: {grading_statement(grader)}", grade
    )


def structured_review_result_scorer(expected: JsonObject) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        try:
            result = json.loads(output.response)
        except json.JSONDecodeError as error:
            return CapabilityVerdict(False, f"the review result is not one JSON object: {error}")
        words = len(output.response.split())
        evidence: JsonObject = {"resultWords": words, "completeObject": result == expected}
        if result != expected:
            return CapabilityVerdict(
                False, "the review result changed or truncated a field", evidence
            )
        return CapabilityVerdict(
            True, "the coding child returned the complete review JSON", evidence
        )

    return DescribedGrader(
        "one complete machine-readable review finding longer than the prose handoff cap", grade
    )


def workspace_agent_result_scorer(objective: str, expected: JsonObject) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawns = tuple(call for call in output.own_calls if call.name == "spawn")
        if len(spawns) != 1:
            return CapabilityVerdict(False, f"the parent recorded {len(spawns)} spawns")
        spawn = spawns[0]
        payload = spawn.input.get("payload")
        task = payload.get("task") if isinstance(payload, dict) else None
        if (
            not spawn.succeeded
            or spawn.input.get("target") != f"agent:{STRUCTURED_AGENT_NAME}"
            or not isinstance(task, str)
            or not _same_transport_objective(objective, task)
        ):
            return CapabilityVerdict(False, "the parent changed the workspace-agent call")
        try:
            response = json.loads(output.response)
            if isinstance(response, dict) and tuple(response) == ("result",):
                response = json.loads(response["result"])
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            return CapabilityVerdict(False, f"the workspace agent returned invalid JSON: {error}")
        evidence: JsonObject = {"completeObject": response == expected}
        if response != expected:
            return CapabilityVerdict(
                False, "the workspace agent changed or truncated a field", evidence
            )
        return CapabilityVerdict(
            True, "the workspace agent returned the complete review JSON", evidence
        )

    return DescribedGrader(
        "one default-contract workspace agent returns a complete machine-readable review finding",
        grade,
    )


def task_environment_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loads = tuple(
            call
            for call in output.own_calls
            if call.name == "load_skill" and call.input.get("name") == "coding"
        )
        if not loads:
            return CapabilityVerdict(False, "the parent did not load the coding workflow")
        spawns = tuple(call for call in output.own_calls if call.name == "spawn")
        if not spawns:
            return CapabilityVerdict(False, "the parent did not delegate the existing checkout")
        first = spawns[0]
        earlier = output.own_calls[: output.own_calls.index(first)]
        lookups = tuple(call for call in earlier if call.name == "bash")
        if not lookups or any(
            token in str(call.input.get("command", "")).casefold()
            for call in lookups
            for token in ("clone", "fetch", "ls-files", " cat ")
        ):
            return CapabilityVerdict(False, "the parent did not limit its shell use to path lookup")
        checkout_paths = [f"/workspace/{TASK_REPOSITORY}"]
        if output.workspace_dir is not None:
            checkout_paths.append(str(output.workspace_dir / TASK_REPOSITORY))
        payload = first.input.get("payload")
        objective = payload.get("objective") if isinstance(payload, dict) else None
        target = first.input.get("target")
        if (
            not isinstance(target, str)
            or target.removeprefix("profile:") != "coding"
            or not isinstance(objective, str)
            or not any(
                f"use the existing checkout at {path}".casefold() in objective.casefold()
                for path in checkout_paths
            )
            or "do not clone or fetch" not in objective.casefold()
        ):
            return CapabilityVerdict(False, "the parent did not delegate the discovered path")
        if any(
            call.name in {"memory_search", "call_external_tool", "search_web"} for call in earlier
        ):
            return CapabilityVerdict(
                False, "the parent searched remotely before using the checkout"
            )
        return CapabilityVerdict(True, "the discovered checkout was delegated by exact path")

    return DescribedGrader(
        "the parent locates a named checkout and delegates its exact path without repository work",
        grade,
    )


def workspace_repository_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loads = tuple(
            call
            for call in output.own_calls
            if call.name == "load_skill" and call.input.get("name") == "coding"
        )
        if not loads:
            return CapabilityVerdict(False, "the parent did not load the coding workflow")
        spawns = tuple(call for call in output.own_calls if call.name == "spawn")
        if not spawns:
            return CapabilityVerdict(False, "the parent did not delegate the shared checkout")
        first = spawns[0]
        payload = first.input.get("payload")
        objective = payload.get("objective") if isinstance(payload, dict) else None
        target = first.input.get("target")
        if (
            not isinstance(target, str)
            or target.removeprefix("profile:") != "coding"
            or not isinstance(objective, str)
            or "use the existing checkout at /workspace/dclm" not in objective.casefold()
            or objective.casefold().lstrip().startswith("repository setup: clone")
        ):
            return CapabilityVerdict(False, "the parent did not use the shared checkout")
        if any(
            call.name in {"bash", "glob", "read"}
            for call in output.own_calls[: output.own_calls.index(first)]
        ):
            return CapabilityVerdict(False, "the parent inspected before delegating")
        return CapabilityVerdict(True, "the shared checkout stayed delegated")

    return DescribedGrader(
        "the parent loads coding and delegates an existing checkout under /workspace",
        grade,
    )


def url_repository_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loads = tuple(
            call
            for call in output.own_calls
            if call.name == "load_skill" and call.input.get("name") == "coding"
        )
        if not loads:
            return CapabilityVerdict(False, "the parent did not load the coding workflow")
        spawns = tuple(call for call in output.own_calls if call.name == "spawn")
        if not spawns:
            return CapabilityVerdict(False, "the parent did not delegate the URL-backed task")
        first = spawns[0]
        payload = first.input.get("payload")
        objective = payload.get("objective") if isinstance(payload, dict) else None
        target = first.input.get("target")
        if (
            not isinstance(target, str)
            or target.removeprefix("profile:") != "coding"
            or not isinstance(objective, str)
            or "clone https://github.com/octocat/Hello-World" not in objective
        ):
            return CapabilityVerdict(False, "the parent did not delegate with the repository URL")
        if any(
            call.name in {"bash", "glob", "read"}
            for call in output.own_calls[: output.own_calls.index(first)]
        ):
            return CapabilityVerdict(False, "the parent inspected before delegating")
        return CapabilityVerdict(True, "the URL-backed task stayed delegated")

    return DescribedGrader(
        "the parent loads coding and delegates a URL-backed repository before inspecting it",
        grade,
    )


def existing_checkout_execution_scorer(*, clone: bool) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        commands = tuple(
            str(call.input.get("command", "")) for call in output.calls if call.name == "bash"
        )
        cloned = any(re.search(r"(?:^|\s)git\s+clone(?:\s|$)", command) for command in commands)
        if cloned != clone:
            return CapabilityVerdict(False, "the child chose the wrong missing-checkout behavior")
        if not any(TASK_REPOSITORY in command and "ls-files" in command for command in commands):
            return CapabilityVerdict(False, "the child did not inspect the requested checkout")
        return CapabilityVerdict(True, "the child used the requested existing-checkout branch")

    branch = "clones the supplied URL when missing" if clone else "does not clone without a URL"
    return DescribedGrader(f"the coding child {branch} and reads the requested checkout", grade)


async def _prepare_repository(workspace_dir: Path, name: str) -> None:
    repository = workspace_dir / name
    await asyncio.to_thread(repository.mkdir, parents=True)
    for relative in TASK_REPOSITORY_FILES:
        path = repository / relative
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, f"{relative}\n")
    for argv in (
        ("init", "--initial-branch", "main"),
        ("add", *TASK_REPOSITORY_FILES),
        (*GIT_IDENTITY, "commit", "-m", "fixture"),
    ):
        process = await asyncio.create_subprocess_exec(
            "git",
            *argv,
            cwd=repository,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, error = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)} failed: {error.decode().strip()}")


async def prepare_task_repository(_workspace_id: UUID, workspace_dir: Path) -> None:
    await _prepare_repository(workspace_dir, TASK_REPOSITORY)


async def prepare_fallback_repository(_workspace_id: UUID, workspace_dir: Path) -> None:
    await _prepare_repository(workspace_dir, FALLBACK_REPOSITORY)


async def _seed_structured_agent(
    workspace_id: UUID, agent_id: UUID, _blob: WorkspaceBlobStore
) -> None:
    async with workspace_tx() as connection:
        parent = (
            await connection.execute(
                sa.select(tables.agent.c.model, tables.agent.c.reasoning).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.id == agent_id,
                )
            )
        ).one()
        existing = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == STRUCTURED_AGENT_NAME,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name=STRUCTURED_AGENT_NAME,
                    prompt=(
                        "Follow the task. Return its required machine-readable payload in full "
                        "through finish."
                    ),
                    model=parent.model,
                    reasoning=parent.reasoning,
                    visibility="workspace",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        else:
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == existing)
                .values(
                    prompt=(
                        "Follow the task. Return its required machine-readable payload in full "
                        "through finish."
                    ),
                    model=parent.model,
                    reasoning=parent.reasoning,
                    visibility="workspace",
                    updated_at=sa.func.now(),
                )
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

AUTODOC_DISCOVERY_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do not
use tools or implement. Return only the first wrong discovery boundary, the complete producer and
consumer path, the rejected partial boundary, and focused public regression coverage.

Sphinx must document a property wrapped by classmethod through both an explicit autoproperty
directive and autoclass with :members:. A patch extends PropertyDocumenter.can_document_member(),
import_object(), and add_directive_header(). The direct autoproperty and Python-domain tests pass,
but autoclass omits the member entirely. Member discovery calls getdoc() with self.parent and
self.object_name instead of the owning class and current member name. Sphinx's inspect helpers can
unwrap a classmethod descriptor through the class MRO when given that owner and name. Identify the
first wrong boundary and the smallest complete path to the already-working renderer."""

AUTODOC_DIRECT_MESSAGE = """You are at the implementation-boundary decision step of a coding task.
Do not use tools or implement. Return only the smallest edit and direct regression test.

Autoclass discovery, getdoc(), and classmethod descriptor unwrapping through the class MRO are
already proven. Explicit autoproperty reaches PropertyDocumenter but omits :classmethod: from its
directive header. Keep the change at the renderer and test its direct output; do not edit discovery
or inspect helpers."""

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

STATEFUL_TRANSFORM_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do not
use tools or implement. Return only the state owner, the required producer and consumer edges, the
rejected partial boundary, and focused public round-trip coverage.

A coordinate library adds direct transforms between ITRS and observed AltAz/HADec frames. A patch
subtracts observed_frame.location inside the direct function, but ITRS owns only cartesian data and
obstime. observed_to_itrs() therefore returns a frame with no observer location, and later ITRS to
CIRS or TETE edges silently use the geocentric default. State the frame attribute that must own the
location, how both direct and intermediate transform directions must propagate it, and tests across
same and different locations and obstimes. Matrix and refraction formulas are already proven."""

STATELESS_TRANSFORM_MESSAGE = """You are at the implementation-boundary decision step of a coding
task. Do not use tools or implement. Return only the smallest edit and focused round-trip test.

A graphics library adds Pixel(x, y) to Normalized(u, v) transforms. Both frames contain only their
coordinate pair; scale and translation are explicit function arguments, and no origin, calibration,
time, or location survives a call. The two local conversion functions already cover every consumer.
Keep this proven stateless boundary local; do not add frame attributes or edit unrelated
transforms."""

DERIVED_STATE_REMOVAL_MESSAGE = """You are at the failing-test diagnosis step of a coding task. Do
not use tools or implement. Return only the canonical surviving state, the first wrong production
operation, the minimal repair, and focused branch coverage.

In xarray, Dataset(coords={'x': [1, 2]}).reset_index('x', drop=True) removes the only coordinate and
index. A patch passes the remaining variables and indexes through Dataset._replace(), which retains
self._dims, so the empty Dataset still reports dimension x: 2. It also adds a DataVariables.__len__
assertion. State how dimensions must be reconstructed and test the last-owner deletion path without
weakening or merely checking the invariant."""

DERIVED_STATE_SURVIVOR_MESSAGE = """You are at the implementation-boundary decision step of a coding
task. Do not use tools or implement. Return only the canonical state rule and focused neighboring
regression.

Dataset({'value': ('x', [3, 4])}, coords={'x': [1, 2]}).reset_index('x', drop=True) removes the
index coordinate, but the surviving value variable still owns dimension x. State how reconstruction
from surviving variables retains x here while removing it when no variable references it. Do not
delete the named dimension unconditionally or add a length clamp."""

LOCAL_VALIDATION_MESSAGE = """You are at the validation-planning step of a pinned coding task. Do
not use tools or implement. Return only the tests to run, their connection to the change, and the
stop condition.

A one-function change makes infer_node() return the declared type for a TypeAlias expression
instead of None. A direct inference test covers the new branch. The nearest public consumer is one
writer test that renders the inferred type. The full core suite takes 600 seconds, while a ten-file
inference sweep takes 900 seconds and crosses unrelated packages. Choose the validation boundary
after the implementation and focused regressions are in place."""

CROSS_CUTTING_VALIDATION_MESSAGE = """You are at the validation-planning step of a coding task. Do
not use tools or implement. Return only the tests to run, their connection to the change, and the
stop condition.

The shared InferenceResult protocol changes a serialized field used by every analyzer adapter and
public writer. The patch updates the protocol, every implementation, and every direct consumer.
One focused contract test proves the new wire shape. Choose the remaining validation boundary for
this cross-package public contract change."""


@dataclass(frozen=True)
class DecisionEvidence:
    """A decision case's criteria: what the answer must establish and what its chosen approach
    must avoid. The requirements are judged semantically over the relayed decision text — a
    substring match under-credits a correct answer phrased in words the variant list lacks, which
    a manual review of a 30-case baseline measured at six of twenty-one completed decisions. Each
    `required` entry keeps its variant list as the judge's example phrasings; `forbidden` names
    approaches the answer must not adopt, where naming one only to reject it still passes. The
    deterministic half that remains is the tool ban: the decision step is judged on reasoning
    alone, so any tool call fails before the judge reads a word."""

    statement: str
    required: tuple[tuple[str, tuple[str, ...]], ...]
    forbidden: tuple[str, ...] = ()

    @property
    def rubric(self) -> tuple[str, ...]:
        criteria = tuple(
            f"the answer establishes {label} (for example: {'; '.join(choices[:2])})"
            for label, choices in self.required
        )
        if self.forbidden:
            criteria += (
                "the approach the answer commits to avoids "
                + ", ".join(self.forbidden)
                + " — naming one of these only to reject it still passes",
            )
        return criteria

    def grader(self) -> Grader:
        async def grade(output: CapabilityOutput) -> CapabilityVerdict:
            if output.calls:
                return CapabilityVerdict(False, "used tools at the isolated decision step")
            return CapabilityVerdict(True, "decision produced without tools")

        return DescribedGrader(self.statement, grade)


def cross_layer_error_emitter_evidence() -> DecisionEvidence:
    """The NaN path must repair the first error emitter or restore the shared validator path."""
    return DecisionEvidence(
        "the answer traces the active NaN emitter, repairs its value params, and tests the "
        "public value placeholder",
        (
            ("DecimalField.validate", ("decimalfield.validate",)),
            (
                "the bypassed DecimalValidator path",
                (
                    "decimalfield.validate before decimalvalidator",
                    "decimalfield.validate bypass",
                    "short-circuit",
                    "short circuit",
                    "run_validators never runs",
                    "decimalvalidator is unreachable",
                ),
            ),
            (
                "the production repair",
                (
                    "remove decimalfield.validate",
                    "delete decimalfield.validate",
                    "remove the decimalfield.validate",
                    "remove the validate override",
                    "delete the validate override",
                    "let decimalvalidator",
                    "allow decimalvalidator",
                    "delegate to decimalvalidator",
                    "params={'value': value}",
                    'params={"value": value}',
                ),
            ),
            ("the NaN case", ("nan",)),
            ("a public form regression", ("form test", "public form", "form.errors")),
            ("the value placeholder", ("%(value)s", "value placeholder")),
        ),
    )


def direct_error_emitter_evidence() -> DecisionEvidence:
    """The bad-scheme neighbor stays at its direct emitter instead of expanding across layers."""
    evidence = DecisionEvidence(
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
        (
            "edits to DecimalField",
            "edits to field.clean",
            "edits to run_validators",
            "edits to to_python",
            "model field changes",
        ),
    )
    return evidence


def composite_modulus_boundary_evidence() -> DecisionEvidence:
    """The requested solver spans composite factors; a correct prime-zero shortcut is incomplete."""
    return DecisionEvidence(
        "the answer decomposes the full composite nth-root solver and tests its public boundary",
        (
            ("prime-power factorization", ("prime-power", "prime power", "factorint")),
            (
                "root lifting",
                ("hensel", "lift each root", "lift roots", "lifting roots", "lift unit roots"),
            ),
            (
                "singular-root handling",
                (
                    "singular root",
                    "singular lift",
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


def prime_zero_boundary_evidence() -> DecisionEvidence:
    """A prime-only request stays at the zero branch and does not grow a composite solver."""
    return DecisionEvidence(
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


def middleware_override_evidence(*, repository_wide: bool) -> DecisionEvidence:
    if repository_wide:
        return DecisionEvidence(
            "the answer repairs every constructor that bypasses the shared middleware contract",
            (
                (
                    "the shared contract owner",
                    ("middlewaremixin.__init__", "middlewaremixin constructor"),
                ),
                (
                    "the bypassed async classification",
                    (
                        "_async_check",
                        "async classification",
                        "coroutine classification",
                        "never marked as a coroutine",
                        "classification never runs",
                    ),
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
                        "remaining middlewaremixin subclasses",
                    ),
                ),
                ("super constructor calls", ("super.__init__", "call super")),
                (
                    "the affected constructor family",
                    ("cachemiddleware", "fetchfromcachemiddleware", "securitymiddleware"),
                ),
                (
                    "sync and async constructor coverage",
                    (
                        "sync and async",
                        "sync/async",
                        "both sync and async",
                        "both directions",
                        "sync get_response",
                    ),
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
    return DecisionEvidence(
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


def annotation_state_evidence(*, referenced: bool) -> DecisionEvidence:
    if referenced:
        return DecisionEvidence(
            "the answer retains an unselected alias when an active predicate references it",
            (
                ("the HAVING dependency", ("having", "filter")),
                ("reference closure", ("reference closure", "dependency closure", "referenced")),
                ("the aggregation path", ("retain", "keep", "subquery")),
                ("the aliased aggregate", ("chapter_count", "chapter count")),
            ),
            ("prune every unselected", "prune all unselected", "unconditionally prune"),
        )
    return DecisionEvidence(
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
                (
                    "one select",
                    "single select",
                    "one statement",
                    "no subquery",
                    "without a subquery",
                ),
            ),
        ),
    )


def pyreverse_consumer_evidence(*, public_output: bool) -> DecisionEvidence:
    if public_output:
        return DecisionEvidence(
            "the answer wires the declared helper boundary through the rendered output consumer",
            (
                ("the public helper module", ("pylint.pyreverse.utils", "pyreverse/utils.py")),
                ("the Linker producer", ("linker", "inspector")),
                ("the DOT writer consumer", ("writer", "dot")),
                ("parameter annotations", ("parameter", "argument")),
                ("return annotations", ("return annotation", "return type", "node.returns")),
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
    return DecisionEvidence(
        "the answer keeps a proven private branch and its test in the inspector",
        (
            ("the local implementation", ("inspector.py",)),
            ("the private Linker branch", ("linker",)),
            (
                "a direct regression",
                ("direct test", "direct regression test", "inspector test", "unit test"),
            ),
            (
                "the unchanged writer boundary",
                (
                    "writer unchanged",
                    "writer output is unchanged",
                    "writer output stays unchanged",
                    "writer output stays identical",
                    "leave writer",
                    "no change to writer",
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
                    "no new module-level helper",
                    "add no helper",
                    "do not create a public helper",
                ),
            ),
        ),
    )


def discovery_boundary_evidence(*, public_discovery: bool) -> DecisionEvidence:
    if public_discovery:
        return DecisionEvidence(
            "the answer repairs public member discovery through the existing renderer",
            (
                ("the public collection boundary", ("autoclass", ":members:", "member discovery")),
                ("the metadata producer", ("getdoc",)),
                ("the owning class", ("self.object",)),
                ("the current member", ("membername", "member name")),
                (
                    "descriptor lookup",
                    ("getmro", "class mro", "walks the mro", "through the mro", "__dict__"),
                ),
                ("descriptor unwrapping", ("__func__", "unwrap")),
                ("the render consumer", ("propertydocumenter",)),
                (
                    "public regression coverage",
                    (
                        "autoclass includes",
                        "autoclass output",
                        "class members",
                        "test_ext_autodoc_autoclass",
                        "all through public entry points",
                        "focused public regression",
                    ),
                ),
                (
                    "rejection of the direct-only patch",
                    (
                        "direct-only",
                        "direct only",
                        "incomplete",
                        "not sufficient",
                        "rejected partial boundary",
                        "extra compensation in propertydocumenter",
                        "specialized unwrap",
                    ),
                ),
            ),
        )
    return DecisionEvidence(
        "the answer keeps proven discovery unchanged and repairs only the direct renderer",
        (
            ("the renderer owner", ("propertydocumenter",)),
            ("the directive header", ("add_directive_header", "directive header")),
            ("the classmethod option", (":classmethod:", "classmethod option")),
            (
                "direct regression coverage",
                (
                    "direct autoproperty",
                    "autoproperty output",
                    "direct output",
                    "direct regression test",
                    "do_autodoc(app, 'property'",
                ),
            ),
        ),
        (
            "change getdoc",
            "edit getdoc",
            "change getmro",
            "edit getmro",
            "change member discovery",
            "edit member discovery",
        ),
    )


def precedence_evidence(*, python_mro: bool) -> DecisionEvidence:
    if python_mro:
        return DecisionEvidence(
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
                        ".__mro__ forward",
                        "from most specific to least specific",
                        "from the subclass through its bases",
                        "from left to right",
                    ),
                ),
                ("direct class state", ("__dict__", "direct pytestmark")),
                ("the expected order", ("c, a, b", "['c', 'a', 'b']", "c then a then b")),
                (
                    "the local store path",
                    (
                        "consider_mro=false",
                        "consider_mro = false",
                        "direct marks in store_mark",
                        "store_mark copies only direct marks",
                        "store_mark copy and update only",
                        "store_mark read the target class",
                        "store_mark copy and extend only",
                        "decorated class's directly declared pytestmark",
                        "target class's direct pytestmark",
                    ),
                ),
            ),
        )
    return DecisionEvidence(
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


def transform_state_evidence(*, stateful: bool) -> DecisionEvidence:
    if stateful:
        return DecisionEvidence(
            "the answer gives location one frame owner and propagates it through every transform "
            "edge that consumes it",
            (
                (
                    "the ITRS location owner",
                    ("itrs.location", "earthlocationattribute", "location attribute on itrs"),
                ),
                (
                    "the observed-to-ITRS producer",
                    ("observed_to_itrs", "altaz to itrs", "hadec to itrs"),
                ),
                (
                    "the intermediate consumers",
                    ("cirs and tete", "itrs_to_cirs", "itrs_to_tete"),
                ),
                (
                    "both transform directions",
                    ("both directions", "to and from", "each direction", "round trip"),
                ),
                ("the location branch", ("different locations", "location-to-location")),
                ("the time branch", ("different obstimes", "different obstime")),
                ("public round-trip coverage", ("round trip", "round-trip")),
            ),
        )
    return DecisionEvidence(
        "the answer keeps a proven stateless transform local",
        (
            ("the local conversions", ("pixel", "normalized")),
            (
                "the stateless contract",
                (
                    "stateless",
                    "no state",
                    "no frame attributes",
                    "no value needs an owner",
                ),
            ),
            ("round-trip coverage", ("round trip", "round-trip")),
        ),
        ("add location", "add obstime", "earthlocationattribute", "edit every transform"),
    )


def derived_state_evidence(*, surviving_owner: bool) -> DecisionEvidence:
    if surviving_owner:
        return DecisionEvidence(
            "the answer derives dimensions from surviving variables and retains live owners",
            (
                ("the surviving value variable", ("value variable", "data variable")),
                (
                    "the retained dimension",
                    ("retain x", "keep x", "keeps x", "x remains", "x survives"),
                ),
                (
                    "canonical reconstruction",
                    ("surviving variables", "remaining variables", "rederive", "recompute"),
                ),
                (
                    "the last-owner neighbor",
                    (
                        "no variable",
                        "no surviving variable",
                        "last owner",
                        "last-owner",
                        "unreferenced",
                    ),
                ),
            ),
            (
                "always delete x",
                "unconditionally delete",
                "add a length clamp",
                "use a length clamp",
                "length-clamp fix",
                "clamp the length",
            ),
        )
    return DecisionEvidence(
        "the answer reconstructs derived dimensions after the last canonical owner is removed",
        (
            ("the wrong _replace path", ("_replace",)),
            (
                "dimension reconstruction",
                ("_replace_with_new_dims", "rederive", "recompute", "reconstruct"),
            ),
            (
                "surviving variables as canonical state",
                ("surviving variables", "remaining variables"),
            ),
            (
                "the empty result",
                (
                    "no dimensions",
                    "empty dims",
                    "sizes are empty",
                    "_dims = {}",
                    "_dims == {}",
                    "empty _variables",
                    "identical(dataset",
                    "remove x",
                    "drops x",
                ),
            ),
            ("the last-owner regression", ("last owner", "only coordinate", "sole coordinate")),
        ),
        (
            "add an assertion",
            "add a length clamp",
            "use a length clamp",
            "length-clamp fix",
            "clamp the length",
        ),
    )


def validation_scope_evidence(*, cross_cutting: bool) -> DecisionEvidence:
    if cross_cutting:
        return DecisionEvidence(
            "the answer validates every consumer of a cross-cutting public contract",
            (
                (
                    "the shared public contract",
                    (
                        "shared inferenceresult protocol",
                        "shared protocol",
                        "public contract",
                        "public and serialized",
                        "serialized and public",
                        "serialized field",
                    ),
                ),
                (
                    "the cross-cutting scope",
                    (
                        "cross-cutting",
                        "cross package",
                        "cross-package",
                        "crosses package lines",
                        "whole repository",
                        "indirect consumers",
                    ),
                ),
                (
                    "focused contract coverage",
                    ("focused contract", "contract test", "wire shape"),
                ),
                (
                    "every direct consumer",
                    (
                        "every direct consumer",
                        "all direct consumers",
                        "all adapters",
                        "each analyzer adapter",
                        "every public writer",
                        "each public writer",
                        "each direct consumer",
                        "direct callers",
                        "every test file next to a changed direct consumer",
                    ),
                ),
                (
                    "broad validation",
                    (
                        "full project-wide suite",
                        "full repository suite",
                        "full suite",
                        "all affected integration",
                    ),
                ),
            ),
            (
                "do not run the full",
                "do not run full",
                "full suite out of scope",
                "full repository suite out of scope",
            ),
        )
    evidence = DecisionEvidence(
        "the answer stops validation at the changed rule and nearest public regression",
        (
            ("the changed inference rule", ("inference rule", "infer_node", "typealias")),
            (
                "a direct focused test",
                ("direct test", "direct inference test", "focused test"),
            ),
            (
                "the nearest public regression",
                ("nearest writer", "nearest public consumer", "nearest consumer"),
            ),
            (
                "a stop condition",
                ("stop there", "once both pass", "when both pass", "stop condition"),
            ),
            (
                "rejection of the full suite",
                (
                    "skip the full suite",
                    "do not run the full core suite",
                    "the full suite is out of scope",
                ),
            ),
            (
                "rejection of the broad file sweep",
                (
                    "skip the ten-file inference sweep",
                    "do not run the inference sweep",
                    "the file sweep is out of scope",
                ),
            ),
        ),
    )

    return evidence


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
    "assumptions engine whose facts are authored in `sympy/assumptions/ask.py`, and commits "
    "`sympy/assumptions/ask_generated.py` from it. Inventory the authoritative implementation "
    "files and the generated artifact/action before editing. Do not list tests, "
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
    CapabilityCase(
        "workspace-agent-structured-result",
        "Run exactly one spawn with target `agent:structured-worker`. Pass the text inside <task> "
        "verbatim as `payload.task`; do not solve, summarize, or modify it. When its result "
        "arrives, reply with that result verbatim.\n\n"
        f"<task>\n{STRUCTURED_REVIEW_OBJECTIVE}\n</task>",
        workspace_agent_result_scorer(STRUCTURED_REVIEW_OBJECTIVE, STRUCTURED_REVIEW_RESULT),
        seed=_seed_structured_agent,
        digest_tag="workspace-agent:structured-result:v1",
    ),
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
        "coding-subagent-task-environment",
        "Use the coding workflow to report the number of tracked files in my repository dclm. Do "
        "not change it. Stop after the first attempted route; do not retry failed tools or "
        "delegate substitute workers.",
        task_environment_scorer(),
        samples=1,
        prepare=prepare_task_repository,
        digest_tag="delegation:coding-subagent-task-environment:v6",
    ),
    CapabilityCase(
        "coding-subagent-workspace-repository",
        "Use the coding workflow to report the tracked-file count for the existing checkout at "
        "/workspace/dclm. Do not change it. Stop after the first attempted route; do not retry "
        "failed tools or delegate substitute workers.",
        workspace_repository_scorer(),
        samples=1,
        prepare=prepare_task_repository,
        digest_tag="delegation:coding-subagent-workspace-repository:v1",
    ),
    CapabilityCase(
        "coding-subagent-url-repository",
        "Use the coding workflow to report the tracked-file count for "
        "https://github.com/octocat/Hello-World. Do not change it. Stop after the first attempted "
        "route; do not retry failed tools or delegate substitute workers.",
        url_repository_scorer(),
        samples=1,
        web_dependent=True,
        digest_tag="delegation:coding-subagent-url-repository:v2",
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


def decision_case(
    name: str,
    message: str,
    evidence: DecisionEvidence,
    *,
    digest_tag: str,
    samples: int = 1,
) -> CapabilityCase:
    """A decision case judged on its relayed answer: the proxy contract and the tool ban stay
    deterministic, and the decision content is the case rubric."""
    return CapabilityCase(
        name,
        profile_proxy_message(message),
        profile_proxy_scorer(message, evidence.grader(), relayed_answer=True),
        rubric=evidence.rubric,
        samples=samples,
        digest_tag=digest_tag,
    )


PROFILE_CASES = (
    CapabilityCase(
        "coding-profile-existing-checkout-no-url",
        profile_proxy_message(EXISTING_CHECKOUT_NO_URL_OBJECTIVE),
        profile_proxy_scorer(
            EXISTING_CHECKOUT_NO_URL_OBJECTIVE,
            combine(exact_scorer("3"), existing_checkout_execution_scorer(clone=False)),
        ),
        samples=1,
        prepare=prepare_task_repository,
        digest_tag="coding-profile:existing-checkout:no-url:v1",
    ),
    CapabilityCase(
        "coding-profile-existing-checkout-url-fallback",
        profile_proxy_message(EXISTING_CHECKOUT_URL_FALLBACK_OBJECTIVE),
        profile_proxy_scorer(
            EXISTING_CHECKOUT_URL_FALLBACK_OBJECTIVE,
            combine(exact_scorer("3"), existing_checkout_execution_scorer(clone=True)),
        ),
        samples=1,
        prepare=prepare_fallback_repository,
        digest_tag="coding-profile:existing-checkout:url-fallback:v1",
    ),
    CapabilityCase(
        "coding-subagent-structured-review-result",
        profile_proxy_message(STRUCTURED_REVIEW_OBJECTIVE),
        profile_proxy_scorer(
            STRUCTURED_REVIEW_OBJECTIVE,
            structured_review_result_scorer(STRUCTURED_REVIEW_RESULT),
        ),
        digest_tag="coding-profile:structured-review-result:v1",
    ),
    decision_case(
        "coding-subagent-middleware-override-contract",
        MIDDLEWARE_OVERRIDE_MESSAGE,
        middleware_override_evidence(repository_wide=True),
        digest_tag="coding-profile:middleware-override-contract:v1",
    ),
    decision_case(
        "coding-subagent-middleware-proven-local",
        MIDDLEWARE_LOCAL_MESSAGE,
        middleware_override_evidence(repository_wide=False),
        digest_tag="coding-profile:middleware-override-contract:proven-local-v1",
    ),
    decision_case(
        "coding-subagent-annotation-active-state",
        ANNOTATION_ACTIVE_STATE_MESSAGE,
        annotation_state_evidence(referenced=False),
        digest_tag="coding-profile:annotation-active-state:v1",
    ),
    decision_case(
        "coding-subagent-annotation-referenced-state",
        ANNOTATION_REFERENCED_MESSAGE,
        annotation_state_evidence(referenced=True),
        digest_tag="coding-profile:annotation-active-state:referenced-v1",
    ),
    decision_case(
        "coding-subagent-pyreverse-consumer-boundary",
        PYREVERSE_CONSUMER_MESSAGE,
        pyreverse_consumer_evidence(public_output=True),
        digest_tag="coding-profile:pyreverse-consumer-boundary:v1",
    ),
    decision_case(
        "coding-subagent-pyreverse-proven-local",
        PYREVERSE_LOCAL_MESSAGE,
        pyreverse_consumer_evidence(public_output=False),
        digest_tag="coding-profile:pyreverse-consumer-boundary:proven-local-v1",
    ),
    decision_case(
        "coding-subagent-autodoc-discovery-boundary",
        AUTODOC_DISCOVERY_MESSAGE,
        discovery_boundary_evidence(public_discovery=True),
        digest_tag="coding-profile:autodoc-discovery-boundary:v1",
    ),
    decision_case(
        "coding-subagent-autodoc-direct-renderer",
        AUTODOC_DIRECT_MESSAGE,
        discovery_boundary_evidence(public_discovery=False),
        digest_tag="coding-profile:autodoc-discovery-boundary:direct-v1",
    ),
    decision_case(
        "coding-subagent-mro-precedence",
        MRO_PRECEDENCE_MESSAGE,
        precedence_evidence(python_mro=True),
        digest_tag="coding-profile:mro-precedence:v1",
    ),
    decision_case(
        "coding-subagent-base-first-precedence",
        BASE_FIRST_PRECEDENCE_MESSAGE,
        precedence_evidence(python_mro=False),
        digest_tag="coding-profile:mro-precedence:base-first-v1",
    ),
    decision_case(
        "coding-subagent-stateful-transform-boundary",
        STATEFUL_TRANSFORM_MESSAGE,
        transform_state_evidence(stateful=True),
        digest_tag="coding-profile:transform-state:stateful-v1",
    ),
    decision_case(
        "coding-subagent-stateless-transform-boundary",
        STATELESS_TRANSFORM_MESSAGE,
        transform_state_evidence(stateful=False),
        digest_tag="coding-profile:transform-state:stateless-v1",
    ),
    decision_case(
        "coding-subagent-derived-state-last-owner",
        DERIVED_STATE_REMOVAL_MESSAGE,
        derived_state_evidence(surviving_owner=False),
        digest_tag="coding-profile:derived-state:last-owner-v1",
    ),
    decision_case(
        "coding-subagent-derived-state-surviving-owner",
        DERIVED_STATE_SURVIVOR_MESSAGE,
        derived_state_evidence(surviving_owner=True),
        digest_tag="coding-profile:derived-state:surviving-owner-v1",
    ),
    decision_case(
        "coding-subagent-local-validation-scope",
        LOCAL_VALIDATION_MESSAGE,
        validation_scope_evidence(cross_cutting=False),
        digest_tag="coding-profile:validation-scope:local-v1",
    ),
    decision_case(
        "coding-subagent-cross-cutting-validation-scope",
        CROSS_CUTTING_VALIDATION_MESSAGE,
        validation_scope_evidence(cross_cutting=True),
        digest_tag="coding-profile:validation-scope:cross-cutting-v1",
    ),
    decision_case(
        "coding-subagent-composite-modulus-boundary",
        COMPOSITE_MODULUS_MESSAGE,
        composite_modulus_boundary_evidence(),
        digest_tag="coding-profile:composite-modulus-boundary:v1",
    ),
    decision_case(
        "coding-subagent-prime-zero-boundary",
        PRIME_ZERO_MESSAGE,
        prime_zero_boundary_evidence(),
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
    decision_case(
        "coding-subagent-cross-layer-error-emitter",
        CROSS_LAYER_ERROR_MESSAGE,
        cross_layer_error_emitter_evidence(),
        digest_tag="coding-profile:cross-layer-error-emitter:v1",
    ),
    decision_case(
        "coding-subagent-direct-error-emitter",
        DIRECT_ERROR_MESSAGE,
        direct_error_emitter_evidence(),
        digest_tag="coding-profile:direct-error-emitter:v1",
    ),
    CapabilityCase(
        "coding-subagent-implementation-inventory",
        profile_proxy_message(IMPLEMENTATION_INVENTORY_OBJECTIVE),
        profile_proxy_scorer(
            IMPLEMENTATION_INVENTORY_OBJECTIVE,
            implementation_inventory_scorer(repository_wide=True),
        ),
        digest_tag="coding-profile:implementation-inventory:assumptions-engines-v2",
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
