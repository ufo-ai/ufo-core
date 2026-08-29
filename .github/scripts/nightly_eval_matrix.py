"""Plan the nightly eval sweep's shards from the suite registry.

`--plan` prints shard labels, `--models` prints target models, and `--jobs` prints their valid
combinations. `--write LABEL --dir DIR` writes that shard's template
`ufo.toml` and its one-block `evals.stack` matrix. Both derive from `evals.registry.TASKS`, so a
suite added, renamed, or newly bound to a pack moves through the sweep without a hand-kept list.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import tomli_w

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.harness.registry import EvalTask
from evals.registry import TASKS
from evals.stack import (
    APP_PAGE_SUITES,
    APP_SUITES,
    CREATION_SUITES,
    DOCKER_BACKEND,
    HOMEPAGE_SUITES,
)
from ufo.config import DEFAULT_AUTO_MODEL
from ufo.schema.records import DEFAULT_REASONING_EFFORT, ReasoningEffort

CONCURRENCY = 4
# A weight unit measured at roughly a minute of wall clock, so a shard of 45 runs under an
# hour and stays a fraction of the six hours GitHub gives a job. Every shard also pays the
# checkout, sync, cargo build, and serve boot, so a smaller weight buys wall clock with
# runner minutes.
SHARD_WEIGHT = 45
DATABASE_URL = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"
PUBLIC_BASE_URL = "http://evals.invalid"
SMOKE_PROBE = "basics"
SMOKE_SUITES = (
    SMOKE_PROBE,
    "semantic_quality",
    "scenario_smoke",
    "ab_reversal",
    "ufo-app-qa-replay",
)
UNSAFE_LABEL_CHARS = re.compile(r"[^A-Za-z0-9.-]+")


@dataclass(frozen=True)
class NightlyModel:
    """One target model and its artifact-safe label."""

    id: str
    label: str


NIGHTLY_MODELS = (
    NightlyModel("claude-opus-5", "claude-opus-5"),
    NightlyModel("z-ai/glm-5.3-flash", "z-ai-glm-5.3-flash"),
)
FIXED_MODEL_LABEL = "fixed-model"


@dataclass(frozen=True)
class Arm:
    """One pack a shard can run under, with the knobs that pack's extensions register. A knob
    naming an extension the pack does not ship fails `serve` at boot, so the arms differ."""

    pack: str
    knobs: dict[str, dict[str, str]]


ARMS = (
    Arm("assistant_eval", {"research": {"search_provider": "perplexity"}}),
    Arm(
        "assistant_hosted",
        {
            "research": {"search_provider": "perplexity"},
            "browser": {"cdp_provider": "browserbase"},
            "memory": {"index_backend": "turbopuffer"},
        },
    ),
)


@dataclass(frozen=True)
class Shard:
    label: str
    pack: str
    agent: str | None
    suites: tuple[str, ...]


@dataclass(frozen=True)
class SweepJob:
    """One shard execution and the target model its report must record."""

    shard: Shard
    config_model: NightlyModel
    expected_model: str | None


def _label_token(value: str) -> str:
    """Keep a matrix label safe when it is embedded in a GitHub artifact name."""
    token = UNSAFE_LABEL_CHARS.sub("-", value.replace("_", "-")).strip("-")
    if not token:
        raise ValueError(f"cannot make an artifact-safe label from {value!r}")
    return token


def plan(smoke: bool) -> tuple[Shard, ...]:
    """Every registered suite, split into shards that each fit inside one job's ceiling."""
    tasks = TASKS
    if smoke:
        registered = {task.name for task in TASKS}
        unknown = tuple(name for name in SMOKE_SUITES if name not in registered)
        if unknown:
            raise SystemExit(f"smoke names unregistered suites: {', '.join(unknown)}")
        tasks = tuple(task for task in TASKS if task.name in SMOKE_SUITES)
    probe = next(task for task in TASKS if task.name == SMOKE_PROBE)
    shards: list[Shard] = []
    for arm in ARMS:
        agents = (None, *sorted({task.agent for task in tasks if task.agent is not None}))
        for agent in agents:
            carried = tuple(task for task in tasks if _arm_of(task) is arm and task.agent == agent)
            if smoke and agent is None and probe not in carried:
                carried = (probe, *carried)
            if not carried:
                continue
            stem = f"nightly-{_label_token(arm.pack)}"
            if agent is not None:
                stem += f"-{_label_token(agent)}"
            app_tasks = tuple(task for task in carried if task.name in APP_SUITES)
            creation_tasks = tuple(task for task in carried if task.name in CREATION_SUITES)
            homepage_tasks = tuple(task for task in carried if task.name in HOMEPAGE_SUITES)
            other_tasks = tuple(task for task in carried if task.name not in APP_PAGE_SUITES)
            groups = (
                *_balance(app_tasks),
                *_balance(creation_tasks),
                *_balance(homepage_tasks),
                *_balance(other_tasks),
            )
            for index, group in enumerate(groups, start=1):
                label = stem if len(groups) == 1 else f"{stem}-{index}"
                shards.append(Shard(label, arm.pack, agent, tuple(task.name for task in group)))
    return tuple(shards)


