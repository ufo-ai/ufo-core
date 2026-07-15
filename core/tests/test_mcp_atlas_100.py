import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from evals.harness.capability import CapabilityCase, CapabilityOutput
from evals.harness.judge import JudgeLeg
from evals.harness.target import TargetResult
from evals.mcp_atlas_100.models import McpAtlasCase, McpAtlasDataset
from evals.mcp_atlas_100.profile import APPROVED_SERVERS, APPROVED_TOOLS
from evals.mcp_atlas_100.runner import (
    CLAIM_COVERAGE_THRESHOLD,
    DEFAULT_DATASET,
    ClaimCoverageJudge,
    McpAtlasSuite,
    load_mcp_atlas_task,
    performance_tier,
)
from evals.mcp_atlas_100.select import (
    SourceTask,
    _claims,
    _executable_task,
    _improve_redundancy,
    _reference_key,
    _select,
    _server,
)
from evals.mcp_atlas_100.target import (
    MAX_TOOL_REQUEST_BYTES,
    McpAtlasTarget,
    _validated_cli_arguments,
)
from ufo.sdk.context import ModelAccess
from ufo.sdk.models import Message, ModelRequest, TextBlock, ToolUseBlock


def _case(index: int) -> dict[str, object]:
    return {
        "task": f"task-{index:03d}",
        "prompt": f"solve task {index}",
        "enabled_tools": ["calculator_calculate"],
        "claims": [f"claim {index}"],
        "trajectory_tools": ["calculator_calculate"],
        "tool_servers": {"calculator_calculate": "calculator"},
    }


def _dataset() -> dict[str, object]:
    return {"cases": [_case(index) for index in range(100)]}


def _source(
    task: str,
    enabled_tools: tuple[str, ...],
    trajectory_tools: tuple[str, ...],
    difficulty: int,
) -> SourceTask:
    return SourceTask(
        task=task,
        prompt=task,
        enabled_tools=enabled_tools,
        claims=(task,),
        trajectory_tools=trajectory_tools,
        trajectory_calls=(trajectory_tools[0],),
        servers=frozenset(),
        difficulty=difficulty,
    )


def test_dataset_accepts_only_executable_profile_cases() -> None:
    dataset = McpAtlasDataset.model_validate(_dataset())

    assert {server for case in dataset.cases for server in case.reference_servers} == {"calculator"}
    assert all(set(case.trajectory_tools) <= set(case.enabled_tools) for case in dataset.cases)


def test_committed_executable_dataset_is_fail_closed() -> None:
    dataset = McpAtlasDataset.model_validate({"cases": json.loads(DEFAULT_DATASET.read_bytes())})
    enabled = {tool for case in dataset.cases for tool in case.enabled_tools}
    reference = {tool for case in dataset.cases for tool in case.trajectory_tools}
    servers = {server for case in dataset.cases for server in case.enabled_catalog_servers}

    assert len(dataset.cases) == 100
    assert len(enabled) == 100
    assert len(reference) == 57
    assert servers == set(APPROVED_SERVERS)
    assert enabled == set(APPROVED_TOOLS)
    assert all(set(case.trajectory_tools) <= set(case.enabled_tools) for case in dataset.cases)


def test_executable_profile_is_an_exact_positive_allowlist() -> None:
    assert len(APPROVED_TOOLS) == 100
    assert len(APPROVED_SERVERS) == 21
    assert APPROVED_TOOLS["e2b-server_run_code"] == "e2b-server"
    assert "github_create_issue" not in APPROVED_TOOLS
    assert "desktop-commander_start_process" not in APPROVED_TOOLS
    assert not any(server == "desktop-commander" for server in APPROVED_TOOLS.values())
    assert "mcp-server-code-runner_run-code" not in APPROVED_TOOLS
    assert "pubmed_download_pubmed_pdf" not in APPROVED_TOOLS


