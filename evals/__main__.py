"""Run the eval suites against the workspace and render a report per task.

`python -m evals --list` lists the tasks and their digests. `python -m evals` drives each case as a
real turn through the agent (a `selfhost serve` must be running to execute the admitted turns),
grades the answer + trajectory, writes an HTML report per task under `--out`, prints the pass line,
and exits non-zero if any suite failed."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from selfhost_ext_eval_harness.harness import EvalReport
from selfhost_ext_eval_harness.registry import run_tasks, selected_tasks
from selfhost_ext_eval_harness.report_html import render_report_html
from selfhost_ext_eval_harness.target import InProcessTarget

from evals.driver import WorkspaceDriver, eval_context, resolve_workspace_and_agent
from evals.registry import TASKS
from selfhost.blob import blob_store_for
from selfhost.config import Config, load_config
from selfhost.db import dispose_db, init_db
from selfhost.schema.records import DEFAULT_AGENT_NAME

DEFAULT_OUT = Path("eval-reports")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", nargs="*", default=())
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--agent", default=DEFAULT_AGENT_NAME)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    names = tuple(args.only)
    if args.list:
        for task in selected_tasks(TASKS, names):
            print(f"{task.name}\t{task.digest}")
        return
    config = load_config()
    reports = asyncio.run(_run(config, names, args.agent))
    args.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for report in reports:
        (args.out / f"{report.name}.html").write_bytes(render_report_html(report))
        passed = sum(1 for case in report.cases if case.passed)
        print(
            f"{report.name} {passed}/{len(report.cases)} "
            f"(rate {report.pass_rate:.0%}) {report.digest}"
        )
        failed = failed or not report.passed
    if failed:
        raise SystemExit(1)


async def _run(config: Config, names: tuple[str, ...], agent_name: str) -> tuple[EvalReport, ...]:
    init_db(config.database.url)
    try:
        workspace_id, agent_id, agent_prompt = await resolve_workspace_and_agent(agent_name)
        blob = blob_store_for(config.blob)
        driver = WorkspaceDriver(workspace_id, agent_id, agent_prompt, blob)
        target = InProcessTarget(
            ctx=eval_context(config, workspace_id, blob),
            agent_id=agent_id,
            conversations=driver,
            outcome=driver,
        )
        return await run_tasks(TASKS, target, names)
    finally:
        await dispose_db()


if __name__ == "__main__":
    main()
