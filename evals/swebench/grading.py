from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal
from uuid import uuid4

from evals.swebench.models import SUBSET_SIZES, SWEbenchCase, SWEbenchSnapshot
from evals.swebench.snapshot import SWEBENCH_UPSTREAM, load_snapshot, verify_source

HARNESS_DISTRIBUTION = "swebench"
HARNESS_VERSION = "4.1.0"
HARNESS_PIN = f"{HARNESS_DISTRIBUTION}=={HARNESS_VERSION}"
OFFICIAL_IMAGE_NAMESPACE = "swebench"
OFFICIAL_IMAGE_ARCHITECTURE = "x86_64"
OFFICIAL_IMAGE_TAG = "latest"
OFFICIAL_IMAGE_PLATFORM = "linux/amd64"
OFFICIAL_IMAGE_INSPECT_FORMAT = '{{.Os}}/{{.Architecture}}{{println}}{{join .RepoDigests "\\n"}}'
DEFAULT_SNAPSHOT = Path(".local/swebench/snapshot")
DEFAULT_PARQUET = Path(".local/swebench/assets/test.parquet")
DEFAULT_GRADES_ROOT = Path(".local/swebench/grades")
PREDICTIONS_FILE = "predictions.jsonl"
SUMMARY_FILE = "summary.json"
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SUSPECT_GOLD_OVERLAP = 0.9
GradingSubset = Literal["smoke", "hillclimb", "holdout", "hard", "all"]


@dataclass(frozen=True)
class _OfficialOutcomes:
    resolved: frozenset[str]
    empty: frozenset[str]


def _diff_sections(patch: str) -> tuple[str, ...]:
    starts = tuple(match.start() for match in re.finditer(r"^diff --git ", patch, re.MULTILINE))
    if not starts:
        return ()
    return tuple(
        patch[start:stop] for start, stop in zip(starts, (*starts[1:], len(patch)), strict=True)
    )


def _diff_paths(section: str) -> frozenset[str]:
    header = section.partition("\n")[0]
    try:
        tokens = shlex.split(header)
    except ValueError:
        return frozenset()
    if len(tokens) != 4 or tokens[:2] != ["diff", "--git"]:
        return frozenset()
    return frozenset(token[2:] if token.startswith(("a/", "b/")) else token for token in tokens[2:])


def _remove_official_test_changes(case: SWEbenchCase, patch: str) -> str:
    test_paths = frozenset(
        path for section in _diff_sections(case.test_patch) for path in _diff_paths(section)
    )
    sections = _diff_sections(patch)
    if not test_paths or not sections:
        return patch
    return "".join(section for section in sections if _diff_paths(section).isdisjoint(test_paths))


def _changed_lines(patch: str) -> frozenset[str]:
    return frozenset(
        line
        for line in patch.splitlines()
        if line[:1] in {"+", "-"} and not line.startswith(("+++", "---")) and line[1:].strip()
    )


def gold_overlap(gold_patch: str, submission: str) -> float:
    """Share of the gold patch's changed lines the submitted patch reproduces."""
    gold_lines = _changed_lines(gold_patch)
    if not gold_lines:
        return 0.0
    return len(_changed_lines(submission) & gold_lines) / len(gold_lines)


def official_instance_image(instance_id: str) -> str:
    """Official prebuilt evaluation image for one instance, named as the pinned harness names it."""
    key = f"sweb.eval.{OFFICIAL_IMAGE_ARCHITECTURE}.{instance_id.lower()}:{OFFICIAL_IMAGE_TAG}"
    return f"{OFFICIAL_IMAGE_NAMESPACE}/{key}".replace("__", "_1776_")