@pytest.mark.parametrize(
    ("command", "safe"),
    (
        ("ls", "ls /data"),
        ('cat "Top Movies.csv"', "cat '/data/Top Movies.csv'"),
        ("ls /data", "ls /data"),
        ('cat "/data/reports/Top Movies.csv"', "cat '/data/reports/Top Movies.csv'"),
        ("find repos", "find /data/repos"),
    ),
)
def test_cli_guard_accepts_source_command_shape(command: str, safe: str) -> None:
    assert _validated_cli_arguments({"command": command}) == {"command": safe}


@pytest.mark.parametrize(
    "command",
    (
        "rm /data/report.txt",
        "ls -la /data",
        "find /data -delete",
        "find /data -exec rm {} ;",
        "cat ../secrets",
        "cat /etc/passwd",
        "cat /data/../etc/passwd",
        "cat /data/$(whoami)",
        "ls /data; rm /data/report.txt",
    ),
)
def test_cli_guard_rejects_commands_outside_the_positive_shape(command: str) -> None:
    with pytest.raises(ValueError, match="MCP-Atlas CLI"):
        _validated_cli_arguments({"command": command})


def test_committed_executable_dataset_starts_with_the_live_smoke() -> None:
    dataset = McpAtlasDataset.model_validate({"cases": json.loads(DEFAULT_DATASET.read_bytes())})
    smoke = dataset.cases[:3]

    assert tuple(case.task for case in smoke) == (
        "68925f01db31fe35560e3eb5",
        "689f4d693e212e8ef3390731",
        "6896416f7b30e5d8ccd7c8f7",
    )
    assert set().union(*(set(case.reference_servers) for case in smoke)) == {
        "cli-mcp-server",
        "e2b-server",
        "exa",
        "fetch",
        "filesystem",
        "git",
        "github",
        "mcp-code-executor",
        "met-museum",
        "whois",
    }


@pytest.mark.parametrize(
    ("tool", "server"),
    (
        ("slack_channels_list", "slack"),
        ("google-workspace_list_events", "google-workspace"),
        ("github_create_issue", "github"),
        ("calculator_new_mutating_tool", "calculator"),
    ),
)
def test_executable_dataset_rejects_forbidden_tools(tool: str, server: str) -> None:
    raw = json.loads(DEFAULT_DATASET.read_bytes())
    raw[0]["enabled_tools"].append(tool)
    raw[0]["tool_servers"][tool] = server

    with pytest.raises(ValueError, match=f"{server}:{tool}"):
        McpAtlasDataset.model_validate({"cases": raw})


def test_executable_selection_drops_forbidden_distractors_and_tasks() -> None:
    allowed = SourceTask(
        task="1",
        prompt="calculate",
        enabled_tools=("calculator_calculate", "slack_channels_list", "github_create_issue"),
        claims=("four",),
        trajectory_tools=("calculator_calculate",),
        trajectory_calls=("calculator_calculate",),
        servers=frozenset({"calculator"}),
        difficulty=1,
    )
    forbidden = SourceTask(
        task="2",
        prompt="read slack",
        enabled_tools=("slack_channels_list",),
        claims=("channel",),
        trajectory_tools=("slack_channels_list",),
        trajectory_calls=("slack_channels_list",),
        servers=frozenset({"slack"}),
        difficulty=1,
    )

    executable = _executable_task(allowed)

    assert executable is not None
    assert executable.enabled_tools == ("calculator_calculate",)
    assert _executable_task(forbidden) is None


def test_source_claims_repair_literal_control_characters() -> None:
    assert _claims("['line\nbreak', 'tab\tvalue']") == ("line\nbreak", "tab\tvalue")

    with pytest.raises(ValueError, match="list of strings"):
        _claims("[1]")


@pytest.mark.parametrize(
    ("tool", "server"),
    (
        ("calculator_calculate", "calculator"),
        ("brave_web_search", "brave-search"),
        ("MongoDB_find", "mongodb"),
    ),
)
def test_source_tool_server_resolution(tool: str, server: str) -> None:
    assert _server(tool) == server


