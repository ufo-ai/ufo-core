"""Stale-memory cases: a durable memory item states a pull request's status, the connected GitHub
state has moved past it, and the turn is asked something that repeats the claim. Two behaviours are
graded — the agent reads the live pull request through the eval environment's GitHub provider
before it answers, and, when live state contradicts the item, it records the corrected statement so
a later turn recalls the current status instead of the stale one.

The stale item is seeded straight onto `memory_item`, un-embedded, the way `object_tools` seeds its
remembered cadence: the tail leg of recall reaches it without the index job. Live state is the
`eval_github` fixture the real `list_external_tools` → `describe_external_tools` →
`call_external_tool` dispatch returns, so the verification under grade is a real connector round
trip. Each case owns one repository and number, so its fixture key, its seeded memory rows, and its
graded rows belong to it alone and cases may run concurrently.

Deprecating the stale item is the write the product path offers: `memory_update` records the
corrected statement and the dedup sweep retires the near-duplicate original toward it, so the
grader requires a live memory row carrying the current status, and records whether the seeded row
was retired as evidence beside it.

The reply is graded in two parts. A phrase scan settles that the live status term is there. Whether
the reply carries the remembered status forward goes to the judge, because the correct reply names
that status to retract it and a scan reads the retraction as the assertion it withdraws."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_eval_env.manifest import (
    ACCOUNT_ID,
    GITHUB_HOST,
    GITHUB_ITEM_FIXTURE_PREFIX,
    GITHUB_PROVIDER,
)
from ufo_ext_eval_env.manifest import (
    NAME as EVAL_ENV_NAME,
)
from ufo_ext_memory.manifest import RECORD_CORRECTION_ACTION
from ufo_ext_memory.objects import MEMORY_KIND
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    body_digest,
    memory_item,
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
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.sdk.context import ScopedStore

CALL_TOOL = "call_external_tool"
PULL_REQUEST_SLUG = "get_pull_request"
MEMORY_WRITE_CALLS = frozenset(
    {"memory_update", f"action:{MEMORY_KIND}:{RECORD_CORRECTION_ACTION}"}
)
REPOSITORY = "evalco/atlas"
FOREIGN_REPOSITORY = "northwind/atlas"
OPEN_CLAIMS = (
    "still open",
    "still waiting",
    "still awaiting",
    "still in review",
    "still with",
    "not merged",
    "remains open",
    "remains unmerged",
    "still unmerged",
    "still not",
)
MERGED_CLAIMS = (
    "already merged",
    "was merged",
    "has merged",
    "merged on 14",
    "merged and shipped",
    "shipped to production",
)


@dataclass(frozen=True)
class PullRequest:
    """One repository item in both the states a case needs it in: the memory the workspace holds
    about it, and the record the connector answers with."""

    repository: str
    number: int
    title: str
    remembered: str
    live: JsonObject
    live_terms: tuple[str, ...]
    stale_claims: tuple[str, ...]
    """Phrases the live record contradicts for this item. A phrase that is stale about one pull
    request states the truth about another — 412 is merged and 418 is open — so the set belongs to
    the item rather than to the suite."""


MERGED_412 = PullRequest(
    repository=REPOSITORY,
    number=412,
    title="Retry the sync token refresh",
    remembered=(
        "evalco/atlas pull request 412, 'Retry the sync token refresh' — open and waiting on "
        "Priya Raman's review since 12 August 2026."
    ),
    live={
        "repository": REPOSITORY,
        "number": 412,
        "title": "Retry the sync token refresh",
        "author": "marisol",
        "state": "merged",
        "review": "approved",
        "checks": "passing",
        "merged_at": "2026-08-19T11:24:00+00:00",
        "merged_by": "priya",
        "head_sha": "b41f0ce",
    },
    live_terms=("merged",),
    stale_claims=OPEN_CLAIMS,
)
OPEN_418 = PullRequest(
    repository=REPOSITORY,
    number=418,
    title="Bound the webhook retry window",
    remembered=(
        "evalco/atlas pull request 418, 'Bound the webhook retry window' — merged on 14 August "
        "2026 and shipped to production."
    ),
    live={
        "repository": REPOSITORY,
        "number": 418,
        "title": "Bound the webhook retry window",
        "author": "priya",
        "state": "open",
        "review": "changes_requested",
        "checks": "failing",
        "merged_at": None,
        "unresolved_threads": 3,
        "head_sha": "2cc90a7",
    },
    live_terms=("open", "changes requested", "changes_requested"),
    stale_claims=MERGED_CLAIMS,
)
MERGED_419 = PullRequest(
    repository=REPOSITORY,
    number=419,
    title="Drop the unused export flag",
    remembered=(
        "evalco/atlas pull request 419, 'Drop the unused export flag' — merged on 21 August 2026."
    ),
    live={
        "repository": REPOSITORY,
        "number": 419,
        "title": "Drop the unused export flag",
        "author": "dana",
        "state": "merged",
        "review": "approved",
        "checks": "passing",
        "merged_at": "2026-08-21T09:05:00+00:00",
        "merged_by": "dana",
        "head_sha": "77de311",
    },
    live_terms=("merged",),
    stale_claims=OPEN_CLAIMS,
)
MERGED_421 = PullRequest(
    repository=REPOSITORY,
    number=421,
    title="Cache the schema reflection",
    remembered=(
        "evalco/atlas pull request 421, 'Cache the schema reflection' — open, review not started, "
        "as of 13 August 2026."
    ),
    live={
        "repository": REPOSITORY,
        "number": 421,
        "title": "Cache the schema reflection",
        "author": "marisol",
        "state": "merged",
        "review": "approved",
        "checks": "passing",
        "merged_at": "2026-08-22T14:40:00+00:00",
        "merged_by": "marisol",
        "head_sha": "0ab4419",
    },
    live_terms=("merged",),
    stale_claims=OPEN_CLAIMS,
)
UNREADABLE_77 = PullRequest(
    repository=FOREIGN_REPOSITORY,
    number=77,
    title="Split the ingest queue",
    remembered=(
        "northwind/atlas pull request 77, 'Split the ingest queue' — open and blocked on a failing "
        "integration check since 11 August 2026."
    ),
    live={
        "repository": FOREIGN_REPOSITORY,
        "number": 77,
        "found": False,
        "detail": "this connection cannot read northwind/atlas",
    },
    live_terms=(),
    stale_claims=OPEN_CLAIMS,
)


def _seeding(item: PullRequest) -> CapabilitySeed:
    """The stale statement as a durable memory row, the live record under this item's own fixture
    key, and the grant that lets the turn's agent reach the provider. Every memory row naming the
    item goes first, so a re-run — or the correction an earlier run recorded — cannot stand in for
    what this case seeds. A grant must pre-exist, because an eval conversation has no speaking
    member for `connect_account` to run under, and `GrantStore.record` upserts."""

    async def seed(workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        await _forget(workspace_id, item)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    subject=SHARED_SUBJECT,
                    body=item.remembered,
                    body_digest=body_digest(item.remembered),
                    item_class=FACT,
                    memory_kind=KIND_FACT,
                    confidence=DEFAULT_CONFIDENCE,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await ScopedStore(extension=EVAL_ENV_NAME).put(
            f"{GITHUB_ITEM_FIXTURE_PREFIX}{item.repository}#{item.number}", item.live
        )
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
                    queue_key=f"{EVAL_SURFACE}-memory-staleness-seed:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with agent(agent_id):
            await GrantStore().record(
                provider=GITHUB_PROVIDER,
                account_id=ACCOUNT_ID,
                host=GITHUB_HOST,
                grantor_member_id=member_id,
                shared=True,
            )

    return seed


def _cleaning(item: PullRequest) -> CapabilitySeed:
    """Drop every memory row naming this item once the case settles: the seeded stale statement
    and whatever the turn recorded about it are the case's own, and a later leaf must not recall
    them."""

    async def cleanup(workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
        await _forget(workspace_id, item)

    return cleanup


async def _forget(workspace_id: UUID, item: PullRequest) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(memory_item).where(
                memory_item.c.workspace_id == workspace_id,
                memory_item.c.created_from_page_uid.is_(None),
                memory_item.c.body.ilike(f"%{item.repository}%"),
                memory_item.c.body.ilike(f"%{item.number}%"),
            )
        )


def _reads(output: CapabilityOutput, item: PullRequest) -> tuple[ToolInvocation, ...]:
    def asked(call: ToolInvocation) -> bool:
        arguments = call.input.get("arguments")
        return (
            isinstance(arguments, dict)
            and arguments.get("repository") == item.repository
            and str(arguments.get("number")) == str(item.number)
        )

    return tuple(
        call
        for call in output.calls
        if call.name == CALL_TOOL
        and call.succeeded
        and call.input.get("tool_name") == PULL_REQUEST_SLUG
        and asked(call)
    )


def _memory_writes(output: CapabilityOutput) -> tuple[ToolInvocation, ...]:
    return tuple(
        call for call in output.calls if call.call in MEMORY_WRITE_CALLS and call.succeeded
    )


def _written_bodies(output: CapabilityOutput) -> tuple[str, ...]:
    return tuple(
        body
        for call in _memory_writes(output)
        if isinstance(body := call.arguments.get("body"), str)
    )


async def _live_rows(item: PullRequest) -> tuple[str, ...]:
    """The workspace's memory about this item after the turn: bodies of the rows recall can still
    reach, so a superseded or retired row is already gone from the answer this grades."""
    async with workspace_tx() as connection:
        bodies = (
            (
                await connection.execute(
                    sa.select(memory_item.c.body).where(
                        memory_item.c.workspace_id == ws_current().workspace_id,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                        memory_item.c.created_from_page_uid.is_(None),
                        memory_item.c.body.ilike(f"%{item.number}%"),
                    )
                )
            )
            .scalars()
            .all()
        )
    return tuple(bodies)


NEGATIONS = frozenset({"not", "never", "no", "isn't", "wasn't", "aren't", "hasn't", "haven't"})
WORDS = re.compile(r"[\w']+")


def _carries(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """The terms the text states. A term matches whole words only, and only where neither of the
    two words before it denies it: `merged` sits inside `unmerged` and behind the `not` of `not
    merged`, and each of those says the opposite of what the term asserts."""
    folded = text.casefold()
    return tuple(term for term in terms if _stated(folded, term.casefold()))


def _stated(folded: str, term: str) -> bool:
    return any(
        not NEGATIONS & set(WORDS.findall(folded[: match.start()])[-2:])
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", folded)
    )


def verified(item: PullRequest) -> Grader:
    """The live read of the item the memory speaks about, before the answer repeats it."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        reads = _reads(output, item)
        evidence: JsonObject = {"liveReads": len(reads)}
        if not reads:
            return CapabilityVerdict(
                False,
                f"answered without reading {item.repository}#{item.number} from the connector",
                evidence,
            )
        return CapabilityVerdict(
            True, f"read {item.repository}#{item.number} from the connector", evidence
        )

    return DescribedGrader(
        f"a successful {PULL_REQUEST_SLUG} call reads {item.repository}#{item.number}", grade
    )


