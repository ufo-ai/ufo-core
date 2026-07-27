"""Oracle guards for the connector-reference suite: every grader is driven in both directions, so
no case can pass through a grader that cannot fail. The tool result a grader reads is assembled from
the same shipped constants the graders key on — the condensing pass's reference key and the engine's
own offload notice — so the guards exercise the real markers rather than a copy of them. That the
seeded pages actually carry those markers, on the side of the inline budget each case measures, is
proved against the real dispatch in `extensions/eval_env/tests/test_ext_eval_env.py`."""

import asyncio
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_connectors.tools import DEDUPE_REFERENCE_KEY
from ufo_ext_eval_env.manifest import CODE_FIXTURE_PREFIX, CODE_PROVIDER, NAME

from evals import connector_refs
from evals.harness.capability import CapabilityOutput, ToolInvocation
from ufo.agent_scope import agent
from ufo.db import workspace_tx
from ufo.grants import GrantStore
from ufo.loop.engine import OFFLOAD_NOTICE, TOOL_OUTPUT_DIR
from ufo.schema import tables
from ufo.sdk.context import ScopedStore
from ufo.workspace import ws

FLEET_LICENSE = connector_refs.token("LIC", connector_refs.FLEET)
LEGACY_LICENSE = connector_refs.token("LIC", connector_refs.LEGACY)
NARROWED = ToolInvocation(
    "bash",
    {"command": f"jq '.items[26]' {TOOL_OUTPUT_DIR}/call-1.txt"},
    "{}",
    has_result=True,
)


def _result(query: str, pointers: bool = True, offloaded: bool = False) -> str:
    body = json.dumps(
        {
            "query": query,
            "items": [
                {"repository": {"license": {"spdx_id": FLEET_LICENSE}}},
                {"repository": {DEDUPE_REFERENCE_KEY: "/items/0/repository"} if pointers else {}},
            ],
        }
    )
    if not offloaded:
        return body
    return body + OFFLOAD_NOTICE.format(total=99_999, path=f"{TOOL_OUTPUT_DIR}/call-1.txt")


def _search(
    query: str,
    pointers: bool = True,
    offloaded: bool = False,
    tool: str = connector_refs.CALL_TOOL,
    is_error: bool = False,
) -> ToolInvocation:
    return ToolInvocation(
        tool,
        {"tool_name": connector_refs.SEARCH_SLUG, "arguments": {"query": query}},
        _result(query, pointers, offloaded),
        has_result=True,
        is_error=is_error,
    )


def _output(
    query: str,
    answer: str,
    pointers: bool = True,
    offloaded: bool = False,
    narrowed: bool = False,
) -> CapabilityOutput:
    search = _search(query, pointers, offloaded)
    calls = (search, NARROWED) if narrowed else (search,)
    return CapabilityOutput(f"Here you go.\nANSWER: {answer}", calls)


def _answered(answer: str, calls: tuple[ToolInvocation, ...]) -> CapabilityOutput:
    return CapabilityOutput(f"Here you go.\nANSWER: {answer}", calls)


async def _workspace_with_owner() -> tuple[UUID, UUID]:
    """A workspace carrying the member a seed reads and the agent its grant references."""
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="evals@localhost",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def test_every_case_seed_lands_when_they_all_run_at_once(db: None) -> None:
    """The harness fans cases out under the run's `--concurrency` semaphore, so every seed can run
    against one workspace at the same time. `ScopedStore.put` is check-then-act, so two seeds on one
    key would both insert and the loser would raise on `ext_store`'s primary key, crashing the whole
    invocation rather than one case — which is why each case seeds only its own page. Driven
    concurrently here, on Postgres as well as SQLite: nothing raises and every page lands."""
    workspace_id, agent_id = await _workspace_with_owner()
    seeds = [case.seed for case in connector_refs.CASES if case.seed is not None]
    assert len(seeds) == len(connector_refs.QUERIES)
    with ws(workspace_id), agent(agent_id):
        await asyncio.gather(*(seed(workspace_id, agent_id) for seed in seeds))
        store = ScopedStore(extension=NAME)
        landed = {
            query: await store.get(f"{CODE_FIXTURE_PREFIX}{query}")
            for query in connector_refs.QUERIES
        }
        grants = await GrantStore().active_grants()

    assert landed == connector_refs.QUERIES
    assert [grant.provider for grant in grants] == [CODE_PROVIDER]


