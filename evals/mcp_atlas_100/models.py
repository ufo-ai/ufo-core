from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.mcp_atlas_100.profile import EXECUTABLE_PROFILE

EXPECTED_CASES = 100
NonemptyText = Annotated[str, Field(min_length=1)]


class McpAtlasCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: NonemptyText
    prompt: NonemptyText
    enabled_tools: tuple[NonemptyText, ...] = Field(min_length=1)
    claims: tuple[NonemptyText, ...] = Field(min_length=1)
    trajectory_tools: tuple[NonemptyText, ...] = Field(min_length=1)
    tool_servers: dict[NonemptyText, NonemptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_metadata(self) -> McpAtlasCase:
        for name, values in (
            ("enabled_tools", self.enabled_tools),
            ("claims", self.claims),
            ("trajectory_tools", self.trajectory_tools),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        unmapped = sorted(set(self.enabled_tools) - self.tool_servers.keys())
        if unmapped:
            raise ValueError(f"enabled tools have no server mapping: {', '.join(unmapped)}")
        unmapped_reference = sorted(set(self.trajectory_tools) - self.tool_servers.keys())
        if unmapped_reference:
            raise ValueError(
                f"trajectory tools have no server mapping: {', '.join(unmapped_reference)}"
            )
        return self

    @property
    def reference_servers(self) -> frozenset[str]:
        return frozenset(self.tool_servers[tool] for tool in self.trajectory_tools)

    @property
    def enabled_catalog_servers(self) -> frozenset[str]:
        return frozenset(self.tool_servers[tool] for tool in self.enabled_tools)


class McpAtlasDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: tuple[McpAtlasCase, ...] = Field(min_length=EXPECTED_CASES, max_length=EXPECTED_CASES)

    @model_validator(mode="after")
    def _valid_benchmark(self) -> McpAtlasDataset:
        tasks = [case.task for case in self.cases]
        if len(tasks) != len(set(tasks)):
            raise ValueError("MCP-Atlas task IDs must be unique")
        for case in self.cases:
            EXECUTABLE_PROFILE.validate(case.tool_servers)
            missing = sorted(set(case.trajectory_tools) - set(case.enabled_tools))
            if missing:
                raise ValueError(
                    f"MCP-Atlas executable task {case.task} does not enable reference tools: "
                    f"{', '.join(missing)}"
                )
        return self
