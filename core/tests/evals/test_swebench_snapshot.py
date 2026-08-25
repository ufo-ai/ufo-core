import gzip
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.swebench import build
from evals.swebench.build import build_snapshot
from evals.swebench.models import SubsetSelection, SWEbenchCase
from evals.swebench.snapshot import (
    CASES_FILE,
    MANIFEST_FILE,
    load_snapshot,
    load_upstream,
    select_cases,
    write_snapshot,
)

SMOKE_IDS = (
    "django__django-10097",
    "sympy__sympy-20590",
    "scikit-learn__scikit-learn-25102",
)
HILLCLIMB_IDS = (
    "django__django-16877",
    "sympy__sympy-21612",
    "sphinx-doc__sphinx-7440",
    "matplotlib__matplotlib-24570",
    "scikit-learn__scikit-learn-11578",
    "astropy__astropy-7336",
    "pydata__xarray-4629",
    "pytest-dev__pytest-6197",
    "pylint-dev__pylint-7080",
    "psf__requests-1921",
)
HOLDOUT_IDS = (
    "mwaskom__seaborn-3069",
    "pallets__flask-5014",
    "django__django-15022",
    "sympy__sympy-11618",
    "sphinx-doc__sphinx-8621",
    "matplotlib__matplotlib-24870",
    "scikit-learn__scikit-learn-10297",
    "astropy__astropy-12907",
    "pydata__xarray-4075",
    "pytest-dev__pytest-5840",
)
HARD_IDS = (
    "pydata__xarray-6992",
    "sphinx-doc__sphinx-7590",
    "sympy__sympy-13878",
)
EXPECTED_IDS = SMOKE_IDS + HILLCLIMB_IDS + HOLDOUT_IDS + HARD_IDS
EXPECTED_COLUMNS = (
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "difficulty",
)


def _row(instance_id: str, index: int) -> dict[str, str]:
    owner, remainder = instance_id.split("__", maxsplit=1)
    repository = remainder.rsplit("-", maxsplit=1)[0]
    return {
        "repo": f"{owner}/{repository}",
        "instance_id": instance_id,
        "base_commit": f"{index + 1:040x}",
        "patch": f"diff --git a/file{index}.py b/file{index}.py\n",
        "test_patch": f"diff --git a/test{index}.py b/test{index}.py\n",
        "problem_statement": f"Fix regression {index}.",
        "hints_text": f"Hint {index}",
        "created_at": f"2024-01-01T00:00:{index:02d}Z",
        "version": f"{index + 1}.0",
        "FAIL_TO_PASS": json.dumps([f"test_fails_{index}"]),
        "PASS_TO_PASS": json.dumps([f"test_passes_{index}"]),
        "environment_setup_commit": f"{index + 100:040x}",
        "difficulty": (
            ">4 hours"
            if instance_id in HARD_IDS
            else ("<15 min fix", "15 min - 1 hour", "1-4 hours")[index % 3]
        ),
    }


def _rows() -> tuple[dict[str, str], ...]:
    return tuple(_row(instance_id, index) for index, instance_id in enumerate(EXPECTED_IDS))


def test_upstream_pin_and_subset_roster_are_exact() -> None:
    upstream = load_upstream()
    assert upstream.dataset == "princeton-nlp/SWE-bench_Verified"
    assert upstream.revision == "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
    assert upstream.parquet.path == "data/test-00000-of-00001.parquet"
    assert upstream.parquet.size_bytes == 2_096_679
    assert upstream.parquet.sha256 == (
        "sha256:a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd"
    )
    assert upstream.parquet.rows == 500
    assert tuple(column.name for column in upstream.parquet.columns) == EXPECTED_COLUMNS
    assert tuple(column.arrow_type for column in upstream.parquet.columns) == ("string",) * 13
    assert upstream.subsets.seed == "swebench-hillclimb-2026-08-22"
    assert upstream.subsets.smoke == SMOKE_IDS
    assert upstream.subsets.hillclimb == HILLCLIMB_IDS
    assert upstream.subsets.holdout == HOLDOUT_IDS
    assert upstream.subsets.hard == HARD_IDS
    assert upstream.subsets.all_ids == EXPECTED_IDS


