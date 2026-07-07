"""The gate's verdict: score the acceptance lift a candidate prompt shows over the current one.

A candidate promotes only when the lower confidence bound on its acceptance lift clears a floor
(with each arm carrying enough replays) AND it does not regress the agent's other task classes. The
lift is bounded by Newcombe's score interval for a difference of proportions (1998, method 10 /
MOVER-W): the root-sum-square of each arm's Wilson half-width, which charges only the difference's
own uncertainty and so clears a real effect at small n where subtracting two one-sample bounds never
could — perfect 4/4 vs 0/4 scores ~0.31, while genuine noise still falls below zero."""

from __future__ import annotations

import math
from dataclasses import dataclass

WILSON_Z_95 = 1.959963984540054

# The local lift lower bound must clear `LIFT_LOWER_BOUND`, and each arm must carry at least
# `N_FLOOR` replays. The global stage blocks only on evidence of harm: a candidate is rejected when
# the whole lift interval on the other task classes sits below `-GLOBAL_REGRESSION_MARGIN` (even
# its optimistic upper bound is a meaningful regression). Non-inferiority at a tight margin is
# unprovable at modest n, so equal-or-noisy global performance passes.
LIFT_LOWER_BOUND = 0.05
N_FLOOR = 4
GLOBAL_REGRESSION_MARGIN = 0.05


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    reason: str
    acceptance_lower_bound: float
    n_present: int
    n_absent: int


@dataclass(frozen=True)
class OutcomeLabel:
    """One replayed example under one prompt arm: `present` is whether the arm carried the
    candidate prompt, `success` is whether the judge accepted the answer — the axis lift scores."""

    present: bool
    success: bool


@dataclass(frozen=True)
class Contingency:
    present_accepted: int
    present_total: int
    absent_accepted: int
    absent_total: int


def wilson_lower_bound(accepted: int, total: int, z: float = WILSON_Z_95) -> float:
    if total <= 0:
        return 0.0
    phat = accepted / total
    denom = 1.0 + z * z / total
    center = phat + z * z / (2.0 * total)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * total)) / total)
    return max(0.0, (center - margin) / denom)


def wilson_upper_bound(accepted: int, total: int, z: float = WILSON_Z_95) -> float:
    if total <= 0:
        return 1.0
    phat = accepted / total
    denom = 1.0 + z * z / total
    center = phat + z * z / (2.0 * total)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * total)) / total)
    return min(1.0, (center + margin) / denom)


def lift_lower_bound(cont: Contingency) -> float:
    """The Newcombe lower confidence bound on the acceptance lift (present rate minus absent),
    bounding the difference directly by the root-sum-square of each arm's Wilson half-width."""
    if cont.present_total == 0 or cont.absent_total == 0:
        return 0.0
    present_rate = cont.present_accepted / cont.present_total
    absent_rate = cont.absent_accepted / cont.absent_total
    present_low = wilson_lower_bound(cont.present_accepted, cont.present_total)
    absent_high = wilson_upper_bound(cont.absent_accepted, cont.absent_total)
    return (present_rate - absent_rate) - math.sqrt(
        (present_rate - present_low) ** 2 + (absent_high - absent_rate) ** 2
    )


def lift_upper_bound(cont: Contingency) -> float:
    """The Newcombe UPPER bound on the lift — the optimistic end of the difference interval. The
    global non-inferiority check rejects only when even this is below `-margin`."""
    if cont.present_total == 0 or cont.absent_total == 0:
        return 0.0
    present_rate = cont.present_accepted / cont.present_total
    absent_rate = cont.absent_accepted / cont.absent_total
    present_high = wilson_upper_bound(cont.present_accepted, cont.present_total)
    absent_low = wilson_lower_bound(cont.absent_accepted, cont.absent_total)
    return (present_rate - absent_rate) + math.sqrt(
        (present_high - present_rate) ** 2 + (absent_rate - absent_low) ** 2
    )


def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency:
    present = [label for label in labels if label.present]
    absent = [label for label in labels if not label.present]
    return Contingency(
        present_accepted=sum(1 for label in present if label.success),
        present_total=len(present),
        absent_accepted=sum(1 for label in absent if label.success),
        absent_total=len(absent),
    )


def score_gate(
    labels: tuple[OutcomeLabel, ...],
    lower_bound: float = LIFT_LOWER_BOUND,
    n_floor: int = N_FLOOR,
) -> GateVerdict:
    """The local lift verdict: the Newcombe lower bound on the acceptance lift must clear
    `lower_bound`, with each arm at or above `n_floor`. A no-op candidate (no present/absent split)
    or a harmful one (zero/negative lift) fails."""
    cont = contingency(labels)
    lower = lift_lower_bound(cont)
    if cont.present_total < n_floor or cont.absent_total < n_floor:
        return GateVerdict(
            False,
            f"per-arm n below nFloor {n_floor}: "
            f"present={cont.present_total} absent={cont.absent_total}",
            lower,
            cont.present_total,
            cont.absent_total,
        )
    if lower <= lower_bound:
        return GateVerdict(
            False,
            f"acceptance lower bound {lower:.4f} below floor {lower_bound}",
            lower,
            cont.present_total,
            cont.absent_total,
        )
    return GateVerdict(True, "gate crossed", lower, cont.present_total, cont.absent_total)


def global_non_inferior(
    labels: tuple[OutcomeLabel, ...],
    margin: float = GLOBAL_REGRESSION_MARGIN,
    n_floor: int = N_FLOOR,
) -> bool:
    """The global-stage check: the whole-prompt rewrite did not regress acceptance across the
    agent's other task classes. It blocks only on CONFIDENCE of harm — when the whole lift interval
    sits below `-margin`. Insufficient global evidence (either arm below `n_floor`) passes, since
    non-inferiority at a tight margin is unprovable at modest n."""
    cont = contingency(labels)
    if cont.present_total < n_floor or cont.absent_total < n_floor:
        return True
    return lift_upper_bound(cont) >= -margin


def two_stage_gate(
    local_labels: tuple[OutcomeLabel, ...],
    global_labels: tuple[OutcomeLabel, ...],
) -> GateVerdict:
    """The full verdict: cross the local lift gate on the mined task class AND stay non-inferior on
    the held-out global set. A class-local win that regresses the other classes never promotes."""
    local = score_gate(local_labels)
    if not local.passed:
        return local
    if not global_non_inferior(global_labels):
        return GateVerdict(
            False,
            "global regression: candidate lowers acceptance on other task classes",
            local.acceptance_lower_bound,
            local.n_present,
            local.n_absent,
        )
    return local
