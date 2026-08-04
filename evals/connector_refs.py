"""Connector-reference cases: a connector result that repeats a sub-object crosses with the later
copies replaced by `{"same_as": "<JSON Pointer>"}`, and these cases ask whether the *model* can work
with that — resolve a pointer whose target sits past the offload preview, read RFC 6901 escaping and
indices correctly, leave a provider's own `same_as` field alone, and reach a fact through a pointer
without costing more rounds than the uncondensed result it replaced.

Every payload is authored here to the byte and seeded whole under its query, because each case turns
on where the bytes land: which side of the engine's 25,600-char inline budget the condensed result
falls on, and where the 6,144-char offload preview cuts. The agent reaches them through the real
`list_external_tools` → `describe_external_tools` → `call_external_tool` dispatch against the eval
environment's code-search provider, so the condensing under test is the shipped one.

Grading is deterministic — no rubric judge. Each case plants a token that appears exactly once in
the condensed result (inside the object a pointer names) and, where a wrong-but-tempting record is
reachable, a decoy token the answer must not carry. Every grader also reads the recorded
`call_external_tool` result itself, so a fixture that stopped straddling the budget fails the case
instead of quietly grading a different payload."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_connectors.tools import DEDUPE_REFERENCE_KEY
from ufo_ext_eval_env.manifest import (
    ACCOUNT_ID,
    CODE_FIXTURE_PREFIX,
    CODE_HOST,
    CODE_PROVIDER,
    NAME,
)

from evals.driver import EVAL_SURFACE
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ToolInvocation,
)
from evals.harness.harness import Json, JsonObject
from evals.harness.scorers import answer_text, combine, restraint_scorer
from ufo.agent_scope import agent
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.grants import GrantStore
from ufo.loop.engine import TOOL_OUTPUT_DIR
from ufo.schema import tables
from ufo.sdk.context import ScopedStore

CALL_TOOL = "call_external_tool"
SEARCH_SLUG = "search_code"
POINTER = f'"{DEDUPE_REFERENCE_KEY}": "/'
NARROWING_TOOLS = frozenset({"bash", "read", "grep"})
ANSWER_INSTRUCTION = (
    "Answer from the search result alone and end your reply with a single line "
    "'ANSWER: <value>' and nothing after it."
)
COST_QUESTION = (
    "Report the SPDX ids of the licenses of the repositories that the 6th, 14th and 23rd hits "
    "(indexes 5, 13 and 22) belong to, in that order."
)
COST_SAMPLES = 3
SNIPPET = (
    "async def reserve(pool: WidgetPool, count: int) -> Reservation:\n"
    "    if count > pool.capacity:\n"
    "        raise CapacityError(pool.name, count)\n"
    "    held = await pool.hold(count)\n"
    "    return Reservation(pool=pool.name, held=held, expires=pool.lease_seconds)\n"
)


def token(prefix: str, seed: str) -> str:
    """A short opaque literal a model can only report by reading the record that holds it. Derived
    from the seed so a fixture edit moves every token it should and none it should not."""
    return f"{prefix}-{hashlib.sha256(seed.encode()).hexdigest()[:6].upper()}"


def _owner(org: str) -> JsonObject:
    return {
        "login": org,
        "id": int(hashlib.sha256(org.encode()).hexdigest()[:6], 16),
        "node_id": token("OWN", org),
        "avatar_url": f"https://avatars.evalenv.test/u/{org}?v=4",
        "gravatar_id": "",
        "url": f"https://api.evalenv.test/users/{org}",
        "html_url": f"https://code.evalenv.test/{org}",
        "followers_url": f"https://api.evalenv.test/users/{org}/followers",
        "following_url": f"https://api.evalenv.test/users/{org}/following{{/other_user}}",
        "gists_url": f"https://api.evalenv.test/users/{org}/gists{{/gist_id}}",
        "starred_url": f"https://api.evalenv.test/users/{org}/starred{{/owner}}{{/repo}}",
        "subscriptions_url": f"https://api.evalenv.test/users/{org}/subscriptions",
        "organizations_url": f"https://api.evalenv.test/users/{org}/orgs",
        "repos_url": f"https://api.evalenv.test/users/{org}/repos",
        "events_url": f"https://api.evalenv.test/users/{org}/events{{/privacy}}",
        "received_events_url": f"https://api.evalenv.test/users/{org}/received_events",
        "type": "Organization",
        "site_admin": False,
    }


def repository(slug: str) -> JsonObject:
    """One repository record, shaped like the one a code-search provider embeds in every hit:
    ~4.4 KB of mostly URL templates, which is why thirty of them are 85% of a thirty-hit page.
    `license.spdx_id` and `owner.node_id` are the planted literals — nothing else in the payload
    carries them, so reporting one is evidence the record itself was reached."""
    org, name = slug.split("/")
    api = f"https://api.evalenv.test/repos/{slug}"
    return {
        "id": int(hashlib.sha256(slug.encode()).hexdigest()[:8], 16),
        "node_id": token("REP", slug),
        "name": name,
        "full_name": slug,
        "private": True,
        "owner": _owner(org),
        "html_url": f"https://code.evalenv.test/{slug}",
        "description": f"Internal service code for {name}.",
        "fork": False,
        "url": api,
        "forks_url": f"{api}/forks",
        "keys_url": f"{api}/keys{{/key_id}}",
        "collaborators_url": f"{api}/collaborators{{/collaborator}}",
        "teams_url": f"{api}/teams",
        "hooks_url": f"{api}/hooks",
        "issue_events_url": f"{api}/issues/events{{/number}}",
        "events_url": f"{api}/events",
        "assignees_url": f"{api}/assignees{{/user}}",
        "branches_url": f"{api}/branches{{/branch}}",
        "tags_url": f"{api}/tags",
        "blobs_url": f"{api}/git/blobs{{/sha}}",
        "git_tags_url": f"{api}/git/tags{{/sha}}",
        "git_refs_url": f"{api}/git/refs{{/sha}}",
        "trees_url": f"{api}/git/trees{{/sha}}",
        "statuses_url": f"{api}/statuses/{{sha}}",
        "languages_url": f"{api}/languages",
        "stargazers_url": f"{api}/stargazers",
        "contributors_url": f"{api}/contributors",
        "subscribers_url": f"{api}/subscribers",
        "subscription_url": f"{api}/subscription",
        "commits_url": f"{api}/commits{{/sha}}",
        "git_commits_url": f"{api}/git/commits{{/sha}}",
        "comments_url": f"{api}/comments{{/number}}",
        "issue_comment_url": f"{api}/issues/comments{{/number}}",
        "contents_url": f"{api}/contents/{{+path}}",
        "compare_url": f"{api}/compare/{{base}}...{{head}}",
        "merges_url": f"{api}/merges",
        "archive_url": f"{api}/{{archive_format}}{{/ref}}",
        "downloads_url": f"{api}/downloads",
        "issues_url": f"{api}/issues{{/number}}",
        "pulls_url": f"{api}/pulls{{/number}}",
        "milestones_url": f"{api}/milestones{{/number}}",
        "notifications_url": f"{api}/notifications{{?since,all,participating}}",
        "labels_url": f"{api}/labels{{/name}}",
        "releases_url": f"{api}/releases{{/id}}",
        "deployments_url": f"{api}/deployments",
        "default_branch": token("BR", slug).lower(),
        "topics": ["internal", name, "service"],
        "visibility": "internal",
        "license": {
            "key": "evalco-internal",
            "name": "EvalCo Internal License",
            "spdx_id": token("LIC", slug),
            "url": "https://code.evalenv.test/legal/internal",
            "node_id": token("LICN", slug),
        },
    }


def _hit(index: int, slug: str, record: JsonObject, snippet: bool) -> JsonObject:
    path = f"src/{slug.split('/')[1]}/module_{index:02d}.py"
    hit: JsonObject = {
        "name": f"module_{index:02d}.py",
        "path": path,
        "sha": hashlib.sha256(f"{slug}:{index}".encode()).hexdigest(),
        "url": f"https://api.evalenv.test/repos/{slug}/contents/{path}",
        "git_url": f"https://api.evalenv.test/repos/{slug}/git/blobs/{index}",
        "html_url": f"https://code.evalenv.test/{slug}/blob/main/{path}",
        "repository": record,
        "score": 1.0,
    }
    if snippet:
        hit["text_matches"] = [
            {"object_url": hit["url"], "property": "content", "fragment": SNIPPET}
        ]
    return hit


def page(slugs: tuple[str, ...], snippet: bool = False) -> JsonObject:
    """A search page over `slugs`, one hit per entry in order. Repeating a slug means the identical
    repository record is embedded again — which is what the condensing under test collapses; a
    distinct slug carries a distinct record and collapses nothing."""
    records: dict[str, JsonObject] = {}
    hits: list[Json] = []
    for index, slug in enumerate(slugs):
        records.setdefault(slug, repository(slug))
        hits.append(_hit(index, slug, records[slug], snippet))
    return {"total_count": len(hits), "incomplete_results": False, "items": hits}


FLEET = "orbital/fleet"
LEGACY = "orbital/legacy-fleet"
TELEMETRY = "orbital/telemetry"
LEDGER = "orbital/ledger"
RELAY = "lunar/relay"
KERNEL = "mainline/kernel"
REF_MAIN = "acme/widgets-main"
REF_SWAPPED = "acme-widgets/main"


def _others(count: int) -> tuple[str, ...]:
    """Distinct repositories under distinct owner orgs, so nothing anywhere in a page carrying them
    is identical to anything else in it — the shape a cross-repo search really returns, and the one
    the condensing pass must leave alone."""
    return tuple(f"svc{index:02d}/api{index:02d}" for index in range(count))


def _collision() -> JsonObject:
    """A provider whose own records carry a `same_as` field — a catalog cross-reference, not a
    pointer. The condensing pass leaves such a response byte-for-byte untouched, so this page keeps
    all three full copies of the repository."""
    record = repository(FLEET)
    hits: list[Json] = [
        {**_hit(index, FLEET, record, False), DEDUPE_REFERENCE_KEY: f"widget-catalog-v{index + 2}"}
        for index in range(3)
    ]
    return {"total_count": len(hits), "incomplete_results": False, "items": hits}


def _refs() -> JsonObject:
    """A by-name index rather than a list, so the pointer has to escape its reference token: the
    repeated record sits under `acme/widgets~main`, which RFC 6901 writes `acme~1widgets~0main`.
    `acme-widgets/main` is present as a distinct record because reading the escapes the wrong way
    round (`~0` as `/`, `~1` as `~`) names it instead."""
    main = repository(REF_MAIN)
    return {
        "total_count": 3,
        "incomplete_results": False,
        "refs": {
            "acme/widgets~main": main,
            "acme-widgets/main": repository(REF_SWAPPED),
            "acme/widgets~dev": main,
        },
    }


QUERIES: dict[str, JsonObject] = {
    "widget-reserve": page((FLEET,) * 30),
    "widget-lease": page((FLEET,) * 30, snippet=True),
    "widget-capacity": page((LEGACY,) * 3 + (FLEET,) * 27, snippet=True),
    "telemetry-flush": page((TELEMETRY,) * 12),
    "ref-index": _refs(),
    "kernel-hold": page(_others(3) + (KERNEL,) * 9),
    "fleet-telemetry": page((FLEET, RELAY) + (TELEMETRY,) * 9),
    "catalog-sync": _collision(),
    "ledger-post": page((FLEET,) * 8 + (TELEMETRY,) * 8 + (LEDGER,) * 8, snippet=True),
    "ledger-audit": page(_others(24), snippet=True),
}


def _seeding(query: str) -> CapabilitySeed:
    """The seed for one case: that case's own page, and the grant that lets the turn's agent reach
    the provider. A grant must pre-exist — an eval conversation has no speaking member, so
    `connect_account` cannot run inside one — and `GrantStore.record` upserts, so cases may seed it
    concurrently.

    One page rather than all ten, because `ScopedStore.put` is check-then-act: it updates, and
    inserts only when nothing was updated. Two cases writing one `ext_store` key both see nothing to
    update on a first run and both insert, and the loser raises on the primary key — which the
    harness's concurrency semaphore makes reachable at `--concurrency` 2 and up. A case owning its
    own key cannot race another, and the message asking for that query is built beside it below."""

    async def seed(workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        await ScopedStore(extension=NAME).put(f"{CODE_FIXTURE_PREFIX}{query}", QUERIES[query])
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-connector-refs-seed:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with agent(agent_id):
            await GrantStore().record(
                provider=CODE_PROVIDER,
                account_id=ACCOUNT_ID,
                host=CODE_HOST,
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=True,
            )

    return seed


def _searches(output: CapabilityOutput, query: str) -> tuple[ToolInvocation, ...]:
    def asked(call: ToolInvocation) -> bool:
        arguments = call.input.get("arguments")
        return isinstance(arguments, dict) and arguments.get("query") == query

    return tuple(
        call
        for call in output.calls
        if call.name == CALL_TOOL
        and call.succeeded
        and call.input.get("tool_name") == SEARCH_SLUG
        and asked(call)
    )


def _narrowings(output: CapabilityOutput) -> tuple[ToolInvocation, ...]:
    return tuple(
        call
        for call in output.calls
        if call.name in NARROWING_TOOLS and TOOL_OUTPUT_DIR in json.dumps(call.input)
    )


def _graded_reference(
    query: str,
    expected: str,
    decoys: tuple[str, ...] = (),
    pointers: bool = True,
    offloaded: bool = False,
) -> Grader:
    """The one grader every reference case uses, top to bottom: the search ran on this case's query;
    the result it returned really is the payload the case was built around (pointers present or
    absent, past the inline budget or inside it); the answer names the planted literal and no decoy;
    and the answer is a value, never the pointer that led to it. An offloaded case additionally
    requires the trajectory to show the offload file being narrowed: the hit the question names is
    past the preview cut either way, so an answer that never opened the file read its literal off
    whichever record happened to be visible, not off the one the pointer names."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        searches = _searches(output, query)
        if not searches:
            return CapabilityVerdict(False, f"no successful {SEARCH_SLUG} call carried {query!r}")
        result = searches[-1].result
        if pointers and POINTER not in result:
            return CapabilityVerdict(
                False, f"the result carried no pointer; the {query!r} fixture stopped repeating"
            )
        if not pointers and POINTER in result:
            return CapabilityVerdict(
                False, f"the {query!r} fixture was condensed; its records stopped being distinct"
            )
        if offloaded != (TOOL_OUTPUT_DIR in result):
            landed = "inline" if TOOL_OUTPUT_DIR not in result else "offloaded"
            return CapabilityVerdict(
                False, f"the {query!r} result landed {landed}, not what the case measures"
            )
        answer = answer_text(output.response)
        if expected.lower() not in answer:
            return CapabilityVerdict(False, f"answered {answer!r}, expected {expected!r}")
        named = [decoy for decoy in decoys if decoy.lower() in answer]
        if named:
            return CapabilityVerdict(
                False, f"answered with the record it did not resolve: {', '.join(named)}"
            )
        if pointers and ("/items/" in answer or "/refs/" in answer):
            return CapabilityVerdict(False, f"handed back the pointer, not the value: {answer!r}")
        if offloaded and not _narrowings(output):
            return CapabilityVerdict(
                False, f"reported {expected!r} without ever reading {TOOL_OUTPUT_DIR}"
            )
        return CapabilityVerdict(
            True,
            f"{expected} via {len(searches)} search(es), {len(_narrowings(output))} narrowing(s)",
        )

    statement = (
        f"the {query!r} search returns a {'condensed' if pointers else 'distinct-record'} result "
        f"that lands {'past' if offloaded else 'inside'} the inline budget, and the ANSWER names "
        f"{expected!r}"
    )
    if decoys:
        statement += f" and none of {', '.join(decoys)}"
    if offloaded:
        statement += f", with {TOOL_OUTPUT_DIR} narrowed in the trajectory"
    return DescribedGrader(statement, grade)