def write_predictions(
    cases: tuple[SWEbenchCase, ...],
    patches: dict[str, str],
    output: Path,
    model_name: str,
) -> Path:
    """Write one official prediction per selected case in manifest order."""
    payload = b"".join(
        json.dumps(
            {
                "instance_id": case.instance_id,
                "model_patch": _remove_official_test_changes(case, patches[case.instance_id]),
                "model_name_or_path": model_name,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        + b"\n"
        for case in cases
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return output


def load_submission_patches(
    cases: tuple[SWEbenchCase, ...], known_ids: set[str], submissions_root: Path
) -> dict[str, str]:
    """Load the selected cases' patches, tolerating sibling directories for other known cases."""
    patches = {case.instance_id: "" for case in cases}
    if submissions_root.is_symlink():
        raise ValueError(
            f"SWE-bench submissions root must not be a symbolic link: {submissions_root}"
        )
    if not submissions_root.exists():
        return patches
    if not submissions_root.is_dir():
        raise ValueError(f"SWE-bench submissions root is not a directory: {submissions_root}")
    entries = tuple(submissions_root.iterdir())
    unexpected = sorted(
        entry.name for entry in entries if entry.name not in known_ids or not entry.is_dir()
    )
    if unexpected:
        raise ValueError(f"unexpected SWE-bench submission directories: {', '.join(unexpected)}")
    for case in cases:
        directory = submissions_root / case.instance_id
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise ValueError(
                f"SWE-bench submission directory must not be a symbolic link: {directory}"
            )
        entries = tuple(directory.iterdir())
        patch_candidates = tuple(
            entry for entry in entries if entry.suffix == ".patch" and entry.is_file()
        )
        if len(patch_candidates) > 1:
            raise ValueError(f"duplicate SWE-bench patches for {case.instance_id}")
        expected_patch = directory / f"{case.instance_id}.patch"
        unexpected = sorted(entry.name for entry in entries if entry != expected_patch)
        if unexpected:
            raise ValueError(
                f"unexpected SWE-bench submission files for {case.instance_id}: "
                f"{', '.join(unexpected)}"
            )
        if not expected_patch.exists():
            continue
        if expected_patch.is_symlink() or not expected_patch.is_file():
            raise ValueError(f"invalid SWE-bench submission patch: {expected_patch}")
        try:
            patches[case.instance_id] = expected_patch.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            print(f"SWE-bench patch for {case.instance_id} is not UTF-8; submitting empty patch")
    return patches


@dataclass(frozen=True)
class SWEbenchGrading:
    """Grade selected pinned cases once with the official SWE-bench harness."""

    snapshot: SWEbenchSnapshot
    selected_cases: tuple[SWEbenchCase, ...]
    subset: GradingSubset
    parquet: Path
    submissions_root: Path | None
    grade_directory: Path
    run_id: str
    gold: bool = False
    prune_images: bool = False
    model_name: str = "ufo"

    def __post_init__(self) -> None:
        selected_ids = tuple(case.instance_id for case in self.selected_cases)
        selected = set(selected_ids)
        manifest_cases = tuple(case for case in self.snapshot.cases if case.instance_id in selected)
        if not selected_ids:
            raise ValueError("SWE-bench grading requires at least one selected case")
        if self.selected_cases != manifest_cases:
            raise ValueError("SWE-bench grading cases must be unique and use manifest order")
        selection = self.snapshot.manifest.upstream.subsets
        allowed = selection.all_ids if self.subset == "all" else selection.ids(self.subset)
        outside = sorted(selected - set(allowed))
        if outside:
            raise ValueError(
                f"SWE-bench cases outside the {self.subset} subset: {', '.join(outside)}"
            )
        if not SAFE_RUN_ID.fullmatch(self.run_id):
            raise ValueError(f"unsafe SWE-bench run id: {self.run_id}")
        if self.gold and self.submissions_root is not None:
            raise ValueError("gold SWE-bench grading does not accept submissions")
        if not self.gold and self.submissions_root is None:
            raise ValueError("SWE-bench prediction grading requires submissions")

    def run(self) -> Path:
        """Run official grading once and return the factual summary path."""
        self._validate_parquet()
        patches = None if self.gold else self._load_patches()
        self._validate_harness()
        grade_directory = self.grade_directory.resolve()
        grade_directory.mkdir(parents=True, exist_ok=False)
        predictions_path = (
            None
            if patches is None
            else write_predictions(
                self.selected_cases,
                patches,
                grade_directory / PREDICTIONS_FILE,
                self.model_name,
            )
        )
        gold_failed: list[str] = []
        for case in self.selected_cases:
            self._ensure_official_image(case)
            self._invoke_official_harness(
                grade_directory, predictions_path, (case,), rewrite_reports=False
            )
            outcomes = self._validate_official_report(
                self._official_report_path(grade_directory, gold=self.gold),
                (case,),
                gold=self.gold,
            )
            if outcomes.resolved:
                continue
            if not self.gold:
                if outcomes.empty or patches is None or not patches[case.instance_id]:
                    continue
                self._invoke_official_harness(grade_directory, None, (case,), rewrite_reports=False)
                if self._validate_official_report(
                    self._official_report_path(grade_directory, gold=True),
                    (case,),
                    gold=True,
                ).resolved:
                    continue
            gold_failed.append(case.instance_id)
            print(f"official gold patch failed for {case.instance_id}; recorded as gold_failed")
        self._invoke_official_harness(
            grade_directory, predictions_path, self.selected_cases, rewrite_reports=True
        )
        report_path = self._official_report_path(grade_directory, gold=self.gold)
        outcomes = self._validate_official_report(report_path, self.selected_cases, gold=self.gold)
        overlaps = {} if patches is None else self._gold_overlaps(patches)
        summary_path = self._write_summary(
            grade_directory, report_path, len(outcomes.resolved), tuple(gold_failed), overlaps
        )
        self._prune_official_images()
        return summary_path

    def _validate_parquet(self) -> None:
        verify_source(self.parquet.read_bytes(), SWEBENCH_UPSTREAM)

    def _load_patches(self) -> dict[str, str]:
        if self.submissions_root is None:
            raise AssertionError("prediction grading has no submissions root")
        known_ids = {case.instance_id for case in self.snapshot.cases}
        return load_submission_patches(self.selected_cases, known_ids, self.submissions_root)

    def _validate_harness(self) -> None:
        try:
            installed = version(HARNESS_DISTRIBUTION)
        except PackageNotFoundError as error:
            raise RuntimeError(
                f"official grading requires {HARNESS_PIN}; run with "
                f"`uv run --with {HARNESS_PIN} python -m evals.swebench.grading`"
            ) from error
        if installed != HARNESS_VERSION:
            raise RuntimeError(
                f"official grading requires {HARNESS_PIN}, found "
                f"{HARNESS_DISTRIBUTION}=={installed}"
            )

    def _ensure_official_image(self, case: SWEbenchCase) -> None:
        image = official_instance_image(case.instance_id)
        try:
            details = subprocess.check_output(
                (
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    OFFICIAL_IMAGE_INSPECT_FORMAT,
                    image,
                ),
                stderr=subprocess.DEVNULL,
                text=True,
            ).splitlines()
        except subprocess.CalledProcessError:
            details = []
        repository = image.rsplit(":", maxsplit=1)[0]
        if details[:1] == [OFFICIAL_IMAGE_PLATFORM] and any(
            re.fullmatch(rf"{re.escape(repository)}@sha256:[0-9a-f]{{64}}", digest)
            for digest in details[1:]
        ):
            return
        subprocess.run(
            (
                "docker",
                "pull",
                "--platform",
                OFFICIAL_IMAGE_PLATFORM,
                image,
            ),
            check=True,
        )

    def _invoke_official_harness(
        self,
        grade_directory: Path,
        predictions_path: Path | None,
        cases: tuple[SWEbenchCase, ...],
        *,
        rewrite_reports: bool,
    ) -> None:
        command = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            str(self.parquet.resolve().parent),
            "--split",
            "test",
            "--predictions_path",
            "gold" if predictions_path is None else str(predictions_path),
            "--max_workers",
            "1",
            "--instance_ids",
            *(case.instance_id for case in cases),
            "--run_id",
            self.run_id,
        ]
        if rewrite_reports:
            command.extend(("--rewrite_reports", "true"))
        subprocess.run(command, cwd=grade_directory, check=True)

    def _official_report_path(self, grade_directory: Path, *, gold: bool) -> Path:
        model = "gold" if gold else self.model_name
        return grade_directory / f"{model.replace('/', '__')}.{self.run_id}.json"

    def _validate_official_report(
        self,
        report_path: Path,
        selected_cases: tuple[SWEbenchCase, ...],
        *,
        gold: bool,
    ) -> _OfficialOutcomes:
        if not report_path.is_file():
            raise FileNotFoundError(f"official SWE-bench report is missing: {report_path}")
        try:
            report = json.loads(report_path.read_bytes())
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"official SWE-bench report is invalid: {report_path}") from error
        if not isinstance(report, dict):
            raise ValueError(f"official SWE-bench report is not an object: {report_path}")

        def ids(key: str) -> tuple[str, ...]:
            value = report.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"official SWE-bench report has invalid {key}")
            if len(value) != len(set(value)):
                raise ValueError(f"official SWE-bench report has duplicate {key}")
            return tuple(value)

        def count(key: str) -> int:
            value = report.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"official SWE-bench report has invalid {key}")
            return value

        selected = {case.instance_id for case in selected_cases}
        submitted = set(ids("submitted_ids"))
        completed = set(ids("completed_ids"))
        resolved = set(ids("resolved_ids"))
        unresolved = set(ids("unresolved_ids"))
        incomplete = set(ids("incomplete_ids"))
        empty = set(ids("empty_patch_ids"))
        errors = set(ids("error_ids"))
        expected_submissions = {case.instance_id for case in self.selected_cases}
        if not selected.issubset(submitted) or (not gold and submitted != expected_submissions):
            raise ValueError("official SWE-bench report omitted selected submissions")
        completed_outcomes = resolved | unresolved
        if completed != completed_outcomes:
            raise ValueError("official SWE-bench report has inconsistent completed outcomes")
        if resolved & unresolved or resolved & empty or unresolved & empty:
            raise ValueError("official SWE-bench report has overlapping selected outcomes")
        if incomplete or errors:
            raise ValueError("official SWE-bench report contains incomplete or error outcomes")
        if resolved | unresolved | empty != selected:
            raise ValueError("official SWE-bench report omitted selected case outcomes")
        expected_counts = {
            "total_instances": len(selected),
            "submitted_instances": len(submitted),
            "completed_instances": len(completed),
            "resolved_instances": len(resolved),
            "unresolved_instances": len(unresolved),
            "empty_patch_instances": len(empty),
            "error_instances": len(errors),
        }
        for key, expected in expected_counts.items():
            if count(key) != expected:
                raise ValueError(f"official SWE-bench report has inconsistent {key}")
        if report.get("schema_version") != 2:
            raise ValueError("official SWE-bench report has an unsupported schema version")
        return _OfficialOutcomes(resolved=frozenset(resolved), empty=frozenset(empty))

    def _gold_overlaps(self, patches: dict[str, str]) -> dict[str, float]:
        overlaps: dict[str, float] = {}
        for case in self.selected_cases:
            overlap = gold_overlap(case.patch, patches[case.instance_id])
            overlaps[case.instance_id] = overlap
            if overlap >= SUSPECT_GOLD_OVERLAP:
                print(
                    f"SWE-bench patch for {case.instance_id} reproduces "
                    f"{overlap:.0%} of gold changed lines"
                )
        return overlaps

    def _write_summary(
        self,
        grade_directory: Path,
        report_path: Path,
        resolved: int,
        gold_failed: tuple[str, ...],
        overlaps: dict[str, float],
    ) -> Path:
        summary = {
            "pins": {
                "harness": HARNESS_PIN,
                "snapshot": self.snapshot.manifest.digest,
                "dataset": self.snapshot.manifest.upstream.dataset,
                "revision": self.snapshot.manifest.upstream.revision,
                "parquet": self.snapshot.manifest.upstream.parquet.sha256,
            },
            "subset": self.subset,
            "selected_ids": [case.instance_id for case in self.selected_cases],
            "official_report": str(report_path),
            "official_resolved": resolved,
            "official_gold_failed": list(gold_failed),
            "gold_overlap": {case_id: round(overlap, 4) for case_id, overlap in overlaps.items()},
            "suspect_retrieval": [
                case_id for case_id, overlap in overlaps.items() if overlap >= SUSPECT_GOLD_OVERLAP
            ],
        }
        summary_path = grade_directory / SUMMARY_FILE
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        return summary_path

    def _prune_official_images(self) -> None:
        if not self.prune_images:
            return
        for case in self.selected_cases:
            subprocess.run(("docker", "rmi", official_instance_image(case.instance_id)), check=True)


