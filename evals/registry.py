"""The eval task registry: the concrete capability suites, each a digest-pinned EvalTask the runner
selects and runs against a target."""

from selfhost_ext_eval_harness.registry import EvalTask, capability_task

from evals import basics, browser_nav, deliverable, tool_calling, web_research

TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task("web_research", web_research.CASES),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("deliverable", deliverable.CASES),
)