def test_subset_accessors_name_every_case() -> None:
    subsets = load_upstream().subsets
    assert subsets.ids("smoke") == SMOKE_IDS
    assert subsets.ids("hillclimb") == HILLCLIMB_IDS
    assert subsets.ids("holdout") == HOLDOUT_IDS
    assert subsets.ids("hard") == HARD_IDS
    assert subsets.subset_of(SMOKE_IDS[0]) == "smoke"
    assert subsets.subset_of(HILLCLIMB_IDS[4]) == "hillclimb"
    assert subsets.subset_of(HOLDOUT_IDS[9]) == "holdout"
    assert subsets.subset_of(HARD_IDS[2]) == "hard"
    with pytest.raises(ValueError, match="unknown SWE-bench instance id"):
        subsets.subset_of("pallets__flask-99999")


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"smoke": SMOKE_IDS[::-1]}, "smoke case ids must use the approved order"),
        ({"hillclimb": HILLCLIMB_IDS[:9]}, "hillclimb subset requires 10 case ids, found 9"),
        ({"holdout": (*HOLDOUT_IDS[:9], HILLCLIMB_IDS[0])}, "subsets must be disjoint"),
        ({"hard": HARD_IDS[:2]}, "hard subset requires 3 case ids, found 2"),
        ({"hillclimb": (*HILLCLIMB_IDS[:9], "not-an-instance")}, "String should match pattern"),
    ),
)
def test_subset_selection_rejects_roster_drift(
    override: Mapping[str, tuple[str, ...]], message: str
) -> None:
    fields = {
        "seed": "swebench-hillclimb-2026-08-22",
        "smoke": SMOKE_IDS,
        "hillclimb": HILLCLIMB_IDS,
        "holdout": HOLDOUT_IDS,
        "hard": HARD_IDS,
        **override,
    }
    with pytest.raises(ValidationError, match=message):
        SubsetSelection.model_validate(fields)


def test_case_rejects_unsafe_instance_ids_and_short_base_commits() -> None:
    row = _row(EXPECTED_IDS[0], 0)
    with pytest.raises(ValidationError, match="instance_id"):
        SWEbenchCase.model_validate({**row, "instance_id": "../django__django-10097"})
    with pytest.raises(ValidationError, match="base_commit"):
        SWEbenchCase.model_validate({**row, "base_commit": "abc123"})


def test_selection_retains_every_official_field_in_manifest_order() -> None:
    rows = tuple(reversed(_rows()))
    selected = select_cases(rows)
    assert tuple(case.instance_id for case in selected) == EXPECTED_IDS
    assert tuple(selected[0].model_dump()) == EXPECTED_COLUMNS
    assert selected[0].model_dump() == _rows()[0]