def test_source_reference_priority_rewards_rarity_and_new_enabled_tools() -> None:
    task = _source("a", ("tool-a", "tool-c"), ("tool-a", "tool-b"), 7)

    priority = _reference_key(
        task,
        {"tool-a"},
        {"tool-a"},
        Counter({"tool-a": 2, "tool-b": 4}),
    )

    assert priority == (250_000, 1, 1, 7, -10)


def test_source_selection_covers_reference_and_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("evals.mcp_atlas_100.select.CASE_COUNT", 2)
    tasks = (
        _source("1", ("tool-a",), ("tool-a",), 1),
        _source("2", ("tool-b",), ("tool-b",), 1),
        _source("3", ("tool-a", "tool-c"), ("tool-a",), 5),
    )

    selected = _select(tasks)

    assert tuple(task.task for task in selected) == ("2", "3")
    assert set().union(*(set(task.trajectory_tools) for task in selected)) == {
        "tool-a",
        "tool-b",
    }
    assert set().union(*(set(task.enabled_tools) for task in selected)) == {
        "tool-a",
        "tool-b",
        "tool-c",
    }


def test_source_selection_swaps_for_reference_redundancy() -> None:
    selected = (
        _source("1", ("tool-a",), ("tool-a",), 1),
        _source("2", ("tool-a",), ("tool-a",), 2),
        _source("3", ("tool-a", "tool-b"), ("tool-a", "tool-b"), 3),
    )
    incoming = _source("4", ("tool-b",), ("tool-b",), 10)

    improved = _improve_redundancy((*selected, incoming), selected)

    assert tuple(task.task for task in improved) == ("4", "2", "3")


def test_dataset_rejects_missing_tool_server_mapping() -> None:
    raw = _case(0)
    tool_servers = cast(dict[str, str], raw["tool_servers"])
    tool_servers["arxiv_search_papers"] = "arxiv"
    tool_servers.pop("calculator_calculate")

    with pytest.raises(ValueError, match="enabled tools have no server mapping"):
        McpAtlasCase.model_validate(raw)


def test_loader_takes_a_deterministic_prefix(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text(json.dumps(_dataset()))

    first = load_mcp_atlas_task(path, limit=3)
    second = load_mcp_atlas_task(path, limit=3)

    assert first.cases == ("task-000", "task-001", "task-002")
    assert first.digest == second.digest


@dataclass
class StaticJudge(JudgeLeg):
    statuses: dict[str, str] = field(default_factory=dict)

    async def complete(self, _system: str, messages: tuple[Message, ...]) -> str:
        content = cast(str, messages[0].content)
        payload = json.loads(content.splitlines()[1])
        claim = cast(str, payload["claim"])
        status = self.statuses.get(claim, "fulfilled")
        return json.dumps({"status": status, "reason": f"{status} evidence"})


async def test_claim_coverage_uses_exact_tristate_weights() -> None:
    raw = _case(0)
    raw["claims"] = ["one", "two", "three", "four"]
    case = McpAtlasCase.model_validate(raw)

    judge = StaticJudge(
        {"one": "fulfilled", "two": "partial", "three": "unfulfilled", "four": "fulfilled"}
    )
    coverage = await ClaimCoverageJudge(judge).grade(case, "candidate")

    assert coverage.score == 0.625


@dataclass
class SuiteTarget:
    judge: JudgeLeg | None

    async def run(self, _case: CapabilityCase) -> TargetResult:
        raise AssertionError("MCP-Atlas must not use the workspace capability target")

    async def preflight_mcp_atlas(self, required_tool_servers: dict[str, str]) -> frozenset[str]:
        return frozenset(required_tool_servers)

    async def run_mcp_atlas(
        self,
        prompt: str,
        _enabled_tools: tuple[str, ...],
        _tool_servers: dict[str, str],
    ) -> TargetResult:
        return TargetResult(CapabilityOutput(f"answer to {prompt}", ()), clean=True)


async def test_suite_reports_threshold_tier_and_catalog_coverage() -> None:
    raw = _dataset()
    first = cast(list[dict[str, object]], raw["cases"])[0]
    cast(list[str], first["enabled_tools"]).append("arxiv_search_papers")
    cast(dict[str, str], first["tool_servers"])["arxiv_search_papers"] = "arxiv"
    dataset = McpAtlasDataset.model_validate(raw)
    target = SuiteTarget(StaticJudge())

    report = await McpAtlasSuite(dataset.cases, "sha256:test").run(target)

    assert report.pass_rate == 1.0
    assert report.benchmark is not None
    assert report.benchmark["claimCoverageThreshold"] == CLAIM_COVERAGE_THRESHOLD
    performance = cast(dict[str, object], report.benchmark["performance"])
    coverage = cast(dict[str, object], report.benchmark["coverage"])
    assert performance["tier"] == "Top"
    assert performance["meanClaimCoverage"] == 1.0
    assert coverage["referenceServerCount"] == 1
    assert coverage["enabledCatalogServerCount"] == 2
    assert "Top tier, mean claim coverage 100% at 75% task threshold" in report.console_summary


@pytest.mark.parametrize(
    ("rate", "tier"),
    ((1.0, "Top"), (0.78, "Top"), (0.779, "Mid"), (0.65, "Mid"), (0.649, "Tail")),
)
def test_performance_tier_has_half_open_boundaries(rate: float, tier: str) -> None:
    assert performance_tier(rate) == tier


@dataclass
class ToolCallingModel:
    calls: list[ModelRequest] = field(default_factory=list)

    async def turn(self, request: ModelRequest) -> Message:
        self.calls.append(request)
        if len(self.calls) == 1:
            return Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="call-1",
                        name="calculator_calculate",
                        input={"expression": "2 + 3"},
                    ),
                ),
            )
        return Message(role="assistant", content=(TextBlock(text="The answer is 5."),))


