"""The eval task registry: the concrete capability suites, each a digest-pinned EvalTask the runner
selects and runs against a target."""

from selfhost_ext_eval_harness.registry import EvalTask, capability_task

from evals import basics, tool_calling

TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("tool_calling", tool_calling.CASES),
)