def answers(item: PullRequest) -> Grader:
    """The reply carries the live status term. Whether the reply also repeats the remembered claim
    as current is the rubric's to judge: a correct reply names the remembered status to retract it,
    and a phrase scan reads that retraction as the assertion it withdraws."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        live = _carries(output.response, item.live_terms)
        evidence: JsonObject = {"liveTerms": list(live)}
        if not live:
            return CapabilityVerdict(
                False,
                f"the reply states no live status for {item.repository}#{item.number}",
                evidence,
            )
        return CapabilityVerdict(True, f"the reply states {', '.join(live)}", evidence)

    return DescribedGrader(
        f"the reply states the live state of {item.repository}#{item.number}", grade
    )


def corrects(item: PullRequest) -> Grader:
    """Memory itself carries the live status once the turn settles, not only the reply."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        writes = _memory_writes(output)
        rows = await _live_rows(item)
        corrected = tuple(row for row in rows if _carries(row, item.live_terms))
        remembered = tuple(row for row in rows if row == item.remembered)
        evidence: JsonObject = {
            "memoryWrites": len(writes),
            "correctedRows": len(corrected),
            "staleRowRetired": not remembered,
        }
        if not writes:
            return CapabilityVerdict(
                False, "the turn recorded no memory about the contradicted item", evidence
            )
        if not corrected:
            return CapabilityVerdict(
                False,
                f"no memory row states the live state of {item.repository}#{item.number}",
                evidence,
            )
        return CapabilityVerdict(
            True, f"memory now states the live state of {item.repository}#{item.number}", evidence
        )

    return DescribedGrader(
        f"a memory write lands and a live memory row states {item.repository}#{item.number} "
        "as the connector reports it",
        grade,
    )