async def test_atlas_target_preflights_filters_and_calls_the_pinned_sandbox() -> None:
    def respond(request: Request) -> Response:
        if request.url.path == "/list-tools":
            return Response(
                200,
                json=[
                    {
                        "name": "calculator_calculate",
                        "description": None,
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "distractor",
                        "description": "Do something else",
                        "inputSchema": {"type": "object"},
                    },
                ],
            )
        return Response(200, json=[{"type": "text", "text": "5"}])

    model = ToolCallingModel()
    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, model),
            "claude-opus-4-8",
            "system",
            "low",
        )
        await target.preflight({"calculator_calculate": "calculator"})
        result = await target.run(
            "add", ("calculator_calculate",), {"calculator_calculate": "calculator"}
        )

    assert result.clean is True
    assert result.output.response == "The answer is 5."
    assert [call.name for call in result.output.calls] == ["calculator_calculate"]
    assert [schema.name for schema in model.calls[0].tools] == ["calculator_calculate"]
    assert model.calls[0].tools[0].description == ""
    assert model.calls[0].reasoning == "low"


@dataclass
class MetToolCallingModel:
    calls: int = 0

    async def turn(self, _request: ModelRequest) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="call-1",
                        name="met-museum_get-museum-object",
                        input={"objectID": 42, "returnImage": True},
                    ),
                ),
            )
        return Message(role="assistant", content="The object is a sculpture.")


async def test_atlas_target_disables_met_image_payloads() -> None:
    def respond(request: Request) -> Response:
        if request.url.path == "/list-tools":
            return Response(
                200,
                json=[
                    {
                        "name": "met-museum_get-museum-object",
                        "description": "Get a museum object",
                        "inputSchema": {"type": "object"},
                    }
                ],
            )
        return Response(200, json={"objectID": 42, "title": "Sculpture"})

    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, MetToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        result = await target.run(
            "inspect object",
            ("met-museum_get-museum-object",),
            {"met-museum_get-museum-object": "met-museum"},
        )

    expected = {"objectID": 42, "returnImage": False}
    assert result.clean
    assert result.output.calls[0].input == expected


