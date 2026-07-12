"""The shipped eval suites in their stable execution order."""

from __future__ import annotations

from evals import (
    basics,
    browser_nav,
    coding_subagent,
    pdf_build,
    semantic_quality,
    site_build,
    skill_routing,
    tool_calling,
    web_research,
)
from evals.harness.registry import EvalTask, capability_task

TASKS: tuple[EvalTask, ...] = (
    capability_task("basics", basics.CASES),
    capability_task("semantic_quality", semantic_quality.CASES),
    capability_task("skill_routing", skill_routing.CASES),
    capability_task("tool_calling", tool_calling.CASES),
    capability_task("browser_nav", browser_nav.CASES),
    capability_task("coding_subagent", coding_subagent.CASES),
    capability_task("pdf_build", pdf_build.CASES),
    capability_task("site_build", site_build.CASES),
    capability_task("web_research", web_research.CASES),
)
