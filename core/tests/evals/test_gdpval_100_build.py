import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import pytest

from evals.gdpval_100.build import SnapshotBuilder, UpstreamRow
from evals.gdpval_100.models import UpstreamParquet
from evals.gdpval_100.snapshot import GDPVAL_UPSTREAM, content_digest, load_snapshot


def _task_id(index: int) -> str:
    return str(UUID(int=index + 1))


def _asset_columns(path: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    encoded = quote(path, safe="/")
    return (
        (path,),
        (f"https://huggingface.co/datasets/openai/gdpval/resolve/main/{encoded}",),
        (f"hf://datasets/openai/gdpval@main/{encoded}",),
    )


def _rows() -> tuple[UpstreamRow, ...]:
    rubric = json.dumps(
        [
            {
                "score": 2,
                "criterion": "The artifact answers the request.",
                "required": None,
                "rubric_item_id": "00000000-0000-0000-0000-000000000001",
                "author_type": "human",
                "tags": ["true"],
                "read_only": None,
            }
        ]
    )
    rows = []
    for index in range(220):
        references = (
            _asset_columns("reference_files/fixture/input.txt") if index == 0 else ((), (), ())
        )
        deliverables = (
            _asset_columns("deliverable_files/fixture/gold.txt") if index == 0 else ((), (), ())
        )
        rows.append(
            UpstreamRow(
                task_id=_task_id(index),
                sector=f"Sector {index // 25}",
                occupation=f"Occupation {index // 5}",
                prompt=f"Produce artifact {index}",
                reference_files=references[0],
                reference_file_urls=references[1],
                reference_file_hf_uris=references[2],
                deliverable_files=deliverables[0],
                deliverable_file_urls=deliverables[1],
                deliverable_file_hf_uris=deliverables[2],
                rubric_pretty="The artifact answers the request.",
                rubric_json=rubric,
            )
        )
    return tuple(rows)


def test_upstream_pin_fixes_revision_parquet_and_schema() -> None:
    assert GDPVAL_UPSTREAM.revision == "11e7900cdcac61bc4daf59e65feb238acda98fbf"
    assert GDPVAL_UPSTREAM.size_bytes == 1_913_489
    assert GDPVAL_UPSTREAM.sha256 == (
        "sha256:f8422fab9b21d90c0ee5f0659842ab666d418cb8940842918f9f4b0df7ae0202"
    )
    assert GDPVAL_UPSTREAM.rows == 220
    assert tuple(column.name for column in GDPVAL_UPSTREAM.columns) == (
        "task_id",
        "sector",
        "occupation",
        "prompt",
        "reference_files",
        "reference_file_urls",
        "reference_file_hf_uris",
        "deliverable_files",
        "deliverable_file_urls",
        "deliverable_file_hf_uris",
        "rubric_pretty",
        "rubric_json",
    )


def test_parquet_verification_fails_on_byte_drift(tmp_path: Path) -> None:
    parquet = tmp_path / "gdpval.parquet"
    parquet.write_bytes(b"pinned parquet")
    upstream = GDPVAL_UPSTREAM.model_copy(
        update={
            "size_bytes": parquet.stat().st_size,
            "sha256": content_digest(parquet.read_bytes()),
        }
    )
    builder = SnapshotBuilder(parquet, tmp_path / "snapshots", smoke_count=1, upstream=upstream)
    builder._verify_parquet()
    parquet.write_bytes(b"changed parquet")
    with pytest.raises(ValueError, match="digest mismatch"):
        SnapshotBuilder(
            parquet,
            tmp_path / "snapshots",
            smoke_count=1,
            upstream=upstream.model_copy(update={"size_bytes": parquet.stat().st_size}),
        )._verify_parquet()


def test_case_rejects_a_main_url_that_does_not_match_its_path() -> None:
    row = _rows()[0]
    changed = row.model_copy(
        update={
            "reference_file_urls": (
                "https://huggingface.co/datasets/openai/gdpval/resolve/main/wrong.txt",
            )
        }
    )
    with pytest.raises(ValueError, match="URL does not match"):
        SnapshotBuilder(
            Path("unused.parquet"), Path("unused-snapshots"), smoke_count=1
        )._case_from_row(changed)


def test_builder_materializes_a_content_addressed_offline_smoke_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parquet = tmp_path / "gdpval.parquet"
    parquet.write_bytes(b"fixture parquet")
    digest = f"sha256:{hashlib.sha256(parquet.read_bytes()).hexdigest()}"
    upstream = UpstreamParquet(
        revision=GDPVAL_UPSTREAM.revision,
        size_bytes=parquet.stat().st_size,
        sha256=digest,
        columns=GDPVAL_UPSTREAM.columns,
    )
    assets = tmp_path / "assets"
    reference = assets / "reference_files/fixture/input.txt"
    deliverable = assets / "deliverable_files/fixture/gold.txt"
    reference.parent.mkdir(parents=True)
    deliverable.parent.mkdir(parents=True)
    reference.write_text("reference body")
    deliverable.write_text("gold body")
    monkeypatch.setattr(SnapshotBuilder, "_read_parquet", lambda _self: _rows())

    root = SnapshotBuilder(
        parquet=parquet,
        output_root=tmp_path / "snapshots",
        smoke_count=1,
        asset_root=assets,
        upstream=upstream,
    ).build()
    with pytest.raises(ValueError, match="pinned upstream parquet"):
        load_snapshot(root)
    snapshot = load_snapshot(root, upstream)

    assert len(snapshot.cases) == 220
    assert snapshot.manifest.materialized_case_ids == (_task_id(0),)
    first = snapshot.cases[0]
    assert first.references[0].snapshot_path == (
        f"objects/sha256/{first.references[0].sha256.removeprefix('sha256:')}"
    )
    assert (root / first.references[0].snapshot_path).read_text() == "reference body"
    assert first.deliverables[0].snapshot_path is not None
    assert all(not case.references and not case.deliverables for case in snapshot.cases[1:])
    assert root.parent == tmp_path / "snapshots"
    assert len(root.name) == 64
