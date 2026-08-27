"""The shared plugin's timing artifacts, driven through a nested pytest run — the `pytester` shape
`test_test_sharding.py` uses for the same plugin's sharding hook.

What the recorder writes is a property of a whole session (which process wrote which file, what a
failing test's row says, what a session-scoped fixture cost), so it can only be asserted from
outside, by running a session that behaves that way and reading the files it left behind."""

import csv
import json
import re
from pathlib import Path

import pytest
import yaml

pytest_plugins = ("pytester",)

CSV_HEADER = (
    "nodeid,file,directory,outcome,rerun,database,param_id,worker,"
    "setup_seconds,call_seconds,teardown_seconds,total_seconds,start,stop"
)

MIXED_SESSION = """
import time

import pytest


@pytest.fixture(scope="session")
def expensive():
    time.sleep(0.05)


def test_passes(expensive):
    pass


def test_fails():
    raise AssertionError("boom")


@pytest.mark.skip("not today")
def test_skips():
    pass


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"])
def test_both_databases(database_url):
    pass
"""


def rows(directory: Path) -> dict[str, dict[str, object]]:
    return {
        json.loads(line)["nodeid"]: json.loads(line)
        for path in sorted(directory.glob("tests-*.jsonl"))
        for line in path.read_text().splitlines()
        if line
    }


def test_every_outcome_gets_a_row_with_its_phase_durations(pytester: pytest.Pytester) -> None:
    """A run's data has to survive the run failing — the shard that fails is the one worth reading —
    so a failing test's row is written the same as a passing one's, and each row splits the three
    phases so a slow test and a slow fixture are separable."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"

    result = pytester.runpytest("-q", "--timings-dir", str(timings), "inner_session.py")

    assert result.parseoutcomes().get("failed") == 1, result.stdout.str()
    recorded = rows(timings)
    assert {nodeid.split("::")[1]: row["outcome"] for nodeid, row in recorded.items()} == {
        "test_passes": "passed",
        "test_fails": "failed",
        "test_skips": "skipped",
        "test_both_databases[sqlite]": "passed",
        "test_both_databases[postgres]": "passed",
    }
    passed = recorded["inner_session.py::test_passes"]
    assert passed["file"] == "inner_session.py"
    assert passed["worker"] == "master"
    assert passed["rerun"] is False
    assert passed["setup_seconds"] >= 0.05, "the session fixture's cost belongs to its first test"
    assert passed["total_seconds"] == pytest.approx(
        passed["setup_seconds"] + passed["call_seconds"] + passed["teardown_seconds"], abs=1e-9
    )
    assert passed["stop"] >= passed["start"] > 0


def test_the_database_parametrization_is_its_own_column(pytester: pytest.Pytester) -> None:
    """Every root-suite test runs once per database, so the sqlite and postgres halves of a shard's
    wall-clock are only separable if the param that split them is on the row."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"

    pytester.runpytest("-q", "--timings-dir", str(timings), "inner_session.py")

    recorded = rows(timings)
    assert recorded["inner_session.py::test_both_databases[postgres]"]["database"] == "postgres"
    assert recorded["inner_session.py::test_both_databases[sqlite]"]["param_id"] == "sqlite"
    assert recorded["inner_session.py::test_fails"]["database"] == ""


def test_the_csv_header_is_stable(pytester: pytest.Pytester) -> None:
    """The CSV is what a spreadsheet and a merged report read across many runs; its column order is
    a published shape, not an implementation detail, so changing it is a deliberate act."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"

    pytester.runpytest("-q", "--timings-dir", str(timings), "inner_session.py")

    table = (timings / "tests-master.csv").read_text().splitlines()
    assert table[0] == CSV_HEADER
    parsed = list(csv.DictReader(table))
    assert len(parsed) == 5
    assert {row["outcome"] for row in parsed} == {"passed", "failed", "skipped"}


def test_the_run_summary_carries_the_ci_context(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary is what makes one artifact comparable to another: without the sha, the PR, and
    the shard it came from, a merged report cannot tell a slow shard from a slow commit. GitHub
    supplies them through the environment, and their absence locally must not break the write."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"
    monkeypatch.setenv("GITHUB_SHA", "c0ffee")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/1441/merge")
    monkeypatch.setenv("GITHUB_JOB", "test-shard")
    monkeypatch.setenv("GITHUB_WORKFLOW", "ci")
    monkeypatch.setenv("RUNNER_OS", "Linux")

    pytester.runpytest_subprocess(
        "-q", "--timings-dir", str(timings), "--shard", "1/2", "inner_session.py"
    )

    summary = json.loads((timings / "run-summary.json").read_text())
    assert summary["sha"] == "c0ffee"
    assert summary["pull_request"] == "1441"
    assert summary["job"] == "test-shard"
    assert summary["workflow"] == "ci"
    assert summary["shard"] == "1/2"
    assert summary["runner_os"] == "Linux"
    assert summary["wall_seconds"] > 0
    assert sum(summary["counts"].values()) == summary["tests"]
    slowest = [entry["total_seconds"] for entry in summary["slowest"]]
    assert slowest and slowest == sorted(slowest, reverse=True)


def test_a_session_fixture_records_what_it_cost(pytester: pytest.Pytester) -> None:
    """The suite's wall-clock is dominated by session fixtures (the database params, the DBOS
    launch, the sandbox image), and each is paid once per process — so the cost is recorded against
    the fixture, not only against whichever test happened to be first to ask for it."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"

    pytester.runpytest("-q", "--timings-dir", str(timings), "inner_session.py")

    document = json.loads((timings / "fixtures-master.json").read_text())
    costs = {entry["name"]: entry for entry in document["fixtures"]}
    assert costs["expensive"]["scope"] == "session"
    assert costs["expensive"]["setups"] == 1
    assert costs["expensive"]["total_seconds"] >= 0.05


