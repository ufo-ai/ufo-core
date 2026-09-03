"""Run eval suites, browse recorded runs, share a two-run comparison, or reconstruct one.

`python -m evals` drives capability cases as real turns and MCP-Atlas cases through their pinned
sandbox, grades each answer and trajectory, and records one immutable run under `--out`. `--remote`
admits those turns through `ufo --remote --json`; otherwise the runner admits through the shared
in-process boundary. `--view` opens the offline archive; `--share CURRENT [BASELINE]` publishes only
those runs, whole, behind a private expiring S3 URL; `--reconstruct RUN_ID` rebuilds a diagnostic
copy of a run recorded without evidence from the workspace's durable conversations, turns, and
blobs, without touching the original. Capability cases create durable conversations and may write
memory or artifacts, so target a disposable workspace with `--workspace`."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from httpx import AsyncClient, Timeout
from pydantic import ValidationError
from ufo_ext_memory.events import MEMORY_RECALL_EVENT

from evals.budget import EvalRunBudget
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
from evals.driver import (
    CANDIDATE_AGENT_NAME,
    WORKFLOW_WAIT_SECONDS,
    RemoteClient,
    RemoteWorkspaceProvisioner,
    WorkspaceDriver,
    resolve_workspace_and_agent,
    runner_model_key,
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
from evals.harness.registry import EvalTask, narrowed_tasks, selected_tasks
from evals.harness.target import InProcessTarget
from evals.harness.viewer import (
    MAX_SHARE_EXPIRY_SECONDS,
    EvalRun,
    RunRecorder,
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
from evals.memory_ingestion.runner import (
    WORKFLOW_WAIT_SECONDS as MEMORY_INGESTION_WORKFLOW_WAIT_SECONDS,
)
from evals.memory_ingestion.runner import MemoryIngestionRun, load_memory_ingestion
from evals.reconstruct import RunReconstruction, write_reconstruction
from evals.registry import TASKS, selected_run_tasks
from evals.skill_loading.catalog import CASES as SKILL_LOADING_CASES
from evals.skill_loading.catalog import SKILL_LOADING_PACKS
from evals.skill_loading.member import CASES as SKILL_MEMBER_CASES
from evals.skill_loading.runner import SUITE as SKILL_LOADING_SUITE
from evals.skill_loading.runner import skill_loading_task
from evals.suites.document_visual import (
    WORKFLOW_WAIT_SECONDS as DOCUMENT_VISUAL_WORKFLOW_WAIT_SECONDS,
)
from evals.suites.ufo_app_bench import (
    SUPPORTED_BACKENDS as UFO_APP_BENCH_BACKENDS,
)
from evals.suites.ufo_app_bench import (
    WORKFLOW_WAIT_SECONDS as UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS,
)
from evals.suites.ufo_app_bench import (
    AppBenchWorkspaceProbe,
)
from evals.swebench.runner import (
    SNAPSHOT_ROOT as SWEBENCH_SNAPSHOT_ROOT,
)
from evals.swebench.runner import (
    SUBSETS as SWEBENCH_SUBSETS,
)
from evals.swebench.runner import (
    SWEBENCH_PACKS,
    CapturedPatches,
    capture_shared_patches,
    load_swebench,
    new_submissions_root,
)
from evals.swebench.runner import (
    WORKFLOW_WAIT_SECONDS as SWEBENCH_WORKFLOW_WAIT_SECONDS,
)
from evals.terminal_bench.run import CLIENT as TERMINAL_BENCH_CLIENT
from evals.terminal_bench.run import (
    DAYTONA_BACKEND,
    BenchCredentials,
    HarborBackend,
    TerminalBenchRun,
)
from evals.terminal_bench.setup import DEFAULT_ROOT as TERMINAL_BENCH_ROOT
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
from ufo.blob import S3BlobStore, WorkspaceBlobStore, blob_store_for
from ufo.config import Config, config_path, load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog_skill import model_catalog_skill
from ufo.harness.models.pricing import MICRO_USD_PER_USD
from ufo.harness.models.registry import ModelRegistry, model_registry
from ufo.host.ext.loader import (
    embed_backend,
    index_backend,
    load_manifests,
    skill_registry,
    turn_subagents,
)
from ufo.host.spawn_catalog import spawn_catalog_skill, spawn_targets
from ufo.onboard.onboard_control import ONBOARD_CONTROL_TOKEN_ENV
from ufo.runtime.access.credentials import (
    CredentialStore,
)
from ufo.runtime.agent_scope import agent
from ufo.runtime.authority import WORKSPACE_AUTHORITY
from ufo.runtime.ext.context import context_for
from ufo.runtime.kinds.governance import prompt_digest
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.prompts.render import render_system_prompt
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, ReasoningEffort

DEFAULT_OUT = Path("eval-reports")
REMOTE_HOME_ROOT = Path(".local/eval-ufo")
EVAL_SHARE_BUCKET_ENV = "UFO_EVAL_SHARE_BUCKET"
EVAL_TARGET_JOB = "evals:target"
EVAL_JUDGE_JOB = "evals:judge"
MCP_ATLAS_JOB = "evals:mcp_atlas"
MCP_ATLAS_URL_ENV = "MCP_ATLAS_URL"
MCP_ATLAS_EXTERNAL_URL_ENV = "MCP_ATLAS_EXTERNAL_URL"
MCP_ATLAS_TIMEOUT_SECONDS = 1_800.0
UFO_APP_TASKS = ("ufo-app-bench", "ufo-app-copy", "ufo-app-qa-replay", "new_application")
TERMINAL_BENCH_TOKEN_TTL = timedelta(days=1)


def _task_workflow_wait_seconds(tasks: tuple[EvalTask, ...]) -> float:
    if tasks and all(task.name == "document_visual" for task in tasks):
        return DOCUMENT_VISUAL_WORKFLOW_WAIT_SECONDS
    if tasks and all(task.name in UFO_APP_TASKS for task in tasks):
        return UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS
    return WORKFLOW_WAIT_SECONDS


async def _terminal_bench_credentials(
    config: Config, workspace_id: UUID, root: Path
) -> BenchCredentials:
    """Mint the member bearer a remote Harbor task uses to reach one workspace."""
    public_url = config.connect.public_base_url
    if public_url is None:
        raise ValueError("Terminal-Bench remote runs require connect.public_base_url")
    secret = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not secret:
        raise ValueError(f"Terminal-Bench remote runs require {UFO_TOKEN_SECRET_ENV}")
    with ws(workspace_id):
        async with workspace_tx() as connection:
            email = (
                await connection.execute(
                    sa.select(tables.member.c.email)
                    .where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.is_admin.is_(True),
                    )
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
    if email is None:
        raise ValueError(f"workspace {workspace_id} has no admin member")
    return BenchCredentials(
        client=(root / TERMINAL_BENCH_CLIENT).resolve(),
        token=mint_token(secret, str(workspace_id), email, TERMINAL_BENCH_TOKEN_TTL),
        workspace_url=public_url,
    )


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
    action.add_argument(
        "--swebench-capture",
        action="store_true",
        help="copy durable SWE-bench patches into a submissions directory without rerunning",
    )
    parser.add_argument("--only", nargs="*", default=(), help="run only the named suites")
    parser.add_argument(
        "--case",
        nargs="*",
        default=(),
        help="run only the named cases within the --only suites",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="run archive directory")
    parser.add_argument(
        "--agent",
        default=DEFAULT_AGENT_NAME,
        help="target durable agent name or a profile:<name> pinned by the selected suite",
    )
    parser.add_argument(
        "--candidate-from-proposal",
        type=UUID,
        metavar="PROPOSAL_ID",
        help="run the suites against a pending proposal's candidate prompt on a scratch agent",
    )
    parser.add_argument("--label", default="", help="human-readable run label")
    parser.add_argument("--concurrency", type=int, default=1, help="max eval cases in flight")
    parser.add_argument(
        "--remote",
        action="store_true",
        help="run against the configured remote execution boundary",
    )
    parser.add_argument(
        "--model",
        help="concrete model id sent with each remote eval turn",
    )
    parser.add_argument(
        "--environment",
        type=Path,
        help="overrides document stored once and pinned by digest on each remote eval turn",
    )
    parser.add_argument(
        "--fresh-workspace",
        action="store_true",
        help="provision a clean hosted workspace for a remote eval run",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        help="fund a remote eval workspace with this run's spend allocation",
    )
    parser.add_argument("--run-id", type=UUID, help=argparse.SUPPRESS)
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
    parser.add_argument("--memory-ingestion", type=Path, metavar="SNAPSHOT")
    parser.add_argument("--memory-ingestion-state", type=Path, metavar="READINESS")
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
    parser.add_argument(
        "--swebench",
        action="store_true",
        help="run the pinned SWE-bench Verified roster",
    )
    parser.add_argument("--swebench-subset", choices=(*SWEBENCH_SUBSETS, "all"))
    parser.add_argument(
        "--swebench-case",
        action="append",
        default=[],
        metavar="INSTANCE_ID",
    )
    parser.add_argument(
        "--swebench-snapshot",
        type=Path,
        default=SWEBENCH_SNAPSHOT_ROOT,
        metavar="DIR",
    )
    parser.add_argument(
        "--swebench-submissions",
        type=Path,
        default=None,
        metavar="DIR",
    )
    parser.add_argument(
        "--terminal-bench",
        action="store_true",
        help="run the pinned Terminal-Bench 2.1 roster through remote Harbor environments",
    )
    parser.add_argument("--terminal-bench-case", action="append", default=[], metavar="CASE")
    parser.add_argument(
        "--terminal-bench-root",
        type=Path,
        default=TERMINAL_BENCH_ROOT,
        metavar="DIR",
    )
    parser.add_argument(
        "--terminal-bench-environment",
        default=DAYTONA_BACKEND.environment,
        metavar="ENVIRONMENT",
    )
    parser.add_argument(
        "--terminal-bench-harbor-extra",
        default=DAYTONA_BACKEND.extra,
        metavar="EXTRA",
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
    if args.remote and (
        args.list or args.view or args.share or args.reconstruct or args.swebench_capture
    ):
        parser.error("--remote runs eval suites")
    if args.fresh_workspace and not args.remote:
        parser.error("--fresh-workspace requires --remote")
    if args.model is not None and not args.remote:
        parser.error("--model requires --remote")
    if args.fresh_workspace and args.workspace is not None:
        parser.error("--fresh-workspace conflicts with --workspace")
    if args.budget_usd is not None and (not args.remote or args.budget_usd <= 0):
        parser.error("--budget-usd requires --remote and a positive amount")
    if args.run_id is not None and args.budget_usd is None:
        parser.error("--run-id requires --budget-usd")
    if args.fresh_workspace and args.budget_usd is None:
        parser.error("--fresh-workspace requires --budget-usd")
    names = tuple(args.only)
    if args.case and not names:
        parser.error("--case requires --only naming the suites to narrow")
    if (args.memory_100 is None) != (args.memory_100_state is None):
        parser.error("--memory-100 and --memory-100-state must be provided together")
    if (args.memory_ingestion is None) != (args.memory_ingestion_state is None):
        parser.error("--memory-ingestion and --memory-ingestion-state must be provided together")
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
    if (args.swebench_subset is not None or args.swebench_case) and not (
        args.swebench or args.swebench_capture
    ):
        parser.error(
            "--swebench-subset and --swebench-case require --swebench or --swebench-capture"
        )
    if args.swebench and args.swebench_subset is None and not args.swebench_case:
        parser.error("--swebench requires --swebench-subset or --swebench-case")
    if args.swebench and args.swebench_submissions is None:
        args.swebench_submissions = new_submissions_root()
    if args.swebench_capture and args.workspace is None:
        parser.error("--swebench-capture requires --workspace")
    if args.swebench_capture and args.swebench_subset is None and not args.swebench_case:
        parser.error("--swebench-capture requires --swebench-subset or --swebench-case")
    if args.swebench_capture and args.swebench_submissions is None:
        parser.error("--swebench-capture requires --swebench-submissions")
    if args.terminal_bench_case and not args.terminal_bench:
        parser.error("--terminal-bench-case requires --terminal-bench")
    if args.terminal_bench and not args.remote:
        parser.error("--terminal-bench requires --remote")
    if args.terminal_bench and args.workspace is None:
        parser.error("--terminal-bench requires --workspace")
    if args.terminal_bench and args.budget_usd is not None:
        parser.error("--budget-usd is not supported with --terminal-bench")
    if args.terminal_bench and (names or args.case):
        parser.error("Terminal-Bench is a separate eval run")
    if args.skill_loading_case and "skill_loading" not in names:
        parser.error("--skill-loading-case requires --only skill_loading")
    if (args.wandr_subset is not None or args.wandr_case) and args.wandr is None:
        parser.error("--wandr-subset and --wandr-case require --wandr")
    if args.wandr is not None and args.wandr_subset is None and not args.wandr_case:
        parser.error("--wandr requires --wandr-subset or --wandr-case")
    if args.swebench_capture:
        try:
            capture_tasks = load_swebench(
                tuple(args.swebench_case),
                args.swebench_snapshot,
                args.swebench_submissions,
                None if args.swebench_subset == "all" else args.swebench_subset,
            )
            captured = asyncio.run(
                _capture_swebench(
                    load_config(),
                    args.workspace,
                    tuple(case for task in capture_tasks for case in task.cases),
                    args.swebench_submissions,
                )
            )
        except (OSError, ValueError, ValidationError) as error:
            parser.error(str(error))
        print(f"captured {len(captured.paths)} SWE-bench patches in {args.swebench_submissions}")
        if captured.missing:
            print(f"missing {len(captured.missing)}: {', '.join(captured.missing)}")
        return
    requested_runs = sum(
        source is not None
        for source in (
            args.memory_100,
            args.memory_ingestion,
            args.dsqa_100,
            args.gdpval_100,
            args.jobbench,
            args.hle_gold,
            args.compaction,
            args.wandr,
            args.issue_recall,
            args.handbook,
            args.coding_repo or None,
            args.swebench or None,
            args.terminal_bench or None,
        )
    )
    if requested_runs > 1:
        parser.error("corpus-backed evals are separate eval runs")
    if args.candidate_from_proposal is not None and requested_runs:
        parser.error(
            "--candidate-from-proposal runs the capability/scenario suites, not a corpus eval"
        )
    if args.terminal_bench:
        config = load_config()
        init_db(config.database.url)
        try:
            try:
                credentials = asyncio.run(
                    _terminal_bench_credentials(config, args.workspace, args.terminal_bench_root)
                )
            finally:
                asyncio.run(dispose_db())
            status = TerminalBenchRun(
                root=args.terminal_bench_root,
                cases=tuple(args.terminal_bench_case),
                concurrency=args.concurrency,
                credentials=credentials,
                backend=HarborBackend(
                    environment=args.terminal_bench_environment,
                    extra=args.terminal_bench_harbor_extra,
                ),
                model=args.model,
                environment_document=args.environment,
            ).run()
        except (OSError, ValueError, ValidationError) as error:
            parser.error(str(error))
        if status:
            raise SystemExit(status)
        return
    memory_run: Memory100Run | None = None
    if args.memory_100 is not None and args.memory_100_state is not None:
        memory_run = load_memory_100(args.memory_100, args.memory_100_state)
    memory_ingestion_run: MemoryIngestionRun | None = None
    if args.memory_ingestion is not None and args.memory_ingestion_state is not None:
        memory_ingestion_run = load_memory_ingestion(
            args.memory_ingestion, args.memory_ingestion_state
        )
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
        swebench_tasks = (
            load_swebench(
                tuple(args.swebench_case),
                args.swebench_snapshot,
                args.swebench_submissions,
                None if args.swebench_subset == "all" else args.swebench_subset,
            )
            if args.swebench
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
            memory_ingestion_run,
            issue_run,
            dsqa_run,
            compaction_run,
            gdpval_run,
            jobbench_tasks,
            wandr_tasks,
            handbook_tasks,
            coding_repo_tasks,
            swebench_tasks,
            args.mcp_atlas_data,
            args.mcp_atlas_samples,
            hle_run,
        )
        if args.skill_loading_case:
            subset = _skill_loading_subset(tuple(args.skill_loading_case))
            tasks = tuple(subset if task.name == "skill_loading" else task for task in tasks)
        if args.case:
            tasks = narrowed_tasks(tasks, tuple(args.case))
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
    if swebench_tasks is not None and config.pack.name not in SWEBENCH_PACKS:
        parser.error(
            f"swebench requires [pack] name in {SWEBENCH_PACKS}, found {config.pack.name!r}"
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
    if any(task.name in UFO_APP_TASKS for task in tasks) and (
        config.sandbox.backend not in UFO_APP_BENCH_BACKENDS
    ):
        parser.error(
            f"ufo app suites require [sandbox] backend in {UFO_APP_BENCH_BACKENDS}, "
            f"found {config.sandbox.backend!r}"
        )
    for task in tasks:
        if task.packs and config.pack.name not in task.packs:
            parser.error(
                f"{task.name} requires [pack] name in {task.packs}, found {config.pack.name!r}"
            )
        if args.agent.startswith("profile:") and task.agent != args.agent:
            parser.error(f"{task.name} does not target --agent {args.agent!r}")
        if task.agent is not None and task.agent != args.agent:
            parser.error(f"{task.name} requires --agent {task.agent!r}, found {args.agent!r}")
    workspace_id = args.workspace
    recall_workspace_id = (
        memory_run.readiness.workspace_id
        if memory_run is not None
        else (
            memory_ingestion_run.readiness.workspace_id
            if memory_ingestion_run is not None
            else (issue_run.readiness.workspace_id if issue_run is not None else None)
        )
    )
    if recall_workspace_id is not None:
        if workspace_id is not None and workspace_id != recall_workspace_id:
            parser.error("--workspace does not match the recall readiness workspace")
        workspace_id = recall_workspace_id
        forgetful = sorted(
            task.name
            for task in tasks
            if task.suite == SKILL_LOADING_SUITE or task.name == "new_application"
        )
        if forgetful:
            parser.error(
                f"{', '.join(forgetful)} forget the workspace's memories before each case; "
                "running them against a recall corpus would delete it"
            )
    collector = (
        None
        if recall_workspace_id is None
        else TurnLogCollector.from_endpoint(
            config.o11y.otlp_endpoint,
            recall_workspace_id,
            MEMORY_RECALL_EVENT,
        )
    )
    workflow_wait_seconds = _task_workflow_wait_seconds(tasks)
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
    if swebench_tasks is not None:
        workflow_wait_seconds = SWEBENCH_WORKFLOW_WAIT_SECONDS
        print(f"SWE-bench submissions {args.swebench_submissions}")
    if memory_run is not None:
        workflow_wait_seconds = MEMORY_100_WORKFLOW_WAIT_SECONDS
    if memory_ingestion_run is not None:
        workflow_wait_seconds = MEMORY_INGESTION_WORKFLOW_WAIT_SECONDS
    run_agent = (
        args.agent
        if args.candidate_from_proposal is None
        else CANDIDATE_AGENT_NAME.format(proposal_id=args.candidate_from_proposal)
    )
    recorder = RunRecorder(
        root=args.out,
        id=args.run_id or uuid4(),
        created_at=datetime.now(UTC),
        label=args.label,
        agent=run_agent,
        ufo_version=version("ufo"),
        revision=_revision(),
    )
    reports, agent_prompt = asyncio.run(
        _run(
            config,
            tasks,
            args.agent,
            recorder,
            workspace_id=workspace_id,
            collector=collector,
            workflow_wait_seconds=workflow_wait_seconds,
            mcp_atlas_url=args.mcp_atlas_url,
            mcp_atlas_external_url=args.mcp_atlas_external_url,
            fresh_workspace=args.fresh_workspace,
            remote=args.remote,
            model=args.model,
            environment=args.environment,
            candidate_proposal=args.candidate_from_proposal,
            concurrency=args.concurrency,
            budget_micro_usd=(
                None if args.budget_usd is None else round(args.budget_usd * MICRO_USD_PER_USD)
            ),
        )
    )
    recorder.agent_prompt = agent_prompt
    failed = False
    for report in reports:
        print(report.console_summary)
        failed = failed or not report.passed or report.uncertified is not None
    record = record_run(args.out, recorder.run()).resolve()
    print(f"run {recorder.id} · {record}")
    print(f"viewer {(args.out / 'index.html').resolve()}")
    if failed:
        raise SystemExit(1)


def _with_task_wait(
    target: InProcessTarget, driver: WorkspaceDriver, wait_seconds: float | None
) -> InProcessTarget:
    """The target one suite runs against, carrying that suite's own harness wait. The wait lives on
    the driver behind the target's seams, so a suite that needs longer than the run's default gets a
    driver of its own instead of the shard voting on one number — a deck case was cancelled at the
    default 300s in every shard that carried anything besides `document_visual`."""
    if wait_seconds is None:
        return target
    waited = replace(driver, workflow_wait_seconds=wait_seconds)
    return replace(target, conversations=waited, outcome=waited, turn_steps=waited)


def _revision() -> str:
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
    return revision


async def _run(
    config: Config,
    tasks: tuple[EvalTask, ...],
    agent_name: str,
    recorder: RunRecorder,
    workspace_id: UUID | None = None,
    collector: TurnLogCollector | None = None,
    workflow_wait_seconds: float = WORKFLOW_WAIT_SECONDS,
    mcp_atlas_url: str | None = None,
    mcp_atlas_external_url: str | None = None,
    fresh_workspace: bool = False,
    remote: bool = False,
    model: str | None = None,
    environment: Path | None = None,
    candidate_proposal: UUID | None = None,
    concurrency: int = 1,
    budget_micro_usd: int | None = None,
) -> tuple[tuple[EvalReport, ...], str]:
    init_db(config.database.url)
    manifests = load_manifests(config.pack.name)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    try:
        async with AsyncExitStack() as stack:
            if collector is not None:
                await stack.enter_async_context(collector.serving())
            remote_client: RemoteClient | None = None
            if remote:
                executable = shutil.which("ufo")
                if executable is None:
                    raise RuntimeError("--remote requires ufo on PATH")
                token_secret = os.environ.get(UFO_TOKEN_SECRET_ENV)
                if not token_secret:
                    raise RuntimeError(f"--remote requires {UFO_TOKEN_SECRET_ENV}")
                remote_client = RemoteClient(
                    executable=executable,
                    workspace_url=config.connect.public_base_url
                    or f"http://{config.serve.host}:{config.serve.port}",
                    token_secret=token_secret,
                    home_root=REMOTE_HOME_ROOT,
                    model=model,
                    environment_document=environment,
                )
                await remote_client.validate()
            if fresh_workspace:
                if budget_micro_usd is None:
                    raise ValueError("a fresh remote eval workspace requires a run budget")
                public_url = config.connect.public_base_url
                if public_url is None:
                    raise RuntimeError("--fresh-workspace requires connect.public_base_url")
                control_token = os.environ.get(ONBOARD_CONTROL_TOKEN_ENV)
                if not control_token:
                    raise RuntimeError(f"--fresh-workspace requires {ONBOARD_CONTROL_TOKEN_ENV}")
                onboard = await stack.enter_async_context(
                    AsyncClient(
                        base_url=public_url,
                        headers={"authorization": f"Bearer {control_token}"},
                    )
                )
                workspace_id = await RemoteWorkspaceProvisioner(
                    onboard, budget_micro_usd, runner_model_key()
                ).provision(recorder.id)
            profile = None
            if agent_name.startswith("profile:"):
                if candidate_proposal is not None:
                    raise ValueError("a profile target cannot use --candidate-from-proposal")
                profile_name = agent_name.removeprefix("profile:")
                profile = SubagentRegistry(
                    (*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests))
                ).get(profile_name)
                resolved_agent = await resolve_workspace_and_agent(DEFAULT_AGENT_NAME, workspace_id)
            elif candidate_proposal is not None:
                workspace_id, agent_name = await seed_candidate_agent(
                    candidate_proposal, workspace_id
                )
                resolved_agent = await resolve_workspace_and_agent(agent_name, workspace_id)
            else:
                resolved_agent = await resolve_workspace_and_agent(agent_name, workspace_id)
            (
                workspace_id,
                agent_id,
                agent_prompt,
                agent_model,
                agent_reasoning,
                agent_sandbox_size,
            ) = resolved_agent
            if budget_micro_usd is not None and not fresh_workspace:
                await EvalRunBudget(recorder.id, budget_micro_usd).install(workspace_id)
            target_prompt = profile.prompt if profile is not None else agent_prompt
            recorder.agent_prompt = target_prompt
            recorder.workspace_id = workspace_id
            blob_backend = blob_store_for(config.blob)
            match blob_backend:
                case S3BlobStore():
                    stack.push_async_callback(blob_backend.close)
            blob = WorkspaceBlobStore(backend=blob_backend)
            dbos = replay_safe_client(config.database.system_url)
            registry = model_registry(config, manifests)
            if model is None:
                resolved_agent_model = registry.resolve(agent_model)
            else:
                resolved_agent_model = model
                registry.spec(resolved_agent_model)
            driver = WorkspaceDriver(
                workspace_id,
                agent_id,
                agent_prompt,
                blob,
                dbos,
                config.sandbox.workspace_root,
                resolved_agent_model,
                pricing=registry.pricing,
                workflow_wait_seconds=workflow_wait_seconds,
                remote=remote_client,
                environment_document=environment,
            )
            ctx = context_for(
                "evals",
                frozenset(),
                blob=blob,
                invoker=AdmissionInvoker(
                    admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
                    workspace_id=workspace_id,
                ),
                model_resolver=registry,
                model_job=EVAL_TARGET_JOB,
            )
            if ctx.model is None:
                raise RuntimeError("eval context requires model access")
            slots = asyncio.Semaphore(concurrency)
            with ws(workspace_id), agent(agent_id):
                loadable_skills: frozenset[str] | None = None
                if any(task.suite == SKILL_LOADING_SUITE for task in tasks):
                    loadable_skills = frozenset(
                        skill_registry(manifests, (model_catalog_skill(registry),)).by_name
                    )
                    subagents = SubagentRegistry(
                        (*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests))
                    )
                    loadable_skills |= frozenset(
                        (
                            spawn_catalog_skill(
                                await spawn_targets(subagents, WORKSPACE_AUTHORITY)
                            ).name,
                        )
                    )
                compaction: CompactionTarget | None = None
                if any(task.suite == "compaction" for task in tasks):
                    compaction = CompactionTarget(
                        client=await registry.client_for(resolved_agent_model),
                        model=resolved_agent_model,
                        blob=blob,
                        workspace_root=config.sandbox.workspace_root,
                        context_window=registry.spec(resolved_agent_model).context_window,
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
                        resolved_agent_model,
                        agent_reasoning,
                        mcp_atlas_url,
                        mcp_atlas_external_url,
                    ),
                    compaction=compaction,
                    loadable_skills=loadable_skills,
                    workspace_probe_for=(
                        lambda conversation_id: AppBenchWorkspaceProbe(
                            conversation_id,
                            driver,
                            (
                                config.sandbox.image_ref
                                if any(task.name == "ufo-app-qa-replay" for task in tasks)
                                else None
                            ),
                        )
                    )
                    if config.sandbox.backend in UFO_APP_BENCH_BACKENDS
                    and any(task.name in UFO_APP_TASKS for task in tasks)
                    else None,
                )
                targets = tuple(
                    _with_task_wait(
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
                        ),
                        driver,
                        task.wait_seconds,
                    )
                    for task in tasks
                )
                completed: dict[int, EvalReport] = {}

                def finished(index: int, report: EvalReport) -> None:
                    """Stamp one suite's report with the runtime it ran against and record it. The
                    stamp happens here rather than after the whole run, so the report on disk is the
                    final one from the moment its suite ends. Every identity the run's cases
                    attested rides along informationally — a fleet deploy rolling mid-run shows as
                    two identities, never a refusal — while a missing or mismatched attestation
                    marks the report uncertified with the refusal instead of raising: the
                    measurements survive on disk and on the console, and the run fails at exit (a
                    66-case remote run once lost every verdict to a mid-run deploy)."""
                    task = tasks[index]
                    runtime_attestation = None
                    uncertified = None
                    runtime_identities = None
                    if remote_client is not None:
                        runtime_identities = remote_client.runtime.identity_counts() or None
                        try:
                            runtime_attestation = remote_client.runtime.verify(
                                resolved_agent_model,
                                agent_reasoning,
                                remote_client.stored_environment(),
                            )
                        except RuntimeError as error:
                            uncertified = str(error)
                        else:
                            recorder.runtime = runtime_attestation
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
                                "agentModel": resolved_agent_model,
                                "agentSandboxSize": agent_sandbox_size,
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
                                **(
                                    {"remoteRuntime": runtime_attestation.model_dump(mode="json")}
                                    if runtime_attestation is not None
                                    else {}
                                ),
                                **({"uncertified": uncertified} if uncertified is not None else {}),
                            }
                        )
                    completed[index] = report.model_copy(
                        update={
                            "digest": digest,
                            "target_model": resolved_agent_model,
                            "judge_model": task.judge_model,
                            "simulator_model": task.simulator_model,
                            "judge_revision": task.judge_revision,
                            "uncertified": uncertified,
                            "runtime_identities": runtime_identities,
                        }
                    )
                    recorder.record(index, completed[index])

                await _task_reports(tasks, targets, slots, finished)
            return tuple(completed[index] for index in sorted(completed)), target_prompt
    finally:
        init_workspace_credentials(None)
        await dispose_db()


async def _task_reports(
    tasks: tuple[EvalTask, ...],
    targets: tuple[InProcessTarget, ...],
    slots: asyncio.Semaphore,
    on_report: Callable[[int, EvalReport], None],
) -> None:
    """Run every suite of the shard, handing each finished report to `on_report` in completion
    order. The exclusive suites are guarded the way the concurrent wave already is: they run last,
    one at a time, and one of them raising leaves the rest to run and still surfaces in the group
    raised at the end — an exclusive suite raising unguarded used to end the shard on the spot."""

    async def run_task(index: int) -> None:
        on_report(index, await tasks[index].run(targets[index], slots))

    overlapping = tuple(index for index, task in enumerate(tasks) if not task.exclusive)
    outcomes = await asyncio.gather(
        *(run_task(index) for index in overlapping), return_exceptions=True
    )
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    if not errors:
        for index, task in enumerate(tasks):
            if task.exclusive:
                try:
                    await run_task(index)
                except Exception as error:
                    errors.append(error)
    if errors:
        raise BaseExceptionGroup("eval tasks raised", tuple(errors))


async def _capture_swebench(
    config: Config,
    workspace_id: UUID,
    case_ids: tuple[str, ...],
    submissions_root: Path,
) -> CapturedPatches:
    init_db(config.database.url)
    try:
        async with AsyncExitStack() as stack:
            backend = blob_store_for(config.blob)
            match backend:
                case S3BlobStore():
                    stack.push_async_callback(backend.close)
            with ws(workspace_id):
                return await capture_shared_patches(
                    WorkspaceBlobStore(backend=backend), case_ids, submissions_root
                )
    finally:
        await dispose_db()


async def _reconstruct(config: Config, run: EvalRun, workspace_id: UUID) -> EvalRun:
    init_db(config.database.url)
    try:
        with ws(workspace_id):
            return await RunReconstruction(
                workspace_id=workspace_id,
                blob=WorkspaceBlobStore(backend=blob_store_for(config.blob)),
                run=run,
            ).reconstruct()
    finally:
        await dispose_db()


async def _mcp_atlas_target(
    stack: AsyncExitStack,
    config: Config,
    tasks: tuple[EvalTask, ...],
    agent_prompt: str,
    resolved_agent_model: str,
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
    target_context = context_for(
        "evals",
        frozenset(),
        model_resolver=replace(registry, auto_model=resolved_agent_model),
        model_job=MCP_ATLAS_JOB,
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
        knowledge_cutoff=registry.spec(resolved_agent_model).knowledge_cutoff,
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
        resolved_agent_model,
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
        blob=WorkspaceBlobStore(backend=blob_store_for(config.blob)),
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
        model_job=EVAL_JUDGE_JOB,
    )
    if context.model is None:
        raise RuntimeError(f"eval model leg {model!r} requires model access")
    return ModelJudge(context.model, max_tokens, reasoning)


def _skill_loading_subset(names: tuple[str, ...]) -> EvalTask:
    by_name = {case.name: case for case in (*SKILL_LOADING_CASES, *SKILL_MEMBER_CASES)}
    missing = tuple(name for name in names if name not in by_name)
    if missing:
        raise ValueError(f"unknown skill_loading case: {', '.join(missing)}")
    return skill_loading_task(tuple(by_name[name] for name in names), packs=SKILL_LOADING_PACKS)


def _tasks(
    names: tuple[str, ...],
    memory_run: Memory100Run | None,
    memory_ingestion_run: MemoryIngestionRun | None = None,
    issue_run: IssueRecallRun | None = None,
    dsqa_run: DSQA100Run | None = None,
    compaction_run: CompactionRun | None = None,
    gdpval_run: GDPvalCalibration | None = None,
    jobbench_tasks: tuple[EvalTask, ...] | None = None,
    wandr_tasks: tuple[EvalTask, ...] | None = None,
    handbook_tasks: tuple[EvalTask, ...] | None = None,
    coding_repo_tasks: tuple[EvalTask, ...] | None = None,
    swebench_tasks: tuple[EvalTask, ...] | None = None,
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
    if swebench_tasks is not None:
        return selected_tasks(swebench_tasks, names)
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
    if memory_ingestion_run is not None:
        return selected_tasks(
            (*TASKS, *memory_ingestion_run.tasks, *mcp_atlas),
            names or tuple(task.name for task in memory_ingestion_run.tasks),
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
