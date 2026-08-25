from collections import Counter
from collections.abc import Mapping

import pytest

from evals.swebench.models import SMOKE_CASE_IDS
from evals.swebench.selection import (
    HARD_DIFFICULTY,
    SWEBENCH_SELECTION_SEED,
    SubsetBuilder,
)

POOL_REPOS = {"django/django": 40, "sympy/sympy": 12, "psf/requests": 4, "pallets/flask": 1}
GRADEABLE = "15 min - 1 hour"
HARD_INDEX = 900
HARD_REPOS = tuple(POOL_REPOS)[:3]


def row(repo: str, index: int, difficulty: str = GRADEABLE) -> dict[str, str]:
    owner, project = repo.split("/")
    return {
        "repo": repo,
        "instance_id": f"{owner}__{project}-{index}",
        "difficulty": difficulty,
    }


def smoke_row(repo: str, instance_id: str) -> dict[str, str]:
    return {"repo": repo, "instance_id": instance_id, "difficulty": GRADEABLE}


def pool() -> tuple[Mapping[str, object], ...]:
    return (
        *(row(repo, index) for repo, count in POOL_REPOS.items() for index in range(1, count + 1)),
        *(row(repo, HARD_INDEX, HARD_DIFFICULTY) for repo in HARD_REPOS),
        smoke_row("django/django", SMOKE_CASE_IDS[0]),
        smoke_row("sympy/sympy", SMOKE_CASE_IDS[1]),
        smoke_row("scikit-learn/scikit-learn", SMOKE_CASE_IDS[2]),
    )


def repositories(instance_ids: tuple[str, ...]) -> list[str]:
    return [instance_id.split("__", maxsplit=1)[0] for instance_id in instance_ids]


def test_the_same_seed_and_rows_always_draw_the_same_roster() -> None:
    first = SubsetBuilder(pool(), SWEBENCH_SELECTION_SEED).build()

    assert SubsetBuilder(tuple(reversed(pool())), SWEBENCH_SELECTION_SEED).build() == first
    assert SubsetBuilder(pool(), "another-seed").build() != first


def test_the_roster_takes_repositories_in_turn_instead_of_the_largest_pool() -> None:
    selection = SubsetBuilder(pool(), SWEBENCH_SELECTION_SEED).build()

    assert repositories(selection.hillclimb[:4]) == ["django", "sympy", "psf", "pallets"]
    assert Counter(repositories(selection.hillclimb)) == Counter(
        {"django": 3, "sympy": 3, "psf": 3, "pallets": 1}
    )
    assert Counter(repositories(selection.holdout)) == Counter({"django": 5, "sympy": 4, "psf": 1})


def test_the_representative_subsets_are_disjoint_from_smoke_and_hard_cases() -> None:
    selection = SubsetBuilder(pool(), SWEBENCH_SELECTION_SEED).build()
    drawn = selection.hillclimb + selection.holdout

    assert selection.smoke == SMOKE_CASE_IDS
    assert len(set(drawn)) == 20
    assert not set(drawn) & set(SMOKE_CASE_IDS)
    assert selection.hard == ("django__django-900", "psf__requests-900", "sympy__sympy-900")
    assert not set(drawn) & set(selection.hard)


def test_a_pool_short_of_the_roster_refuses_to_draw() -> None:
    rows = tuple(row("django/django", index) for index in range(1, 20))

    with pytest.raises(ValueError, match="holds 19 gradeable rows, needs 20"):
        SubsetBuilder(rows, SWEBENCH_SELECTION_SEED).build()


def test_a_pool_of_only_smoke_and_hard_rows_refuses_to_draw() -> None:
    rows = (
        smoke_row("django/django", SMOKE_CASE_IDS[0]),
        row("django/django", 1, HARD_DIFFICULTY),
    )

    with pytest.raises(ValueError, match="selection pool is empty"):
        SubsetBuilder(rows, SWEBENCH_SELECTION_SEED).build()
