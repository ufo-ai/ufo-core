#!/usr/bin/env python3

"""Merge the timing artifacts pytest jobs upload and print where test CI's wall-clock went.

Point it at one or more downloaded artifact directories (`gh run download <run-id> -D dir` gives one
subdirectory per job, which is the shape this reads): every `run-summary.json` is one pytest
invocation and the `tests-*.jsonl` beside it are that invocation's rows, one per test. Stdlib only,
so it runs against a bare `python3` next to a folder of downloads.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUMMARY_NAME = "run-summary.json"
ROW_GLOB = "tests-*.jsonl"
FIXTURE_GLOB = "fixtures-*.json"
MERGED_FIELDS = (
    "run",
    "job",
    "shard",
    "sha",
    "nodeid",
    "file",
    "directory",
    "outcome",
    "rerun",
    "database",
    "param_id",
    "worker",
    "setup_seconds",
    "call_seconds",
    "teardown_seconds",
    "total_seconds",
    "start",
    "stop",
)


@dataclass
class Invocation:
    """One pytest invocation: the summary a run wrote plus every row its workers wrote."""

    directory: Path
    summary: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    fixtures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        job = str(self.summary.get("job") or self.directory.name)
        shard = str(self.summary.get("shard") or "")
        return f"{job} {shard}".strip()

    @property
    def wall(self) -> float:
        return float(self.summary.get("wall_seconds") or 0.0)

    @property
    def workers(self) -> int:
        return int(self.summary.get("workers") or 1)


def load(directories: list[Path]) -> list[Invocation]:
    invocations = []
    for directory in directories:
        for summary_path in sorted(directory.rglob(SUMMARY_NAME)):
            home = summary_path.parent
            invocation = Invocation(directory=home, summary=json.loads(summary_path.read_text()))
            for rows_path in sorted(home.glob(ROW_GLOB)):
                invocation.rows.extend(
                    json.loads(line) for line in rows_path.read_text().splitlines() if line
                )
            for fixtures_path in sorted(home.glob(FIXTURE_GLOB)):
                document = json.loads(fixtures_path.read_text())
                invocation.fixtures.extend(document.get("fixtures", []))
            invocations.append(invocation)
    return invocations


def merged_rows(invocations: list[Invocation]) -> list[dict[str, Any]]:
    """Every row stamped with the invocation it came from, so one flat table answers per-job
    questions the rows alone cannot."""
    rows = []
    for invocation in invocations:
        context = {
            "run": invocation.directory.name,
            "job": invocation.summary.get("job", ""),
            "shard": invocation.summary.get("shard", ""),
            "sha": invocation.summary.get("sha", ""),
        }
        for row in invocation.rows:
            rows.append({key: {**context, **row}.get(key, "") for key in MERGED_FIELDS})
    return rows


def _table(title: str, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (nothing recorded)")
        return
    widths = [
        max(len(str(cell)) for cell in (headers[index], *(row[index] for row in rows)))
        for index in range(len(headers))
    ]
    print("  " + "  ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))))
    for row in rows:
        print("  " + "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def report_runs(invocations: list[Invocation]) -> None:
    rows = [
        (
            invocation.directory.name,
            invocation.label,
            f"{invocation.wall:.1f}",
            str(invocation.workers),
            str(invocation.summary.get("tests", len(invocation.rows))),
            ",".join(
                f"{outcome}={count}"
                for outcome, count in sorted(invocation.summary.get("counts", {}).items())
            ),
            f"{invocation.summary.get('runner_os', '')} py{invocation.summary.get('python', '')}",
        )
        for invocation in sorted(invocations, key=lambda one: -one.wall)
    ]
    _table(
        "runs",
        ("artifact", "job", "wall s", "workers", "tests", "outcomes", "runner"),
        rows,
    )
    total = sum(invocation.wall for invocation in invocations)
    longest = max((invocation.wall for invocation in invocations), default=0.0)
    print(f"\n  runner-seconds across {len(invocations)} invocations: {total:.1f}")
    print(f"  longest single invocation (the critical path): {longest:.1f}s")


def report_shard_balance(invocations: list[Invocation]) -> None:
    by_job: dict[str, list[Invocation]] = defaultdict(list)
    for invocation in invocations:
        if invocation.summary.get("shard"):
            by_job[str(invocation.summary.get("job") or invocation.directory.name)].append(
                invocation
            )
    rows = []
    for job, group in sorted(by_job.items()):
        walls = [invocation.wall for invocation in group]
        slowest = max(group, key=lambda one: one.wall)
        rows.append(
            (
                job,
                str(len(group)),
                f"{min(walls):.1f}",
                f"{max(walls):.1f}",
                f"{max(walls) - min(walls):.1f}",
                f"{statistics.fmean(walls):.1f}",
                str(slowest.summary.get("shard", "")),
            )
        )
    _table(
        "shard balance (per job, over the shards present)",
        ("job", "shards", "min s", "max s", "spread s", "mean s", "slowest shard"),
        rows,
    )


def _aggregate(rows: list[dict[str, Any]], key: str) -> list[tuple[str, float, int, float]]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    setups: dict[str, float] = defaultdict(float)
    for row in rows:
        name = str(row.get(key, ""))
        totals[name] += float(row.get("total_seconds") or 0.0)
        setups[name] += float(row.get("setup_seconds") or 0.0)
        counts[name] += 1
    return sorted(
        ((name, totals[name], counts[name], setups[name]) for name in totals),
        key=lambda entry: -entry[1],
    )


def report_slowest(rows: list[dict[str, Any]], key: str, title: str, top: int) -> None:
    aggregate = _aggregate(rows, key)
    _table(
        title,
        (key, "total s", "runs", "mean s", "setup s"),
        [
            (name, f"{total:.2f}", str(count), f"{total / count:.3f}", f"{setup:.2f}")
            for name, total, count, setup in aggregate[:top]
        ],
    )


def report_database_split(rows: list[dict[str, Any]]) -> None:
    aggregate = _aggregate(rows, "database")
    _table(
        "database parametrization (the sqlite-vs-postgres double run)",
        ("database", "total s", "runs", "mean s", "setup s"),
        [
            (
                name or "(unparametrized)",
                f"{total:.1f}",
                str(count),
                f"{total / count:.3f}",
                f"{setup:.1f}",
            )
            for name, total, count, setup in aggregate
        ],
    )


def report_fixtures(invocations: list[Invocation], top: int) -> None:
    totals: dict[tuple[str, str], tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for invocation in invocations:
        for entry in invocation.fixtures:
            key = (str(entry.get("name", "")), str(entry.get("scope", "")))
            seconds, setups = totals[key]
            totals[key] = (
                seconds + float(entry.get("total_seconds") or 0.0),
                setups + int(entry.get("setups") or 0),
            )
    ordered = sorted(totals.items(), key=lambda entry: -entry[1][0])
    _table(
        "slowest fixtures (summed over every worker in every invocation)",
        ("fixture", "scope", "total s", "setups"),
        [
            (name, scope, f"{seconds:.1f}", str(setups))
            for (name, scope), (seconds, setups) in ordered[:top]
        ],
    )


def report_unstable(rows: list[dict[str, Any]], top: int) -> None:
    """A test whose outcome is not the same everywhere it ran, or that a rerun retried: the flake
    candidates a single run cannot show."""
    outcomes: dict[str, set[str]] = defaultdict(set)
    reruns: dict[str, int] = defaultdict(int)
    for row in rows:
        nodeid = str(row.get("nodeid", ""))
        outcomes[nodeid].add(str(row.get("outcome", "")))
        reruns[nodeid] += 1 if row.get("rerun") in (True, "True", "true") else 0
    unstable = sorted(
        (
            (nodeid, seen, reruns[nodeid])
            for nodeid, seen in outcomes.items()
            if len(seen) > 1 or reruns[nodeid]
        ),
        key=lambda entry: (-entry[2], entry[0]),
    )
    _table(
        "unstable tests (mixed outcomes across runs, or reruns)",
        ("nodeid", "outcomes", "reruns"),
        [(nodeid, ",".join(sorted(seen)), str(count)) for nodeid, seen, count in unstable[:top]],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "directories",
        metavar="DIR",
        nargs="+",
        type=Path,
        help="directories holding downloaded timing artifacts; searched recursively",
    )
    parser.add_argument(
        "--top", type=int, default=20, help="how many rows each ranking prints (default 20)"
    )
    parser.add_argument(
        "--merged-csv", type=Path, help="also write every row, stamped with its job, to this CSV"
    )
    arguments = parser.parse_args(argv)

    invocations = load(arguments.directories)
    if not invocations:
        names = ", ".join(str(directory) for directory in arguments.directories)
        print(f"no {SUMMARY_NAME} found under {names}", file=sys.stderr)
        return 1
    rows = merged_rows(invocations)

    report_runs(invocations)
    report_shard_balance(invocations)
    report_slowest(
        rows, "nodeid", f"slowest tests (top {arguments.top}, summed across runs)", arguments.top
    )
    report_slowest(rows, "directory", f"slowest directories (top {arguments.top})", arguments.top)
    report_database_split(rows)
    report_fixtures(invocations, arguments.top)
    report_unstable(rows, arguments.top)

    if arguments.merged_csv:
        arguments.merged_csv.parent.mkdir(parents=True, exist_ok=True)
        with arguments.merged_csv.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(MERGED_FIELDS))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {arguments.merged_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