def keeps(item: PullRequest) -> Grader:
    """Memory agreeing with live state is left as it is: no row contradicting it is written."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        contradictions = tuple(
            body for body in _written_bodies(output) if _carries(body, item.stale_claims)
        )
        evidence: JsonObject = {"memoryWrites": len(_memory_writes(output))}
        if contradictions:
            return CapabilityVerdict(
                False,
                f"wrote memory contradicting the live state of {item.repository}#{item.number}",
                evidence,
            )
        return CapabilityVerdict(True, "left the agreeing memory item as it is", evidence)

    return DescribedGrader(
        f"no memory write contradicts the live state of {item.repository}#{item.number}", grade
    )


def records_nothing(item: PullRequest) -> Grader:
    """An unreadable item is not written into memory as though it had been checked."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        claimed = tuple(body for body in _written_bodies(output) if str(item.number) in body)
        evidence: JsonObject = {"memoryWrites": len(_memory_writes(output))}
        if claimed:
            return CapabilityVerdict(
                False,
                f"recorded a status for the unreadable {item.repository}#{item.number}",
                evidence,
            )
        return CapabilityVerdict(True, "recorded no status it could not read", evidence)

    return DescribedGrader(
        f"no memory write records a status for the unreadable {item.repository}#{item.number}",
        grade,
    )


