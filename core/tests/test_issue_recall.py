import asyncio
import json
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ufo_ext_memory.condenser import MIN_PAGE_BODY_CHARS
from ufo_ext_memory.events import MAX_RECALLED_MEMORY_IDS, MEMORY_RECALL_EVENT

import evals.issue_recall.corpus as corpus_module
import evals.issue_recall.runner as runner_module
from evals.__main__ import _tasks as selected_eval_tasks
from evals.harness.capability import CapabilityCase, CapabilityOutput, ToolInvocation, TurnLog
from evals.harness.target import TargetResult
from evals.issue_recall.corpus import (
    ABSENT_FILINGS,
    AMBIENT_CLAIMS,
    AMBIENT_SUBJECTS,
    CASES,
    FILINGS,
    HARD_NEGATIVES,
    ISSUE_SHAPED,
    MID_THREAD,
    PAGES,
    PHRASINGS,
    REPO,
    SIBLING_REPOS,
    FilingCase,
    IssueThread,
    ambient_memories,
    corpus_digest,
    corpus_issue_numbers,
    related_refs,
    rendered_pages,
)
from evals.issue_recall.runner import (
    ISSUE_RECALL_TASK,
    AbsentTopicGrader,
    ExpectedPage,
    IssueRecallGrader,
    load_issue_recall,
)
from evals.issue_recall.state import AmbientOwner, CorpusReadiness, PageOwners
from ufo.sources.sync import page_id_for

SOURCE_ID = UUID("11111111-2222-3333-4444-555555555555")
WORKSPACE_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def _owner(source_ref: str, ordinal: int) -> UUID:
    return UUID(bytes=sha256(f"{source_ref}/{ordinal}".encode()).digest()[:16], version=4)


def _readiness(derived: frozenset[str] | None = None) -> CorpusReadiness:
    """One materialized corpus where every page (or only `derived`) yielded two facts, holding the
    declared ambient haystack alongside them."""
    pages = rendered_pages()
    ambient = ambient_memories()
    return CorpusReadiness(
        corpus_digest=corpus_digest(),
        workspace_id=WORKSPACE_ID,
        source_id=SOURCE_ID,
        pages_root=Path("/tmp/issue-recall/pages"),
        page_count=len(pages),
        fact_count=2 * len(pages),
        chunk_count=3 * len(pages),
        pages=tuple(
            PageOwners(
                source_ref=page.source_ref,
                page_id=page_id_for(SOURCE_ID, page.source_ref),
                memory_ids=(
                    ()
                    if derived is not None and page.source_ref not in derived
                    else (_owner(page.source_ref, 0), _owner(page.source_ref, 1))
                ),
            )
            for page in pages
        ),
        ambient=tuple(
            AmbientOwner(ref=memory.ref, memory_id=_owner(memory.ref, 0)) for memory in ambient
        ),
    )


def _output(memory_ids: tuple[UUID, ...], **overrides: object) -> CapabilityOutput:
    attributes: dict[str, object] = {"memory_ids": [str(memory_id) for memory_id in memory_ids]}
    attributes.update(overrides)
    return CapabilityOutput(
        "Filed: sync wedges on token expiry. Related: #412, #431.",
        (),
        log=TurnLog(event=MEMORY_RECALL_EVENT, turn_id=uuid4(), attributes=attributes),
    )


@dataclass(frozen=True)
class RecallTarget:
    """A target whose every turn injected the same recalled memories, keeping the cases it drove."""

    memory_ids: tuple[UUID, ...]
    driven: list[CapabilityCase] = field(default_factory=list)

    async def run(self, case: CapabilityCase) -> TargetResult:
        self.driven.append(case)
        return TargetResult(_output(self.memory_ids), clean=True)


