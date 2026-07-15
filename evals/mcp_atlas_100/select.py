from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, TypedDict, cast

from evals.mcp_atlas_100.profile import APPROVED_SERVERS, EXECUTABLE_PROFILE

CASE_COUNT = 100
EXPECTED_SOURCE_TASKS = 500
EXPECTED_SERVERS = 36
EXPECTED_ENABLED_TOOLS = 220
EXPECTED_REFERENCE_TOOLS = 106
EXPECTED_EXECUTABLE_TASKS = 111
EXPECTED_EXECUTABLE_ENABLED_TOOLS = 100
EXPECTED_EXECUTABLE_REFERENCE_TOOLS = 57
SMOKE_TASKS = (
    "68925f01db31fe35560e3eb5",
    "689f4d693e212e8ef3390731",
    "6896416f7b30e5d8ccd7c8f7",
)
SERVER_NAMES = (
    "airtable",
    "alchemy",
    "arxiv",
    "brave-search",
    "calculator",
    "cli-mcp-server",
    "clinicaltrialsgov-mcp-server",
    "context7",
    "ddg-search",
    "desktop-commander",
    "e2b-server",
    "exa",
    "fetch",
    "filesystem",
    "git",
    "github",
    "google-maps",
    "google-workspace",
    "lara-translate",
    "mcp-code-executor",
    "mcp-server-code-runner",
    "memory",
    "met-museum",
    "mongodb",
    "national-parks",
    "notion",
    "open-library",
    "osm-mcp-server",
    "oxylabs",
    "pubmed",
    "slack",
    "twelvedata",
    "weather",
    "weather-data",
    "whois",
    "wikipedia",
)
ENABLED_ONLY_SERVER_NAMES = (
    "anili",
    "balldontlie",
    "f1-mcp-server",
    "rijksmuseum-server",
)
TOOL_SERVER_NAMES = SERVER_NAMES + ENABLED_ONLY_SERVER_NAMES
HARD_SERVERS = frozenset(
    {
        "airtable",
        "cli-mcp-server",
        "desktop-commander",
        "e2b-server",
        "filesystem",
        "git",
        "github",
        "google-workspace",
        "mcp-code-executor",
        "mcp-server-code-runner",
        "memory",
        "mongodb",
        "notion",
        "slack",
    }
)


@dataclass(frozen=True)
class SourceTask:
    task: str
    prompt: str
    enabled_tools: tuple[str, ...]
    claims: tuple[str, ...]
    trajectory_tools: tuple[str, ...]
    trajectory_calls: tuple[str, ...]
    servers: frozenset[str]
    difficulty: int


class ParquetTable(Protocol):
    def to_pylist(self) -> list[dict[str, str]]: ...


class ParquetModule(Protocol):
    def read_table(self, source: Path) -> ParquetTable: ...


