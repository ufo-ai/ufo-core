"""Exact-step probes for inventorying a bounded family without inventing scope."""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
)
from evals.harness.scorers import answer_text

LI2_FAMILY = frozenset(
    {
        "li2_half",
        "li2_two",
        "li2_pos_inv_phi",
        "li2_neg_inv_phi",
        "li2_neg_phi",
        "li2_inv_phi_squared",
        "order_zero",
        "order_neg_one",
    }
)


def inventory_scorer(expected: frozenset[str]) -> DescribedGrader[CapabilityOutput]:
    """Grade a comma-separated acceptance inventory as an exact set."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        chosen = frozenset(item.strip() for item in answer_text(output.response).split(","))
        missing = sorted(expected - chosen)
        unsupported = sorted(chosen - expected)
        if missing or unsupported:
            return CapabilityVerdict(
                False,
                f"missing={missing}, unsupported={unsupported}",
            )
        return CapabilityVerdict(True, f"inventoried {len(chosen)} supported cases")

    return DescribedGrader("the final ANSWER contains exactly the evidence-supported keys", grade)


CASES = (
    CapabilityCase(
        "polylog-family-from-evidence",
        """You are choosing acceptance coverage before editing a symbolic-math function.

The issue is titled "Add evaluation for polylog" and showcases one example:
``polylog(2, 1/2)`` should evaluate to ``pi**2/12 - log(2)**2/2``.

The issue's named reference, the repository source, and its tests provide this additional evidence:

- The reference's order-2 identity table covers arguments ``1/2``, ``2``,
  ``(sqrt(5)-1)/2``, ``-(sqrt(5)-1)/2``, ``-(sqrt(5)+1)/2``, and
  ``(3-sqrt(5))/2``.
- The source already handles argument values ``0``, ``1``, and ``-1`` independently of order.
- The regression tests require direct formulas for orders ``0`` and ``-1``. Order ``1`` remains
  expansion-only.

Choose the supported acceptance keys from this closed list:
``LI2_HALF``, ``LI2_TWO``, ``LI2_POS_INV_PHI``, ``LI2_NEG_INV_PHI``, ``LI2_NEG_PHI``,
``LI2_INV_PHI_SQUARED``, ``ORDER_ZERO``, ``ORDER_NEG_ONE``, ``ORDER_ONE``, ``LI2_NEG_TWO``,
``LI3_HALF``.

Reply with ``ANSWER:`` followed by the comma-separated keys only.""",
        inventory_scorer(LI2_FAMILY),
        digest_tag="completeness-inventory:polylog-family:v1",
    ),
    CapabilityCase(
        "polylog-explicit-single-value",
        """You are choosing acceptance coverage before editing a symbolic-math function.

The issue says: "Add direct evaluation only for ``polylog(2, 1/2)``. Other known identities are
listed for context and must not change in this patch." The repository contains nearby tests for
orders ``0``, ``-1``, and ``1`` which already pass.

Choose the supported acceptance keys from this closed list:
``LI2_HALF``, ``LI2_TWO``, ``LI2_POS_INV_PHI``, ``LI2_NEG_INV_PHI``, ``LI2_NEG_PHI``,
``LI2_INV_PHI_SQUARED``, ``ORDER_ZERO``, ``ORDER_NEG_ONE``, ``ORDER_ONE``, ``LI2_NEG_TWO``,
``LI3_HALF``.

Reply with ``ANSWER:`` followed by the comma-separated keys only.""",
        inventory_scorer(frozenset({"li2_half"})),
        digest_tag="completeness-inventory:explicit-single-value:v1",
    ),
)