def test_fixture_pages_are_what_a_github_sync_would_land() -> None:
    """The body is checked against the fixture record itself, never against a second `render` call:
    the header is the connector's own shape, the payload is the record's JSON with its id scoped to
    the repo the fan-out stamped (that scoped id keys the page, so no two repos collide on one id),
    and the pull request's `head`/`base` have lost the foreign `repo` object that `flatten`
    strips."""
    pages = rendered_pages()
    issue = next(page for page in PAGES if page.key == "issue-412")
    landed = next(page for page in pages if page.key == "issue-412")
    header, _, payload = landed.body.partition("\n\n")
    pull = next(page for page in PAGES if page.key == "pull-431")
    pull_body = json.loads(
        next(page for page in pages if page.key == "pull-431").body.partition("\n\n")[2]
    )

    assert landed.source_ref == f"issues/{REPO}/2400412"
    assert landed.title == issue.title
    assert header == f"# github issues: {issue.title}"
    assert json.loads(payload) == issue.record() | {"id": f"{REPO}/2400412"}
    assert pull.record()["head"]["repo"] == {"full_name": REPO}
    assert pull_body["head"] == {"ref": "fix/431"}
    assert pull_body["base"] == {"ref": "main"}
    assert all(page.digest == "sha256:" + sha256(page.body.encode()).hexdigest() for page in pages)
    assert len(pages) == len(PAGES) == 23
    assert min(len(page.body) for page in pages) >= MIN_PAGE_BODY_CHARS
    assert len({page.source_ref for page in pages}) == len(pages)
    assert {page.stream for page in pages} == {"issues", "pull_requests", "comments"}


def _overlap(body: str, vocabulary: frozenset[str]) -> int:
    return len({word.strip("`.,:;()") for word in body.lower().split()} & vocabulary)


def test_the_haystack_makes_the_pool_realistic_and_leans_near_topic() -> None:
    """Recall injects a fixed number of memories, so a corpus of only the graded evidence hands
    retrieval a third of itself and a coverage bar sits near chance. The haystack is what makes
    coverage mean something: it takes the injected share of the pool under 5%, and its hand-written
    half is measurably nearer the filing topics than the generated bank, so what is tested is
    discrimination rather than topic detection."""
    ambient = ambient_memories()
    generated = ambient[len(HARD_NEGATIVES) + len(ISSUE_SHAPED) :]
    pool = len(ambient) + len(rendered_pages())
    vocabulary = frozenset(
        word.strip("`.,:;()")
        for filing in FILINGS
        for word in filing.symptom.lower().split()
        if len(word) > 3
    )
    hard_mean = sum(_overlap(m.body, vocabulary) for m in HARD_NEGATIVES) / len(HARD_NEGATIVES)
    filler_mean = sum(_overlap(m.body, vocabulary) for m in generated) / len(generated)

    assert len(ambient) == (
        len(HARD_NEGATIVES) + len(ISSUE_SHAPED) + len(AMBIENT_CLAIMS) * len(AMBIENT_SUBJECTS)
    )
    assert pool >= 400
    assert MAX_RECALLED_MEMORY_IDS / pool < 0.05
    assert len({memory.ref for memory in ambient}) == len(ambient)
    assert len({memory.body for memory in ambient}) == len(ambient)
    assert len(generated) == len(AMBIENT_CLAIMS) * len(AMBIENT_SUBJECTS)
    assert hard_mean > filler_mean * 2
    assert not any(
        str(number) in memory.body
        for memory in ambient
        for number in (412, 388, 431, 355, 402, 297)
    )


async def test_the_issue_shaped_bank_denies_recall_a_shortcut_on_form() -> None:
    """The graded facts read as issue-tracker records — `derive_facts` writes bodies like "Atlas
    issue #388 (closed) reported ...". If they were the workspace's only records of that shape, a
    filing-shaped query could separate them by form and never discriminate on topic. The sibling
    repositories supply that shape in quantity, several of them deliberately adjacent: a nightly job
    that stalls on a late FX feed, a stock count that hangs on an offline printer queue, a weekly
    report whose CSV stops at 5,000 rows, callbacks retried without backoff."""
    shaped = tuple(m for m in ISSUE_SHAPED if "issue #" in m.body or "PR #" in m.body)
    graded = sum(len(filing.related) for filing in FILINGS)

    assert len(shaped) == len(ISSUE_SHAPED)
    assert all(any(repo in m.body for repo in SIBLING_REPOS) for m in ISSUE_SHAPED)
    assert not any(REPO in m.body for m in ISSUE_SHAPED)
    assert len(ISSUE_SHAPED) > len(rendered_pages()) * 2
    assert len(ISSUE_SHAPED) > graded * 6


