"""The shipped eval suites in their stable execution order."""

from __future__ import annotations

from evals import (
    basics,
    browser_nav,
    closing_message,
    coding_subagent,
    connector_refs,
    cos_workflows,
    document_visual,
    memory_hygiene,
    object_tools,
    pdf_build,
    response_register,
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
from evals.scenario_env import frontier, lookups, multistep, restraint, writes
from evals.skill_loading.catalog import CASES as SKILL_LOADING_CASES
from evals.skill_loading.runner import skill_loading_task

SEMANTIC_JUDGE_MODEL = "gpt-5.4-mini"
SCENARIO_SIMULATOR_MODEL = "claude-haiku-4-5"
VISUAL_JUDGE_MODEL = "claude-sonnet-4-6"

DEFAULT_TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("semantic_quality", semantic_quality.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task("response_register", response_register.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task("closing_message", closing_message.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task("skill_routing", skill_routing.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task("object_tools", object_tools.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    scenario_task(
        "object_tools_flows",
        object_tools.SCENARIOS,
        simulator_model=SCENARIO_SIMULATOR_MODEL,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("coding_subagent", coding_subagent.CASES),
    capability_task("pdf_build", pdf_build.CASES),
    capability_task("site_build", site_build.CASES),
    capability_task("web_research", web_research.CASES),
    scenario_task("scenario_smoke", scenario_smoke.CASES, simulator_model=SCENARIO_SIMULATOR_MODEL),
)
TASKS: tuple[EvalTask, ...] = (
    *DEFAULT_TASKS,
    scenario_task(
        "scenario_env",
        (
            *lookups.CASES,
            *writes.CASES,
            *multistep.CASES,
            *restraint.CASES,
            *frontier.CONSTRAINT_CASES,
            *frontier.CASES,
        ),
        simulator_model=SCENARIO_SIMULATOR_MODEL,
    ),
    skill_loading_task(SKILL_LOADING_CASES),
    capability_task("connector_refs", connector_refs.CASES),
    capability_task(
        "document_visual",
        document_visual.CASES,
        judge_model=VISUAL_JUDGE_MODEL,
        judge_max_tokens=16_000,
        judge_reasoning="high",
    ),
    scenario_task("memory_hygiene", memory_hygiene.CASES, simulator_model=SCENARIO_SIMULATOR_MODEL),
    capability_task("yc_recall", yc_recall.CASES),
    capability_task("yc_workflows", yc_workflows.CASES),
    capability_task("cos_workflows", cos_workflows.CASES),
)


def selected_run_tasks(names: tuple[str, ...] = ()) -> tuple[EvalTask, ...]:
    return selected_tasks(TASKS if names else DEFAULT_TASKS, names)
