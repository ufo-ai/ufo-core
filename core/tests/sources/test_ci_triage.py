import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yaml"
NON_PR_FORCES_TRUE = "github.event_name != 'pull_request' ||"
CROSS_CUTTING = ("testsupport/**", ".github/**", "*")
CHECKS_ONLY_ROOTS = frozenset({"client", "docs"})
GATED_JOBS = {
    "checks": "checks",
    "wheel": "wheel",
    "control-test": "control",
    "rls": "control",
}


def _jobs() -> dict[str, dict]:
    loaded = yaml.load(CI.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    jobs = loaded["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _filters() -> dict[str, list[str]]:
    steps = _jobs()["triage"]["steps"]
    filter_steps = [s for s in steps if s.get("id") == "filter"]
    assert len(filter_steps) == 1
    loaded = yaml.load(filter_steps[0]["with"]["filters"], Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_triage_runs_on_pull_requests_only_and_forces_every_lane_on_push() -> None:
    triage = _jobs()["triage"]
    for expression in triage["outputs"].values():
        assert NON_PR_FORCES_TRUE in expression
    (filter_step,) = [s for s in triage["steps"] if s.get("id") == "filter"]
    assert filter_step["if"] == "github.event_name == 'pull_request'"


def test_docs_exemption_is_exactly_docs_markdown() -> None:
    assert _filters()["not_docs_md"] == ["!docs/**/*.md"]


def test_cross_cutting_inputs_light_every_lane() -> None:
    filters = _filters()
    for name, patterns in filters.items():
        if name == "not_docs_md":
            continue
        for pattern in CROSS_CUTTING:
            assert pattern in patterns, f"{name} misses {pattern}"


def test_every_tracked_top_level_path_is_classified() -> None:
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    roots = {path.split("/", 1)[0] for path in tracked if "/" in path}
    claimed = {
        pattern.split("/", 1)[0]
        for patterns in _filters().values()
        for pattern in patterns
        if not pattern.startswith("!") and "/" in pattern
    }
    unclassified = roots - claimed - CHECKS_ONLY_ROOTS
    assert not unclassified, f"classify {sorted(unclassified)} in the triage filters"


def test_every_lane_decision_is_consumed_and_every_consumer_names_a_lane() -> None:
    jobs = _jobs()
    declared = set(jobs["triage"]["outputs"])
    consumed = {
        match
        for job in jobs.values()
        for match in re.findall(r"needs\.triage\.outputs\.(\w+)", str(job))
    }
    assert consumed == declared
    for job_id, lane in GATED_JOBS.items():
        job = jobs[job_id]
        assert "triage" in job["needs"], job_id
        assert job["if"] == f"needs.triage.outputs.{lane} == 'true'", job_id


def test_required_contexts_come_from_an_unfiltered_pull_request_trigger() -> None:
    loaded = yaml.load(CI.read_text(), Loader=yaml.BaseLoader)
    assert loaded["on"]["pull_request"] == ""
    jobs = _jobs()
    for context in ("test", "checks", "rls"):
        assert "name" not in jobs[context], context


def test_gate_asserts_every_need_including_triage_itself() -> None:
    gate = _jobs()["test"]
    assert gate["if"] == "always()"
    script = "\n".join(step["run"] for step in gate["steps"])
    for need in gate["needs"]:
        assert f"needs.{need}.result" in script, need
    assert 'test "${{ needs.triage.result }}" = success' in script


def test_paths_filter_action_is_pinned_to_a_commit() -> None:
    (filter_step,) = [s for s in _jobs()["triage"]["steps"] if s.get("id") == "filter"]
    assert re.fullmatch(
        r"dorny/paths-filter@[0-9a-f]{40}",
        filter_step["uses"],
    )
