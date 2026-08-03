"""The pinned history the coding suite's tests read.

Those tests prove things about specific commits — that every diff this repository shipped still
passes its case's gate at that case's base commit — so they need those commits present. The test job
checks out full history for that reason; this states the requirement and names what is absent when
it is not met, rather than fetching, which under `pytest -n auto` means every worker racing the same
`.git/shallow.lock`.

A base commit needs only its own tree, which `git archive` reads. A reference commit needs its
parent too: `git show` on a commit whose parent is absent diffs it against the empty tree and yields
the whole repository instead of the change."""

import subprocess

import pytest

from evals.coding_repo.cases import CASES, PATCH_CASES
from evals.coding_repo.runner import REPO_ROOT

BASES = {case.base_sha for case in CASES}
REFERENCES = {case.reference_sha for case in PATCH_CASES}


def missing() -> set[str]:
    absent = set()
    for sha, wanted in ((sha, f"{sha}^{{commit}}") for sha in BASES):
        if _absent(wanted):
            absent.add(sha)
    for sha in REFERENCES:
        if _absent(f"{sha}^^{{commit}}"):
            absent.add(sha)
    return absent


def _absent(revision: str) -> bool:
    return bool(
        subprocess.run(
            ("git", "-C", str(REPO_ROOT), "cat-file", "-e", revision),
            capture_output=True,
            check=False,
        ).returncode
    )


@pytest.fixture(scope="session", autouse=True)
def pinned_history() -> None:
    absent = missing()
    if absent:
        pytest.fail(
            "this clone lacks pinned history the coding suite is proven against: "
            f"{', '.join(sorted(sha[:12] for sha in absent))}. Check out full history "
            "(`git fetch --unshallow`) — a shallow clone cannot answer for these commits."
        )


def reference_diff(sha: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(REPO_ROOT), "show", "--format=", sha), capture_output=True, check=True
    ).stdout
