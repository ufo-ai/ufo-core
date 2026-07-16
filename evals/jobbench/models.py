from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
TASK_ID_PATTERN = r"^[a-z0-9_]+__task[1-9][0-9]*$"
CASE_ID_PATTERN = r"^(main|easy)\.[a-z0-9_]+__task[1-9][0-9]*$"

Split = Literal["main", "easy"]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParquetColumn(BoundaryModel):
    name: str = Field(min_length=1)
    arrow_type: str = Field(min_length=1)


class SplitParquet(BoundaryModel):
    split: Split
    relative_path: Literal["tasks.parquet", "easy.parquet"]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=DIGEST_PATTERN)
    rows: Literal[65, 63]

    @model_validator(mode="after")
    def validate_split_shape(self) -> "SplitParquet":
        expected = {"main": ("tasks.parquet", 65), "easy": ("easy.parquet", 63)}[self.split]
        if (self.relative_path, self.rows) != expected:
            raise ValueError(f"JobBench {self.split} split must pin {expected}")
        return self


class UpstreamCorpus(BoundaryModel):
    repository: Literal["JobBench/job-bench"] = "JobBench/job-bench"
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    columns: tuple[ParquetColumn, ...] = Field(min_length=1)
    main: SplitParquet
    easy: SplitParquet
    license: Literal["MIT"] = "MIT"

    @model_validator(mode="after")
    def validate_splits(self) -> "UpstreamCorpus":
        if self.main.split != "main" or self.easy.split != "easy":
            raise ValueError("JobBench upstream split parquets are mislabeled")
        return self

    def split_parquet(self, split: Split) -> "SplitParquet":
        return self.main if split == "main" else self.easy


class RubricItem(BoundaryModel):
    rubric: str = Field(min_length=1)
    weight: int = Field(ge=1, le=100)
    criteria: tuple[NonEmpty, ...] = Field(min_length=1)


class SnapshotAsset(BoundaryModel):
    relative_path: str = Field(min_length=1)
    snapshot_path: str | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_materialization(self) -> "SnapshotAsset":
        values = (self.snapshot_path, self.size_bytes, self.sha256)
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("snapshot asset materialization fields must be complete or absent")
        return self


class SnapshotCase(BoundaryModel):
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    split: Split
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    occupation: str = Field(min_length=1)
    task_num: int = Field(ge=1)
    prompt: str = Field(min_length=1)
    references: tuple[SnapshotAsset, ...] = Field(min_length=1)
    rubric: tuple[RubricItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> "SnapshotCase":
        if self.task_id != f"{self.occupation}__task{self.task_num}":
            raise ValueError(f"JobBench task id {self.task_id!r} does not match its occupation")
        if self.case_id != f"{self.split}.{self.task_id}":
            raise ValueError(f"JobBench case id {self.case_id!r} does not match its split")
        return self


class SnapshotFile(BoundaryModel):
    path: Literal["cases.jsonl.gz"] = "cases.jsonl.gz"
    records: Literal[128] = 128
    sha256: str = Field(pattern=DIGEST_PATTERN)


class SnapshotManifest(BoundaryModel):
    name: Literal["jobbench"] = "jobbench"
    digest: str = Field(pattern=DIGEST_PATTERN)
    upstream: UpstreamCorpus
    cases: SnapshotFile
    materialized_case_ids: tuple[str, ...]


class Snapshot(BoundaryModel):
    root: str = Field(min_length=1)
    manifest: SnapshotManifest
    cases: tuple[SnapshotCase, ...]
