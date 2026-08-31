"""The member-tier case families: seeded corpora probing the RFC 0038 member tier. Queries name
member intent and never quote a seeded description back — a query that echoes the card tests
string matching, not routing. Each regime case asserts its own construction at collection time —
a fold corpus fits the prompt fold and renders no member block; past the fold, the block is
rendered for the case's query and the target's placement checked (full line, bare name, absent) —
so a regime mislabel fails at import, never in a live run. The names family seeds the observed
99th-percentile corpus; retrieval, tail, and crowding seed exactly at the user-skill cap.

The subagent-handoff family's mount may come from the parent's own load, from `preload_skills` in
the spawn, or from the child's own block-routed load — a child turn carries the member block too,
and every mount lands in the same conversation workspace. The tasks are shaped so delegation is
the natural route; the verdict proves the member skill reached whoever wrote."""

from __future__ import annotations

from dataclasses import replace
from textwrap import dedent

from evals.harness.capability import WorkspaceFile
from evals.skill_loading.catalog import (
    EXPORT_CSV,
    REPORT_PART1_PDF,
    REPORT_PART2_PDF,
    SCHEMA_SQL,
)
from evals.skill_loading.corpus import CORPUS_P99, CORPUS_STRESS, crowd_corpus, spread_corpus
from evals.skill_loading.runner import SkillFixture, SkillLoadCase
from ufo.runtime.skills.runtime import SkillCard
from ufo.runtime.skills.selection import catalog_fits, folds_into_prompt, member_block, skill_line

FULL = "full"
NAME = "name"
ABSENT = "absent"
REGIME_PLACEMENTS = {"names": NAME, "retrieval": FULL, "tail": ABSENT}


def _cards(corpus: tuple[SkillFixture, ...]) -> tuple[SkillCard, ...]:
    return tuple(
        SkillCard(
            name=fixture.name,
            description=fixture.description,
            depends=fixture.depends,
            pinned=fixture.pinned,
        )
        for fixture in corpus
    )


def _placements(query: str, corpus: tuple[SkillFixture, ...]) -> dict[str, str]:
    """Each card's placement in the rendered block, classified by exact line: a card is full when
    its `skill_line` render appears, bare when only its name line does, absent otherwise."""
    cards = _cards(corpus)
    lines = frozenset(member_block(query, cards).split("\n"))
    placements: dict[str, str] = {}
    for card in cards:
        if skill_line(card) in lines:
            placements[card.name] = FULL
        elif f"- {card.name}" in lines:
            placements[card.name] = NAME
        else:
            placements[card.name] = ABSENT
    return placements


def _assert_regime(
    regime: str,
    case_name: str,
    message: str,
    expected: str,
    corpus: tuple[SkillFixture, ...],
) -> None:
    cards = _cards(corpus)
    if all(fixture.name != expected for fixture in corpus):
        raise ValueError(f"case {case_name!r}: the corpus does not seed {expected!r}")
    if regime == "fold":
        if not folds_into_prompt(cards):
            raise ValueError(f"case {case_name!r}: a fold corpus must fit the prompt fold budget")
        if member_block(message, cards):
            raise ValueError(f"case {case_name!r}: a folded corpus must render no member block")
        return
    if folds_into_prompt(cards):
        raise ValueError(
            f"case {case_name!r}: regime {regime!r} needs a corpus past the prompt fold"
        )
    if catalog_fits(cards):
        raise ValueError(
            f"case {case_name!r}: regime {regime!r} needs a corpus past the full catalog"
        )
    required = REGIME_PLACEMENTS[regime]
    found = _placements(message, corpus)[expected]
    if found != required:
        raise ValueError(
            f"case {case_name!r}: regime {regime!r} needs the target rendered as {required!r}, "
            f"found {found!r}"
        )


