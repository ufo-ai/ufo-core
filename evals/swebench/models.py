from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
INSTANCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*__[A-Za-z0-9][A-Za-z0-9_.-]*-[1-9][0-9]*$"
REPOSITORY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
APPROVED_CASE_IDS = (
    "django__django-10097",
    "sympy__sympy-20590",
    "scikit-learn__scikit-learn-25102",
)
EXPECTED_PARQUET_COLUMNS = (
    ("repo", "string"),
    ("instance_id", "string"),
    ("base_commit", "string"),
    ("patch", "string"),
    ("test_patch", "string"),
    ("problem_statement", "string"),
    ("hints_text", "string"),
    ("created_at", "string"),
    ("version", "string"),
    ("FAIL_TO_PASS", "string"),
    ("PASS_TO_PASS", "string"),
    ("environment_setup_commit", "string"),
    ("difficulty", "string"),
)


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParquetColumn(BoundaryModel):
    name: str = Field(min_length=1)
    arrow_type: str = Field(min_length=1)


class UpstreamParquet(BoundaryModel):
    path: Literal["data/test-00000-of-00001.parquet"] = "data/test-00000-of-00001.parquet"
    size_bytes: Literal[2_096_679] = 2_096_679
    sha256: Literal["sha256:a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd"] = (
        "sha256:a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd"
    )
    rows: Literal[500] = 500
    columns: tuple[ParquetColumn, ...]

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        observed = tuple((column.name, column.arrow_type) for column in self.columns)
        if observed != EXPECTED_PARQUET_COLUMNS:
            raise ValueError("SWE-bench upstream parquet columns do not match the pinned schema")
        return self


class SWEbenchUpstream(BoundaryModel):
    dataset: Literal["princeton-nlp/SWE-bench_Verified"] = "princeton-nlp/SWE-bench_Verified"
    revision: Literal["c104f840cc67f8b6eec6f759ebc8b2693d585d4a"] = (
        "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
    )
    parquet: UpstreamParquet
    smoke_case_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_smoke_case_ids(self) -> Self:
        if self.smoke_case_ids != APPROVED_CASE_IDS:
            raise ValueError("SWE-bench smoke case ids must use the approved order")
        return self


class SWEbenchCase(BoundaryModel):
    repo: str = Field(pattern=REPOSITORY_PATTERN)
    instance_id: str = Field(pattern=INSTANCE_ID_PATTERN)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    patch: str = Field(min_length=1)
    test_patch: str
    problem_statement: str = Field(min_length=1)
    hints_text: str
    created_at: str
    version: str
    FAIL_TO_PASS: str = Field(min_length=1)
    PASS_TO_PASS: str = Field(min_length=1)
    environment_setup_commit: str
    difficulty: str


class SnapshotFile(BoundaryModel):
    path: Literal["cases.jsonl.gz"] = "cases.jsonl.gz"
    records: Literal[3] = 3
    sha256: str = Field(pattern=DIGEST_PATTERN)


class SnapshotManifest(BoundaryModel):
    name: Literal["swebench_verified"] = "swebench_verified"
    digest: str = Field(pattern=DIGEST_PATTERN)
    upstream: SWEbenchUpstream
    cases: SnapshotFile
    case_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_case_ids(self) -> Self:
        if self.case_ids != self.upstream.smoke_case_ids:
            raise ValueError("SWE-bench manifest case ids must use the approved order")
        return self


class SWEbenchSnapshot(BoundaryModel):
    root: str = Field(min_length=1)
    manifest: SnapshotManifest
    cases: tuple[SWEbenchCase, ...]
