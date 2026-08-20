"""Labeled routing queries over the fixture corpora — the ground truth of the offline half of the
RFC 0038 promote gate. Queries paraphrase member intent and never quote a card's description back;
wordings differ from the live `skill_loading` case messages, so the two instruments do not share a
surface. The crowd fixtures ring `expense-audit`, so its queries measure near-duplicate
discrimination, and both corpora anchor at the RFC's sizes."""

from __future__ import annotations

from dataclasses import dataclass

from evals.skill_loading.corpus import CORPUS_P99, CORPUS_STRESS, crowd_corpus, spread_corpus
from evals.skill_loading.member import (
    BRAND_PALETTE,
    CUSTOMS_PACK,
    DATA_ROOM,
    EQUITY_CLAWBACK,
    EXPENSE_AUDIT,
    INCIDENT_RUNBOOK,
    NAMES_CROWD_EQUITY,
    NAMES_CROWD_TAX,
    RELEASE_NOTES,
    RETENTION_HOLDS,
    SPONSORSHIP_TIERS,
    TAX_PROVISION,
    VENDOR_ONBOARDING,
    WEEKLY_METRICS_NOTE,
)
from evals.skill_loading.runner import SkillFixture


@dataclass(frozen=True)
class LabeledQuery:
    name: str
    query: str
    expected: str


TARGETS: tuple[SkillFixture, ...] = (
    VENDOR_ONBOARDING,
    EXPENSE_AUDIT,
    RELEASE_NOTES,
    INCIDENT_RUNBOOK,
    CUSTOMS_PACK,
    DATA_ROOM,
    RETENTION_HOLDS,
    SPONSORSHIP_TIERS,
    TAX_PROVISION,
    EQUITY_CLAWBACK,
    WEEKLY_METRICS_NOTE,
    BRAND_PALETTE,
)
FIXED = (*TARGETS, *NAMES_CROWD_TAX, *NAMES_CROWD_EQUITY)
CORPORA: dict[str, tuple[SkillFixture, ...]] = {
    "p99": (
        *FIXED,
        *crowd_corpus(EXPENSE_AUDIT, 25),
        *spread_corpus(CORPUS_P99 - len(FIXED) - 25),
    ),
    "stress": (
        *FIXED,
        *crowd_corpus(EXPENSE_AUDIT, 99),
        *spread_corpus(CORPUS_STRESS - len(FIXED) - 99),
    ),
}

QUERIES: tuple[LabeledQuery, ...] = (
    LabeledQuery(
        "vendor-setup",
        "Nova Metals just signed with us — set them up as a supplier.",
        "vendor-onboarding",
    ),
    LabeledQuery(
        "expense-violations",
        "Anything in the June expense reports that violates policy?",
        "expense-audit",
    ),
    LabeledQuery(
        "expense-preapproval",
        "Before I sign off on this quarter's reimbursements, check them against the rules.",
        "expense-audit",
    ),
    LabeledQuery(
        "customer-release-notes",
        "Turn the merged PRs since 2.3 into notes customers can read.",
        "release-notes",
    ),
    LabeledQuery(
        "prod-incident",
        "Prod checkout is down — start the incident response.",
        "incident-runbook",
    ),
    LabeledQuery(
        "export-documents",
        "I need the export documents for the Hamburg container by Thursday.",
        "customs-clearance-pack",
    ),
    LabeledQuery(
        "diligence-room",
        "Investors start diligence Monday — get the data room ready.",
        "diligence-data-room",
    ),
    LabeledQuery(
        "purge-question",
        "Can we purge the 2019 support tickets or are they on hold?",
        "retention-hold-review",
    ),
    LabeledQuery(
        "retention-check",
        "Is anything stopping us from deleting the old candidate records?",
        "retention-hold-review",
    ),
    LabeledQuery(
        "gold-sponsorship",
        "What does a Gold sponsorship include and what does it cost?",
        "sponsorship-tier-pricing",
    ),
    LabeledQuery(
        "q3-provision",
        "Prepare the tax provision for the Q3 close.",
        "quarterly-tax-provision",
    ),
    LabeledQuery(
        "options-after-departure",
        "What happens to Priya's unvested options now that they've left?",
        "equity-grant-clawback",
    ),
    LabeledQuery(
        "weekly-numbers",
        "Send the usual weekly numbers note.",
        "weekly-metrics-note",
    ),
    LabeledQuery(
        "slide-colors",
        "What colors and fonts do we use for slides?",
        "brand-palette",
    ),
)