def _assert_full(case_name: str, message: str, corpus: tuple[SkillFixture, ...], name: str):
    """The seeded trap must be visible with its description — the whole corpus folded into the
    prompt, or the trap's full line in the member block."""
    if all(fixture.name != name for fixture in corpus):
        raise ValueError(f"case {case_name!r}: the corpus does not seed {name!r}")
    if folds_into_prompt(_cards(corpus)):
        return
    found = _placements(message, corpus)[name]
    if found != FULL:
        raise ValueError(
            f"case {case_name!r}: the seeded trap {name!r} must render in full to tempt the "
            f"route, found {found!r}"
        )


def _regime_case(
    regime: str,
    name: str,
    message: str,
    expected: str,
    corpus: tuple[SkillFixture, ...],
    forbidden: tuple[str, ...] = (),
    workspace_files: tuple[WorkspaceFile, ...] = (),
) -> SkillLoadCase:
    _assert_regime(regime, name, message, expected, corpus)
    return SkillLoadCase(
        name,
        message,
        expected=expected,
        forbidden=forbidden,
        workspace_files=workspace_files,
        member_skills=corpus,
        regime=regime,
    )


FRESHNESS_PRELUDE = "Are you there? I have a task coming in a minute."


def _freshness_case(
    name: str,
    message: str,
    target: SkillFixture,
    corpus: tuple[SkillFixture, ...],
    forbidden: tuple[str, ...] = (),
) -> SkillLoadCase:
    """The target is saved only after the conversation's first turn settles, so the very next
    turn must find it through the always-fresh lexical leg — no index job has seen it."""
    _assert_regime("retrieval", name, message, target.name, (*corpus, target))
    return SkillLoadCase(
        name,
        message,
        expected=target.name,
        forbidden=forbidden,
        member_skills=corpus,
        late_skills=(target,),
        prelude_message=FRESHNESS_PRELUDE,
        regime="retrieval",
    )


def _pinned_case(
    name: str,
    message: str,
    target: SkillFixture,
    corpus: tuple[SkillFixture, ...],
    forbidden: tuple[str, ...],
) -> SkillLoadCase:
    """A pin proves itself at collection: the pinned target renders in full, and the same corpus
    with the pin removed does not — so the full line is the pin's doing, not the query's rank. A
    pin only matters in the block, so the corpus must sit past the prompt fold."""
    seeded = (*corpus, target)
    if folds_into_prompt(_cards(seeded)):
        raise ValueError(f"case {name!r}: a pins corpus must sit past the prompt fold")
    if _placements(message, seeded)[target.name] != FULL:
        raise ValueError(f"case {name!r}: the pinned target must render in full")
    if _placements(message, (*corpus, replace(target, pinned=False)))[target.name] == FULL:
        raise ValueError(
            f"case {name!r}: the target renders in full even unpinned, so the pin proves nothing"
        )
    return SkillLoadCase(
        name,
        message,
        expected=target.name,
        forbidden=forbidden,
        member_skills=seeded,
        regime="retrieval",
    )


def _crowding_case(
    name: str,
    message: str,
    target: SkillFixture,
    crowd: tuple[SkillFixture, ...],
    corpus: tuple[SkillFixture, ...],
) -> SkillLoadCase:
    seeded = (target, *crowd, *corpus)
    if _placements(message, seeded)[target.name] == ABSENT:
        raise ValueError(f"case {name!r}: the crowded target must stay visible in the block")
    return SkillLoadCase(
        name,
        message,
        expected=target.name,
        forbidden=tuple(fixture.name for fixture in crowd),
        member_skills=seeded,
    )


def _ablation_pair(
    name: str,
    message: str,
    corpus: tuple[SkillFixture, ...],
    trap: str,
    expected: str = "",
    forbidden: tuple[str, ...] = (),
    expects_no_load: bool = False,
    workspace_files: tuple[WorkspaceFile, ...] = (),
) -> tuple[SkillLoadCase, ...]:
    _assert_full(name, message, corpus, trap)
    return tuple(
        SkillLoadCase(
            f"{name}-{tag}",
            message,
            expected=expected,
            forbidden=forbidden,
            workspace_files=workspace_files,
            member_skills=corpus,
            expects_no_load=expects_no_load,
            ablation=tag,
        )
        for tag in ("block-on", "block-off")
    )