def _graded_facts(query: str, expected: tuple[str, ...], offloaded: bool, pointers: bool) -> Grader:
    """The cost arms' grader: the same three facts in both arms, so the arms differ only in how
    the result presents them. The reported metrics (rounds, calls, tokens) come from the recorded
    attempt, never from this verdict."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        searches = _searches(output, query)
        if not searches:
            return CapabilityVerdict(False, f"no successful {SEARCH_SLUG} call carried {query!r}")
        result = searches[-1].result
        if pointers != (POINTER in result):
            return CapabilityVerdict(False, f"the {query!r} fixture no longer matches its arm")
        if offloaded != (TOOL_OUTPUT_DIR in result):
            return CapabilityVerdict(False, f"the {query!r} result landed on the wrong side")
        answer = answer_text(output.response)
        missing = [value for value in expected if value.lower() not in answer]
        if missing:
            return CapabilityVerdict(False, f"answer {answer!r} omits {', '.join(missing)}")
        return CapabilityVerdict(True, f"all {len(expected)} licenses reported")

    return DescribedGrader(
        f"the {query!r} result presents its records "
        f"{'as pointers' if pointers else 'in full'} and the ANSWER names "
        f"{', '.join(expected)}",
        grade,
    )


POINTER_LESSON = json.dumps(
    {
        "total_count": 2,
        "items": [
            {"path": "a.py", "repository": {"full_name": "acme/one", "build": "BLD-11AA22"}},
            {"path": "b.py", "repository": {DEDUPE_REFERENCE_KEY: "/items/0/repository"}},
        ],
        "refs": {"acme/widgets~main": {"build": "BLD-77CC88"}},
    }
)
POINTER_QUESTION = (
    "A code-search connector returned exactly this result:\n\n"
    f"{POINTER_LESSON}\n\n"
    "What is the build id of the repository the hit at b.py belongs to, and what JSON Pointer "
    "would name the record under the key acme/widgets~main? Do not use any tool. End your reply "
    "with a single line 'ANSWER: <build id> <pointer>' and nothing after it."
)
POINTER_ESCAPE = "/refs/acme~1widgets~0main"


def _graded_pointer_literacy() -> Grader:
    """The tool-free probe: resolving a pointer and writing one are the two halves of RFC 6901, and
    both are graded on literals. The build id is only reachable by following the reference, and the
    escaped pointer is only writable by escaping `/` and `~` in that order."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        answer = answer_text(output.response)
        if "bld-11aa22" not in answer:
            return CapabilityVerdict(False, f"answer {answer!r} did not resolve to BLD-11AA22")
        if "bld-77cc88" in answer:
            return CapabilityVerdict(False, "answered with the record the pointer does not name")
        if POINTER_ESCAPE.lower() not in answer:
            return CapabilityVerdict(False, f"answer {answer!r} lacks {POINTER_ESCAPE}")
        return CapabilityVerdict(True, f"{POINTER_ESCAPE} and BLD-11AA22")

    return DescribedGrader(
        f"the ANSWER resolves the reference to BLD-11AA22 (not BLD-77CC88) and writes "
        f"{POINTER_ESCAPE}",
        grade,
    )