def main(argv: Sequence[str] | None = None) -> None:
    """Grade pinned SWE-bench submissions with the official harness."""
    parser = argparse.ArgumentParser(prog="python -m evals.swebench.grading")
    parser.add_argument("--submissions", type=Path)
    parser.add_argument("--subset", choices=(*SUBSET_SIZES, "all"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--gold", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--prune-images",
        action="store_true",
        help="remove each graded instance image once the summary is written",
    )
    args = parser.parse_args(argv)
    if args.gold and args.submissions is not None:
        parser.error("--gold does not accept --submissions")
    if not args.gold and args.submissions is None:
        parser.error("--submissions is required without --gold")

    snapshot = load_snapshot(DEFAULT_SNAPSHOT)
    selection = snapshot.manifest.upstream.subsets
    requested = tuple(args.case)
    duplicates = sorted(case_id for case_id in set(requested) if requested.count(case_id) > 1)
    if duplicates:
        parser.error(f"duplicate SWE-bench case ids: {', '.join(duplicates)}")
    available = {case.instance_id for case in snapshot.cases}
    unknown = sorted(set(requested) - available)
    if unknown:
        parser.error(f"unknown SWE-bench case ids: {', '.join(unknown)}")
    memberships = tuple(selection.subsets_of(case_id) for case_id in requested)
    if args.subset is None and not memberships:
        parser.error("SWE-bench grading requires --subset or --case")
    if args.subset not in (None, "all") and any(
        args.subset not in subsets for subsets in memberships
    ):
        parser.error(
            f"SWE-bench cases are outside the {args.subset} subset: {', '.join(requested)}"
        )
    spanned = sorted({subset for subsets in memberships for subset in subsets})
    if args.subset is None and len(spanned) > 1:
        parser.error(f"SWE-bench cases span subsets: {', '.join(spanned)}")
    subset: GradingSubset = args.subset or spanned[0]
    roster = selection.all_ids if subset == "all" else selection.ids(subset)
    wanted = set(requested) or set(roster)
    selected_cases = tuple(case for case in snapshot.cases if case.instance_id in wanted)
    run_id = args.run_id or (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}")
    summary = SWEbenchGrading(
        snapshot=snapshot,
        selected_cases=selected_cases,
        subset=subset,
        parquet=DEFAULT_PARQUET,
        submissions_root=args.submissions,
        grade_directory=DEFAULT_GRADES_ROOT / subset / run_id,
        run_id=run_id,
        gold=args.gold,
        prune_images=args.prune_images,
    ).run()
    print(f"summary: {summary}")


if __name__ == "__main__":
    main()