def _vs_deploy_case(
    name: str,
    message: str,
    expected: str,
    trap: str | None,
    corpus: tuple[SkillFixture, ...],
    forbidden: tuple[str, ...],
    workspace_files: tuple[WorkspaceFile, ...] = (),
) -> SkillLoadCase:
    if trap is not None:
        _assert_full(name, message, corpus, trap)
    return SkillLoadCase(
        name,
        message,
        expected=expected,
        forbidden=forbidden,
        workspace_files=workspace_files,
        member_skills=corpus,
    )


def _note(path: str, body: str) -> WorkspaceFile:
    return WorkspaceFile(path, dedent(body).strip().encode() + b"\n")


VENDOR_ONBOARDING = SkillFixture(
    "vendor-onboarding",
    "Load when a member asks to onboard a supplier the company just signed: collect the W-9, "
    "insurance certificate, and banking details, then register the vendor in the tracker.",
    "1. Collect the W-9, certificate of insurance, and banking details.\n"
    "2. Register the vendor in the tracker and name the owning approver.",
)
CUSTOMER_ONBOARDING = SkillFixture(
    "customer-onboarding",
    "Load when a member asks to kick off a new customer's rollout: seats, data import, and the "
    "launch review schedule.",
    "1. Open the rollout checklist.\n2. Schedule the launch review.",
)
EXPENSE_AUDIT = SkillFixture(
    "expense-audit",
    "Load when a member asks to review submitted expense reports for policy violations before "
    "approval.",
    "1. Pull the period's submissions.\n2. Flag violations with the rule each one breaks.",
)
TRAVEL_BOOKING = SkillFixture(
    "travel-booking",
    "Load when a member asks to arrange work travel inside the company's booking rules.",
    "1. Check the travel policy tier.\n2. Book within it and log the itinerary.",
)
RELEASE_NOTES = SkillFixture(
    "release-notes",
    "Load when a member asks to turn merged pull requests into customer-facing notes for a "
    "version release.",
    "1. Group merged changes by customer impact.\n2. Write one plain sentence per change.",
)
DATA_QUALITY_CHECKUP = SkillFixture(
    "data-quality-checkup",
    "Load when a member asks for the monthly data hygiene sweep: refresh stale account owners "
    "and archive dormant records.",
    "1. List records untouched for 90 days.\n2. Reassign or archive each one.",
)
WEEKLY_METRICS_NOTE = SkillFixture(
    "weekly-metrics-note",
    "Load when a member asks for the weekly metrics note prepared the team's usual way: deltas "
    "on signups, activation, and MRR with a headline and asks.",
    "1. Compute week-over-week deltas.\n2. Write headline, wins, risks, asks — under 200 words.",
)
REMINDER_RULES = SkillFixture(
    "reminder-rules",
    "Load when a member asks how the team's reminder conventions work: which channels carry "
    "which nudges and when a reminder escalates.",
    "1. Answer from the conventions table.\n2. Name the escalation threshold.",
)
PAYMENT_RECONCILIATION = SkillFixture(
    "payment-reconciliation",
    "Load when a member asks to match incoming bank payments against open invoices and chase "
    "the unmatched remainder.",
    "1. Match by amount and reference.\n2. List the unmatched with a next step each.",
)
PDF_INVOICE_FILING = SkillFixture(
    "pdf-invoice-filing",
    "Load when a member asks to file scanned invoice PDFs into the vendor folders and log each "
    "total.",
    "1. Read each scan's vendor and total.\n2. File it and append the log row.",
)
INCIDENT_RUNBOOK = SkillFixture(
    "incident-runbook",
    "Load when a member reports a production incident: page the on-call owner right away, open "
    "the incident doc, and start the timeline now.",
    "1. Page the on-call owner.\n2. Open the incident doc and start the timeline.",
)
BRAND_PALETTE = SkillFixture(
    "brand-palette",
    "Load when a member asks to make something match the company's brand look: approved colors, "
    "type pairings, and logo spacing.",
    "1. Use the approved palette and type pairings.\n2. Keep the logo clear space.",
)
ESCALATION_MACRO = SkillFixture(
    "escalation-macro",
    "Load when a member asks to escalate a customer issue: capture impact, tag severity, and "
    "notify the account owner.",
    "1. Capture impact and severity.\n2. Notify the account owner with the summary.",
    pinned=True,
)
BRIEF_FORMAT = SkillFixture(
    "brief-format",
    "Load when a member asks for a written brief: the five-section shape every deliverable "
    "follows, with word limits per section.",
    "1. Use the five sections in order.\n2. Hold each section to its word limit.",
    pinned=True,
)
ANNOUNCEMENT_STYLE = SkillFixture(
    "announcement-style",
    "Load when drafting an external announcement: the company voice, banned phrases, and the "
    "closing call-to-action shape.",
    "1. Write in the company voice.\n2. End with the standard call to action.",
)
CASE_STUDY_FRAME = SkillFixture(
    "case-study-frame",
    "Load when writing a customer case study: problem, rollout, measured result, quote — in "
    "that order, with numbers verified.",
    "1. Follow the four sections in order.\n2. Verify every number against the source.",
)
TAX_PROVISION = SkillFixture(
    "quarterly-tax-provision",
    "Load when a member wants the estimated income tax accrual assembled for a period close.",
    "1. Assemble the accrual schedule.\n2. Note open judgment items for the reviewer.",
)
NAMES_CROWD_TAX = (
    SkillFixture(
        "quarter-close-readout",
        "Load when a member asks to get the quarterly close readout ready before the finance "
        "leads arrive.",
        "1. Prepare the readout.\n2. Circulate to the finance leads.",
    ),
    SkillFixture(
        "accountant-handoff",
        "Load when a member asks to get the books ready for the outside accountants before "
        "fieldwork starts.",
        "1. Close the open entries.\n2. Package the support files.",
    ),
    SkillFixture(
        "quarterly-board-pack",
        "Load when a member asks to get the quarterly pack ready before the board arrives on "
        "schedule.",
        "1. Assemble the pack.\n2. Flag anything unresolved.",
    ),
    SkillFixture(
        "monday-priorities",
        "Load when a member asks what must be ready before Monday morning across the team's "
        "open work.",
        "1. List what lands Monday.\n2. Name each owner.",
    ),
    SkillFixture(
        "readiness-check",
        "Load when a member asks whether a deliverable is ready to get to the requester before "
        "the deadline arrives.",
        "1. Run the readiness list.\n2. Report gaps.",
    ),
    SkillFixture(
        "quarterly-spend-review",
        "Load when a member asks to get the quarterly spend review ready for the leads before "
        "month end.",
        "1. Pull the spend by owner.\n2. Mark what moved.",
    ),
    SkillFixture(
        "visitor-arrivals",
        "Load when a member asks to get the office ready before guests arrive on a scheduled "
        "morning.",
        "1. Book the rooms.\n2. Notify reception.",
    ),
    SkillFixture(
        "monday-standup-notes",
        "Load when a member asks for the Monday standup notes ready before the team arrives.",
        "1. Collect updates.\n2. Post the notes.",
    ),
)
EQUITY_CLAWBACK = SkillFixture(
    "equity-grant-clawback",
    "Load when a member asks what happens to unvested shares after a departure and how the "
    "paperwork runs.",
    "1. Compute the unvested balance.\n2. Prepare the repurchase paperwork.",
)
NAMES_CROWD_EQUITY = (
    SkillFixture(
        "departure-checklist",
        "Load when a member says someone resigned and asks to get their offboarding handled "
        "this week.",
        "1. Revoke access.\n2. Recover hardware and settle final pay.",
    ),
    SkillFixture(
        "equity-refresh-cycle",
        "Load when a member asks how the annual equity grant refresh works and who qualifies "
        "this year.",
        "1. Answer from the refresh policy.\n2. Name the next cycle date.",
    ),
    SkillFixture(
        "grant-approvals",
        "Load when a member asks to route a new equity grant through the approval chain and get "
        "it handled this week.",
        "1. Prepare the grant packet.\n2. Route it for approval.",
    ),
    SkillFixture(
        "resignation-response",
        "Load when a member says someone resigned and asks what needs doing in their first week "
        "away.",
        "1. Acknowledge and confirm the last day.\n2. Start the departure checklist.",
    ),
    SkillFixture(
        "separation-paperwork",
        "Load when a member asks to prepare the separation paperwork a departing teammate "
        "needs, handled this week.",
        "1. Prepare the separation packet.\n2. Send it for signature.",
    ),
    SkillFixture(
        "final-pay-calculation",
        "Load when a member asks to calculate final pay for someone who resigned and needs the "
        "amounts this week.",
        "1. Compute the final pay.\n2. Send it to payroll.",
    ),
    SkillFixture(
        "access-revocation",
        "Load when a member asks to revoke access for someone who resigned and needs it handled "
        "the same week.",
        "1. Revoke each system's access.\n2. Confirm completion.",
    ),
    SkillFixture(
        "teammate-transition-plan",
        "Load when a member asks for a transition plan when their teammate resigned and work "
        "needs a new owner this week.",
        "1. List the open work.\n2. Assign each item a new owner.",
    ),
)
CUSTOMS_PACK = SkillFixture(
    "customs-clearance-pack",
    "Load when a member asks for export customs documents assembled for an outbound "
    "international shipment.",
    "1. Assemble the commercial invoice, packing list, and declarations.\n"
    "2. Confirm the incoterms with the carrier.",
)
DATA_ROOM = SkillFixture(
    "diligence-data-room",
    "Load when a member asks to stand up an investor due-diligence data room and track what "
    "each visitor may open.",
    "1. Stand up the folder tree.\n2. Track visitor access per folder.",
)
RETENTION_HOLDS = SkillFixture(
    "retention-hold-review",
    "Load when a member asks whether records under a legal or retention hold may be purged.",
    "1. Check the hold register for the affected records.\n2. Answer per record, with the hold.",
)
SPONSORSHIP_TIERS = SkillFixture(
    "sponsorship-tier-pricing",
    "Load when a member asks what each conference sponsorship tier includes and costs.",
    "1. Answer from the tier table.\n2. Include the current-year prices.",
)