def test_every_seeded_page_is_asked_for_by_exactly_one_case() -> None:
    """Both ends: a fixture page no case searches is dead weight, and a case naming a page the seed
    never lays down would fail loud inside the provider instead of grading anything."""
    asked = [
        query
        for query in connector_refs.QUERIES
        if sum(f"'{query}'" in case.message for case in connector_refs.CASES) == 1
    ]
    assert asked == list(connector_refs.QUERIES)
    seeded = {
        case.name: case.seed is not None
        for case in connector_refs.CASES
        if any(f"'{query}'" in case.message for query in connector_refs.QUERIES)
    }
    assert all(seeded.values()), seeded


async def test_the_reference_grader_accepts_a_resolved_answer() -> None:
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    verdict = await grader(_output("widget-reserve", FLEET_LICENSE))
    assert verdict.passed, verdict.reason


async def test_the_reference_grader_rejects_a_wrong_literal() -> None:
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    verdict = await grader(_output("widget-reserve", "LIC-000000"))
    assert not verdict.passed
    assert "expected" in verdict.reason


async def test_the_reference_grader_rejects_the_record_it_did_not_resolve() -> None:
    """The trap direction: the answer that is reachable without following the pointer fails, even
    though it names a repository the payload really contains."""
    grader = connector_refs._graded_reference(
        "widget-capacity", FLEET_LICENSE, decoys=(LEGACY_LICENSE,), offloaded=True
    )
    verdict = await grader(
        _output(
            "widget-capacity",
            f"{FLEET_LICENSE} (or {LEGACY_LICENSE})",
            offloaded=True,
            narrowed=True,
        )
    )
    assert not verdict.passed
    assert LEGACY_LICENSE in verdict.reason


async def test_the_reference_grader_rejects_a_result_that_stopped_repeating() -> None:
    """Raising the dedupe floor, or perturbing one copy, leaves a result with no pointer in it — the
    case would then grade nothing about references, so it fails instead."""
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    verdict = await grader(_output("widget-reserve", FLEET_LICENSE, pointers=False))
    assert not verdict.passed
    assert "no pointer" in verdict.reason


async def test_the_reference_grader_rejects_a_result_on_the_wrong_side_of_the_budget() -> None:
    inline = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    offloaded = connector_refs._graded_reference("widget-lease", FLEET_LICENSE, offloaded=True)
    grew = await inline(_output("widget-reserve", FLEET_LICENSE, offloaded=True, narrowed=True))
    shrank = await offloaded(_output("widget-lease", FLEET_LICENSE))
    assert not grew.passed and "offloaded" in grew.reason
    assert not shrank.passed and "inline" in shrank.reason


async def test_the_reference_grader_rejects_an_offloaded_answer_that_read_no_file() -> None:
    grader = connector_refs._graded_reference("widget-lease", FLEET_LICENSE, offloaded=True)
    verdict = await grader(_output("widget-lease", FLEET_LICENSE, offloaded=True))
    assert not verdict.passed
    assert TOOL_OUTPUT_DIR in verdict.reason


async def test_the_reference_grader_rejects_a_pointer_handed_back_as_the_value() -> None:
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    verdict = await grader(_output("widget-reserve", f"{FLEET_LICENSE} at /items/0/repository"))
    assert not verdict.passed
    assert "handed back the pointer" in verdict.reason