def test_xdist_workers_never_share_a_file(pytester: pytest.Pytester) -> None:
    """Ten shards each run `-n auto`, so the writers are the workers: each must own its own file, or
    the shard's data is whatever the last worker to close happened to hold. The controller writes
    the one summary and no rows of its own, so no test is counted twice."""
    pytester.makepyfile(inner_session=MIXED_SESSION)
    timings = pytester.path / "timings"

    result = pytester.runpytest_subprocess(
        "-q", "-n", "2", "--timings-dir", str(timings), "inner_session.py"
    )

    assert result.parseoutcomes().get("failed") == 1, result.stdout.str()
    written = sorted(path.name for path in timings.glob("tests-*.jsonl"))
    assert written == ["tests-gw0.jsonl", "tests-gw1.jsonl"]
    recorded = rows(timings)
    assert len(recorded) == 5, "a row per test, written exactly once"
    assert {row["worker"] for row in recorded.values()} == {"gw0", "gw1"}
    summary = json.loads((timings / "run-summary.json").read_text())
    assert summary["workers"] == 2
    assert summary["tests"] == 5


def test_a_write_that_cannot_land_only_warns(pytester: pytest.Pytester) -> None:
    """Instrumentation is never worth a red run: a timings directory that cannot be created — a
    read-only mount, a name already taken by a file — leaves the session's own verdict intact."""
    pytester.makepyfile(inner_session="def test_sample(): pass")
    blocked = pytester.path / "occupied"
    blocked.write_text("not a directory")

    result = pytester.runpytest("-q", "--timings-dir", str(blocked), "inner_session.py")

    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*ufo-timings: timing artifacts not written*"])


def test_the_default_directory_is_the_ignored_one(pytester: pytest.Pytester) -> None:
    """The default has to be the path `.gitignore` ignores and CI uploads, and it has to resolve
    against the directory pytest was invoked from — `control` roots its own pytest config, and one
    location per checkout is what a job can name as a single upload path."""
    pytester.makepyfile(inner_session="def test_sample(): pass")

    pytester.runpytest("-q", "inner_session.py")

    assert list(rows(pytester.path / ".pytest-timings")).pop().endswith("test_sample")


def test_instrumentation_can_be_turned_off(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(inner_session="def test_sample(): pass")

    result = pytester.runpytest("-q", "--timings-dir", "", "inner_session.py")

    assert result.ret == pytest.ExitCode.OK
    assert not (pytester.path / ".pytest-timings").exists()


def _make_recipes(makefile: Path) -> dict[str, str]:
    """Each target's recipe body, so a step running `make <target>` can be read for what the
    target actually invokes — the integration job reaches pytest that way, and a gate that only
    scanned the workflow text would miss it."""
    recipes: dict[str, str] = {}
    target = None
    for line in makefile.read_text().splitlines():
        if line.startswith("\t"):
            if target is not None:
                recipes[target] += line
            continue
        head = line.split(":", 1)
        target = (
            head[0].strip() if len(head) == 2 and head[0] and " " not in head[0].strip() else None
        )
        if target is not None:
            recipes.setdefault(target, "")
    return recipes


MAKE_TARGET = re.compile(r"\bmake\s+(?:-{1,2}[\w-]+\s+)*([\w.-]+)")


def _runs_pytest(command: str, recipes: dict[str, str]) -> bool:
    """Whether this step executes tests, following only the targets a literal `make` names — a
    bare word matching a target is not one. `cargo test` and `test "$x" = success` both carry the
    word `test`, and resolving those against the `test` target would read every Rust job and the
    aggregate gate as pytest runners.

    `--collect-only` executes no test and writes no timings, so a job that only collects is not
    one that has to upload."""
    reached = command
    for target in MAKE_TARGET.findall(command):
        reached += recipes.get(target, "")
    return "pytest" in reached and "--collect-only" not in reached


def test_ci_uploads_every_hidden_timing_directory() -> None:
    """Every job that *runs* pytest uploads the directory the plugin wrote, so merging the
    artifacts accounts for the whole suite's wall clock.

    The expectation is derived rather than counted. A hardcoded total fails whenever a job is
    added or removed and is repaired by editing the number, which asserts nothing about the job
    that moved; a job that executes pytest and forgets its upload has to be what breaks this."""
    repo = Path(__file__).parents[2]
    workflows = repo / ".github" / "workflows"
    recipes = _make_recipes(repo / "Makefile")
    executes: set[str] = set()
    uploaded: dict[str, dict[str, str]] = {}
    for name in ("ci.yaml", "integration.yaml"):
        workflow = yaml.load((workflows / name).read_text(), Loader=yaml.BaseLoader)
        for job, spec in workflow["jobs"].items():
            for step in spec.get("steps", []):
                if _runs_pytest(step.get("run", ""), recipes):
                    executes.add(f"{name}:{job}")
                given = step.get("with", {})
                if step.get("uses") == "actions/upload-artifact@v4" and given.get(
                    "name", ""
                ).startswith("test-timings-"):
                    uploaded[f"{name}:{job}"] = given

    assert executes, "no job runs pytest — the workflow parse is wrong, not the suite"
    assert executes == set(uploaded), (
        f"jobs running pytest without a timings upload: {sorted(executes - set(uploaded))}; "
        f"uploads from jobs that run none: {sorted(set(uploaded) - executes)}"
    )
    assert all(upload["path"] == ".pytest-timings" for upload in uploaded.values())
    assert all(upload["include-hidden-files"] == "true" for upload in uploaded.values())
