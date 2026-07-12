"""Run the eval suites against the workspace and render a report per task.

`python -m evals --list` lists the tasks and their digests. `python -m evals` drives each case as a
real turn through the agent (a `ufoctl serve` must be running to execute the admitted turns),
grades the answer + trajectory, writes an HTML report per task under `--out`, prints the pass line,
and exits non-zero if any suite failed. Cases create durable conversations and may write memory or
artifacts, so run them in a disposable target workspace selected with `--workspace`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet

from evals.driver import WorkspaceDriver, eval_context, resolve_workspace_and_agent
from evals.harness.harness import EvalReport
from evals.harness.judge import JUDGE_REVISION, ModelJudge
from evals.harness.registry import selected_tasks
from evals.harness.report_html import render_report_html
from evals.harness.target import InProcessTarget
from evals.registry import TASKS, selected_run_tasks
from ufo.blob import blob_store_for
from ufo.config import Config, load_config
from ufo.credentials import CredentialStore
from ufo.db import dispose_db, init_db
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.workspace import init_workspace_credentials, ws

DEFAULT_OUT = Path("eval-reports")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", nargs="*", default=())
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--agent", default=DEFAULT_AGENT_NAME)
    parser.add_argument(
        "--workspace",
        type=UUID,
        help="target workspace UUID; eval cases mutate its conversations, memory, and artifacts",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    names = tuple(args.only)
    if args.list:
        for task in selected_tasks(TASKS, names):
            print(f"{task.name}\t{task.digest}")
        return
    config = load_config()
    reports = asyncio.run(_run(config, names, args.agent, args.workspace))
    args.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for report in reports:
        (args.out / f"{report.name}.html").write_bytes(render_report_html(report))
        passed = sum(1 for case in report.scored if case.passed)
        print(
            f"{report.name} {passed}/{len(report.scored)} passed, "
            f"{report.excluded_count} excluded (rate {report.pass_rate:.0%}) {report.digest}"
        )
        failed = failed or not report.passed
    if failed:
        raise SystemExit(1)


async def _run(
    config: Config,
    names: tuple[str, ...],
    agent_name: str,
    workspace_id: UUID | None = None,
) -> tuple[EvalReport, ...]:
    init_db(config.database.url)
    key = os.environ.get(config.credentials.key_env)
    init_workspace_credentials(CredentialStore(fernet=Fernet(key.encode())) if key else None)
    try:
        workspace_id, agent_id, agent_prompt, agent_model = await resolve_workspace_and_agent(
            agent_name, workspace_id
        )
        blob = blob_store_for(config.blob)
        driver = WorkspaceDriver(workspace_id, agent_id, agent_prompt, blob)
        ctx = eval_context(config, workspace_id, blob)
        if ctx.model is None:
            raise RuntimeError("eval context requires model access")
        target = InProcessTarget(
            ctx=ctx,
            agent_id=agent_id,
            conversations=driver,
            outcome=driver,
            judge=ModelJudge(ctx.model),
            blob=blob,
        )
        with ws(workspace_id):
            reports = tuple([await task.run(target) for task in selected_run_tasks(names)])
        return tuple(
            replace(
                report,
                target_model=agent_model,
                judge_model=ctx.model.model,
                judge_revision=JUDGE_REVISION,
            )
            for report in reports
        )
    finally:
        init_workspace_credentials(None)
        await dispose_db()


if __name__ == "__main__":
    main()
