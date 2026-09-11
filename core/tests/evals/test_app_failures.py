import json
from pathlib import Path

from evals.app_failures import FailureCensus, region_drift


def _record(cases: list[dict]) -> dict:
    return {"reports": [{"name": "ufo-app-bench", "cases": cases}]}


def _case(name: str, reason: str, spawns: tuple[str, ...] = ()) -> dict:
    return {
        "name": name,
        "reason": reason,
        "evidence": {
            "attempts": [
                {"calls": [{"name": "spawn", "input": {"target": target}} for target in spawns]}
            ]
        },
    }


def test_a_sample_with_no_page_is_counted_apart_from_the_ones_that_built(tmp_path: Path) -> None:
    """A page that never rendered scores zero on every layer, so counting its verdict beside a
    built page's reads a delivery rate as a quality rate — the misreading this census exists to
    stop."""

    (tmp_path / "run.json").write_text(
        json.dumps(
            _record(
                [
                    _case("built", "light desktop lacks #42"),
                    _case("lost", "turn produced no terminal transcript"),
                ]
            )
        )
    )
    report = FailureCensus((tmp_path,)).report()

    assert "samples 2 | produced a page 1 (50%)" in report
    assert "required fact missing             100%       50%" in report


def test_the_census_names_what_each_spawn_targeted(tmp_path: Path) -> None:
    """`target` is the spawn input's key. Reading `profile` instead returns `unnamed` for every
    call and hides that the repeat spawns are the builder while the `general_purpose` ones gather
    connector data."""

    (tmp_path / "run.json").write_text(
        json.dumps(
            _record(
                [
                    _case(
                        "built",
                        "ok",
                        ("profile:ufo_application_builder", "general_purpose", "general_purpose"),
                    )
                ]
            )
        )
    )
    report = FailureCensus((tmp_path,)).report()

    assert "general_purpose" in report
    assert "unnamed" not in report


def test_region_drift_reads_the_report_keys(tmp_path: Path) -> None:
    """The report spells the drawn regions `designRegions` and the rendered ones `views[].regions`.
    Reaching for any other key returns nothing rendered, so every region reads as dropped — which
    is how a page missing one region was read as missing all five."""

    audit = {
        "designRegions": [{"name": "overview"}, {"name": "queue"}, {"name": "needs"}],
        "views": [{"regions": [{"name": "overview"}]}, {"regions": [{"name": "queue"}]}],
    }

    assert region_drift(audit) == ("needs",)
    assert region_drift({"designRegions": [{"name": "overview"}], "views": []}) == ("overview",)
    assert region_drift({}) == ()


def test_a_directory_with_no_app_bench_records_reports_none(tmp_path: Path) -> None:
    (tmp_path / "other.json").write_text(json.dumps({"reports": [{"name": "basics", "cases": []}]}))

    assert "no app-bench samples found" in FailureCensus((tmp_path,)).report()


def test_the_census_ranks_delivery_by_how_often_the_builder_was_spawned(tmp_path: Path) -> None:
    """A second `ufo_application_builder` spawn means the first child came back with no site, and
    over 44 recorded samples delivery fell from 77% at one spawn to 45% at two. The
    `general_purpose` spawns beside them gather connector data and predict nothing, so only the
    builder is counted."""

    (tmp_path / "run.json").write_text(
        json.dumps(
            _record(
                [
                    _case("once", "ok", ("profile:ufo_application_builder", "general_purpose")),
                    _case(
                        "twice",
                        "turn produced no terminal transcript",
                        ("profile:ufo_application_builder", "ufo_application_builder"),
                    ),
                ]
            )
        )
    )
    report = FailureCensus((tmp_path,)).report()

    assert "builder spawns in a sample     samples     built" in report
    assert "1                                    1      100%" in report
    assert "2                                    1        0%" in report
