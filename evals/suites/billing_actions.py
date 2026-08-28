"""Billing as the workspace object's `manage_billing` action: an admin reads the balance and the
card on file through it, and a question about what an app spent is not a billing act. Both cases are
authored — no production turn has invoked the moved action yet — and each brief avoids the action's
name. The suite runs only where the metronome extension is active, which is what the pack gate
names."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import attempted_tools_scorer, restraint_scorer

MANAGE_BILLING = "action:workspace:manage_billing"
BILLING_PACKS = ("assistant_billing", "assistant_hosted")

CASES = (
    CapabilityCase(
        "authored-balance-and-card",
        "How much prepaid balance does this workspace have left, and is there a card on file?",
        attempted_tools_scorer(
            required=((MANAGE_BILLING, {"operation": "status"}),),
            forbidden=(),
            orderings=(),
        ),
        digest_tag="billing-actions:balance-and-card:authored",
    ),
    CapabilityCase(
        "authored-spend-question-is-not-billing",
        "Roughly how much did the research app spend on model calls this week?",
        restraint_scorer((MANAGE_BILLING,)),
        digest_tag="billing-actions:spend-question:authored",
    ),
)
