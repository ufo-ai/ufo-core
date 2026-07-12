"""Answer cases: score the agent's final text against a known answer or a format constraint —
exact string, numeric within tolerance, and a shape predicate. Deterministic, no tool trajectory
required."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import exact_scorer, numeric_scorer, predicate_scorer


def _three_bullets(text: str) -> bool:
    return sum(1 for line in text.splitlines() if line.strip().startswith(("-", "•"))) == 3


CASES = (
    CapabilityCase(
        "capital",
        "What is the capital of Japan? Reply with a single line 'ANSWER: <city>'.",
        exact_scorer("Tokyo"),
        digest_tag="basic:capital",
    ),
    CapabilityCase(
        "arithmetic",
        "What is 17 multiplied by 23? Reply with a single line 'ANSWER: <number>'.",
        numeric_scorer(391),
        digest_tag="basic:arithmetic",
    ),
    CapabilityCase(
        "three-bullets",
        "List exactly three benefits of unit testing, each on its own line starting with '-'.",
        predicate_scorer((("exactly three '-' bullet lines", _three_bullets),)),
        digest_tag="basic:bullets",
    ),
)
