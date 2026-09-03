"""The shipped eval suites in their stable execution order."""

from __future__ import annotations

from evals.harness.registry import (
    EvalTask,
    arc_task,
    capability_task,
    rewrapped,
    scenario_task,
    selected_tasks,
)
from evals.scenario_env import frontier, lookups, multistep, restraint, writes
from evals.skill_authoring.catalog import CASES as SKILL_AUTHORING_CASES
from evals.skill_authoring.runner import skill_authoring_task
from evals.skill_loading.catalog import CASES as SKILL_LOADING_CASES
from evals.skill_loading.catalog import SKILL_LOADING_PACKS
from evals.skill_loading.member import CASES as SKILL_MEMBER_CASES
from evals.skill_loading.runner import skill_loading_task
from evals.skill_selection.runner import skill_selection_task
from evals.suites import (
    ab_reversal,
    app_builder,
    app_home_change,
    authority_handoff,
    bash_waiting,
    basics,
    billing_actions,
    browser_nav,
    business_goal,
    closing_message,
    code_review,
    coding_caveat_completeness,
    coding_subagent,
    completeness_inventory,
    connector_connections,
    connector_refs,
    credential_handoff,
    dead_route_repeat,
    document_read,
    document_visual,
    fanout,
    github_connections,
    handback,
    language_drift,
    low_stakes_default,
    member_add_notify,
    monitor_arm,
    new_application,
    object_tools,
    onboarding_help,
    pdf_build,
    problem_report,
    rebuild_actions,
    red_after_green,
    repeated_input_coherence,
    report_digest,
    response_formatting,
    response_register,
    sandbox_cli,
    scenario_smoke,
    scope_preservation,
    semantic_quality,
    site_build,
    skill_routing,
    skill_tail_search,
    slack_ladder_coherence,
    slack_message_block,
    surface_setup,
    tool_activity,
    tool_calling,
    ufo_app_bench,
    ufo_app_qa_replay,
    web_research,
    writing_subagent,
)
from evals.suites.asd_writing import asd_writing_task
from evals.suites.skill_gtm import CASES as SKILL_GTM_CASES
from evals.suites.slack_silence import CASES as SLACK_SILENCE_CASES
from evals.suites.slack_silence import slack_silence_task
from evals.suites.wiki_generation import wiki_generation_task

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
    capability_task("language_drift", language_drift.CASES),
    capability_task("closing_message", closing_message.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    capability_task(
        "slack_message_block", slack_message_block.CASES, judge_model=SEMANTIC_JUDGE_MODEL
    ),
    capability_task("skill_routing", skill_routing.CASES),
    capability_task("skill_tail_search", skill_tail_search.CASES, serial=True),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task("sandbox_cli", sandbox_cli.CASES),
    capability_task(
        "bash_waiting", bash_waiting.CASES, wait_seconds=bash_waiting.WORKFLOW_WAIT_SECONDS
    ),
    capability_task("problem_report", problem_report.CASES),
    capability_task(
        "authority_handoff",
        authority_handoff.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("object_tools", object_tools.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    scenario_task(
        "object_tools_flows",
        object_tools.SCENARIOS,
        simulator_model=SCENARIO_SIMULATOR_MODEL,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("coding_subagent", coding_subagent.CASES),
    capability_task("low_stakes_default", low_stakes_default.CASES),
    capability_task("member_add_notify", member_add_notify.CASES, serial=True),
    capability_task("dead_route_repeat", dead_route_repeat.CASES),
    capability_task("pdf_build", pdf_build.CASES),
    capability_task("site_build", site_build.CASES),
    capability_task("web_research", web_research.CASES),
    scenario_task("scenario_smoke", scenario_smoke.CASES, simulator_model=SCENARIO_SIMULATOR_MODEL),
)
TASKS: tuple[EvalTask, ...] = (
    *DEFAULT_TASKS,
    capability_task(
        "coding_profile",
        coding_subagent.PROFILE_CASES,
        agent="profile:coding",
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task(
        "coding_caveat_completeness",
        coding_caveat_completeness.CASES,
        agent="profile:coding",
    ),
    capability_task("scope_preservation", scope_preservation.CASES),
    capability_task("completeness_inventory", completeness_inventory.CASES),
    capability_task(
        "repeated_input_coherence",
        repeated_input_coherence.CASES,
        nightly=False,
    ),
    capability_task(
        "slack_ladder_coherence",
        slack_ladder_coherence.CASES,
        nightly=False,
    ),
    tool_activity.tool_activity_task(),
    capability_task(
        "code_review",
        code_review.CASES,
        serial=True,
        packs=("assistant", "assistant_eval", "assistant_hosted"),
        agent="code",
    ),
    rewrapped(
        capability_task(
            "ufo-app-bench",
            ufo_app_bench.CASES,
            judge_model=VISUAL_JUDGE_MODEL,
            wait_seconds=ufo_app_bench.WORKFLOW_WAIT_SECONDS,
        ),
        ufo_app_bench._scored_task,
    ),
    ufo_app_qa_replay.TASK,
    capability_task(
        "ufo-app-copy",
        ufo_app_bench.COPY_CASES,
        wait_seconds=ufo_app_bench.WORKFLOW_WAIT_SECONDS,
    ),
    capability_task("document_read", document_read.CASES),
    capability_task(
        "app_home_change",
        app_home_change.CASES,
        agent=app_home_change.APP_SLUG,
        wait_seconds=app_home_change.WORKFLOW_WAIT_SECONDS,
    ),
    capability_task(
        response_register.DELEGATED_TASK,
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
    capability_task("report_digest", report_digest.CASES, judge_model=SEMANTIC_JUDGE_MODEL),
    asd_writing_task(SEMANTIC_JUDGE_MODEL),
    wiki_generation_task(SEMANTIC_JUDGE_MODEL),
    skill_loading_task(SKILL_LOADING_CASES, packs=SKILL_LOADING_PACKS),
    skill_loading_task(SKILL_MEMBER_CASES, name="skill_loading_member"),
    skill_selection_task(),
    scenario_task(
        "new_application",
        new_application.SCENARIOS,
        simulator_model=SCENARIO_SIMULATOR_MODEL,
        judge_model=SEMANTIC_JUDGE_MODEL,
        wait_seconds=ufo_app_bench.WORKFLOW_WAIT_SECONDS,
    ),
    skill_authoring_task("skill_authoring", SKILL_AUTHORING_CASES),
    skill_authoring_task("skill_gtm", SKILL_GTM_CASES),
    capability_task(
        "github_connections",
        github_connections.CASES,
        serial=True,
        judge_model=SEMANTIC_JUDGE_MODEL,
    ),
    capability_task("billing_actions", billing_actions.CASES, packs=billing_actions.BILLING_PACKS),
    capability_task("rebuild_actions", rebuild_actions.CASES, serial=True),
    capability_task(
        "credential_handoff",
        credential_handoff.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
        serial=True,
    ),
    capability_task(
        "surface_setup",
        surface_setup.CASES,
        packs=surface_setup.SURFACE_SETUP_PACKS,
        judge_model=SEMANTIC_JUDGE_MODEL,
        serial=True,
    ),
    capability_task("monitor_arm", monitor_arm.CASES, packs=monitor_arm.MONITOR_PACKS),
    capability_task("app_builder", app_builder.CASES),
    capability_task(
        "business_goal",
        business_goal.CASES,
        judge_model=SEMANTIC_JUDGE_MODEL,
        packs=business_goal.BUSINESS_GOAL_PACKS,
    ),
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
        wait_seconds=document_visual.WORKFLOW_WAIT_SECONDS,
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