def sweep_jobs(smoke: bool) -> tuple[SweepJob, ...]:
    """Pair auto-model shards with both targets and fixed-model shards with one execution."""
    jobs = []
    for shard in plan(smoke):
        fixed_model = not APP_SUITES.isdisjoint(shard.suites) or (
            shard.agent is not None and shard.agent.startswith("profile:")
        )
        models = NIGHTLY_MODELS[:1] if fixed_model else NIGHTLY_MODELS
        jobs.extend(
            SweepJob(
                shard=shard,
                config_model=model,
                expected_model=None if fixed_model else model.id,
            )
            for model in models
        )
    return tuple(jobs)


def _arm_of(task: EvalTask) -> Arm:
    if not task.packs:
        return ARMS[0]
    for arm in ARMS:
        if arm.pack in task.packs:
            return arm
    raise SystemExit(f"suite {task.name!r} requires a pack no sweep arm runs: {task.packs}")


def _weight(task: EvalTask) -> int:
    """A suite's share of a shard's wall clock: an exclusive suite runs its cases one at a time
    whatever the concurrency, so it costs a slot each."""
    return len(task.cases) if task.exclusive else math.ceil(len(task.cases) / CONCURRENCY)


def _balance(tasks: tuple[EvalTask, ...]) -> tuple[tuple[EvalTask, ...], ...]:
    count = max(1, math.ceil(sum(_weight(task) for task in tasks) / SHARD_WEIGHT))
    groups: list[list[EvalTask]] = [[] for _ in range(count)]
    loads = [0] * count
    for task in sorted(tasks, key=_weight, reverse=True):
        lightest = loads.index(min(loads))
        groups[lightest].append(task)
        loads[lightest] += _weight(task)
    order = {task.name: index for index, task in enumerate(TASKS)}
    return tuple(
        tuple(sorted(group, key=lambda task: order[task.name])) for group in groups if group
    )


def write(shard: Shard, directory: Path, model: str, reasoning: ReasoningEffort) -> None:
    arm = next(arm for arm in ARMS if arm.pack == shard.pack)
    app_suite_shard = not APP_SUITES.isdisjoint(shard.suites)
    app_page_shard = not APP_PAGE_SUITES.isdisjoint(shard.suites)
    reasoning_override = (
        None if reasoning == DEFAULT_REASONING_EFFORT or app_suite_shard else reasoning
    )
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "ufo.toml"
    config.write_text(
        tomli_w.dumps(
            {
                "database": {"url": DATABASE_URL},
                "blob": {"backend": "filesystem", "root": "./blobs"},
                "connect": {"public_base_url": PUBLIC_BASE_URL},
                "models": {"auto_model": model},
                "pack": {"name": shard.pack},
                **({"sandbox": {"backend": DOCKER_BACKEND}} if app_page_shard else {}),
                **arm.knobs,
            }
        )
    )
    (directory / "matrix.toml").write_text(
        tomli_w.dumps(
            {
                "run": [
                    {
                        "label": shard.label,
                        "config": str(config),
                        **({} if reasoning_override is None else {"reasoning": reasoning_override}),
                        "args": [
                            "--concurrency",
                            str(CONCURRENCY),
                            *(["--agent", shard.agent] if shard.agent is not None else []),
                            "--only",
                            *shard.suites,
                        ],
                    }
                ]
            }
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nightly_eval_matrix.py")
    parser.add_argument(
        "--smoke",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="plan the cheap proving subset only",
    )
    parser.add_argument("--plan", action="store_true", help="print the job matrix as JSON")
    parser.add_argument("--jobs", action="store_true", help="print shard and model jobs as JSON")
    parser.add_argument("--models", action="store_true", help="print the nightly models as JSON")
    parser.add_argument(
        "--model", default=DEFAULT_AUTO_MODEL, help="concrete model that agents named auto use"
    )
    parser.add_argument(
        "--reasoning",
        choices=("auto", "off", "low", "medium", "high"),
        default=DEFAULT_REASONING_EFFORT,
        help="reasoning effort for the evaluated agent",
    )
    parser.add_argument("--write", metavar="LABEL", help="write one shard's stack input")
    parser.add_argument("--dir", type=Path, help="directory the shard's input is written to")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.models:
        print(json.dumps([{"id": model.id, "label": model.label} for model in NIGHTLY_MODELS]))
        return
    if args.jobs:
        print(
            json.dumps(
                [
                    {
                        "label": job.shard.label,
                        "model": job.config_model.id,
                        "target": job.expected_model or "fixed model",
                        "artifact": (
                            job.config_model.label
                            if job.expected_model is not None
                            else FIXED_MODEL_LABEL
                        ),
                    }
                    for job in sweep_jobs(args.smoke)
                ]
            )
        )
        return
    shards = plan(args.smoke)
    if args.plan:
        print(json.dumps([shard.label for shard in shards]))
        return
    if args.write is None or args.dir is None:
        parser.error("pass --plan, or --write LABEL --dir DIR")
    named = next((shard for shard in shards if shard.label == args.write), None)
    if named is None:
        parser.error(f"no shard is labelled {args.write!r}")
    write(named, args.dir, args.model, args.reasoning)


if __name__ == "__main__":
    main()