@pytest.mark.parametrize(
    ("rows", "message"),
    (
        (
            (*_rows(), _row("pallets__flask-99999", 99)),
            "unknown SWE-bench selected instance ids",
        ),
        (
            (_rows()[0], _rows()[0], *_rows()[1:]),
            "duplicate SWE-bench selected instance ids",
        ),
        (_rows()[:-1], "missing SWE-bench selected instance ids"),
    ),
)
def test_selection_rejects_unknown_duplicate_and_missing_ids(
    rows: tuple[dict[str, str], ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        select_cases(rows)


def test_snapshot_round_trip_preserves_every_subset_row(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    write_snapshot(root, select_cases(_rows()))
    snapshot = load_snapshot(root)
    assert snapshot.root == str(root.resolve())
    assert snapshot.manifest.case_ids == EXPECTED_IDS
    assert snapshot.manifest.cases.records == 26
    assert snapshot.cases == select_cases(_rows())
    payload = gzip.decompress((root / CASES_FILE).read_bytes())
    assert len(payload.splitlines()) == 26
    assert tuple(json.loads(line) for line in payload.splitlines()) == _rows()


def test_snapshot_replacement_atomically_swaps_an_immutable_version_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshot"
    first_cases = select_cases(_rows())
    write_snapshot(root, first_cases)
    first_target = root.resolve(strict=True)

    changed_rows = list(_rows())
    changed_rows[0] = {
        **changed_rows[0],
        "problem_statement": "Fix the replacement regression.",
    }
    second_cases = select_cases(changed_rows)
    write_snapshot(root, second_cases)
    second_target = root.resolve(strict=True)

    assert root.is_symlink()
    assert first_target != second_target
    assert first_target.is_dir()
    assert second_target.is_dir()

    first_pointer = tmp_path / "first-snapshot"
    second_pointer = tmp_path / "second-snapshot"
    first_pointer.symlink_to(first_target, target_is_directory=True)
    second_pointer.symlink_to(second_target, target_is_directory=True)
    assert load_snapshot(first_pointer).cases == first_cases
    assert load_snapshot(second_pointer).cases == second_cases

    next_pointer = tmp_path / ".snapshot-next"
    next_pointer.symlink_to(first_target, target_is_directory=True)
    next_pointer.replace(root)
    original_read_bytes = Path.read_bytes
    swapped = False

    def read_bytes(path: Path) -> bytes:
        nonlocal swapped
        if path == first_target / MANIFEST_FILE and not swapped:
            swapped = True
            replacement = tmp_path / ".snapshot-replacement"
            replacement.symlink_to(second_target, target_is_directory=True)
            replacement.replace(root)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    loaded = load_snapshot(root)
    assert swapped
    assert root.resolve(strict=True) == second_target
    assert loaded.cases == first_cases


def test_snapshot_rejects_a_non_pointer_output(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    with pytest.raises(ValueError, match="must be a symbolic link"):
        write_snapshot(root, select_cases(_rows()))
    with pytest.raises(ValueError, match="must be a symbolic link"):
        load_snapshot(root)


def test_snapshot_rejects_a_symlinked_versions_directory(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    outside = tmp_path / "outside"
    outside.mkdir()
    versions = tmp_path / "snapshot.versions"
    versions.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="versions path must not be a symbolic link"):
        write_snapshot(root, select_cases(_rows()))
    assert not tuple(outside.iterdir())
    assert not root.exists()


def test_snapshot_load_rejects_payload_digest_drift(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    write_snapshot(root, select_cases(_rows()))
    payload = gzip.decompress((root / CASES_FILE).read_bytes()) + b"{}\n"
    (root / CASES_FILE).write_bytes(gzip.compress(payload, mtime=0))
    with pytest.raises(ValueError, match="case records do not match"):
        load_snapshot(root)


def test_snapshot_load_rejects_manifest_digest_and_count_drift(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    write_snapshot(root, select_cases(_rows()))
    manifest_path = root / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_bytes())
    manifest["digest"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="snapshot digest does not match"):
        load_snapshot(root)

    count_root = tmp_path / "count-snapshot"
    write_snapshot(count_root, select_cases(_rows()))
    payload = gzip.decompress((count_root / CASES_FILE).read_bytes())
    truncated = b"\n".join(payload.splitlines()[:-1]) + b"\n"
    manifest_path = count_root / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_bytes())
    manifest["cases"]["sha256"] = f"sha256:{hashlib.sha256(truncated).hexdigest()}"
    manifest["digest"] = "sha256:" + "0" * 64
    (count_root / CASES_FILE).write_bytes(gzip.compress(truncated, mtime=0))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="requires exactly 26 cases"):
        load_snapshot(count_root)


def test_snapshot_load_rejects_case_order_and_upstream_drift(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    write_snapshot(root, select_cases(_rows()))
    manifest_path = root / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_bytes())
    manifest["case_ids"] = list(reversed(EXPECTED_IDS))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValidationError, ValueError), match=r"approved order|pinned upstream"):
        load_snapshot(root)

    upstream_root = tmp_path / "upstream-snapshot"
    write_snapshot(upstream_root, select_cases(_rows()))
    manifest_path = upstream_root / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_bytes())
    manifest["upstream"]["revision"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValidationError, ValueError), match=r"revision|pinned upstream"):
        load_snapshot(upstream_root)


def test_build_rejects_source_drift_without_replacing_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "snapshot"
    write_snapshot(output, select_cases(_rows()))
    manifest_before = (output / MANIFEST_FILE).read_bytes()
    parquet = tmp_path / "source.parquet"
    parquet.write_bytes(b"not the pinned source")
    with pytest.raises(ValueError, match="size mismatch"):
        build_snapshot(parquet, output)
    assert (output / MANIFEST_FILE).read_bytes() == manifest_before


def test_build_refuses_a_source_whose_seeded_split_leaves_the_pinned_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "snapshot"
    write_snapshot(output, select_cases(_rows()))
    manifest_before = (output / MANIFEST_FILE).read_bytes()
    parquet = tmp_path / "source.parquet"
    parquet.write_bytes(b"accepted by the patched verifier")
    monkeypatch.setattr(build, "verify_source", lambda _source, _upstream: None)
    monkeypatch.setattr(build, "read_parquet", lambda _source, _upstream: _rows())

    with pytest.raises(ValueError, match="does not match the pinned roster"):
        build_snapshot(parquet, output)

    assert (output / MANIFEST_FILE).read_bytes() == manifest_before
