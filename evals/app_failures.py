"""What `ufo-app-bench` samples actually failed on, read off records already written.

`python -m evals.app_failures <path>...` walks run JSON — `eval-reports/runs/`, an ablation's
`runs/<arm>/`, or a single file — and counts the verdicts behind the scores. It runs no turn and
costs nothing, so a wording change can be weighed against every sample already paid for before
another one is bought.

A sample that produced no page scores zero on every layer, so the counts are reported against the
samples that built a page as well as against all of them. Reading a 96% failure rate that is really
a delivery rate is how a run gets misread.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SUITE = "ufo-app-bench"
NO_PAGE = "turn produced no terminal transcript"
SOURCES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wireframe named no region", re.compile(r"design has 0 unique visible named regions")),
    ("page dropped a drawn region", re.compile(r"renders no such region")),
    ("region names differ", re.compile(r"region names differ")),
    ("required fact missing", re.compile(r"lacks rendered facts|desktop lacks")),
    ("overlap or clipping", re.compile(r"overlaps|Fix clipped content")),
    ("deploys over the guide", re.compile(r"needs at most \d+|took \d+ deploys")),
    ("parent spawn shape", re.compile(r"spawned \d+ times|spawned general_purpose")),
    ("visual rubric unmet", re.compile(r"unmet: \d+\.")),
    ("wireframe component absent", re.compile(r"Kit component\(s\) not rendered")),
    ("wrong skill loaded", re.compile(r"loaded '[^']+' first, expected")),
)
BUILDER = "ufo_application_builder"


@dataclass(frozen=True)
class FailureCensus:
    """Every app-bench sample under `roots`, counted by what its verdict blamed."""

    roots: tuple[Path, ...]
    samples: int = 0
    built: int = 0
    by_source: Counter[str] = field(default_factory=Counter)
    built_by_source: Counter[str] = field(default_factory=Counter)
    spawn_targets: Counter[str] = field(default_factory=Counter)
    builder_spawns: Counter[int] = field(default_factory=Counter)
    builder_spawns_built: Counter[int] = field(default_factory=Counter)

    def report(self) -> str:
        census = self._count()
        lines = [
            f"samples {census.samples} | produced a page {census.built} "
            f"({census.built / census.samples:.0%})"
            if census.samples
            else "no app-bench samples found",
            "",
            f"{'failure source':<28}{'of built':>10}{'of all':>10}",
        ]
        for name, _ in SOURCES:
            built_hits = census.built_by_source[name]
            all_hits = census.by_source[name]
            if not all_hits:
                continue
            built_share = f"{built_hits / census.built:.0%}" if census.built else "-"
            lines.append(f"{name:<28}{built_share:>10}{all_hits / census.samples:>10.0%}")
        lines += ["", f"{'spawn target':<28}{'count':>10}"]
        for target, count in census.spawn_targets.most_common():
            lines.append(f"{target:<28}{count:>10}")
        lines += ["", f"{'builder spawns in a sample':<28}{'samples':>10}{'built':>10}"]
        for count in sorted(census.builder_spawns):
            samples = census.builder_spawns[count]
            page = census.builder_spawns_built[count]
            lines.append(f"{count:<28}{samples:>10}{page / samples:>10.0%}")
        return "\n".join(lines)

    def _count(self) -> FailureCensus:
        samples = built = 0
        by_source: Counter[str] = Counter()
        built_by_source: Counter[str] = Counter()
        profiles: Counter[str] = Counter()
        spawned: Counter[int] = Counter()
        spawned_built: Counter[int] = Counter()
        for record in self._records():
            for report in record.get("reports", []):
                if report.get("name") != SUITE:
                    continue
                for case in report.get("cases", []):
                    samples += 1
                    reason = " ".join(str(case.get("reason", "")).split())
                    page = NO_PAGE not in reason
                    built += page
                    for name, pattern in SOURCES:
                        if pattern.search(reason):
                            by_source[name] += 1
                            if page:
                                built_by_source[name] += 1
                    builders = 0
                    for attempt in case.get("evidence", {}).get("attempts", []):
                        for call in attempt.get("calls", []):
                            if call.get("name") != "spawn":
                                continue
                            target = str((call.get("input") or {}).get("target", "unnamed"))
                            profiles[target] += 1
                            builders += BUILDER in target
                    spawned[builders] += 1
                    if page:
                        spawned_built[builders] += 1
        return FailureCensus(
            self.roots,
            samples,
            built,
            by_source,
            built_by_source,
            profiles,
            spawned,
            spawned_built,
        )

    def _records(self) -> list[dict]:
        found: list[dict] = []
        for root in self.roots:
            paths = sorted(root.rglob("*.json")) if root.is_dir() else [root]
            for path in paths:
                try:
                    payload = json.loads(path.read_text())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(payload, dict) and "reports" in payload:
                    found.append(payload)
        return found


def region_drift(audit: dict) -> tuple[str, ...]:
    """The regions the wireframe drew that no measured view rendered, in drawing order.

    Takes the audit report rather than the two sets, because the report spells the drawn regions
    `designRegions` and the rendered ones `views[].regions`, and a caller that reaches for any other
    key gets an empty set and reads every region as missing.
    """

    drawn = dict.fromkeys(region["name"] for region in audit.get("designRegions") or ())
    rendered = {
        region["name"] for view in audit.get("views") or () for region in view.get("regions") or ()
    }
    return tuple(name for name in drawn if name not in rendered)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(FailureCensus(tuple(args.paths)).report())


if __name__ == "__main__":
    main()