CLEANUP_NOTE = _note(
    "notes/data-cleanup.md",
    """
    # Data cleanup — standing rule

    Any deletion must first pass the legal retention hold review. Check retention holds for
    the affected records before removing anything.
    """,
)
SPONSOR_QUESTION = _note(
    "inbox/sponsor-question.md",
    """
    # Forwarded from the events inbox

    Hi — what do the Gold and Silver sponsorship tiers at your conference include, and what
    do they cost? We are deciding this week.
    """,
)

CATALOG_SPREAD = spread_corpus(17)
SMALL_SPREAD = spread_corpus(12)
BIAS_SPREAD = spread_corpus(40)
RANKED_SPREAD = spread_corpus(90, detailed=True)
HANDOFF_SPREAD = spread_corpus(15)
NAMES_SPREAD = spread_corpus(CORPUS_P99 - 9)
STRESS_SPREAD = spread_corpus(CORPUS_STRESS - 1)
CROWDING_SPREAD = spread_corpus(CORPUS_STRESS - 100)

MEMBER_CATALOG_CASES = (
    _regime_case(
        "fold",
        "member-vendor-onboarding",
        "Acme Fabrication's contract came back countersigned yesterday. Get them set up as a "
        "vendor on our end.",
        expected="vendor-onboarding",
        corpus=(VENDOR_ONBOARDING, CUSTOMER_ONBOARDING, TRAVEL_BOOKING, *CATALOG_SPREAD),
        forbidden=("customer-onboarding", "procurement-intake-review"),
    ),
    _regime_case(
        "fold",
        "member-expense-audit",
        "Go through last month's expense submissions and flag anything that breaks our travel "
        "policy before I approve them.",
        expected="expense-audit",
        corpus=(EXPENSE_AUDIT, TRAVEL_BOOKING, VENDOR_ONBOARDING, *CATALOG_SPREAD),
        forbidden=("travel-booking",),
    ),
    _regime_case(
        "fold",
        "member-release-notes",
        "Write up what changed for customers in the 2.4 release from the merged PRs.",
        expected="release-notes",
        corpus=(RELEASE_NOTES, WEEKLY_METRICS_NOTE, *CATALOG_SPREAD),
        forbidden=("research-report", "weekly-metrics-note"),
    ),
)

