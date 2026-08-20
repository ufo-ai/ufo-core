"""Deterministic member-skill corpora: `spread_corpus` fans distinct routing cards across
business areas from a fixed word table, `crowd_corpus` rings one description with near-duplicate
paraphrases. No randomness anywhere — the same call always yields the same fixtures, and
`fixtures_digest` pins their content into the case payload. Corpora anchor at `CORPUS_P99` (the
observed heavy member corpus) and `CORPUS_STRESS` (the RFC 0038 stress bound), the product of the
word tables."""

from __future__ import annotations

from evals.skill_loading.runner import SkillFixture

CORPUS_P99 = 500
CORPUS_STRESS = 5_000

AREAS = (
    ("payroll", "the pay run"),
    ("invoicing", "customer invoices"),
    ("procurement", "purchase requests"),
    ("recruiting", "open roles"),
    ("onboarding", "new-hire setup"),
    ("compliance", "regulatory filings"),
    ("inventory", "stock counts"),
    ("logistics", "carrier movements"),
    ("marketing", "campaign assets"),
    ("support", "open tickets"),
    ("sales", "pipeline deals"),
    ("budgeting", "department budgets"),
    ("forecasting", "demand projections"),
    ("contracts", "signed agreements"),
    ("facilities", "site maintenance"),
    ("security", "access reviews"),
    ("licensing", "software seats"),
    ("translations", "localized copy"),
    ("events", "event programs"),
    ("training", "course completions"),
    ("benefits", "enrollment windows"),
    ("shipping", "outbound parcels"),
    ("warranty", "claim decisions"),
    ("auditing", "control evidence"),
    ("subscriptions", "renewal accounts"),
)
WORKFLOWS = (
    ("intake-review", "screen what arrived and route each item to its owner"),
    ("weekly-summary", "condense the week's activity for the team readout"),
    ("exception-triage", "sort the flagged entries and clear the false alarms"),
    ("approval-prep", "assemble the packet an approver signs off on"),
    ("record-cleanup", "repair stale or duplicated records"),
    ("status-report", "state where every open item stands"),
    ("escalation-notes", "write up an escalation for the next level"),
    ("handover-brief", "brief the person taking a workload over"),
    ("backlog-grooming", "reorder the queue by urgency and age"),
    ("quality-check", "verify finished work against the checklist"),
    ("renewal-tracking", "track what expires soon and who must act"),
    ("dispute-response", "answer a disputed charge or decision"),
    ("vendor-comparison", "compare supplier offers on cost and terms"),
    ("policy-mapping", "map current practice to the written policy"),
    ("cost-breakdown", "split spending by driver and owner"),
    ("trend-digest", "read the month's movement and name the drivers"),
    ("checklist-run", "walk the standing checklist end to end"),
    ("archive-sweep", "move closed items into the archive"),
    ("gap-analysis", "find what the current setup fails to cover"),
    ("rollout-plan", "plan the staged rollout and its checkpoints"),
)
SCOPES = ("emea", "apac", "amer", "q1", "q2", "q3", "q4", "retail", "wholesale", "internal")
ELABORATIONS = (
    "start from the exceptions register, note who already signed off, and keep the running log "
    "current as each item clears",
    "confirm the owning approver in the tracker first, then work oldest to newest so nothing "
    "waits past its window",
    "record the outcome where the whole team reads it, flag anything unusual, and name the "
    "follow-up owner explicitly",
    "compare the result against the last completed cycle, call out what moved, and attach the "
    "supporting numbers inline",
    "close the loop with the requester when the pass completes, listing what changed and what "
    "still needs a decision",
)


def spread_corpus(n: int, detailed: bool = False) -> tuple[SkillFixture, ...]:
    """`n` topically distinct fixtures across the area, workflow, and scope tables, in a stable
    order; names stay unique up to the tables' product. `detailed` appends an elaboration clause,
    the long-card variant that pushes a sub-cap corpus past the full-catalog budget."""
    limit = len(AREAS) * len(WORKFLOWS) * len(SCOPES)
    if n > limit:
        raise ValueError(f"spread_corpus caps at {limit} distinct fixtures, asked for {n}")
    fixtures: list[SkillFixture] = []
    for index in range(n):
        area, subject = AREAS[index % len(AREAS)]
        workflow, intent = WORKFLOWS[(index // len(AREAS)) % len(WORKFLOWS)]
        scope = SCOPES[index // (len(AREAS) * len(WORKFLOWS))]
        plain = index < len(AREAS) * len(WORKFLOWS)
        name = f"{area}-{workflow}" if plain else f"{area}-{workflow}-{scope}"
        suffix = "" if plain else f" for {scope}"
        tail = f"; {ELABORATIONS[index % len(ELABORATIONS)]}" if detailed else ""
        fixtures.append(
            SkillFixture(
                name=name,
                description=f"Load when a member asks to {intent} across {subject}{suffix}{tail}.",
                body=(
                    f"1. Follow the saved {area} {workflow.replace('-', ' ')} procedure.\n"
                    "2. Report what changed and what still needs a decision."
                ),
            )
        )
    return tuple(fixtures)


CROWD_REWORDINGS = (
    ("Load when", "Load whenever", ""),
    ("a member asks", "someone asks", ""),
    ("a member asks", "a member wants", ""),
    ("Load when", "Load when", " Covers the recurring weekly pass."),
    ("Load when", "Load when", " Use for the routine version of this request."),
    ("a member asks", "a teammate asks", ""),
    ("Load when", "Load whenever", " Applies to the standard flow."),
    ("a member asks", "a member needs", ""),
)


def crowd_corpus(target: SkillFixture, k: int) -> tuple[SkillFixture, ...]:
    """`k` near-duplicate paraphrases of `target`'s description, named `<target>-take-<i>` — the
    crowd a near-dup drop must collapse so clones cannot saturate the block's top-k."""
    fixtures: list[SkillFixture] = []
    for index in range(k):
        old, new, suffix = CROWD_REWORDINGS[index % len(CROWD_REWORDINGS)]
        marker = f" Take {index + 1}." if index >= len(CROWD_REWORDINGS) else ""
        fixtures.append(
            SkillFixture(
                name=f"{target.name}-take-{index + 1}",
                description=target.description.replace(old, new, 1) + suffix + marker,
                body=target.body,
            )
        )
    return tuple(fixtures)
