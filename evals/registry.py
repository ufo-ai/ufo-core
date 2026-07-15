"""The shipped eval suites in their stable execution order."""

from __future__ import annotations

from evals import (
    basics,
    browser_nav,
    coding_subagent,
    pdf_build,
    scenario_env,
    scenario_smoke,
    semantic_quality,
    site_build,
    skill_routing,
    tool_calling,
    web_research,
    yc_recall,
    yc_workflows,
)
from evals.harness.registry import EvalTask, capability_task, scenario_task, selected_tasks

DEFAULT_TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("semantic_quality", semantic_quality.CASES),
    capability_task("skill_routing", skill_routing.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("coding_subagent", coding_subagent.CASES),
    capability_task("pdf_build", pdf_build.CASES),
    capability_task("site_build", site_build.CASES),
    capability_task("web_research", web_research.CASES),
    scenario_task("scenario_smoke", scenario_smoke.CASES),
)
TASKS: tuple[EvalTask, ...] = (
    *DEFAULT_TASKS,
    scenario_task("scenario_env", scenario_env.CASES),
    capability_task("yc_recall", yc_recall.CASES),
    capability_task("yc_workflows", yc_workflows.CASES),
)


def selected_run_tasks(names: tuple[str, ...] = ()) -> tuple[EvalTask, ...]:
    return selected_tasks(TASKS if names else DEFAULT_TASKS, names)