def _searching(
    name: str, tag: str, query: str, question: str, grader: Grader, samples: int = 1
) -> CapabilityCase:
    """One search case: the message that names the query and the seed that lays that query's page
    down, built from the same argument so they cannot drift apart — a case whose seed wrote another
    page would fail loud inside the provider rather than grade anything."""
    return CapabilityCase(
        name,
        f"Use the code search connector (provider {CODE_PROVIDER}) and run its {SEARCH_SLUG} tool "
        f"with the query exactly '{query}'. {question} {ANSWER_INSTRUCTION}",
        grader,
        samples=samples,
        digest_tag=f"connector-refs:{tag}",
        seed=_seeding(query),
    )


CASES = (
    _searching(
        "R01-inline-reference",
        "inline-reference",
        "widget-reserve",
        "Report the SPDX id of the license of the repository that the 25th hit (index 24) "
        "belongs to.",
        _graded_reference("widget-reserve", token("LIC", FLEET)),
    ),
    _searching(
        "R02-reference-below-the-fold",
        "reference-below-the-fold",
        "widget-lease",
        "Report the SPDX id of the license of the repository that the 27th hit (index 26) "
        "belongs to.",
        _graded_reference("widget-lease", token("LIC", FLEET), offloaded=True),
    ),
    _searching(
        "R03-nearest-record-trap",
        "nearest-record-trap",
        "widget-capacity",
        "Report the SPDX id of the license of the repository that the 28th hit (index 27) "
        "belongs to.",
        _graded_reference(
            "widget-capacity", token("LIC", FLEET), decoys=(token("LIC", LEGACY),), offloaded=True
        ),
    ),
    _searching(
        "R04-deep-target",
        "deep-target",
        "telemetry-flush",
        "Report the node_id of the owner of the repository that the 10th hit (index 9) belongs to.",
        _graded_reference("telemetry-flush", token("OWN", "orbital")),
    ),
    _searching(
        "R05-escaped-reference-token",
        "escaped-reference-token",
        "ref-index",
        "Report the SPDX id of the license of the repository indexed under the ref "
        "'acme/widgets~dev'.",
        _graded_reference("ref-index", token("LIC", REF_MAIN), decoys=(token("LIC", REF_SWAPPED),)),
    ),
    _searching(
        "R06-non-zero-index",
        "non-zero-index",
        "kernel-hold",
        "Report the SPDX id of the license of the repository that the 10th hit (index 9) "
        "belongs to.",
        _graded_reference(
            "kernel-hold", token("LIC", KERNEL), decoys=(token("LIC", "svc02/api02"),)
        ),
    ),
    _searching(
        "R07-reference-inside-a-target",
        "reference-inside-a-target",
        "fleet-telemetry",
        "Report the node_id of the owner of the repository that the 9th hit (index 8) belongs to.",
        _graded_reference(
            "fleet-telemetry", token("OWN", "orbital"), decoys=(token("OWN", "lunar"),)
        ),
    ),
    _searching(
        "R08-provider-field-not-a-pointer",
        "provider-field-not-a-pointer",
        "catalog-sync",
        "Report the value of the same_as field on the 2nd hit (index 1) exactly as the provider "
        "gave it.",
        _graded_reference("catalog-sync", "widget-catalog-v3", pointers=False),
    ),
    CapabilityCase(
        "R09-pointer-literacy",
        POINTER_QUESTION,
        combine(_graded_pointer_literacy(), restraint_scorer((CALL_TOOL, "bash", "read"))),
        digest_tag="connector-refs:pointer-literacy",
    ),
    _searching(
        "R10-cost-condensed-arm",
        "cost-condensed-arm",
        "ledger-post",
        COST_QUESTION,
        _graded_facts(
            "ledger-post",
            (token("LIC", FLEET), token("LIC", TELEMETRY), token("LIC", LEDGER)),
            offloaded=True,
            pointers=True,
        ),
        samples=COST_SAMPLES,
    ),
    _searching(
        "R11-cost-uncondensed-arm",
        "cost-uncondensed-arm",
        "ledger-audit",
        COST_QUESTION,
        _graded_facts(
            "ledger-audit",
            (token("LIC", "svc05/api05"), token("LIC", "svc13/api13"), token("LIC", "svc22/api22")),
            offloaded=True,
            pointers=False,
        ),
        samples=COST_SAMPLES,
    ),
)
