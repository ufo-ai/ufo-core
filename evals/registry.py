"""The eval task registry: the concrete capability suites the runner selects and runs against a
target. `basics` and `tool_calling` are the provider-agnostic core suites and live here; every other
suite lives with the extension whose capability it proves, under `extensions/<name>/evals/`, and is
discovered by path — each such file is a sibling of the extension package (never shipped in its
wheel), exposes a module-level `CASES` tuple, and its file stem names the task."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from selfhost_ext_eval_harness.capability import CapabilityCase
from selfhost_ext_eval_harness.registry import EvalTask, capability_task

from evals import basics, tool_calling

EXTENSIONS_ROOT = Path(__file__).resolve().parent.parent / "extensions"


def _discovered_tasks() -> tuple[EvalTask, ...]:
    tasks: dict[str, EvalTask] = {}
    for path in sorted(EXTENSIONS_ROOT.glob("*/evals/*.py")):
        name = path.stem
        if name in tasks:
            raise ValueError(f"duplicate eval task name {name!r} from {path}")
        spec = importlib.util.spec_from_file_location(f"selfhost_eval_task_{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"eval task {path} is not loadable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases: tuple[CapabilityCase, ...] = module.CASES
        tasks[name] = capability_task(name, cases)
    return tuple(tasks.values())


TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    *_discovered_tasks(),
)