MEMBER_VS_DEPLOY_CASES = (
    _vs_deploy_case(
        "deploy-over-member-validation",
        "Validate data/export.csv against data/schema.sql and flag invalid types, duplicates, "
        "and broken foreign keys.",
        expected="data-validation",
        trap="data-quality-checkup",
        corpus=(DATA_QUALITY_CHECKUP, *SMALL_SPREAD),
        forbidden=("data-quality-checkup",),
        workspace_files=(EXPORT_CSV, SCHEMA_SQL),
    ),
    _vs_deploy_case(
        "deploy-over-member-scheduling",
        "Remind me every Friday at 3pm to submit the timesheet.",
        expected="task-scheduling",
        trap="reminder-rules",
        corpus=(REMINDER_RULES, *SMALL_SPREAD),
        forbidden=("reminder-rules",),
    ),
    _vs_deploy_case(
        "member-over-deploy-weekly-note",
        "Put together this week's metrics note the way we always send it.",
        expected="weekly-metrics-note",
        trap="weekly-metrics-note",
        corpus=(WEEKLY_METRICS_NOTE, *SMALL_SPREAD),
        forbidden=("research-report",),
    ),
)

BIAS_ABLATION_CASES = (
    *_ablation_pair(
        "bias-ach-explainer",
        "What's the difference between a wire transfer and an ACH payment? A vendor is asking "
        "which we prefer.",
        corpus=(PAYMENT_RECONCILIATION, *BIAS_SPREAD),
        trap="payment-reconciliation",
        forbidden=("payment-reconciliation", "invoicing-intake-review"),
        expects_no_load=True,
    ),
    *_ablation_pair(
        "bias-deploy-pdf-merge",
        "Merge pdfs/report-part1.pdf and pdfs/report-part2.pdf into one file and compress the "
        "result.",
        corpus=(PDF_INVOICE_FILING, *BIAS_SPREAD),
        trap="pdf-invoice-filing",
        expected="pdf",
        forbidden=("pdf-invoice-filing",),
        workspace_files=(REPORT_PART1_PDF, REPORT_PART2_PDF),
    ),
)