class SelectedPayload(TypedDict):
    task: str
    prompt: str
    enabled_tools: list[str]
    claims: list[str]
    trajectory_tools: list[str]
    tool_servers: dict[str, str]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parquet = cast(ParquetModule, import_module("pyarrow.parquet"))
    source_rows = parquet.read_table(args.source).to_pylist()
    tasks = tuple(_source_task(row) for row in source_rows)
    _validate_source(tasks)
    executable_tasks = tuple(
        executable for task in tasks if (executable := _executable_task(task)) is not None
    )
    if len(executable_tasks) != EXPECTED_EXECUTABLE_TASKS:
        raise ValueError(
            f"expected {EXPECTED_EXECUTABLE_TASKS} executable tasks, found {len(executable_tasks)}"
        )
    executable_selected = _select(executable_tasks)
    by_id = {task.task: task for task in executable_selected}
    missing_smoke = sorted(set(SMOKE_TASKS) - by_id.keys())
    if missing_smoke:
        raise ValueError(f"executable selection is missing smoke tasks: {', '.join(missing_smoke)}")
    ordered = (
        *(by_id[task] for task in SMOKE_TASKS),
        *(
            task
            for task in sorted(executable_selected, key=lambda item: item.task)
            if task.task not in SMOKE_TASKS
        ),
    )
    executable_payload = [_payload(task) for task in ordered]
    _validate_executable(executable_selected, executable_payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(executable_payload, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
                "source_tasks": len(tasks),
                "source_servers": len(set().union(*(task.servers for task in tasks))),
                "source_enabled_tools": len(
                    set().union(*(set(task.enabled_tools) for task in tasks))
                ),
                "source_reference_tools": len(
                    set().union(*(set(task.trajectory_tools) for task in tasks))
                ),
                "tasks": len(executable_selected),
                "servers": len(set().union(*(task.servers for task in executable_selected))),
                "enabled_tools": len(
                    set().union(*(set(task.enabled_tools) for task in executable_selected))
                ),
                "reference_tools": len(
                    set().union(*(set(task.trajectory_tools) for task in executable_selected))
                ),
                "reference_calls": sum(len(task.trajectory_calls) for task in executable_selected),
            },
            indent=2,
        )
    )


def _source_task(row: dict[str, str]) -> SourceTask:
    enabled_tools = tuple(
        item if isinstance(item, str) else item["name"] for item in json.loads(row["ENABLED_TOOLS"])
    )
    trajectory = json.loads(row["TRAJECTORY"])
    calls = tuple(
        call["function"]["name"]
        for message in trajectory
        for call in message.get("tool_calls") or ()
    )
    tools = tuple(dict.fromkeys(calls))
    servers = frozenset(_server(tool) for tool in tools)
    hard_calls = sum(_server(tool) in HARD_SERVERS for tool in calls)
    arguments_by_tool: dict[str, set[str]] = {}
    changed_argument_requeries = 0
    for message in trajectory:
        for call in message.get("tool_calls") or ():
            function = call["function"]
            arguments = function["arguments"]
            prior_arguments = arguments_by_tool.setdefault(function["name"], set())
            if prior_arguments and arguments not in prior_arguments:
                changed_argument_requeries += 1
            prior_arguments.add(arguments)
    parallel_calls = sum(max(0, len(message.get("tool_calls") or ()) - 1) for message in trajectory)
    difficulty = (
        100 * len(calls)
        + 50 * max(0, len(servers) - 1)
        + 20 * len(tools)
        + 10 * hard_calls
        + 25 * changed_argument_requeries
        + 100 * parallel_calls
    )
    return SourceTask(
        task=row["TASK"],
        prompt=row["PROMPT"],
        enabled_tools=enabled_tools,
        claims=_claims(row["GTFA_CLAIMS"]),
        trajectory_tools=tools,
        trajectory_calls=calls,
        servers=servers,
        difficulty=difficulty,
    )


def _executable_task(task: SourceTask) -> SourceTask | None:
    reference_mappings = {tool: _server(tool) for tool in task.trajectory_tools}
    if any(
        not EXECUTABLE_PROFILE.permits(tool, server) for tool, server in reference_mappings.items()
    ):
        return None
    enabled_tools = tuple(
        tool for tool in task.enabled_tools if EXECUTABLE_PROFILE.permits(tool, _server(tool))
    )
    if not set(task.trajectory_tools) <= set(enabled_tools):
        return None
    return SourceTask(
        task=task.task,
        prompt=task.prompt,
        enabled_tools=enabled_tools,
        claims=task.claims,
        trajectory_tools=task.trajectory_tools,
        trajectory_calls=task.trajectory_calls,
        servers=task.servers,
        difficulty=task.difficulty,
    )