async def test_atlas_target_fails_preflight_when_a_selected_tool_is_absent() -> None:
    def respond(_request: Request) -> Response:
        return Response(200, json=[])

    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, ToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        with pytest.raises(RuntimeError, match="missing 1 selected tools"):
            await target.preflight({"calculator_calculate": "calculator"})


@pytest.mark.parametrize(
    ("tool", "server"),
    (
        ("slack_channels_list", "slack"),
        ("google-workspace_list_events", "google-workspace"),
        ("github_create_issue", "github"),
        ("calculator_new_mutating_tool", "calculator"),
    ),
)
async def test_atlas_target_rejects_unapproved_tools_before_http(tool: str, server: str) -> None:
    def reject(_request: Request) -> Response:
        raise AssertionError("profile denial must precede HTTP")

    async with AsyncClient(transport=MockTransport(reject), base_url="http://atlas.test") as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, ToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        with pytest.raises(ValueError, match=f"{server}:{tool}"):
            await target.preflight({tool: server})
        result = await target.run("unsafe", (tool,), {tool: server})

    assert not result.clean
    assert f"{server}:{tool}" in result.failure_reason


async def test_atlas_target_requires_the_external_endpoint_before_http() -> None:
    def reject(_request: Request) -> Response:
        raise AssertionError("missing external endpoint must precede HTTP")

    async with AsyncClient(
        transport=MockTransport(reject), base_url="http://public.test"
    ) as public_client:
        target = McpAtlasTarget(
            public_client,
            cast(ModelAccess, ToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        with pytest.raises(RuntimeError, match="--mcp-atlas-external-url"):
            await target.preflight({"e2b-server_run_code": "e2b-server"})


async def test_atlas_target_requires_distinct_route_endpoints() -> None:
    async with (
        AsyncClient(base_url="http://atlas.test") as public_client,
        AsyncClient(base_url="http://atlas.test") as external_client,
    ):
        with pytest.raises(ValueError, match="endpoints must be distinct"):
            McpAtlasTarget(
                public_client,
                cast(ModelAccess, ToolCallingModel()),
                "claude-opus-4-8",
                "system",
                "high",
                external_client,
            )


async def test_atlas_target_rejects_a_combined_route_endpoint() -> None:
    def respond(_request: Request) -> Response:
        return Response(
            200,
            json=[
                {
                    "name": name,
                    "description": "Tool",
                    "inputSchema": {"type": "object"},
                }
                for name in ("calculator_calculate", "e2b-server_run_code")
            ],
        )

    async with (
        AsyncClient(
            transport=MockTransport(respond), base_url="http://public.test"
        ) as public_client,
        AsyncClient(
            transport=MockTransport(respond), base_url="http://external.test"
        ) as external_client,
    ):
        target = McpAtlasTarget(
            public_client,
            cast(ModelAccess, ToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
            external_client,
        )
        with pytest.raises(RuntimeError, match="other trust boundary"):
            await target.preflight(
                {
                    "calculator_calculate": "calculator",
                    "e2b-server_run_code": "e2b-server",
                }
            )


@dataclass
class RoutedToolCallingModel:
    calls: int = 0

    async def turn(self, request: ModelRequest) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="public",
                        name="calculator_calculate",
                        input={"expression": "2 + 2"},
                    ),
                    ToolUseBlock(
                        id="external",
                        name="e2b-server_run_code",
                        input={"code": "print(4)"},
                    ),
                ),
            )
        assert {schema.name for schema in request.tools} == {
            "calculator_calculate",
            "e2b-server_run_code",
        }
        return Message(role="assistant", content="Both returned 4.")