FRESHNESS_CASES = (
    _freshness_case(
        "freshness-incident-runbook",
        "We're seeing 500s on checkout in production right now — kick off our incident process.",
        INCIDENT_RUNBOOK,
        RANKED_SPREAD,
    ),
    _freshness_case(
        "freshness-brand-palette",
        "Make the September webinar banner match our brand look.",
        BRAND_PALETTE,
        RANKED_SPREAD,
        forbidden=("theme-factory",),
    ),
)

PINS_CASES = (
    _pinned_case(
        "pins-escalation-macro",
        "Someone from Delta Corp is fuming about the billing bug — deal with this properly.",
        ESCALATION_MACRO,
        (*RANKED_SPREAD, *crowd_corpus(replace(ESCALATION_MACRO, pinned=False), 1)),
        forbidden=("escalation-macro-take-1",),
    ),
    _pinned_case(
        "pins-brief-format",
        "Send me a one-pager on how the launch went.",
        BRIEF_FORMAT,
        (*RANKED_SPREAD, *crowd_corpus(replace(BRIEF_FORMAT, pinned=False), 1)),
        forbidden=("brief-format-take-1",),
    ),
)

HANDOFF_CASES = (
    _regime_case(
        "fold",
        "handoff-announcement-style",
        "Draft the launch announcement for the new reporting API — follow our announcement style.",
        expected="announcement-style",
        corpus=(ANNOUNCEMENT_STYLE, *HANDOFF_SPREAD),
    ),
    _regime_case(
        "fold",
        "handoff-case-study",
        "Write up the Meridian deployment as a customer case study for the website.",
        expected="case-study-frame",
        corpus=(CASE_STUDY_FRAME, *HANDOFF_SPREAD),
    ),
)