def _claims(raw: str) -> tuple[str, ...]:
    try:
        claims = ast.literal_eval(raw)
    except SyntaxError:
        escaped: list[str] = []
        quote: str | None = None
        previous_was_escape = False
        for character in raw:
            if quote is None:
                escaped.append(character)
                if character in {"'", '"'}:
                    quote = character
                continue
            if previous_was_escape:
                escaped.append(character)
                previous_was_escape = False
            elif character == "\\":
                escaped.append(character)
                previous_was_escape = True
            elif character == quote:
                escaped.append(character)
                quote = None
            elif character == "\n":
                escaped.append("\\n")
            elif character == "\r":
                escaped.append("\\r")
            elif character == "\t":
                escaped.append("\\t")
            else:
                escaped.append(character)
        claims = ast.literal_eval("".join(escaped))
    if not isinstance(claims, list) or not all(isinstance(claim, str) for claim in claims):
        raise ValueError("GTFA_CLAIMS must decode to a list of strings")
    return tuple(claims)


def _server(tool: str) -> str:
    matches = [name for name in TOOL_SERVER_NAMES if tool.startswith(f"{name}_")]
    if tool.startswith("brave_"):
        matches.append("brave-search")
    if tool.startswith("MongoDB_"):
        matches.append("mongodb")
    unique = set(matches)
    if len(unique) != 1:
        raise ValueError(f"tool {tool!r} maps to {sorted(unique)}")
    return unique.pop()


def _select(tasks: tuple[SourceTask, ...]) -> tuple[SourceTask, ...]:
    all_reference = set().union(*(set(task.trajectory_tools) for task in tasks))
    all_enabled = set().union(*(set(task.enabled_tools) for task in tasks))
    frequencies = Counter(tool for task in tasks for tool in set(task.trajectory_tools))
    selected: list[SourceTask] = []
    covered_reference: set[str] = set()
    covered_enabled: set[str] = set()

    while covered_reference != all_reference:
        task = max(
            (candidate for candidate in tasks if candidate not in selected),
            key=lambda candidate: _reference_key(
                candidate,
                covered_reference,
                covered_enabled,
                frequencies,
            ),
        )
        selected.append(task)
        covered_reference.update(task.trajectory_tools)
        covered_enabled.update(task.enabled_tools)

    while covered_enabled != all_enabled and len(selected) < CASE_COUNT:
        task = max(
            (candidate for candidate in tasks if candidate not in selected),
            key=lambda candidate: (
                len(set(candidate.enabled_tools) - covered_enabled),
                candidate.difficulty,
                -int(candidate.task, 16),
            ),
        )
        selected.append(task)
        covered_reference.update(task.trajectory_tools)
        covered_enabled.update(task.enabled_tools)

    remaining = sorted(
        (candidate for candidate in tasks if candidate not in selected),
        key=lambda candidate: (-candidate.difficulty, candidate.task),
    )
    selected.extend(remaining[: CASE_COUNT - len(selected)])
    return _improve_redundancy(tasks, tuple(selected))


def _improve_redundancy(
    tasks: tuple[SourceTask, ...], selected: tuple[SourceTask, ...]
) -> tuple[SourceTask, ...]:
    selected_set = set(selected)
    reference_counts = Counter(tool for task in selected for tool in set(task.trajectory_tools))
    enabled_counts = Counter(tool for task in selected for tool in set(task.enabled_tools))
    baseline = sum(count >= 2 for count in reference_counts.values())
    candidates: list[tuple[tuple[int, int, int, int], SourceTask, SourceTask]] = []
    for outgoing in selected:
        if any(reference_counts[tool] == 1 for tool in outgoing.trajectory_tools):
            continue
        if any(enabled_counts[tool] == 1 for tool in outgoing.enabled_tools):
            continue
        for incoming in tasks:
            if incoming in selected_set:
                continue
            if len(incoming.trajectory_calls) != len(outgoing.trajectory_calls):
                continue
            replacement_counts = reference_counts.copy()
            replacement_counts.subtract(set(outgoing.trajectory_tools))
            replacement_counts.update(set(incoming.trajectory_tools))
            gain = sum(count >= 2 for count in replacement_counts.values()) - baseline
            if gain <= 0:
                continue
            candidates.append(
                (
                    (
                        gain,
                        incoming.difficulty - outgoing.difficulty,
                        -int(outgoing.task, 16),
                        -int(incoming.task, 16),
                    ),
                    outgoing,
                    incoming,
                )
            )
    if not candidates:
        return selected
    _, outgoing, incoming = max(candidates, key=lambda item: item[0])
    return tuple(incoming if task == outgoing else task for task in selected)


