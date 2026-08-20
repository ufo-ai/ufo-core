"""The shipped eval suites in their stable execution order."""

from __future__ import annotations

from evals import (
    ab_reversal,
    app_builder,
    authority_handoff,
    basics,
    browser_nav,
    closing_message,
    coding_subagent,
    connector_connections,
    connector_refs,
    credential_handoff,
    daily_brief,
    dead_route_repeat,
    document_visual,
    fanout,
    first_run,
    github_connections,
    handback,
    low_stakes_default,
    new_application,
    object_tools,
    onboarding_help,
    pdf_build,
    red_after_green,
    response_formatting,
    response_register,
    scenario_smoke,
    semantic_quality,
    site_build,
    skill_routing,
    slack_message_block,
    tool_calling,
    web_research,
    writing_subagent,
)
from evals.harness.registry import (
    EvalTask,
    arc_task,
    capability_task,
    scenario_task,
    selected_tasks,
)
from evals.scenario_env import frontier, lookups, multistep, restraint, writes
from evals.skill_authoring.catalog import CASES as SKILL_AUTHORING_CASES
from evals.skill_authoring.runner import skill_authoring_task
from evals.skill_gtm import CASES as SKILL_GTM_CASES
from evals.skill_loading.catalog import CASES as SKILL_LOADING_CASES
from evals.skill_loading.member import CASES as SKILL_MEMBER_CASES
from evals.skill_loading.runner import skill_loading_task
from evals.skill_selection.runner import skill_selection_task
from evals.slack_silence import CASES as SLACK_SILENCE_CASES
from evals.slack_silence import slack_silence_task

SEMANTIC_JUDGE_MODEL = "gpt-5.4-mini"
SCENARIO_SIMULATOR_MODEL = "claude-haiku-4-5"
VISUAL_JUDGE_MODEL = "claude-sonnet-5"

DEFAULT_TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("semantic_quality", semantic_quality.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task("response_register", response_register.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    slack_silence_task(SLACK_SILENCE_CASES),
    capability_task(
        "response_formatting", response_formatting.CASES, judge_model=SEMANTIC_JUDGE_MODEL
    ),
    capability_task("closing_message", closing_message.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task(
        "slack_message_block", slack_message_block.CASES, judge_model=SEMANTIC_JUDGE_MODEL
    ),
    capability_task("skill_routing", skill_routing.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task(
        "authority_handoff",
        authority_handoff.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("object_tools", object_tools.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task("daily_brief", daily_brief.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    scenario_task(
        "object_tools_flows",
        object_tools.SCENARIOS,
        simulator_model=SCENARIO_SIMULATOR_MODEL,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("coding_subagent", coding_subagent.CASES),
    capability_task("low_stakes_default", low_stakes_default.CASES),
    capability_task("dead_route_repeat", dead_route_repeat.CASES),
    capability_task("pdf_build", pdf_build.CASES),
    capability_task("site_build", site_build.CASES),
    capability_task("web_research", web_research.CASES),
    scenario_task("scenario_smoke", scenario_smoke.CASES, simulator_model=SCENARIO_SIMULATOR_MODEL),
)
TASKS: tuple[EvalTask, ...] = (
    *DEFAULT_TASKS,
    capability_task(
        "delegated_response_register",
        response_register.DELEGATED_CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
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
    capability_task("connector_connections", connector_connections.CASES, serial=True),
    skill_loading_task((*SKILL_LOADING_CASES, *SKILL_MEMBER_CASES)),
    skill_selection_task(),
    scenario_task(
        "new_application",
        new_application.SCENARIOS,
        simulator_model=SCENARIO_SIMULATOR_MODEL,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    skill_authoring_task("skill_authoring", SKILL_AUTHORING_CASES),
    skill_authoring_task("skill_gtm", SKILL_GTM_CASES),
    capability_task("github_connections", github_connections.CASES, serial=True),
    capability_task(
        "credential_handoff",
        credential_handoff.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
        serial=True,
    ),
    capability_task("app_builder", app_builder.CASES),
    capability_task("first_run", first_run.CASES, packs=first_run.FIRST_RUN_PACKS),
    capability_task("connector_refs", connector_refs.CASES),
    capability_task(
        "writing_subagent",
        writing_subagent.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task(
        "writing_launch_thread",
        writing_subagent.LAUNCH_CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task(
        "document_visual",
        document_visual.CASES,
        judge_model=VISUAL_JUDGE_MODEL,
        judge_max_tokens=16_000,
        judge_reasoning="high",
    ),
    capability_task(
        "onboarding_help",
        onboarding_help.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
        serial=True,
        packs=onboarding_help.ONBOARDING_HELP_PACKS,
    ),
    arc_task("handback", handback.CASES),
    arc_task("fanout", fanout.CASES),
    arc_task("ab_reversal", ab_reversal.CASES),
    arc_task("red_after_green", red_after_green.CASES),
)


def selected_run_tasks(names: tuple[str, ...] = ()) -> tuple[EvalTask, ...]:
    return selected_tasks(TASKS if names else DEFAULT_TASKS, names)