NAMES_CASES = (
    _regime_case(
        "names",
        "names-tax-provision",
        "Get the quarterly tax provision ready before the accountants arrive on Monday morning.",
        expected="quarterly-tax-provision",
        corpus=(*NAMES_CROWD_TAX, TAX_PROVISION, *NAMES_SPREAD),
        forbidden=("accountant-handoff", "quarter-close-readout"),
    ),
    _regime_case(
        "names",
        "names-equity-clawback",
        "Elena resigned and I need the clawback handled on their equity grant this week.",
        expected="equity-grant-clawback",
        corpus=(*NAMES_CROWD_EQUITY, EQUITY_CLAWBACK, *NAMES_SPREAD),
        forbidden=("departure-checklist", "equity-refresh-cycle"),
    ),
)

RETRIEVAL_CASES = (
    _regime_case(
        "retrieval",
        "retrieval-customs-pack",
        "Pull together the customs paperwork for the Rotterdam shipment leaving Friday.",
        expected="customs-clearance-pack",
        corpus=(CUSTOMS_PACK, *STRESS_SPREAD),
        forbidden=("shipping-intake-review",),
    ),
    _regime_case(
        "retrieval",
        "retrieval-data-room",
        "Series C diligence starts next week — set up the data room and the access rules.",
        expected="diligence-data-room",
        corpus=(DATA_ROOM, *STRESS_SPREAD),
        forbidden=("security-intake-review",),
    ),
)

TAIL_CASES = (
    _regime_case(
        "tail",
        "tail-retention-holds",
        "Before we clear out old data, run the review described in notes/data-cleanup.md and "
        "tell me what we may delete.",
        expected="retention-hold-review",
        corpus=(*STRESS_SPREAD, RETENTION_HOLDS),
        workspace_files=(CLEANUP_NOTE,),
    ),
    _regime_case(
        "tail",
        "tail-sponsorship-tiers",
        "A prospect replied to the booth email — answer them from whatever we have on this; "
        "their questions are in inbox/sponsor-question.md.",
        expected="sponsorship-tier-pricing",
        corpus=(*STRESS_SPREAD, SPONSORSHIP_TIERS),
        workspace_files=(SPONSOR_QUESTION,),
    ),
)

CROWDING_CASES = (
    _crowding_case(
        "crowding-expense-audit",
        "Check last month's expense reports for anything that breaks policy before I approve them.",
        EXPENSE_AUDIT,
        crowd_corpus(EXPENSE_AUDIT, 99),
        CROWDING_SPREAD,
    ),
    _crowding_case(
        "crowding-incident-runbook",
        "Checkout is throwing errors for every customer in production — start our incident "
        "process now.",
        INCIDENT_RUNBOOK,
        crowd_corpus(INCIDENT_RUNBOOK, 99),
        CROWDING_SPREAD,
    ),
)

CASES: tuple[SkillLoadCase, ...] = (
    *MEMBER_CATALOG_CASES,
    *MEMBER_VS_DEPLOY_CASES,
    *BIAS_ABLATION_CASES,
    *FRESHNESS_CASES,
    *PINS_CASES,
    *HANDOFF_CASES,
    *NAMES_CASES,
    *RETRIEVAL_CASES,
    *TAIL_CASES,
    *CROWDING_CASES,
)
