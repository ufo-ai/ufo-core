from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
TASK_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
RESULTS_FILE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"

Subset = Literal["smoke", "hillclimb", "holdout"]
Difficulty = Literal["low", "medium", "high"]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]

SCORED_SUBSETS: tuple[Subset, ...] = ("hillclimb", "holdout")
SCORED_SUBSET_SIZE = 21
TIER_BY_DIFFICULTY: dict[Difficulty, int] = {"low": 1, "medium": 2, "high": 3}
PER_DIFFICULTY = SCORED_SUBSET_SIZE // len(TIER_BY_DIFFICULTY)


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UpstreamRepo(BoundaryModel):
    repository: Literal["perplexityai/wandr"] = "perplexityai/wandr"
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: Literal["Apache-2.0"] = "Apache-2.0"


class SelectionCase(BoundaryModel):
    name: str = Field(pattern=TASK_NAME_PATTERN)
    subset: Subset
    difficulty: Difficulty | None
    digest: str = Field(pattern=DIGEST_PATTERN)
    required_files: tuple[Annotated[str, StringConstraints(pattern=RESULTS_FILE_PATTERN)], ...] = (
        Field(min_length=1)
    )

    @model_validator(mode="after")
    def validate_difficulty(self) -> "SelectionCase":
        if (self.difficulty is None) != (self.subset == "smoke"):
            raise ValueError(f"WANDR case {self.name!r} difficulty does not match its subset")
        return self


class Selection(BoundaryModel):
    upstream: UpstreamRepo
    seed: NonEmpty
    cases: tuple[SelectionCase, ...]

    @model_validator(mode="after")
    def validate_subsets(self) -> "Selection":
        names = [case.name for case in self.cases]
        if len(set(names)) != len(names):
            raise ValueError("WANDR selection contains duplicate task names")
        if sum(case.subset == "smoke" for case in self.cases) != 1:
            raise ValueError("WANDR selection requires exactly one smoke case")
        for subset in SCORED_SUBSETS:
            members = [case for case in self.cases if case.subset == subset]
            if len(members) != SCORED_SUBSET_SIZE:
                raise ValueError(
                    f"WANDR {subset} subset requires {SCORED_SUBSET_SIZE} cases, "
                    f"found {len(members)}"
                )
            for difficulty in TIER_BY_DIFFICULTY:
                count = sum(case.difficulty == difficulty for case in members)
                if count != PER_DIFFICULTY:
                    raise ValueError(
                        f"WANDR {subset} subset requires {PER_DIFFICULTY} {difficulty} cases, "
                        f"found {count}"
                    )
        return self

    def subset_cases(self, subset: Subset) -> tuple[SelectionCase, ...]:
        return tuple(case for case in self.cases if case.subset == subset)


class SnapshotManifest(BoundaryModel):
    name: Literal["wandr"] = "wandr"
    digest: str = Field(pattern=DIGEST_PATTERN)
    selection: Selection
    verifier_lock_sha256: str = Field(pattern=DIGEST_PATTERN)


class Snapshot(BoundaryModel):
    root: str = Field(min_length=1)
    manifest: SnapshotManifest