async def test_atlas_target_routes_each_tool_to_its_trust_boundary() -> None:
    def respond(own_tool: str):
        def handler(request: Request) -> Response:
            if request.url.path == "/list-tools":
                return Response(
                    200,
                    json=[
                        {
                            "name": own_tool,
                            "description": "Tool",
                            "inputSchema": {"type": "object"},
                        }
                    ],
                )
            return Response(200, json={"value": 4})

        return handler

    async with (
        AsyncClient(
            transport=MockTransport(
                respond(
                    "calculator_calculate",
                )
            ),
            base_url="http://public.test",
        ) as public_client,
        AsyncClient(
            transport=MockTransport(
                respond(
                    "e2b-server_run_code",
                )
            ),
            base_url="http://external.test",
        ) as external_client,
    ):
        target = McpAtlasTarget(
            public_client,
            cast(ModelAccess, RoutedToolCallingModel()),
            "claude-opus-4-8",
            "system",
            "high",
            external_client,
        )
        mappings = {
            "calculator_calculate": "calculator",
            "e2b-server_run_code": "e2b-server",
        }
        assert target._client("calculator") is public_client
        assert target._client("e2b-server") is external_client
        assert await target.preflight(mappings) == frozenset(mappings)
        result = await target.run("calculate twice", tuple(mappings), mappings)

    assert result.clean
    assert [call.name for call in result.output.calls] == list(mappings)


@dataclass
class OutOfPolicyModel:
    calls: int = 0

    async def turn(self, _request: ModelRequest) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=(ToolUseBlock(id="call-1", name="distractor", input={}),),
            )
        return Message(role="assistant", content="No allowed tool was available.")


async def test_atlas_target_rejects_a_tool_outside_the_case_allow_list() -> None:
    def respond(_request: Request) -> Response:
        return Response(
            200,
            json=[
                {
                    "name": name,
                    "description": "Tool",
                    "inputSchema": {"type": "object"},
                }
                for name in ("calculator_calculate", "distractor")
            ],
        )

    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, OutOfPolicyModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        result = await target.run(
            "add", ("calculator_calculate",), {"calculator_calculate": "calculator"}
        )

    assert result.clean
    assert result.output.calls[0].name == "distractor"
    assert result.output.calls[0].is_error
    assert (
        result.output.calls[0].result == "MCP-Atlas tool 'distractor' is not enabled for this case"
    )


@dataclass
class OversizedToolCallModel:
    calls: int = 0

    async def turn(self, _request: ModelRequest) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="call-1",
                        name="calculator_calculate",
                        input={"expression": "1" * MAX_TOOL_REQUEST_BYTES},
                    ),
                ),
            )
        return Message(role="assistant", content="The request was too large.")


async def test_atlas_target_bounds_outgoing_tool_payloads() -> None:
    def respond(_request: Request) -> Response:
        return Response(
            200,
            json=[
                {
                    "name": "calculator_calculate",
                    "description": "Calculate",
                    "inputSchema": {"type": "object"},
                }
            ],
        )

    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, OversizedToolCallModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        result = await target.run(
            "add", ("calculator_calculate",), {"calculator_calculate": "calculator"}
        )

    assert result.clean
    assert result.output.calls[0].is_error
    assert result.output.calls[0].result == (
        f"MCP-Atlas tool 'calculator_calculate' request exceeds {MAX_TOOL_REQUEST_BYTES} bytes"
    )


@dataclass
class FailingModel:
    async def turn(self, _request: ModelRequest) -> Message:
        raise LookupError("model unavailable")


async def test_atlas_target_converts_model_failure_to_terminal_result() -> None:
    def respond(request: Request) -> Response:
        if request.url.path == "/list-tools":
            return Response(
                200,
                json=[
                    {
                        "name": "calculator_calculate",
                        "description": "Add",
                        "inputSchema": {"type": "object"},
                    }
                ],
            )
        return Response(200, json={"reset": True})

    async with AsyncClient(
        transport=MockTransport(respond), base_url="http://atlas.test"
    ) as client:
        target = McpAtlasTarget(
            client,
            cast(ModelAccess, FailingModel()),
            "claude-opus-4-8",
            "system",
            "high",
        )
        result = await target.run(
            "add", ("calculator_calculate",), {"calculator_calculate": "calculator"}
        )

    assert result.clean is False
    assert result.failure_reason == "LookupError: model unavailable"
    assert result.output.tool_errors == ("LookupError: model unavailable",)
