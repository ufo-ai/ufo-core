"""Run eval suites, browse recorded runs, or share a two-run comparison.

`python -m evals` drives capability cases as real turns and MCP-Atlas cases through their pinned
sandbox, grades each answer and trajectory, and records one immutable run under `--out`. `--view`
opens the offline archive; `--share CURRENT [BASELINE]` publishes only those runs behind an
expiring S3 URL. Capability cases create durable conversations and may write memory or artifacts,
so target a disposable workspace with `--workspace`."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import webbrowser
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from dbos import DBOSClient
from httpx import AsyncClient, Timeout
from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.driver import WorkspaceDriver, resolve_workspace_and_agent
from evals.dsqa_100.runner import (
    DSQA_JUDGE_MODEL,
    DSQA100Run,
    load_dsqa_100,
)
from evals.harness.harness import EvalReport, digest_payload
from evals.harness.judge import ModelJudge
from evals.harness.registry import EvalTask, selected_tasks
from evals.harness.target import InProcessTarget
from evals.harness.viewer import (
    MAX_SHARE_EXPIRY_SECONDS,
    EvalRun,
    S3ViewerShare,
    load_runs,
    record_run,
    render_viewer,
    write_viewer,
)
from evals.mcp_atlas_100.runner import load_mcp_atlas_task
from evals.mcp_atlas_100.target import McpAtlasTarget
from evals.memory_100.runner import Memory100Run, load_memory_100
from evals.registry import TASKS, selected_run_tasks
from evals.turn_logs import TurnLogCollector
from ufo.blob import blob_store_for
from ufo.config import Config, config_path, load_config
from ufo.credentials import CredentialStore
from ufo.db import dispose_db, init_db
from ufo.ext.context import context_for
from ufo.ext.loader import load_manifests, skill_registry
from ufo.governance import prompt_digest
from ufo.loop.prompts.render import render_system_prompt
from ufo.models.registry import model_registry
from ufo.schema.records import DEFAULT_AGENT_NAME, ReasoningEffort
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.workspace import init_workspace_credentials, ws

DEFAULT_OUT = Path("eval-reports")
EVAL_SHARE_BUCKET_ENV = "UFO_EVAL_SHARE_BUCKET"
MCP_ATLAS_URL_ENV = "MCP_ATLAS_URL"
MCP_ATLAS_EXTERNAL_URL_ENV = "MCP_ATLAS_EXTERNAL_URL"
MCP_ATLAS_TIMEOUT_SECONDS = 1_800.0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="list suites and their digests")
    action.add_argument("--view", action="store_true", help="open the local run archive")
    action.add_argument(
        "--share",
        nargs="+",
        metavar="RUN_ID",
        help="publish CURRENT and an optional BASELINE run",
    )
    parser.add_argument("--only", nargs="*", default=(), help="run only the named suites")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="run archive directory")
    parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="target agent name")
    parser.add_argument("--label", default="", help="human-readable run label")
    parser.add_argument("--s3-bucket", help="private bucket override for --share")
    parser.add_argument("--s3-region", help="S3 region for --share")
    parser.add_argument("--s3-endpoint-url", help="S3-compatible endpoint for --share")
    parser.add_argument(
        "--expires-seconds",
        type=int,
        default=MAX_SHARE_EXPIRY_SECONDS,
        help="shared URL lifetime, at most seven days",
    )
    parser.add_argument(
        "--workspace",
        type=UUID,
        help="target workspace UUID; eval cases mutate its conversations, memory, and artifacts",
    )
    parser.add_argument("--memory-100", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--memory-100-state", type=Path, metavar="READINESS")
    parser.add_argument("--dsqa-100", type=Path, metavar="SNAPSHOT")
    parser.add_argument(
        "--mcp-atlas-data",
        type=Path,
        help="MCP-Atlas 100 JSON override; used only when mcp_atlas_100 is selected",
    )
    parser.add_argument(
        "--mcp-atlas-samples",
        type=int,
        help="run the first N digest-pinned MCP-Atlas cases for a deterministic smoke test",
    )
    parser.add_argument(
        "--mcp-atlas-url",
        default=os.environ.get(MCP_ATLAS_URL_ENV),
        help=f"secretless MCP-Atlas sandbox URL (or {MCP_ATLAS_URL_ENV})",
    )
    parser.add_argument(
        "--mcp-atlas-external-url",
        default=os.environ.get(MCP_ATLAS_EXTERNAL_URL_ENV),
        help=f"credentialed MCP-Atlas sandbox URL (or {MCP_ATLAS_EXTERNAL_URL_ENV})",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    names = tuple(args.only)
    if (args.memory_100 is None) != (args.memory_100_state is None):
        parser.error("--memory-100 and --memory-100-state must be provided together")
    if args.dsqa_100 is not None and args.memory_100 is not None:
        parser.error("--dsqa-100 and --memory-100 are separate eval runs")
    memory_run: Memory100Run | None = None
    if args.memory_100 is not None and args.memory_100_state is not None:
        memory_run = load_memory_100(args.memory_100, args.memory_100_state)
    dsqa_run = load_dsqa_100(args.dsqa_100) if args.dsqa_100 is not None else None
    if (args.mcp_atlas_data is not None or args.mcp_atlas_samples is not None) and (
        "mcp_atlas_100" not in names
    ):
        parser.error("MCP-Atlas options require --only mcp_atlas_100")
    try:
        tasks = _tasks(
            names,
            memory_run,
            dsqa_run,
            args.mcp_atlas_data,
            args.mcp_atlas_samples,
        )
    except (OSError, ValueError, ValidationError) as error:
        parser.error(str(error))
    if args.list:
        for task in tasks:
            print(f"{task.name}\t{task.digest}")
        return
    if args.view:
        viewer = write_viewer(args.out, load_runs(args.out)).resolve()
        print(f"viewer {viewer}")
        if not webbrowser.open(viewer.as_uri()):
            raise RuntimeError(f"browser did not open; open {viewer}")
        return
    if args.share:
        if len(args.share) > 2:
            parser.error("--share accepts CURRENT and one optional BASELINE run")
        configured = load_config() if config_path().is_file() else None
        configured_blob = (
            configured.blob if configured is not None and configured.blob.backend == "s3" else None
        )
        bucket = (
            args.s3_bucket
            or os.environ.get(EVAL_SHARE_BUCKET_ENV)
            or (configured_blob.bucket if configured_blob is not None else None)
        )
        if bucket is None:
            parser.error(
                f"--share needs --s3-bucket, {EVAL_SHARE_BUCKET_ENV}, or an S3 [blob] bucket"
            )
        runs = load_runs(args.out)
        selected: list[EvalRun] = []
        for reference in args.share:
            matches = [run for run in runs if str(run.id).startswith(reference)]
            if len(matches) != 1:
                parser.error(f"run id {reference!r} matched {len(matches)} recorded runs")
            selected.append(matches[0])
        current = selected[0]
        baseline = selected[1] if len(selected) == 2 else None
        page = render_viewer(
            tuple(selected), current.id, baseline.id if baseline is not None else None
        )
        url = asyncio.run(
            S3ViewerShare(
                bucket=bucket,
                region=args.s3_region
                or (configured_blob.region if configured_blob is not None else None),
                endpoint_url=args.s3_endpoint_url
                or (configured_blob.endpoint_url if configured_blob is not None else None),
            ).publish(page, args.expires_seconds)
        )
        print(url)
        return
    config = load_config()
    if dsqa_run is not None:
        dsqa_tasks = tuple(task for task in tasks if task.suite == "dsqa_100")
        if not names:
            tasks = dsqa_run.tasks_for_pack(config.pack.name)
            dsqa_tasks = tasks
        if dsqa_tasks:
            try:
                dsqa_run.validate_pack(dsqa_tasks, config.pack.name)
            except ValueError as error:
                parser.error(str(error))
            if config.models.auto_model != DSQA_JUDGE_MODEL:
                parser.error(
                    f"dsqa_100 requires [models] auto_model = {DSQA_JUDGE_MODEL!r} for its judge"
                )
    workspace_id = args.workspace
    if memory_run is not None:
        if workspace_id is not None and workspace_id != memory_run.readiness.workspace_id:
            parser.error("--workspace does not match the memory_100 readiness workspace")
        workspace_id = memory_run.readiness.workspace_id
    collector = (
        None
        if memory_run is None
        else TurnLogCollector.from_endpoint(
            config.o11y.otlp_endpoint,
            memory_run.readiness.workspace_id,
            MEMORY_RECALL_EVENT,
        )
    )
    reports = asyncio.run(
        _run(
            config,
            tasks,
            args.agent,
            workspace_id,
            collector,
            args.mcp_atlas_url,
            args.mcp_atlas_external_url,
        )
    )
    failed = False
    for report in reports:
        print(report.console_summary)
        failed = failed or not report.passed
    try:
        revision_process = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        revision = revision_process.stdout.strip() or version("ufo")
        if revision_process.returncode == 0:
            dirty = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
            )
            if dirty.stdout:
                revision += "+dirty"
    except FileNotFoundError:
        revision = version("ufo")
    run = EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC),
        label=args.label,
        agent=args.agent,
        ufo_version=version("ufo"),
        revision=revision,
        reports=reports,
    )
    record = record_run(args.out, run).resolve()
    print(f"run {run.id} · {record}")
    print(f"viewer {(args.out / 'index.html').resolve()}")
    if failed:
        raise SystemExit(1)


async def _run(
    config: Config,
    tasks: tuple[EvalTask, ...],
    agent_name: str,
    workspace_id: UUID | None = None,
    collector: TurnLogCollector | None = None,
    mcp_atlas_url: str | None = None,
    mcp_atlas_external_url: str | None = None,
) -> tuple[EvalReport, ...]:
    init_db(config.database.url)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    try:
        async with AsyncExitStack() as stack:
            if collector is not None:
                await stack.enter_async_context(collector.serving())
            workspace_id, agent_id, agent_prompt, agent_model = await resolve_workspace_and_agent(
                agent_name, workspace_id
            )
            blob = blob_store_for(config.blob)
            dbos = DBOSClient(system_database_url=config.database.system_url)
            driver = WorkspaceDriver(workspace_id, agent_id, agent_prompt, blob, dbos)
            ctx = context_for(
                "evals",
                frozenset(),
                blob=blob,
                invoker=AdmissionInvoker(
                    admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
                    workspace_id=workspace_id,
                ),
                model_resolver=model_registry(config, load_manifests(config.pack.name)),
            )
            if ctx.model is None:
                raise RuntimeError("eval context requires model access")
            target = InProcessTarget(
                ctx=ctx,
                agent_id=agent_id,
                conversations=driver,
                outcome=driver,
                judge=ModelJudge(
                    ctx.model,
                    _judge_max_tokens(tasks),
                    _judge_reasoning(tasks),
                ),
                blob=blob,
                logs=collector,
                mcp_atlas=await _mcp_atlas_target(
                    stack,
                    config,
                    tasks,
                    agent_prompt,
                    agent_model,
                    mcp_atlas_url,
                    mcp_atlas_external_url,
                ),
            )
            with ws(workspace_id):
                reports = tuple([await task.run(target) for task in tasks])
            manifests = load_manifests(config.pack.name)
            completed: list[EvalReport] = []
            for report, task in zip(reports, tasks, strict=True):
                digest = report.digest
                if task.pin_runtime:
                    digest = digest_payload(
                        {
                            "taskDigest": task.digest,
                            "pack": config.pack.name,
                            "manifests": [
                                {"name": manifest.name, "version": manifest.version}
                                for manifest in manifests
                            ],
                            "agentPromptDigest": prompt_digest(agent_prompt),
                            "agentModel": agent_model,
                            "judgeModel": ctx.model.model,
                            "judgeMaxTokens": task.judge_max_tokens,
                            "judgeReasoning": task.judge_reasoning,
                            "reasoning": config.models.reasoning_effort,
                            "searchProvider": config.research.search_provider,
                            "cdpProvider": config.browser.cdp_provider,
                        }
                    )
                completed.append(
                    report.model_copy(
                        update={
                            "digest": digest,
                            "target_model": agent_model,
                            "judge_model": ctx.model.model,
                            "judge_revision": task.judge_revision,
                        }
                    )
                )
            return tuple(completed)
    finally:
        init_workspace_credentials(None)
        await dispose_db()


async def _mcp_atlas_target(
    stack: AsyncExitStack,
    config: Config,
    tasks: tuple[EvalTask, ...],
    agent_prompt: str,
    agent_model: str,
    url: str | None,
    external_url: str | None,
) -> McpAtlasTarget | None:
    if not any(task.suite == "mcp_atlas" for task in tasks):
        return None
    if url is None:
        raise RuntimeError(f"mcp_atlas_100 requires --mcp-atlas-url or {MCP_ATLAS_URL_ENV}")
    manifests = load_manifests(config.pack.name)
    registry = model_registry(config, manifests)
    resolved_model = registry.resolve(agent_model)
    target_context = context_for(
        "evals",
        frozenset(),
        model_resolver=replace(registry, auto_model=resolved_model),
    )
    if target_context.model is None:
        raise RuntimeError("MCP-Atlas target requires model access")
    sections = tuple(
        (section.name, section.body)
        for manifest in manifests
        for section in manifest.prompt_sections
    )
    system = render_system_prompt(
        agent_prompt,
        sections,
        skills=skill_registry(manifests).index(),
        model=resolved_model,
    ).content
    public_client = await stack.enter_async_context(
        AsyncClient(
            base_url=url,
            timeout=Timeout(MCP_ATLAS_TIMEOUT_SECONDS),
        )
    )
    external_client = (
        None
        if external_url is None
        else await stack.enter_async_context(
            AsyncClient(
                base_url=external_url,
                timeout=Timeout(MCP_ATLAS_TIMEOUT_SECONDS),
            )
        )
    )
    return McpAtlasTarget(
        public_client,
        target_context.model,
        resolved_model,
        system,
        config.models.reasoning_effort,
        external_client,
    )


def _judge_max_tokens(tasks: tuple[EvalTask, ...]) -> int:
    limits = {task.judge_max_tokens for task in tasks}
    if len(limits) != 1:
        raise ValueError("selected eval tasks require different judge token bounds")
    return next(iter(limits))


def _judge_reasoning(tasks: tuple[EvalTask, ...]) -> ReasoningEffort:
    settings = {task.judge_reasoning for task in tasks}
    if len(settings) != 1:
        raise ValueError("selected eval tasks require different judge reasoning settings")
    return next(iter(settings))


def _tasks(
    names: tuple[str, ...],
    memory_run: Memory100Run | None,
    dsqa_run: DSQA100Run | None = None,
    mcp_atlas_data: Path | None = None,
    mcp_atlas_samples: int | None = None,
) -> tuple[EvalTask, ...]:
    mcp_atlas = (
        (load_mcp_atlas_task(mcp_atlas_data, mcp_atlas_samples),)
        if mcp_atlas_data is not None
        else ((load_mcp_atlas_task(limit=mcp_atlas_samples),) if "mcp_atlas_100" in names else ())
    )
    if memory_run is None and dsqa_run is None:
        return selected_tasks((*TASKS, *mcp_atlas), names) if names else selected_run_tasks()
    if dsqa_run is not None:
        return selected_tasks(
            (*TASKS, *dsqa_run.tasks, *mcp_atlas),
            names or tuple(task.name for task in dsqa_run.tasks),
        )
    assert memory_run is not None
    return selected_tasks(
        (*TASKS, *memory_run.tasks, *mcp_atlas),
        names or tuple(task.name for task in memory_run.tasks),
    )


if __name__ == "__main__":
    main()