async def test_the_reference_grader_rejects_a_search_on_another_query() -> None:
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    verdict = await grader(_output("widget-lease", FLEET_LICENSE))
    assert not verdict.passed
    assert "widget-reserve" in verdict.reason


async def test_the_reference_grader_counts_only_a_successful_call_of_the_search_tool() -> None:
    """`_searches` is the filter under every reference grader, so what it admits carries all eleven
    cases: an errored search never produced the result being read, and another tool called with the
    same arguments is not that search at all. Either one alone leaves the case unevidenced."""
    grader = connector_refs._graded_reference("widget-reserve", FLEET_LICENSE)
    errored = await grader(_answered(FLEET_LICENSE, (_search("widget-reserve", is_error=True),)))
    other_tool = await grader(
        _answered(FLEET_LICENSE, (_search("widget-reserve", tool="search_connector_tools"),))
    )
    assert not errored.passed and "no successful" in errored.reason
    assert not other_tool.passed and "no successful" in other_tool.reason


async def test_only_a_narrowing_tool_counts_as_reading_the_offload_file() -> None:
    """The offload check asks whether the model filtered the file, so a call that merely names the
    path under a tool that reads nothing — a write, a share — is not evidence it did."""
    grader = connector_refs._graded_reference("widget-lease", FLEET_LICENSE, offloaded=True)
    wrote = ToolInvocation(
        "write",
        {"path": f"{TOOL_OUTPUT_DIR}/notes.txt", "content": "x"},
        "ok",
        has_result=True,
    )
    verdict = await grader(
        _answered(FLEET_LICENSE, (_search("widget-lease", offloaded=True), wrote))
    )
    assert not verdict.passed
    assert TOOL_OUTPUT_DIR in verdict.reason


async def test_the_cost_arms_grader_requires_every_fact_and_its_own_arm() -> None:
    """The two arms differ only in how their records are presented, so each arm's grader refuses the
    other's result shape — a mis-seeded pair can never be compared as if it were matched."""
    condensed, uncondensed = connector_refs.CASES[-2], connector_refs.CASES[-1]
    facts = (
        FLEET_LICENSE,
        connector_refs.token("LIC", connector_refs.TELEMETRY),
        connector_refs.token("LIC", connector_refs.LEDGER),
    )
    whole = await condensed.grader(
        _output("ledger-post", ", ".join(facts), offloaded=True, narrowed=True)
    )
    partial = await condensed.grader(
        _output("ledger-post", ", ".join(facts[:2]), offloaded=True, narrowed=True)
    )
    wrong_arm = await condensed.grader(
        _output("ledger-post", ", ".join(facts), pointers=False, offloaded=True, narrowed=True)
    )
    assert whole.passed, whole.reason
    assert not partial.passed and facts[2] in partial.reason
    assert not wrong_arm.passed and "arm" in wrong_arm.reason
    audit = await uncondensed.grader(
        _output("ledger-audit", ", ".join(facts), offloaded=True, narrowed=True)
    )
    assert not audit.passed
    unevidenced = await condensed.grader(_answered(", ".join(facts), ()))
    inline = await condensed.grader(_output("ledger-post", ", ".join(facts)))
    assert not unevidenced.passed and "no successful" in unevidenced.reason
    assert not inline.passed and "wrong side" in inline.reason


async def test_the_pointer_literacy_grader_needs_both_halves() -> None:
    grader = connector_refs._graded_pointer_literacy()
    good = await grader(CapabilityOutput(f"ANSWER: BLD-11AA22 {connector_refs.POINTER_ESCAPE}", ()))
    unresolved = await grader(
        CapabilityOutput(f"ANSWER: BLD-77CC88 {connector_refs.POINTER_ESCAPE}", ())
    )
    unescaped = await grader(CapabilityOutput("ANSWER: BLD-11AA22 /refs/acme/widgets~main", ()))
    assert good.passed, good.reason
    assert not unresolved.passed
    assert not unescaped.passed
