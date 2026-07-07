"""Render one EvalReport to a self-contained HTML page: a pass/fail header and one card per case
with its reason, its tool trajectory, and the agent's answer. An infra-excluded case (its external
service was down) is neither pass nor fail — it renders a distinct EXCLUDED badge and is tallied
separately from the passed/scored count, mirroring how the report scores it."""

from __future__ import annotations

from html import escape

from ufo_ext_eval_harness.harness import EvalCaseResult, EvalReport

STYLE = """
body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.meta { color: #666; font-size: 0.85rem; margin-bottom: 1.5rem; }
.case { border: 1px solid #ddd; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
.badge { font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.8rem; }
.pass { background: #e6f4ea; color: #137333; }
.fail { background: #fce8e6; color: #c5221f; }
.excluded { background: #fef7e0; color: #b06000; }
.reason { color: #444; margin: 0.5rem 0; }
.tools { color: #555; font-size: 0.85rem; }
pre {
  background: #f6f8fa; padding: 0.75rem; border-radius: 4px;
  overflow-x: auto; white-space: pre-wrap;
}
"""


def render_report_html(report: EvalReport) -> bytes:
    scored = report.scored
    passed = sum(1 for case in scored if case.passed)
    header_class, header_label = _verdict(report.passed, False)
    rows = "\n".join(_case_section(case) for case in report.cases)
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{escape(report.name)} — eval report</title>
<style>{STYLE}</style>
</head>
<body>
<h1>{escape(report.name)}
<span class="badge {header_class}">{header_label}</span></h1>
<div class="meta">suite {escape(report.suite)} · {passed}/{len(scored)} passed ·
{report.excluded_count} excluded · {escape(report.digest)}</div>
{rows}
</body>
</html>
"""
    return doc.encode()


def _case_section(case: EvalCaseResult) -> str:
    response = str(case.evidence.get("response", ""))
    raw_tools = case.evidence.get("tools", [])
    tools = raw_tools if isinstance(raw_tools, list) else []
    tools_line = ", ".join(escape(str(tool)) for tool in tools) if tools else "—"
    badge_class, label = _verdict(case.passed, case.excluded)
    return f"""<div class="case">
<div><span class="badge {badge_class}">{label}</span>
<b>{escape(case.name)}</b></div>
<div class="reason">{escape(case.reason)}</div>
<div class="tools">tools: {tools_line}</div>
<pre>{escape(response)}</pre>
</div>"""


def _verdict(passed: bool, excluded: bool) -> tuple[str, str]:
    if excluded:
        return "excluded", "EXCLUDED"
    return ("pass", "PASS") if passed else ("fail", "FAIL")