async def test_the_haystack_competes_for_the_injection_slots(tmp_path: Path) -> None:
    """Every ambient row is a distractor for every case, so a turn whose injection is all haystack
    fails on the front-precision check rather than passing for lack of accounting."""
    path = tmp_path / "readiness.json"
    readiness = _readiness()
    path.write_text(readiness.model_dump_json())
    run = load_issue_recall(path)
    haystack = tuple(owner.memory_id for owner in readiness.ambient[:MAX_RECALLED_MEMORY_IDS])

    report = await run.tasks[0].run(RecallTarget(haystack), asyncio.Semaphore(1))

    assert len(readiness.ambient) == len(ambient_memories())
    coverage_cases = tuple(case for case in report.cases if not case.name.endswith(":absent-topic"))
    assert not any(case.passed for case in coverage_cases)
    assert all("covered 0/" in case.reason for case in coverage_cases)


def test_corpus_digest_is_content_addressed(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = corpus_digest()
    assert digest == corpus_digest()

    monkeypatch.setattr(corpus_module, "PAGES", PAGES[:-1])

    assert corpus_digest() != digest


def test_every_filing_claims_pages_no_other_filing_claims() -> None:
    claimed = [
        ref
        for filing in FILINGS
        for ref in related_refs(FilingCase(filing.slug, "", filing.related, filing.min_coverage))
    ]

    assert len(claimed) == len(set(claimed))
    assert all(len(filing.related) >= 2 for filing in FILINGS)
    assert all(
        filing.min_coverage * len(filing.related) >= len(filing.related) / 2 for filing in FILINGS
    )
    assert set(claimed) < {page.source_ref for page in rendered_pages()}


def test_every_filing_is_asked_every_way_and_only_one_way_names_the_repository() -> None:
    """The terse asks are the point: a member files an issue without naming the repository, and the
    whole inbound is the recall query, so each phrasing is a distinct retrieval query per topic.
    Each topic is asked once per phrasing as a first message, then once mid-conversation."""
    named = tuple(case for case in CASES if REPO in case.message)

    assert len(CASES) == len(FILINGS) * (len(PHRASINGS) + 1)
    assert len({case.name for case in CASES}) == len(CASES)
    assert {case.name for case in CASES} == {
        f"{filing.slug}:{phrasing.slug}" for filing in FILINGS for phrasing in PHRASINGS
    } | {f"{filing.slug}:{MID_THREAD}" for filing in FILINGS}
    assert tuple(case.name for case in named) == tuple(
        f"{filing.slug}:named-repo" for filing in FILINGS
    )


def test_every_filing_also_arrives_mid_conversation() -> None:
    """The mid-conversation ask is the next turn of a thread already spent on unrelated technical
    Q&A: an even number of prior messages, so the thread ends on an assistant reply and the graded
    turn is the member's. That ask is terse and names neither the repository nor an issue number,
    and it grades against the same related set and bar as its topic's first-message cases."""
    mid = tuple(case for case in CASES if case.name.endswith(f":{MID_THREAD}"))

    assert tuple(case.name for case in mid) == tuple(
        f"{filing.slug}:{MID_THREAD}" for filing in FILINGS
    )
    assert tuple((case.related, case.min_coverage) for case in mid) == tuple(
        (filing.related, filing.min_coverage) for filing in FILINGS
    )
    assert all(len(case.prior_messages) == 4 for case in mid)
    assert all(len(case.prior_messages) % 2 == 0 for case in mid)
    assert all(message.strip() for case in mid for message in case.prior_messages)
    assert all(len(case.message) <= 80 for case in mid)
    assert not any(
        REPO in text or "#" in text for case in mid for text in (case.message, *case.prior_messages)
    )
    assert all(not case.prior_messages for case in CASES if case not in mid)


def test_related_refs_rejects_an_unknown_page() -> None:
    with pytest.raises(ValueError, match="unknown pages"):
        related_refs(
            FilingCase(name="bogus", message="file it", related=("issue-999",), min_coverage=0.5)
        )


async def test_grader_passes_when_the_related_facts_lead_the_injection() -> None:
    related = ExpectedPage("issues/2400412", frozenset({_owner("issues/2400412", 0)}))
    other = ExpectedPage("pull_requests/2400431", frozenset({_owner("pull/431", 0)}))
    distractor = _owner("issues/2400466", 0)
    grader = IssueRecallGrader((related, other), frozenset({distractor}), frozenset(), 0.5)

    verdict = await grader(_output((_owner("issues/2400412", 0), distractor)))

    assert verdict.passed
    assert verdict.evidence["coverage"] == 0.5
    assert verdict.evidence["evidenceRanks"] == {
        "issues/2400412": 1,
        "pull_requests/2400431": None,
    }
    assert verdict.evidence["relatedInjected"] == 1
    assert verdict.evidence["distractorInjected"] == 1
    assert (verdict.evidence["frontSlots"], verdict.evidence["relatedInFront"]) == (2, 1)
    assert verdict.evidence["citedIssues"] == ["412", "431"]
    assert verdict.evidence["memorySearchCalls"] == []


async def test_grader_fails_below_the_coverage_bar() -> None:
    related = tuple(
        ExpectedPage(f"issues/{number}", frozenset({_owner(f"issues/{number}", 0)}))
        for number in (2400412, 2400388, 2400355, 2400297)
    )
    grader = IssueRecallGrader(related, frozenset(), frozenset(), 0.5)

    verdict = await grader(_output((_owner("issues/2400412", 0),)))

    assert not verdict.passed
    assert "covered 1/4 related pages, below 50%" in verdict.reason
    assert verdict.evidence["coverage"] == 0.25


async def test_grader_passes_when_the_related_set_trails_a_full_context() -> None:
    """Recall injects a fixed number of memories, so a small related set is always outnumbered
    overall; what matters is that it leads the context rather than being buried."""
    related = ExpectedPage("issues/2400412", frozenset({_owner("issues/2400412", 0)}))
    distractors = frozenset(_owner(f"issues/{number}", 0) for number in (2400466, 2400372, 2400360))
    grader = IssueRecallGrader((related,), distractors, frozenset(), 0.5)

    verdict = await grader(_output((_owner("issues/2400412", 0), *sorted(distractors))))

    assert verdict.passed
    assert verdict.evidence["distractorInjected"] == 3
    assert verdict.evidence["relatedInjected"] == 1
    assert (verdict.evidence["frontSlots"], verdict.evidence["relatedInFront"]) == (1, 1)


async def test_grader_fails_when_distractors_lead_the_context() -> None:
    related = tuple(
        ExpectedPage(f"issues/{number}", frozenset({_owner(f"issues/{number}", 0)}))
        for number in (2400412, 2400388, 2400355, 2400297)
    )
    distractors = tuple(
        _owner(f"issues/{number}", 0) for number in (2400466, 2400372, 2400360, 2400501)
    )
    grader = IssueRecallGrader(related, frozenset(distractors), frozenset(), 0.75)

    verdict = await grader(
        _output((*distractors[:3], *(_owner(page.source_ref, 0) for page in related)))
    )

    assert not verdict.passed
    assert "distractors led the injected context" in verdict.reason
    assert verdict.evidence["coverage"] == 1.0
    assert (verdict.evidence["frontSlots"], verdict.evidence["relatedInFront"]) == (4, 1)


async def test_grader_fails_when_no_related_page_derived_a_fact() -> None:
    grader = IssueRecallGrader(
        (ExpectedPage("issues/2400412", frozenset()),), frozenset(), frozenset(), 0.5
    )

    verdict = await grader(_output((uuid4(),)))

    assert not verdict.passed
    assert verdict.reason == "no related page derived a durable memory to recall"
    assert verdict.evidence["coverage"] == 0.0
    assert verdict.evidence["mappedCount"] == 0
    assert verdict.evidence["unmappedEvidence"] == ["issues/2400412"]


async def test_grader_fails_when_recall_degraded() -> None:
    grader = IssueRecallGrader(
        (ExpectedPage("issues/2400412", frozenset({_owner("issues/2400412", 0)})),),
        frozenset(),
        frozenset(),
        0.5,
    )

    verdict = await grader(_output((), error_class="TimeoutError"))

    assert not verdict.passed
    assert verdict.reason == "recall degraded (TimeoutError)"
    assert verdict.evidence["recallError"] == "TimeoutError"


@pytest.mark.parametrize(
    ("output", "reason"),
    (
        (CapabilityOutput("drafted", ()), "memory recall log is missing"),
        (
            CapabilityOutput(
                "drafted",
                (),
                log=TurnLog(event="memory.other", turn_id=uuid4(), attributes={"memory_ids": []}),
            ),
            "memory recall log has the wrong event",
        ),
        (
            CapabilityOutput(
                "drafted",
                (),
                log=TurnLog(
                    event=MEMORY_RECALL_EVENT, turn_id=uuid4(), attributes={"path": "hook"}
                ),
            ),
            "memory recall log is invalid",
        ),
    ),
)
async def test_grader_fails_without_a_valid_recall_log(
    output: CapabilityOutput, reason: str
) -> None:
    grader = IssueRecallGrader(
        (ExpectedPage("issues/2400412", frozenset({uuid4()})),), frozenset(), frozenset(), 0.5
    )

    verdict = await grader(output)

    assert not verdict.passed
    assert verdict.reason == reason


async def test_grader_records_an_explicit_memory_search() -> None:
    memory_id = _owner("issues/2400412", 0)
    grader = IssueRecallGrader(
        (ExpectedPage("issues/2400412", frozenset({memory_id})),), frozenset(), frozenset(), 0.5
    )
    searched = replace(
        _output((memory_id,)),
        calls=(ToolInvocation(name="memory_search", input={}, result="hit", has_result=True),),
    )

    verdict = await grader(searched)

    assert verdict.passed
    assert verdict.evidence["memorySearchCalls"] == ["memory_search"]


def test_load_issue_recall_builds_one_leaf_pinned_to_its_owners(tmp_path: Path) -> None:
    readiness = _readiness()
    path = tmp_path / "readiness.json"
    path.write_text(readiness.model_dump_json())
    run = load_issue_recall(path)
    moved = list(readiness.pages)
    moved[0] = moved[0].model_copy(update={"memory_ids": (uuid4(),)})
    path.write_text(readiness.model_copy(update={"pages": tuple(moved)}).model_dump_json())
    rederived = load_issue_recall(path)

    assert tuple(task.name for task in run.tasks) == (ISSUE_RECALL_TASK,)
    assert run.tasks[0].cases == tuple(case.name for case in CASES) + tuple(
        f"{absent.slug}:absent-topic" for absent in ABSENT_FILINGS
    )
    assert run.readiness.workspace_id == WORKSPACE_ID
    assert rederived.tasks[0].digest != run.tasks[0].digest
    assert selected_eval_tasks((), None, run) == run.tasks
    assert selected_eval_tasks((ISSUE_RECALL_TASK,), None, run) == run.tasks


def test_a_thread_the_ask_lands_in_moves_the_suite_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case's prior turns are part of what a run measured, so editing one is a different suite."""
    path = tmp_path / "readiness.json"
    path.write_text(_readiness().model_dump_json())
    run = load_issue_recall(path)
    mid = next(case for case in CASES if case.name.endswith(f":{MID_THREAD}"))
    edited = replace(mid, prior_messages=(*mid.prior_messages[:-1], "a different answer"))
    monkeypatch.setattr(
        runner_module, "CASES", tuple(edited if case is mid else case for case in CASES)
    )

    assert load_issue_recall(path).tasks[0].digest != run.tasks[0].digest


def test_load_issue_recall_rejects_a_foreign_or_partial_corpus(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    path.write_text(
        _readiness().model_copy(update={"corpus_digest": "sha256:" + "0" * 64}).model_dump_json()
    )
    with pytest.raises(ValueError, match="different fixture corpus"):
        load_issue_recall(path)

    readiness = _readiness()
    path.write_text(readiness.model_copy(update={"pages": readiness.pages[1:]}).model_dump_json())
    with pytest.raises(ValueError, match="missing pages"):
        load_issue_recall(path)


async def test_issue_recall_report_carries_the_recall_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    path.write_text(_readiness().model_dump_json())
    run = load_issue_recall(path)
    injected = tuple(_owner(ref, 0) for ref in related_refs(CASES[0]))
    target = RecallTarget(injected)

    report = await run.tasks[0].run(target, asyncio.Semaphore(1))

    absent = {f"{filing.slug}:absent-topic" for filing in ABSENT_FILINGS}
    passed = {case.name for case in report.cases if case.passed} - absent
    per_topic = len(PHRASINGS) + 1
    assert passed == {f"{FILINGS[0].slug}:{phrasing.slug}" for phrasing in PHRASINGS} | {
        f"{FILINGS[0].slug}:{MID_THREAD}"
    }
    assert report.mean_mapped_evidence_coverage == pytest.approx(per_topic / len(CASES))
    assert report.min_mapped_evidence_coverage == 0.0
    assert report.degraded_recall_count == 0
    assert report.unmapped_evidence_count == 0
    assert not report.passed
    assert {
        case.name: case.prior_messages for case in target.driven if case.name not in absent
    } == {case.name: case.prior_messages for case in CASES}


async def test_a_related_page_that_derived_nothing_counts_against_coverage(
    tmp_path: Path,
) -> None:
    """Coverage is a fraction of the whole related set, never of the subset that happened to derive
    a fact. If it re-based onto the mapped subset, a `derive_facts` regression that left one of a
    topic's four pages deriving would read as a perfect 1.00 pass while three quarters of the graded
    evidence had silently left the grading set — green cases hiding the regression this leaf exists
    to catch."""
    derived = frozenset(related_refs(CASES[0])[:1])
    path = tmp_path / "readiness.json"
    path.write_text(_readiness(derived).model_dump_json())
    run = load_issue_recall(path)

    report = await run.tasks[0].run(
        RecallTarget(tuple(_owner(ref, 0) for ref in derived)), asyncio.Semaphore(1)
    )

    per_topic = len(PHRASINGS) + 1
    coverage_cases = tuple(case for case in report.cases if not case.name.endswith(":absent-topic"))
    partial = tuple(case for case in coverage_cases if case.name.startswith(FILINGS[0].slug))
    assert report.unmapped_evidence_count == sum(len(case.related) for case in CASES) - per_topic
    assert report.mean_mapped_evidence_coverage == pytest.approx(
        per_topic * (1 / len(FILINGS[0].related)) / len(CASES)
    )
    assert not any(case.passed for case in coverage_cases)
    assert len(partial) == per_topic
    assert all("covered 1/4 related pages" in case.reason for case in partial)
    assert all("derived no fact to recall" in case.reason for case in partial)


async def test_absent_topic_grader_passes_when_nothing_existing_is_claimed() -> None:
    """The control's own logic: with no match to find, drafting fresh is the pass. The count of
    issue-shaped memories that reached context is recorded either way, since that exposure is the
    priming the case exists to rule out."""
    shaped = frozenset({_owner("ledger-88", 0), _owner("warehouse-305", 0)})
    grader = AbsentTopicGrader(corpus_issue_numbers(), shaped)
    output = replace(
        _output(tuple(shaped)),
        response="Nothing on file matches this. Here is the issue I would open: cold-start crash …",
    )

    verdict = await grader(output)

    assert verdict.passed
    assert verdict.evidence["issueShapedInjected"] == 2
    assert verdict.evidence["citedIssues"] == []
    assert verdict.evidence["attributedToCorpusIssues"] == []


async def test_absent_topic_grader_fails_a_reply_that_claims_an_existing_issue() -> None:
    grader = AbsentTopicGrader(corpus_issue_numbers(), frozenset())
    output = replace(
        _output((uuid4(),)),
        response="This is already filed as #412, so I have not opened a duplicate.",
    )

    verdict = await grader(output)

    assert not verdict.passed
    assert "attributed an absent topic to existing issues" in verdict.reason
    assert verdict.evidence["attributedToCorpusIssues"] == ["412"]


async def test_absent_topic_grader_ignores_a_number_the_corpus_never_used() -> None:
    """A version string or an unrelated number is not an attribution."""
    grader = AbsentTopicGrader(corpus_issue_numbers(), frozenset())
    output = replace(_output((uuid4(),)), response="Filed as a fresh report; see #9042 upstream.")

    verdict = await grader(output)

    assert verdict.passed
    assert verdict.evidence["citedIssues"] == ["9042"]
    assert verdict.evidence["attributedToCorpusIssues"] == []


async def test_absent_topic_grader_fails_on_a_missing_log_a_bad_event_and_degraded_recall() -> None:
    grader = AbsentTopicGrader(corpus_issue_numbers(), frozenset())
    wrong_event = CapabilityOutput(
        "drafted",
        (),
        log=TurnLog(event="memory.other", turn_id=uuid4(), attributes={"memory_ids": []}),
    )

    assert (await grader(CapabilityOutput("drafted", ()))).reason == "memory recall log is missing"
    assert (await grader(wrong_event)).reason == "memory recall log has the wrong event"
    degraded = await grader(_output((), error_class="TimeoutError"))
    assert degraded.reason == "recall degraded (TimeoutError)"
    empty = await grader(replace(_output((uuid4(),)), response="   "))
    assert empty.reason == "answer is empty"


async def test_absent_topic_grader_catches_a_sibling_repository_citation() -> None:
    """The corpus's numbers span the sibling repositories too — #88 exists only in `ISSUE_SHAPED`
    (`ledger-88`), never in `PAGES` — so a reply reaching for one of those is the same false
    attribution as reaching for an Atlas number."""
    grader = AbsentTopicGrader(corpus_issue_numbers(), frozenset())
    sibling_only = frozenset(int(memory.ref.rpartition("-")[2]) for memory in ISSUE_SHAPED) - {
        page.number for page in PAGES if isinstance(page, IssueThread)
    }
    output = replace(
        _output((uuid4(),)),
        response="Already tracked as #88 in the ledger repo, so I have not opened another.",
    )

    verdict = await grader(output)

    assert 88 in sibling_only
    assert not verdict.passed
    assert verdict.evidence["attributedToCorpusIssues"] == ["88"]


async def test_absent_topic_grader_fails_a_malformed_recall_log() -> None:
    grader = AbsentTopicGrader(corpus_issue_numbers(), frozenset())
    malformed = CapabilityOutput(
        "drafted",
        (),
        log=TurnLog(event=MEMORY_RECALL_EVENT, turn_id=uuid4(), attributes={"path": "hook"}),
    )

    verdict = await grader(malformed)

    assert not verdict.passed
    assert verdict.reason == "memory recall log is invalid"