def _withdrawn(item: PullRequest) -> tuple[str, ...]:
    """The clause the phrase scan cannot decide: a reply that names the remembered status in order
    to retract it is right, and one that carries it forward as current is wrong."""
    return (
        f"The reply gives {item.repository}#{item.number} the status the pull request holds now.",
        f"The reply treats any earlier status of {item.repository}#{item.number} it mentions as "
        "out of date.",
    )


def _case(
    name: str,
    item: PullRequest,
    message: str,
    grader: Grader,
    rubric: tuple[str, ...] = (),
) -> CapabilityCase:
    return CapabilityCase(
        name,
        message,
        grader,
        rubric=rubric,
        digest_tag=f"memory-staleness:{name}:{item.repository}#{item.number}",
        seed=_seeding(item),
        cleanup=_cleaning(item),
    )


CASES = (
    _case(
        "M01-stale-open-claim",
        MERGED_412,
        "Quick one before standup: where does pull request 412 on evalco/atlas stand?",
        combine(verified(MERGED_412), answers(MERGED_412), corrects(MERGED_412)),
        rubric=_withdrawn(MERGED_412),
    ),
    _case(
        "M02-stale-merged-claim",
        OPEN_418,
        "Can I close out the webhook retry work on evalco/atlas? Tell me where pull request 418 "
        "is.",
        combine(verified(OPEN_418), answers(OPEN_418), corrects(OPEN_418)),
        rubric=_withdrawn(OPEN_418),
    ),
    _case(
        "M03-status-repeated-in-a-draft",
        MERGED_421,
        "Draft two lines for my standup update about the schema reflection caching work on "
        "evalco/atlas, pull request 421.",
        combine(verified(MERGED_421), answers(MERGED_421), corrects(MERGED_421)),
        rubric=_withdrawn(MERGED_421),
    ),
    _case(
        "M04-memory-matches-live",
        MERGED_419,
        "Remind me what happened with pull request 419 on evalco/atlas.",
        combine(verified(MERGED_419), answers(MERGED_419), keeps(MERGED_419)),
    ),
    _case(
        "M05-unreadable-item-hedged",
        UNREADABLE_77,
        "What is happening with pull request 77 on northwind/atlas? I need it for the partner "
        "call.",
        combine(verified(UNREADABLE_77), records_nothing(UNREADABLE_77)),
        rubric=(
            "The reply attributes the pull request's status to memory or to another unverified "
            "source, or states that the connected GitHub account cannot read northwind/atlas.",
            "The reply avoids presenting the pull request's status as confirmed current state.",
        ),
    ),
)