def _reference_key(
    task: SourceTask,
    covered_reference: set[str],
    covered_enabled: set[str],
    frequencies: Counter[str],
) -> tuple[int, int, int, int, int]:
    uncovered = set(task.trajectory_tools) - covered_reference
    rarity = sum(1_000_000 // frequencies[tool] for tool in uncovered)
    return (
        rarity,
        len(uncovered),
        len(set(task.enabled_tools) - covered_enabled),
        task.difficulty,
        -int(task.task, 16),
    )


def _payload(task: SourceTask) -> SelectedPayload:
    tools = sorted(set(task.enabled_tools) | set(task.trajectory_tools))
    return {
        "task": task.task,
        "prompt": task.prompt,
        "enabled_tools": list(task.enabled_tools),
        "claims": list(task.claims),
        "trajectory_tools": list(task.trajectory_tools),
        "tool_servers": {tool: _server(tool) for tool in tools},
    }


def _validate_source(tasks: tuple[SourceTask, ...]) -> None:
    if len(tasks) != EXPECTED_SOURCE_TASKS:
        raise ValueError(f"expected {EXPECTED_SOURCE_TASKS} source tasks, found {len(tasks)}")
    source_enabled = set().union(*(set(task.enabled_tools) for task in tasks))
    source_reference = set().union(*(set(task.trajectory_tools) for task in tasks))
    source_servers = set().union(*(task.servers for task in tasks))
    if len(source_enabled) != EXPECTED_ENABLED_TOOLS:
        raise ValueError("source does not contain the 220-tool enabled catalog")
    if len(source_reference) != EXPECTED_REFERENCE_TOOLS:
        raise ValueError("source does not contain all 106 reference-used tools")
    if len(source_servers) != EXPECTED_SERVERS or source_servers != set(SERVER_NAMES):
        raise ValueError("source does not contain all 36 servers")


def _validate_executable(selected: tuple[SourceTask, ...], payload: list[SelectedPayload]) -> None:
    if len(selected) != CASE_COUNT or len({task.task for task in selected}) != CASE_COUNT:
        raise ValueError("executable selection must contain 100 distinct tasks")
    enabled = set().union(*(set(task.enabled_tools) for task in selected))
    reference = set().union(*(set(task.trajectory_tools) for task in selected))
    servers = set().union(*(task.servers for task in selected))
    if len(enabled) != EXPECTED_EXECUTABLE_ENABLED_TOOLS:
        raise ValueError(
            f"executable selection must expose {EXPECTED_EXECUTABLE_ENABLED_TOOLS} tools"
        )
    if len(reference) != EXPECTED_EXECUTABLE_REFERENCE_TOOLS:
        raise ValueError(
            f"executable selection must cover {EXPECTED_EXECUTABLE_REFERENCE_TOOLS} tools"
        )
    if servers != set(APPROVED_SERVERS):
        raise ValueError("executable selection must cover the approved servers")
    if tuple(item["task"] for item in payload[: len(SMOKE_TASKS)]) != SMOKE_TASKS:
        raise ValueError("executable selection does not start with the smoke tasks")
    by_id = {task.task: task for task in selected}
    for item in payload:
        task = by_id[item["task"]]
        if not set(task.trajectory_tools) <= set(task.enabled_tools):
            raise ValueError(f"executable task {task.task} does not enable every reference tool")
        EXECUTABLE_PROFILE.validate(item["tool_servers"])


if __name__ == "__main__":
    main()
