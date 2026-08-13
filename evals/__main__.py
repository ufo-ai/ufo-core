"""Run eval suites, browse recorded runs, share a two-run comparison, or reconstruct one.

`python -m evals` drives capability cases as real turns and MCP-Atlas cases through their pinned
sandbox, grades each answer and trajectory, and records one immutable run under `--out`. `--view`
opens the offline archive; `--share CURRENT [BASELINE]` publishes only those runs, whole, behind a
private expiring S3 URL; `--reconstruct RUN_ID` rebuilds a diagnostic copy of a run recorded
without evidence from the workspace's durable conversations, turns, and blobs, without touching
the original. Capability cases create durable conversations and may write memory or artifacts, so
target a disposable workspace with `--workspace`."""

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
from httpx import AsyncClient, Timeout
from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.code_review import WORKFLOW_WAIT_SECONDS as CODE_REVIEW_WORKFLOW_WAIT_SECONDS
from evals.coding_repo.runner import (
    CODING_REPO_PACKS,
    load_coding_repo,
)
from evals.coding_repo.runner import (
    SUBMISSIONS_ROOT as CODING_REPO_SUBMISSIONS_ROOT,
)
from evals.coding_repo.runner import (
    WORKFLOW_WAIT_SECONDS as CODING_REPO_WORKFLOW_WAIT_SECONDS,
)
from evals.compaction.runner import CompactionRun, load_compaction
from evals.compaction.target import CompactionTarget
from evals.cos_workflows import COS_WORKFLOWS_PACKS
from evals.document_visual import (
    WORKFLOW_WAIT_SECONDS as DOCUMENT_VISUAL_WORKFLOW_WAIT_SECONDS,
)
from evals.driver import (
    CANDIDATE_AGENT_NAME,
    WORKFLOW_WAIT_SECONDS,
    WorkspaceDriver,
    resolve_workspace_and_agent,
    seed_candidate_agent,
)
from evals.dsqa_100.runner import DSQA100Run, load_dsqa_100
from evals.gdpval_100.runner import (
    TREATMENTS,
    GDPvalCalibration,
    load_calibration,
)
from evals.gdpval_100.runner import (
    WORKFLOW_WAIT_SECONDS as GDPVAL_WORKFLOW_WAIT_SECONDS,
)
from evals.handbook.runner import (
    HANDBOOK_BACKENDS,
    HANDBOOK_PACKS,
    IngestDeps,
    load_handbook,
)
from evals.handbook.runner import (
    WORKFLOW_WAIT_SECONDS as HANDBOOK_WORKFLOW_WAIT_SECONDS,
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
from evals.hle_gold.runner import HLEGoldRun, load_hle_gold
from evals.issue_recall.runner import IssueRecallRun, load_issue_recall
from evals.jobbench.runner import (
    JOBBENCH_PACKS,
    SUBMISSIONS_ROOT,
    load_boundary,
)
from evals.jobbench.runner import (
    WORKFLOW_WAIT_SECONDS as JOBBENCH_WORKFLOW_WAIT_SECONDS,
)
from evals.mcp_atlas_100.runner import load_mcp_atlas_task
from evals.mcp_atlas_100.target import McpAtlasTarget
from evals.memory_100.runner import (
    WORKFLOW_WAIT_SECONDS as MEMORY_100_WORKFLOW_WAIT_SECONDS,
)
from evals.memory_100.runner import Memory100Run, load_memory_100
from evals.onboarding_help import ONBOARDING_HELP_PACKS
from evals.reconstruct import RunReconstruction, write_reconstruction
from evals.registry import TASKS, selected_run_tasks
from evals.skill_loading.catalog import CASES as SKILL_LOADING_CASES
from evals.skill_loading.runner import skill_loading_task
from evals.turn_logs import TurnLogCollector
from evals.wandr.runner import (
    SUBMISSIONS_ROOT as WANDR_SUBMISSIONS_ROOT,
)
from evals.wandr.runner import (
    SUBSETS as WANDR_SUBSETS,
)
from evals.wandr.runner import (
    WANDR_PACKS,
)
from evals.wandr.runner import (
    WORKFLOW_WAIT_SECONDS as WANDR_WORKFLOW_WAIT_SECONDS,
)
from evals.wandr.runner import (
    load_boundary as load_wandr_boundary,
)
from ufo.agent_scope import agent
from ufo.blob import blob_store_for
from ufo.config import Config, config_path, load_config
from ufo.credentials import CredentialRequests, CredentialStore, install_credential_requests
from ufo.db import dispose_db, init_db
from ufo.durability import replay_safe_client
from ufo.ext.context import context_for
from ufo.ext.loader import embed_backend, index_backend, load_manifests, skill_registry
from ufo.governance import prompt_digest
from ufo.loop.prompts.render import render_system_prompt
from ufo.models.registry import ModelRegistry, model_registry
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
    action.add_argument(
        "--reconstruct",
        metavar="RUN_ID",
        help="rebuild a diagnostic copy of a run recorded without evidence from durable state",
    )
    parser.add_argument("--only", nargs="*", default=(), help="run only the named suites")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="run archive directory")
    parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="target agent name")
    parser.add_argument(
        "--candidate-from-proposal",
        type=UUID,
        metavar="PROPOSAL_ID",
        help="run the suites against a pending proposal's candidate prompt on a scratch agent",
    )
    parser.add_argument("--label", default="", help="human-readable run label")
    parser.add_argument("--concurrency", type=int, default=1, help="max eval cases in flight")
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
    parser.add_argument("--issue-recall", type=Path, metavar="READINESS")
    parser.add_argument("--hle-gold", type=Path, metavar="GOLD_JSONL")
    parser.add_argument("--hle-gold-smoke", action="store_true")
    parser.add_argument("--dsqa-100", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--compaction", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--gdpval-100", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--gdpval-treatment", choices=TREATMENTS)
    parser.add_argument("--gdpval-task", action="append", default=[], metavar="TASK_ID")
    parser.add_argument(
        "--coding-repo",
        action="store_true",
        help="run the pinned coding suite over this repository's own merged work",
    )
    parser.add_argument("--coding-repo-case", action="append", default=[], metavar="CASE")
    parser.add_argument(
        "--coding-repo-submissions",
        type=Path,
        default=CODING_REPO_SUBMISSIONS_ROOT,
        metavar="DIR",
    )
    parser.add_argument("--jobbench", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--jobbench-case", action="append", default=[], metavar="CASE_ID")
    parser.add_argument(
        "--handbook",
        type=Path,
        metavar="CHECKOUT",
        help="HANDBOOK.md checkout at the pinned revision",
    )
    parser.add_argument("--handbook-task", action="append", default=[], metavar="TASK_ID")
    parser.add_argument(
        "--handbook-ingest",
        type=Path,
        metavar="STAGING",
        help="index each task's policy documents as a synced source before its turn, staging the "
        "extracted text under STAGING — the deployment a company running ufo would have",
    )
    parser.add_argument("--skill-loading-case", action="append", default=[], metavar="CASE_NAME")
    parser.add_argument(
        "--jobbench-submissions",
        type=Path,
        default=SUBMISSIONS_ROOT,
        help="folder that captures each case's share_file deliverables for offline grading",
    )
    parser.add_argument("--wandr", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--wandr-subset", choices=WANDR_SUBSETS)
    parser.add_argument("--wandr-case", action="append", default=[], metavar="TASK")
    parser.add_argument(
        "--wandr-submissions",
        type=Path,
        default=WANDR_SUBMISSIONS_ROOT,
        help="folder that captures each case's share_file results files for offline grading",
    )
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
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    names = tuple(args.only)
    if (args.memory_100 is None) != (args.memory_100_state is None):
        parser.error("--memory-100 and --memory-100-state must be provided together")
    if args.hle_gold_smoke and args.hle_gold is None:
        parser.error("--hle-gold-smoke requires --hle-gold")
    if args.hle_gold is not None and not args.list and args.workspace is None:
        parser.error("--hle-gold requires an explicit disposable --workspace")
    if (args.gdpval_100 is None) != (args.gdpval_treatment is None):
        parser.error("--gdpval-100 and --gdpval-treatment must be provided together")
    if args.gdpval_task and args.gdpval_100 is None:
        parser.error("--gdpval-task requires --gdpval-100")
    if args.jobbench_case and args.jobbench is None:
        parser.error("--jobbench-case requires --jobbench")
    if args.handbook_task and args.handbook is None:
        parser.error("--handbook-task requires --handbook")
    if args.handbook_ingest is not None and args.handbook is None:
        parser.error("--handbook-ingest requires --handbook")
    if args.coding_repo_case and not args.coding_repo:
        parser.error("--coding-repo-case requires --coding-repo")
    if args.skill_loading_case and "skill_loading" not in names:
        parser.error("--skill-loading-case requires --only skill_loading")
    if (args.wandr_subset is not None or args.wandr_case) and args.wandr is None:
        parser.error("--wandr-subset and --wandr-case require --wandr")
    if args.wandr is not None and args.wandr_subset is None and not args.wandr_case:
        parser.error("--wandr requires --wandr-subset or --wandr-case")
    requested_runs = sum(
        source is not None
        for source in (
            args.memory_100,
            args.dsqa_100,
            args.gdpval_100,
            args.jobbench,
            args.hle_gold,
            args.compaction,
            args.wandr,
            args.issue_recall,
            args.handbook,
            args.coding_repo or None,
        )
    )
    if requested_runs > 1:
        parser.error("corpus-backed evals are separate eval runs")
    if args.candidate_from_proposal is not None and requested_runs:
        parser.error(
            "--candidate-from-proposal runs the capability/scenario suites, not a corpus eval"
        )
    memory_run: Memory100Run | None = None
    if args.memory_100 is not None and args.memory_100_state is not None:
        memory_run = load_memory_100(args.memory_100, args.memory_100_state)
    dsqa_run = load_dsqa_100(args.dsqa_100) if args.dsqa_100 is not None else None
    compaction_run = load_compaction(args.compaction) if args.compaction is not None else None
    if (args.mcp_atlas_data is not None or args.mcp_atlas_samples is not None) and (
        "mcp_atlas_100" not in names
    ):
        parser.error("MCP-Atlas options require --only mcp_atlas_100")
    hle_run = load_hle_gold(args.hle_gold, args.hle_gold_smoke) if args.hle_gold else None
    try:
        issue_run = load_issue_recall(args.issue_recall) if args.issue_recall is not None else None
        gdpval_run = (
            load_calibration(
                args.gdpval_100,
                args.gdpval_treatment,
                tuple(args.gdpval_task),
            )
            if args.gdpval_100 is not None and args.gdpval_treatment is not None
            else None
        )
        jobbench_tasks = (
            load_boundary(args.jobbench, tuple(args.jobbench_case), args.jobbench_submissions)
            if args.jobbench is not None
            else None
        )
        coding_repo_tasks = (
            load_coding_repo(
                tuple(args.coding_repo_case), submissions_root=args.coding_repo_submissions
            )
            if args.coding_repo
            else None
        )
        wandr_tasks = (
            load_wandr_boundary(
                args.wandr, args.wandr_subset, tuple(args.wandr_case), args.wandr_submissions
            )
            if args.wandr is not None
            else None
        )
        handbook_tasks = (
            load_handbook(
                args.handbook,
                _credential_store(load_config()),
                tuple(args.handbook_task),
                _handbook_ingest(load_config(), args.handbook_ingest),
            )
            if args.handbook is not None
            else None
        )
        tasks = _tasks(
            names,
            memory_run,
            issue_run,
            dsqa_run,
            compaction_run,
            gdpval_run,
            jobbench_tasks,
            wandr_tasks,
            handbook_tasks,
            coding_repo_tasks,
            args.mcp_atlas_data,
            args.mcp_atlas_samples,
            hle_run,
        )
        if args.skill_loading_case:
            subset = _skill_loading_subset(tuple(args.skill_loading_case))
            tasks = tuple(subset if task.name == "skill_loading" else task for task in tasks)
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
    if args.reconstruct:
        if args.workspace is None:
            parser.error("--reconstruct requires the --workspace the run executed in")
        matches = [run for run in load_runs(args.out) if str(run.id).startswith(args.reconstruct)]
        if len(matches) != 1:
            parser.error(f"run id {args.reconstruct!r} matched {len(matches)} recorded runs")
        rebuilt = asyncio.run(_reconstruct(load_config(), matches[0], args.workspace))
        record, rebuilt_page = write_reconstruction(args.out, rebuilt)
        print(f"reconstruction {record.resolve()}")
        print(f"viewer {rebuilt_page.resolve()}")
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
    if gdpval_run is not None and config.pack.name != gdpval_run.treatment:
        parser.error(
            f"GDPval treatment {gdpval_run.treatment!r} requires [pack] name = "
            f"{gdpval_run.treatment!r}, found {config.pack.name!r}"
        )
    if jobbench_tasks is not None and config.pack.name not in JOBBENCH_PACKS:
        parser.error(
            f"jobbench requires [pack] name in {JOBBENCH_PACKS}, found {config.pack.name!r}"
        )
    if coding_repo_tasks is not None and config.pack.name not in CODING_REPO_PACKS:
        parser.error(
            f"coding_repo requires [pack] name in {CODING_REPO_PACKS}, found {config.pack.name!r}"
        )
    if wandr_tasks is not None and config.pack.name not in WANDR_PACKS:
        parser.error(f"wandr requires [pack] name in {WANDR_PACKS}, found {config.pack.name!r}")
    if handbook_tasks is not None and config.pack.name not in HANDBOOK_PACKS:
        parser.error(
            f"handbook requires [pack] name in {HANDBOOK_PACKS}, found {config.pack.name!r}"
        )
    if handbook_tasks is not None and config.sandbox.backend not in HANDBOOK_BACKENDS:
        parser.error(
            f"handbook grades the conversation workspace on the host, so it requires "
            f"[sandbox] backend in {HANDBOOK_BACKENDS}, found {config.sandbox.backend!r}"
        )
    if any(task.name == "cos_workflows" for task in tasks) and (
        config.pack.name not in COS_WORKFLOWS_PACKS
    ):
        parser.error(
            f"cos_workflows requires [pack] name in {COS_WORKFLOWS_PACKS}, "
            f"found {config.pack.name!r}"
        )
    if any(task.name == "onboarding_help" for task in tasks) and (
        config.pack.name not in ONBOARDING_HELP_PACKS
    ):
        parser.error(
            f"onboarding_help requires [pack] name in {ONBOARDING_HELP_PACKS}, "
            f"found {config.pack.name!r}"
        )
    workspace_id = args.workspace
    recall_workspace_id = (
        memory_run.readiness.workspace_id
        if memory_run is not None
        else (issue_run.readiness.workspace_id if issue_run is not None else None)
    )
    if recall_workspace_id is not None:
        if workspace_id is not None and workspace_id != recall_workspace_id:
            parser.error("--workspace does not match the recall readiness workspace")
        workspace_id = recall_workspace_id
    collector = (
        None
        if recall_workspace_id is None
        else TurnLogCollector.from_endpoint(
            config.o11y.otlp_endpoint,
            recall_workspace_id,
            MEMORY_RECALL_EVENT,
        )
    )
    workflow_wait_seconds = WORKFLOW_WAIT_SECONDS
    if gdpval_run is not None:
        workflow_wait_seconds = GDPVAL_WORKFLOW_WAIT_SECONDS
    if jobbench_tasks is not None:
        workflow_wait_seconds = JOBBENCH_WORKFLOW_WAIT_SECONDS
    if wandr_tasks is not None:
        workflow_wait_seconds = WANDR_WORKFLOW_WAIT_SECONDS
    if handbook_tasks is not None:
        workflow_wait_seconds = HANDBOOK_WORKFLOW_WAIT_SECONDS
    if coding_repo_tasks is not None:
        workflow_wait_seconds = CODING_REPO_WORKFLOW_WAIT_SECONDS
    if memory_run is not None:
        workflow_wait_seconds = MEMORY_100_WORKFLOW_WAIT_SECONDS
    if tasks and all(task.name == "document_visual" for task in tasks):
        workflow_wait_seconds = DOCUMENT_VISUAL_WORKFLOW_WAIT_SECONDS
    if tasks and all(task.name == "code_review" for task in tasks):
        workflow_wait_seconds = CODE_REVIEW_WORKFLOW_WAIT_SECONDS
    reports, agent_prompt = asyncio.run(
        _run(
            config,
            tasks,
            args.agent,
            workspace_id,
            collector,
            workflow_wait_seconds,
            args.mcp_atlas_url,
            args.mcp_atlas_external_url,
            args.candidate_from_proposal,
            args.concurrency,
        )
    )
    run_agent = (
        args.agent
        if args.candidate_from_proposal is None
        else CANDIDATE_AGENT_NAME.format(proposal_id=args.candidate_from_proposal)
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
        agent=run_agent,
        agent_prompt=agent_prompt,
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
    workflow_wait_seconds: float = WORKFLOW_WAIT_SECONDS,
    mcp_atlas_url: str | None = None,
    mcp_atlas_external_url: str | None = None,
    candidate_proposal: UUID | None = None,
    concurrency: int = 1,
) -> tuple[tuple[EvalReport, ...], str]:
    init_db(config.database.url)
    manifests = load_manifests(config.pack.name)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    install_credential_requests(
        None
        if credentials is None
        else CredentialRequests(
            fernet=credentials.fernet,
            declared=frozenset(
                slot.name for manifest in manifests for slot in manifest.credentials
            ),
            fillable=frozenset(
                slot.name
                for manifest in manifests
                for slot in manifest.credentials
                if slot.member_filled
            ),
        )
    )
    try:
        async with AsyncExitStack() as stack:
            if collector is not None:
                await stack.enter_async_context(collector.serving())
            if candidate_proposal is not None:
                workspace_id, agent_name = await seed_candidate_agent(
                    candidate_proposal, workspace_id
                )
            (
                workspace_id,
                agent_id,
                agent_prompt,
                agent_model,
                agent_reasoning,
            ) = await resolve_workspace_and_agent(agent_name, workspace_id)
            blob = blob_store_for(config.blob)
            dbos = replay_safe_client(config.database.system_url)
            driver = WorkspaceDriver(
                workspace_id,
                agent_id,
                agent_prompt,
                blob,
                dbos,
                config.sandbox.workspace_root,
                agent_model,
                workflow_wait_seconds=workflow_wait_seconds,
            )
            registry = model_registry(config, manifests)
            ctx = context_for(
                "evals",
                frozenset(),
                blob=blob,
                invoker=AdmissionInvoker(
                    admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
                    workspace_id=workspace_id,
                ),
                model_resolver=registry,
            )
            if ctx.model is None:
                raise RuntimeError("eval context requires model access")
            slots = asyncio.Semaphore(concurrency)
            with ws(workspace_id), agent(agent_id):
                loadable_skills: frozenset[str] | None = None
                if any(task.suite == "skill_loading" for task in tasks):
                    loadable_skills = frozenset(skill_registry(manifests).by_name)
                compaction: CompactionTarget | None = None
                if any(task.suite == "compaction" for task in tasks):
                    resolved_model = registry.resolve(agent_model)
                    compaction = CompactionTarget(
                        client=await registry.client_for(resolved_model),
                        model=resolved_model,
                        blob=blob,
                        workspace_root=config.sandbox.workspace_root,
                        context_window=registry.spec(resolved_model).context_window,
                    )
                target = InProcessTarget(
                    ctx=ctx,
                    agent_id=agent_id,
                    conversations=driver,
                    outcome=driver,
                    blob=blob,
                    logs=collector,
                    turn_steps=driver,
                    mcp_atlas=await _mcp_atlas_target(
                        stack,
                        config,
                        tasks,
                        agent_prompt,
                        agent_model,
                        agent_reasoning,
                        mcp_atlas_url,
                        mcp_atlas_external_url,
                    ),
                    compaction=compaction,
                    loadable_skills=loadable_skills,
                )
                targets = tuple(
                    replace(
                        target,
                        judge=_model_leg(
                            registry,
                            task.judge_model,
                            task.judge_max_tokens,
                            task.judge_reasoning,
                        ),
                        simulator=_model_leg(
                            registry,
                            task.simulator_model,
                            task.simulator_max_tokens,
                            task.simulator_reasoning,
                        ),
                    )
                    for task in tasks
                )
                reports = await _task_reports(tasks, targets, slots)
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
                            **(
                                {
                                    "judgeModel": task.judge_model,
                                    "judgeMaxTokens": task.judge_max_tokens,
                                    "judgeReasoning": task.judge_reasoning,
                                }
                                if task.judge_model is not None
                                else {}
                            ),
                            **(
                                {
                                    "simulatorModel": task.simulator_model,
                                    "simulatorMaxTokens": task.simulator_max_tokens,
                                    "simulatorReasoning": task.simulator_reasoning,
                                }
                                if task.simulator_model is not None
                                else {}
                            ),
                            "reasoning": agent_reasoning,
                            "searchProvider": config.research.search_provider,
                            "cdpProvider": config.browser.cdp_provider,
                        }
                    )
                completed.append(
                    report.model_copy(
                        update={
                            "digest": digest,
                            "target_model": agent_model,
                            "judge_model": task.judge_model,
                            "simulator_model": task.simulator_model,
                            "judge_revision": task.judge_revision,
                        }
                    )
                )
            return tuple(completed), agent_prompt
    finally:
        install_credential_requests(None)
        init_workspace_credentials(None)
        await dispose_db()


async def _task_reports(
    tasks: tuple[EvalTask, ...],
    targets: tuple[InProcessTarget, ...],
    slots: asyncio.Semaphore,
) -> tuple[EvalReport, ...]:
    reports: dict[int, EvalReport] = {}

    async def run_task(index: int) -> None:
        reports[index] = await tasks[index].run(targets[index], slots)

    overlapping = tuple(index for index, task in enumerate(tasks) if not task.exclusive)
    outcomes = await asyncio.gather(
        *(run_task(index) for index in overlapping), return_exceptions=True
    )
    errors = tuple(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("eval tasks raised", errors)
    for index, task in enumerate(tasks):
        if task.exclusive:
            await run_task(index)
    return tuple(reports[index] for index in range(len(tasks)))


async def _reconstruct(config: Config, run: EvalRun, workspace_id: UUID) -> EvalRun:
    init_db(config.database.url)
    try:
        with ws(workspace_id):
            return await RunReconstruction(
                workspace_id=workspace_id,
                blob=blob_store_for(config.blob),
                run=run,
            ).reconstruct()
    finally:
        await dispose_db()


async def _mcp_atlas_target(
    stack: AsyncExitStack,
    config: Config,
    tasks: tuple[EvalTask, ...],
    agent_prompt: str,
    agent_model: str,
    agent_reasoning: ReasoningEffort,
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
        knowledge_cutoff=registry.spec(resolved_model).knowledge_cutoff,
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
        agent_reasoning,
        external_client,
    )


def _handbook_ingest(config: Config, staging_root: Path | None) -> IngestDeps | None:
    """The ingested arm's dependencies, or None for the default arm that leaves the policy documents
    in the workspace. Built here because the suite loads before the run opens its own backends."""
    if staging_root is None:
        return None
    manifests = load_manifests(config.pack.name)
    credentials = _credential_store(config)
    return IngestDeps(
        staging_root=staging_root,
        blob=blob_store_for(config.blob),
        index=index_backend(manifests, config.memory.index_backend, credentials),
        embed=embed_backend(manifests, config.memory.embed_backend, credentials),
        manifests=manifests,
        postgres=config.database.url.startswith("postgresql"),
    )


def _credential_store(config: Config) -> CredentialStore:
    """The store a suite writes BYOK slots through — the handbook environment points the workspace's
    `mcp_servers` slot at its own services. Fails loud when the deploy has no credential key: a
    silently unwritten slot would leave the agent with no way to reach the environment."""
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise ValueError(f"writing a workspace credential requires {config.credentials.key_env}")
    return CredentialStore(fernet=Fernet(key.encode()))


def _model_leg(
    registry: ModelRegistry,
    model: str | None,
    max_tokens: int,
    reasoning: ReasoningEffort,
) -> ModelJudge | None:
    if model is None:
        return None
    context = context_for(
        "evals",
        frozenset(),
        model_resolver=replace(registry, auto_model=model),
    )
    if context.model is None:
        raise RuntimeError(f"eval model leg {model!r} requires model access")
    return ModelJudge(context.model, max_tokens, reasoning)


def _skill_loading_subset(names: tuple[str, ...]) -> EvalTask:
    by_name = {case.name: case for case in SKILL_LOADING_CASES}
    missing = tuple(name for name in names if name not in by_name)
    if missing:
        raise ValueError(f"unknown skill_loading case: {', '.join(missing)}")
    return skill_loading_task(tuple(by_name[name] for name in names))


def _tasks(
    names: tuple[str, ...],
    memory_run: Memory100Run | None,
    issue_run: IssueRecallRun | None = None,
    dsqa_run: DSQA100Run | None = None,
    compaction_run: CompactionRun | None = None,
    gdpval_run: GDPvalCalibration | None = None,
    jobbench_tasks: tuple[EvalTask, ...] | None = None,
    wandr_tasks: tuple[EvalTask, ...] | None = None,
    handbook_tasks: tuple[EvalTask, ...] | None = None,
    coding_repo_tasks: tuple[EvalTask, ...] | None = None,
    mcp_atlas_data: Path | None = None,
    mcp_atlas_samples: int | None = None,
    hle_run: HLEGoldRun | None = None,
) -> tuple[EvalTask, ...]:
    if hle_run is not None:
        return selected_tasks(hle_run.tasks, names)
    if gdpval_run is not None:
        return selected_tasks(gdpval_run.tasks, names)
    if jobbench_tasks is not None:
        return selected_tasks(jobbench_tasks, names)
    if wandr_tasks is not None:
        return selected_tasks(wandr_tasks, names)
    if handbook_tasks is not None:
        return selected_tasks(handbook_tasks, names)
    if coding_repo_tasks is not None:
        return selected_tasks(coding_repo_tasks, names)
    mcp_atlas = (
        (load_mcp_atlas_task(mcp_atlas_data, mcp_atlas_samples),)
        if mcp_atlas_data is not None
        else ((load_mcp_atlas_task(limit=mcp_atlas_samples),) if "mcp_atlas_100" in names else ())
    )
    if compaction_run is not None:
        return selected_tasks(
            (*TASKS, *compaction_run.tasks, *mcp_atlas),
            names or tuple(task.name for task in compaction_run.tasks),
        )
    if issue_run is not None:
        return selected_tasks(
            (*TASKS, *issue_run.tasks, *mcp_atlas),
            names or tuple(task.name for task in issue_run.tasks),
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
