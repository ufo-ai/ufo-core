"""Render one EvalReport to a self-contained HTML page: a pass/fail header and one card per case
with its reason, its tool trajectory, and the agent's answer."""

from __future__ import annotations

from html import escape

from selfhost_ext_eval_harness.harness import EvalCaseResult, EvalReport

STYLE = """
body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.meta { color: #666; font-size: 0.85rem; margin-bottom: 1.5rem; }
.case { border: 1px solid #ddd; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
.badge { font-weight: 600; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.8rem; }
.pass { background: #e6f4ea; color: #137333; }
.fail { background: #fce8e6; color: #c5221f; }
.reason { color: #444; margin: 0.5rem 0; }
.tools { color: #555; font-size: 0.85rem; }
pre {
  background: #f6f8fa; padding: 0.75rem; border-radius: 4px;
  overflow-x: auto; white-space: pre-wrap;
}
"""


def render_report_html(report: EvalReport) -> bytes:
    passed = sum(1 for case in report.cases if case.passed)
    total = len(report.cases)
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
<span class="badge {_badge(report.passed)}">{_label(report.passed)}</span></h1>
<div class="meta">suite {escape(report.suite)} · {passed}/{total} passed ·
{escape(report.digest)}</div>
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
    return f"""<div class="case">
<div><span class="badge {_badge(case.passed)}">{_label(case.passed)}</span>
<b>{escape(case.name)}</b></div>
<div class="reason">{escape(case.reason)}</div>
<div class="tools">tools: {tools_line}</div>
<pre>{escape(response)}</pre>
</div>"""


def _badge(passed: bool) -> str:
    return "pass" if passed else "fail"


def _label(passed: bool) -> str:
    return "PASS" if passed else "FAIL"
