"""Provision isolated eval stacks and run a matrix of suites concurrently.

`python -m evals.stack matrix.toml` reads `[[run]]` blocks, provisions one isolated stack per
run — a run directory holding a derived ufo.toml (its own database, blob root, serve and proxy
ports), a freshly seeded workspace, and its own `ufoctl serve` — then drives a single-suite
`python -m evals` child against each stack and records every run into one shared archive. Run
directories and per-run databases are retained for reconstruction and post-mortems; the final
summary prints each stack's run dir and database URL."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Literal, Self
from uuid import UUID, uuid4

import asyncpg
import tomli_w
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import AsyncClient, HTTPError
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.engine import make_url

from evals.budget import EvalRunBudget
from evals.harness.viewer import load_runs, write_viewer
from evals.sandbox_image import SandboxImagePlan, sandbox_image_plan
from ufo.config import Config, ConnectConfig, DatabaseConfig, O11yConfig
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.models.pricing import MICRO_USD_PER_USD
from ufo.harness.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    EGRESS_CA_KEY_ENV,
    EGRESS_CONTROL_TOKEN_ENV,
)
from ufo.host.ext.loader import load_manifests
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.schema.records import ReasoningEffort

RUNS_ROOT = Path(".local/evals")
DEFAULT_OUT = Path("eval-reports")
STACK_OWNER_EMAIL = "evals@localhost"
APP_SUITES = frozenset({"ufo-app-bench", "ufo-app-copy", "ufo-app-qa-replay"})
CREATION_SUITES = frozenset({"new_application"})
HOMEPAGE_SUITES = frozenset({"app_home_change"})
APP_PAGE_SUITES = APP_SUITES | CREATION_SUITES | HOMEPAGE_SUITES
SANDBOX_IMAGE_SUITES = APP_PAGE_SUITES | {"red_after_green"}
CREATION_DISABLED_JOBS = ("web:seed_homepages",)
ISOLATED_EXTERNAL_BILLING_JOBS = (
    "metronome:usage_shipper",
    "metronome:balance_topup",
)
APPLICATION_BUILD_PRODUCTS = (
    Path("extensions/sites/ufo_ext_sites/page/kit/kit.js"),
    Path("extensions/sites/ufo_ext_sites/page/kit/kit.css"),
)
POSTGRES_NAME_LIMIT = 63
POSTGRES_SYSTEM_SUFFIX = "_dbos"
DATABASE_NAME_DIGEST_CHARS = 12
DATABASE_OWNERSHIP_FILE = "database-ownership.json"
DATABASE_OWNERSHIP_MARKER_PREFIX = "ufo-eval:"
READY_DEADLINE_SECONDS = 180.0
READY_POLL_SECONDS = 0.5
SHUTDOWN_GRACE_SECONDS = 30.0
STACK_CANCEL_SIGINT_WAIT_SECONDS = 5.0
STACK_CANCEL_SIGTERM_WAIT_SECONDS = 5.0
DOCKER_BACKEND = "docker"
SANDBOX_CONTAINER_PREFIX = "ufo-sbx-"
SANDBOX_NETWORK_PREFIX = "ufo-sandbox-"
EGRESS_READY_DEADLINE_SECONDS = 30.0
EGRESS_GRACEFUL_SHUTDOWN_SECONDS = 2
ORCHESTRATOR_ARGS = (
    "--out",
    "--view",
    "--share",
    "--reconstruct",
    "--list",
    "--memory-100",
    "--memory-100-state",
    "--memory-ingestion",
    "--memory-ingestion-state",
    "--issue-recall",
    "--run-id",
)
ORCHESTRATOR_ENV = ("UFO_CONFIG", "UFOCTL_DIR")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.stack")
    parser.add_argument("matrix", type=Path, help="TOML file of [[run]] blocks")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="shared run archive")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    matrix = Matrix.model_validate(tomllib.loads(args.matrix.read_text()))
    stamp = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex}"
    stacks = tuple(
        EvalStack.provision(
            spec,
            root=RUNS_ROOT / stamp / spec.label,
            out=args.out.resolve(),
            repo_root=Path.cwd(),
        )
        for spec in matrix.run
    )
    results = asyncio.run(_run_stacks(stacks))
    write_viewer(args.out, load_runs(args.out))
    for result in results:
        status = "pass" if result.passed else f"FAIL ({result.error or result.exit_code})"
        print(f"{result.label}\t{status}\t{result.root}\t{result.database_url}")
    print(f"viewer {(args.out / 'index.html').resolve()}")
    if not all(result.passed for result in results):
        raise SystemExit(1)


async def _run_stacks(stacks: tuple["EvalStack", ...]) -> tuple["StackResult", ...]:
    creation = asyncio.Lock()

    async def guarded(stack: EvalStack) -> StackResult:
        try:
            return await stack.run(creation)
        except Exception as error:
            return StackResult(
                label=stack.spec.label,
                passed=False,
                exit_code=None,
                root=stack.root,
                database_url=stack.config.database.url,
                error=f"{type(error).__name__}: {error}",
            )

    return tuple(await asyncio.gather(*(guarded(stack) for stack in stacks)))


async def _stack_exited(
    communication: asyncio.Task[tuple[bytes, bytes | None]], timeout_seconds: float
) -> bool:
    try:
        await asyncio.wait_for(asyncio.shield(communication), timeout_seconds)
    except TimeoutError:
        return False
    return True


async def cancel_stack_process(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes | None]],
) -> None:
    """Terminate and reap a stack subprocess group after its orchestrator is cancelled."""
    for sent, timeout_seconds in (
        (signal.SIGINT, STACK_CANCEL_SIGINT_WAIT_SECONDS),
        (signal.SIGTERM, STACK_CANCEL_SIGTERM_WAIT_SECONDS),
    ):
        if process.returncode is None:
            try:
                os.killpg(process.pid, sent)
            except ProcessLookupError:
                pass
        if await _stack_exited(communication, timeout_seconds):
            return
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await asyncio.shield(communication)


class RunSpec(BaseModel):
    """One `[[run]]` block: a label, the template ufo.toml carrying the suite's knobs (pack,
    models, research), the `python -m evals` arguments, and extra environment to pass through."""

    model_config = ConfigDict(extra="forbid")
    label: str
    config: Path
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}
    model: str | None = None
    environment: Path | None = None
    reasoning: ReasoningEffort | None = None
    memory_100: Path | None = None
    memory_ingestion: Path | None = None
    issue_recall: bool = False
    member_model_provider: Literal["anthropic", "openai"] | None = None

    @field_validator("label")
    @classmethod
    def _label_names_a_directory_and_database(cls, label: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", label):
            raise ValueError(f"label {label!r} must match [a-z0-9][a-z0-9_-]{{0,39}}")
        return label

    @model_validator(mode="after")
    def _stack_owns_the_orchestration_surface(self) -> Self:
        for token in self.args:
            flag = token.partition("=")[0]
            if flag in ORCHESTRATOR_ARGS:
                raise ValueError(f"run {self.label!r} passes {flag} — the stack owns it")
        for name in ORCHESTRATOR_ENV:
            if name in self.env:
                raise ValueError(f"run {self.label!r} sets {name} — the stack owns it")
        if any(
            token in APP_SUITES
            or (token.startswith("--only=") and token.removeprefix("--only=") in APP_SUITES)
            for token in self.args
        ) and (self.model is not None or self.reasoning is not None):
            raise ValueError(
                f"run {self.label!r}: app suites use the template parent agent; "
                "vary the application-builder profile"
            )
        if self.memory_100 is not None and (self.model is not None or self.reasoning is not None):
            raise ValueError(
                f"run {self.label!r} sets model or reasoning with a memory corpus — "
                "materialization owns the agent"
            )
        if self.memory_ingestion is not None and self.model is not None:
            raise ValueError(
                f"run {self.label!r} sets a model with memory ingestion — "
                "models.auto_model owns the recall model"
            )
        corpora = sum(
            (
                self.memory_100 is not None,
                self.memory_ingestion is not None,
                self.issue_recall,
            )
        )
        if corpora > 1:
            raise ValueError(f"run {self.label!r} materializes two corpora — run them separately")
        return self


class Matrix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: tuple[RunSpec, ...]

    @model_validator(mode="after")
    def _non_empty_with_unique_labels(self) -> Self:
        if not self.run:
            raise ValueError("matrix declares no [[run]] blocks")
        labels = [spec.label.replace("-", "_") for spec in self.run]
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        if repeated:
            raise ValueError(
                f"run labels collide once - and _ merge in database names: {', '.join(repeated)}"
            )
        return self


@dataclass(frozen=True)
class StackResult:
    label: str
    passed: bool
    exit_code: int | None
    root: Path
    database_url: str
    error: str | None = None


class DatabaseAuthority(BaseModel):
    """Password-free identity of the Postgres server allowed to own an eval database pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    username: str | None
    host: str | None
    port: int | None
    database: str | None
    options: tuple[tuple[str, tuple[str, ...]], ...]


class DatabaseOwnership(BaseModel):
    """Provision-time authority for one exact eval application and DBOS database pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    run_root: Path
    authority: DatabaseAuthority
    application: str
    system: str

    @property
    def marker(self) -> str:
        return f"{DATABASE_OWNERSHIP_MARKER_PREFIX}{self.id}"


@dataclass(frozen=True)
class EvalStack:
    """One isolated stack: seed a fresh workspace, boot its own serve, drive the eval child
    against it, and tear the serve down — everything under `root`, nothing shared but the archive.
    """

    spec: RunSpec
    root: Path
    out: Path
    repo_root: Path
    config: Config
    config_file: Path
    serve_binary: Path
    admin_database_url: str | None
    database_ownership: DatabaseOwnership | None
    env: dict[str, str]
    serve_probe: socket.socket
    proxy_probe: socket.socket
    otlp_probe: socket.socket | None
    seed_log: IO[bytes]
    serve_log: IO[bytes]
    egress_log: IO[bytes]
    eval_log: IO[bytes]
    process_log: IO[str]
    run_budget: EvalRunBudget | None
    sandbox_image: SandboxImagePlan | None = None

    @classmethod
    def provision(cls, spec: RunSpec, root: Path, out: Path, repo_root: Path) -> Self:
        template = template_config(spec.config.read_text())
        if (
            spec.memory_100 is not None or spec.memory_ingestion is not None
        ) and not template.database.url.startswith("postgresql"):
            raise ValueError(f"run {spec.label!r}: memory evaluation requires a Postgres template")
        if (
            spec.memory_100 is not None or spec.memory_ingestion is not None or spec.issue_recall
        ) and template.o11y.otlp_endpoint is None:
            raise ValueError(
                f"run {spec.label!r}: a recall-graded corpus requires a template [o11y] "
                "otlp_endpoint — the collector binds it"
            )
        root.mkdir(parents=True, exist_ok=False)
        serve_probe, serve_port = _port_probe()
        proxy_probe, proxy_port = _port_probe()
        otlp_probe, otlp_port = _port_probe() if template.o11y.otlp_endpoint else (None, None)
        config = derived_config(
            template,
            root=root.resolve(),
            serve_port=serve_port,
            proxy_port=proxy_port,
            otlp_port=otlp_port,
            database_name=_database_name(root),
        )
        selected_parser = argparse.ArgumentParser(add_help=False)
        selected_parser.add_argument("--only", nargs="*", default=())
        selected_parser.add_argument("--remote", action="store_true")
        selected_parser.add_argument("--budget-usd", type=float)
        selected, _ = selected_parser.parse_known_args(spec.args)
        if selected.budget_usd is not None and (not selected.remote or selected.budget_usd <= 0):
            raise ValueError(
                f"run {spec.label!r}: --budget-usd requires --remote and a positive amount"
            )
        run_budget = (
            None
            if selected.budget_usd is None
            else EvalRunBudget(uuid4(), round(selected.budget_usd * MICRO_USD_PER_USD))
        )
        selected_suites = frozenset(selected.only)
        if not APP_SUITES.isdisjoint(selected_suites) and not selected_suites <= APP_SUITES:
            raise ValueError("app suites require a separate eval stack")
        registered_jobs = {
            f"{manifest.name}:{job.name}"
            for manifest in load_manifests(config.pack.name)
            for job in manifest.jobs
        }
        disabled_jobs = tuple(
            dict.fromkeys(
                (
                    *config.serve.disabled_jobs,
                    *(job for job in ISOLATED_EXTERNAL_BILLING_JOBS if job in registered_jobs),
                )
            )
        )
        if not CREATION_SUITES.isdisjoint(selected.only):
            disabled_jobs = tuple(dict.fromkeys((*disabled_jobs, *CREATION_DISABLED_JOBS)))
        config = config.model_copy(
            update={"serve": config.serve.model_copy(update={"disabled_jobs": disabled_jobs})}
        )
        admin_database_url = (
            template.database.url if template.database.url.startswith("postgresql") else None
        )
        database_ownership = None
        if admin_database_url is not None:
            application = _database_name(root.resolve())
            database_ownership = DatabaseOwnership(
                id=uuid4(),
                run_root=root.resolve(),
                authority=_database_authority(admin_database_url),
                application=application,
                system=f"{application}{POSTGRES_SYSTEM_SUFFIX}",
            )
            (root / DATABASE_OWNERSHIP_FILE).write_text(
                database_ownership.model_dump_json(indent=2)
            )
        sandbox_image = None
        if (
            not SANDBOX_IMAGE_SUITES.isdisjoint(selected_suites)
            and config.sandbox.backend == DOCKER_BACKEND
        ):
            sandbox_image = sandbox_image_plan(repo_root, root / "sandbox.Dockerfile")
            config = config.model_copy(
                update={
                    "sandbox": config.sandbox.model_copy(
                        update={"image_ref": sandbox_image.reference}
                    )
                }
            )
        config_file = root / "ufo.toml"
        config_file.write_text(tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)))
        serve_binary = (root / "eval-serve").absolute()
        serve_binary.symlink_to(Path(sys.executable).with_name("ufoctl"))
        env = dict(os.environ) | spec.env
        # Each stack derives its own per-run owner DSN as database.owner_url. Drop any UFO_OWNER_DSN
        # inherited from the shell, which _shared_owner_dsn prefers over the config — else it would
        # point this run's cross-workspace owner engine at a foreign (possibly production) database.
        env.pop(OWNER_DSN_ENV, None)
        env["UFO_CONFIG"] = str(config_file.resolve())
        env["UFOCTL_DIR"] = str((root / ".ufoctl").resolve())
        key_env = config.credentials.key_env
        env[key_env] = env.get(key_env) or Fernet.generate_key().decode()
        secret_env = config.artifacts.token_secret_env
        env[secret_env] = env.get(secret_env) or secrets.token_urlsafe(32)
        # The shared egress material serve and its ufo-egress read from the same env: serve trusts
        # the CA and mounts the control RPC, the proxy signs leaves with the key and calls back with
        # the control token, and both verify sandbox run tokens against one UFO_TOKEN_SECRET.
        ca_cert, ca_key = _mint_egress_ca()
        env[EGRESS_CA_CERT_ENV] = ca_cert
        env[EGRESS_CA_KEY_ENV] = ca_key
        env.setdefault(EGRESS_CONTROL_TOKEN_ENV, secrets.token_urlsafe(32))
        env.setdefault(UFO_TOKEN_SECRET_ENV, secrets.token_urlsafe(32))
        return cls(
            spec=spec,
            root=root,
            out=out,
            repo_root=repo_root,
            config=config,
            config_file=config_file,
            serve_binary=serve_binary,
            admin_database_url=admin_database_url,
            database_ownership=database_ownership,
            env=env,
            serve_probe=serve_probe,
            proxy_probe=proxy_probe,
            otlp_probe=otlp_probe,
            seed_log=(root / "seed.log").open("wb"),
            serve_log=(root / "serve.log").open("wb"),
            egress_log=(root / "egress.log").open("wb"),
            eval_log=(root / "eval.log").open("wb"),
            process_log=(root / "process.log").open("w"),
            run_budget=run_budget,
            sandbox_image=sandbox_image,
        )

    async def run(self, creation: asyncio.Lock) -> StackResult:
        try:
            self._require_application_build_products()
            egress_binary = _egress_binary(self.repo_root)
            await self._prepare_sandbox_image()
            async with creation:
                await self._create_databases()
            await self._checked(await self._ufoctl("migrate", log=self.seed_log), "seed")
            await self._preflight()
            readiness = await self._seed()
            await self._prepare_app_eval()
            serve: asyncio.subprocess.Process | None = None
            egress: asyncio.subprocess.Process | None = None
            try:
                serve = await self._start_serve()
                egress = await self._start_egress(egress_binary)
                await self._ready(serve)
                await self._egress_ready(egress)
                exit_code = await self._drive(serve, egress, readiness)
            finally:
                if serve is not None:
                    await self._shutdown(serve, egress)
        finally:
            for handle in (
                self.serve_probe,
                self.proxy_probe,
                self.otlp_probe,
                self.seed_log,
                self.serve_log,
                self.egress_log,
                self.eval_log,
                self.process_log,
            ):
                if handle is not None:
                    handle.close()
        return StackResult(
            label=self.spec.label,
            passed=exit_code == 0,
            exit_code=exit_code,
            root=self.root,
            database_url=self.config.database.url,
        )

    async def _prepare_sandbox_image(self) -> None:
        if self.sandbox_image is None:
            return
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "evals.sandbox_image",
            str(self.sandbox_image.dockerfile),
            self.sandbox_image.reference,
            self.sandbox_image.definition_digest,
            cwd=self.repo_root,
            env=self.env,
            stdout=self.seed_log,
            stderr=self.seed_log,
        )
        await self._checked(process, "seed")

    def _require_application_build_products(self) -> None:
        selected = argparse.ArgumentParser(add_help=False)
        selected.add_argument("--only", nargs="*", default=())
        parsed, _ = selected.parse_known_args(self.spec.args)
        if APP_PAGE_SUITES.isdisjoint(parsed.only):
            return
        missing = tuple(
            path for path in APPLICATION_BUILD_PRODUCTS if not (self.repo_root / path).is_file()
        )
        if missing:
            names = ", ".join(str(path) for path in missing)
            raise RuntimeError(
                f"application eval requires generated build products: {names}; "
                "run `pnpm -C extensions/web/frontend run build`"
            )

    async def _create_databases(self) -> None:
        admin_database_url = self.admin_database_url
        if admin_database_url is None:
            return
        ownership = self.database_ownership
        if ownership is None:
            raise RuntimeError("Postgres eval stack has no database ownership record")

        async def create_and_mark() -> None:
            connection = await asyncpg.connect(_asyncpg_dsn(admin_database_url))
            try:
                names = (ownership.application, ownership.system)
                existing = await connection.fetch(
                    "select datname from pg_database where datname = any($1::name[])", list(names)
                )
                if existing:
                    found = ", ".join(sorted(row["datname"] for row in existing))
                    raise RuntimeError(f"eval stack databases already exist: {found}")
                for name in names:
                    await connection.execute(f'create database "{name}"')
                    await connection.execute(
                        f"comment on database \"{name}\" is '{ownership.marker}'"
                    )
            finally:
                await connection.close()

        creation = asyncio.create_task(create_and_mark())
        try:
            await asyncio.shield(creation)
        except asyncio.CancelledError:
            await creation
            raise

    async def _seed(self) -> Path | None:
        if self.spec.memory_100 is not None:
            return await self._materialize(
                "evals.memory_100.materialize", "--snapshot", str(self.spec.memory_100.resolve())
            )
        if self.spec.memory_ingestion is not None:
            await self._checked(await self._ufoctl("migrate", log=self.seed_log), "seed")
            reasoning_args = (
                () if self.spec.reasoning is None else ("--agent-reasoning", self.spec.reasoning)
            )
            return await self._materialize(
                "evals.memory_ingestion.materialize",
                *reasoning_args,
                "--snapshot",
                str(self.spec.memory_ingestion.resolve()),
            )
        await self._checked(await self._ufoctl(*self._seed_args(), log=self.seed_log), "seed")
        if not self.spec.issue_recall:
            return None
        return await self._materialize("evals.issue_recall.materialize")

    async def _materialize(self, module: str, *corpus_args: str) -> Path:
        budget_args: tuple[str, ...] = ()
        if self.run_budget is not None:
            budget_args = (
                "--run-id",
                str(self.run_budget.run_id),
                "--budget-micro-usd",
                str(self.run_budget.micro_usd),
            )
        materialize = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module,
            *corpus_args,
            *budget_args,
            "--state",
            str(self.root.resolve() / "state"),
            cwd=self.repo_root,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.seed_log,
        )
        stdout, _ = await materialize.communicate()
        return materialize_readiness(materialize.returncode, stdout, self._log_path("seed"))

    async def _prepare_app_eval(self) -> None:
        only = argparse.ArgumentParser(add_help=False)
        only.add_argument("--only", nargs="*", default=())
        selected, _ = only.parse_known_args(self.spec.args)
        app_selected = not APP_SUITES.isdisjoint(selected.only)
        creation_selected = not CREATION_SUITES.isdisjoint(selected.only)
        if not app_selected and not creation_selected:
            return
        argv = [sys.executable, "-m", "evals.suites.ufo_app_prepare"]
        if creation_selected and not app_selected:
            argv.append("--creation")
        prepare = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.repo_root,
            env=self.env,
            stdout=self.seed_log,
            stderr=self.seed_log,
        )
        await self._checked(prepare, "seed")

    def _seed_args(self) -> tuple[str, ...]:
        argv = ["init", "--email", STACK_OWNER_EMAIL]
        if self.spec.model is not None:
            argv += ["--model", self.spec.model]
        if self.spec.reasoning is not None:
            argv += ["--reasoning", self.spec.reasoning]
        if self.spec.member_model_provider is not None:
            argv += ["--member-model-provider", self.spec.member_model_provider]
        return tuple(argv)

    async def _ufoctl(
        self, *argv: str, log: IO[bytes], binary: Path | None = None
    ) -> asyncio.subprocess.Process:
        binary = binary or Path(sys.executable).with_name("ufoctl")
        if not binary.exists():
            raise RuntimeError(f"ufoctl not found beside the interpreter: {binary}")
        return await asyncio.create_subprocess_exec(
            str(binary), *argv, cwd=self.root, env=self.env, stdout=log, stderr=log
        )

    async def _checked(self, process: asyncio.subprocess.Process, step: str) -> None:
        if await process.wait() != 0:
            raise RuntimeError(f"{step} exited {process.returncode} — see {self._log_path(step)}")

    async def _preflight(self) -> None:
        """Boot the real serve once to ready, then terminate it, before the corpus is paid for.
        Serve's boot is the config's whole validator — extension seam requirements, the connector
        redirect, model, carrier, and index selection all fail there in seconds — so a template a
        knob behind the pack fails the run immediately rather than after minutes of
        materialization, and every validation serve grows later is preflighted for free. The
        preflight process is gone before seeding, so nothing of serve's races the materializer's
        drained consumers."""
        serve = await self._start_serve()
        try:
            await self._ready(serve)
        finally:
            await self._shutdown(serve)

    async def _start_serve(self) -> asyncio.subprocess.Process:
        self.serve_probe.close()
        self.proxy_probe.close()
        process = await self._ufoctl("serve", log=self.serve_log, binary=self.serve_binary)
        self._process_event("started", "serve", process)
        return process

    async def _start_egress(self, binary: Path) -> asyncio.subprocess.Process:
        """Run ufo-egress on the probed proxy port, sharing serve's env (the CA, control token, and
        run-token secret) and pointing back at serve's egress-control RPC. `_start_serve` freed the
        proxy-port probe, so the wire binds it and the local carrier's HTTP(S)_PROXY reaches it."""
        env = dict(self.env) | {
            "UFO_EGRESS_BIND": "127.0.0.1",
            "UFO_EGRESS_PORT": str(self.config.sandbox.proxy_port),
            "UFO_EGRESS_CONTROL_URL": f"http://127.0.0.1:{self.config.serve.port}",
            "UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS": str(EGRESS_GRACEFUL_SHUTDOWN_SECONDS),
        }
        process = await asyncio.create_subprocess_exec(
            str(binary), cwd=self.root, env=env, stdout=self.egress_log, stderr=self.egress_log
        )
        self._process_event("started", "egress", process)
        return process

    async def _egress_ready(self, egress: asyncio.subprocess.Process) -> None:
        port = self.config.sandbox.proxy_port
        deadline = asyncio.get_running_loop().time() + EGRESS_READY_DEADLINE_SECONDS
        while True:
            if egress.returncode is not None:
                raise RuntimeError(
                    f"ufo-egress exited {egress.returncode} before it bound the proxy port — "
                    f"see {self._log_path('egress')}"
                )
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                pass
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError(
                    f"ufo-egress not listening on :{port} after "
                    f"{EGRESS_READY_DEADLINE_SECONDS:.0f}s — see {self._log_path('egress')}"
                )
            await asyncio.sleep(READY_POLL_SECONDS)

    async def _ready(self, serve: asyncio.subprocess.Process) -> None:
        url = f"http://127.0.0.1:{self.config.serve.port}/openapi.json"
        deadline = asyncio.get_running_loop().time() + READY_DEADLINE_SECONDS
        async with AsyncClient(timeout=READY_POLL_SECONDS * 4) as client:
            while True:
                if serve.returncode is not None:
                    raise RuntimeError(
                        f"serve exited {serve.returncode} before ready — "
                        f"see {self._log_path('serve')}"
                    )
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        return
                except HTTPError:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError(
                        f"serve not ready after {READY_DEADLINE_SECONDS:.0f}s — "
                        f"see {self._log_path('serve')}"
                    )
                await asyncio.sleep(READY_POLL_SECONDS)

    async def _drive(
        self,
        serve: asyncio.subprocess.Process,
        egress: asyncio.subprocess.Process,
        readiness: Path | None,
    ) -> int:
        if self.otlp_probe is not None:
            self.otlp_probe.close()
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "evals",
            *self._child_args(readiness),
            cwd=self.repo_root,
            env=self.env,
            stdout=self.eval_log,
            stderr=self.eval_log,
            start_new_session=True,
        )
        self._process_event("started", "eval", child)
        child_wait = asyncio.ensure_future(child.wait())
        infra = {
            asyncio.ensure_future(serve.wait()): ("serve", serve),
            asyncio.ensure_future(egress.wait()): ("egress", egress),
        }
        try:
            done, _ = await asyncio.wait((child_wait, *infra), return_when=asyncio.FIRST_COMPLETED)
            for future in done:
                if future is child_wait:
                    self._process_event("exited", "eval", child)
                elif future in infra:
                    name, process = infra[future]
                    self._process_event("exited", name, process)
            dead = next((infra[future] for future in infra if future in done), None)
            if dead is not None and child_wait not in done:
                name, process = dead
                await self._stop_eval(child, child_wait)
                raise RuntimeError(
                    f"{name} exited {process.returncode} mid-run — see {self._log_path(name)}"
                )
            return await child_wait
        except asyncio.CancelledError:
            await self._stop_eval(child, child_wait)
            raise
        finally:
            for future in infra:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*infra, return_exceptions=True)

    async def _stop_eval(
        self,
        child: asyncio.subprocess.Process,
        child_wait: asyncio.Future[int],
    ) -> None:
        if child.returncode is None:
            self._process_event("terminate", "eval", child)
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(asyncio.shield(child_wait), SHUTDOWN_GRACE_SECONDS)
        except TimeoutError:
            self._process_event("kill", "eval", child)
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await child_wait
        self._process_event("exited", "eval", child)

    def _child_args(self, readiness: Path | None = None) -> tuple[str, ...]:
        argv = list(self.spec.args)
        flags = {token.partition("=")[0] for token in argv}
        if "--remote" in flags and self.spec.model is not None:
            argv += ["--model", self.spec.model]
        if self.spec.environment is not None:
            argv += ["--environment", str(self.spec.environment.resolve())]
        argv += ["--out", str(self.out)]
        if self.run_budget is not None:
            argv += ["--run-id", str(self.run_budget.run_id)]
        if "--label" not in flags:
            argv += ["--label", self.spec.label]
        if "--jobbench" in flags and "--jobbench-submissions" not in flags:
            argv += ["--jobbench-submissions", str(self.root.resolve() / "submissions")]
        if readiness is not None and self.spec.memory_100 is not None:
            argv += [
                "--memory-100",
                str(self.spec.memory_100.resolve()),
                "--memory-100-state",
                str(readiness),
            ]
        if readiness is not None and self.spec.memory_ingestion is not None:
            argv += [
                "--memory-ingestion",
                str(self.spec.memory_ingestion.resolve()),
                "--memory-ingestion-state",
                str(readiness),
            ]
        if readiness is not None and self.spec.issue_recall:
            argv += ["--issue-recall", str(readiness)]
        return tuple(argv)

    async def _shutdown(
        self,
        serve: asyncio.subprocess.Process,
        egress: asyncio.subprocess.Process | None = None,
    ) -> None:
        # Drain the proxy first, while serve's control RPC is up for its meter flush, then serve.
        for name, process in (("egress", egress), ("serve", serve)):
            if process is None or process.returncode is not None:
                continue
            try:
                self._process_event("terminate", name, process)
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), SHUTDOWN_GRACE_SECONDS)
                except TimeoutError:
                    self._process_event("kill", name, process)
                    process.kill()
                    await process.wait()
                self._process_event("exited", name, process)
            except Exception as error:
                self._cleanup_event(name, f"{type(error).__name__}: {error}")
        try:
            await self._release_sandboxes()
        except Exception as error:
            self._cleanup_event("sandboxes", f"{type(error).__name__}: {error}")

    def _process_event(self, action: str, name: str, process: asyncio.subprocess.Process) -> None:
        self._log_event(
            {
                "action": action,
                "name": name,
                "pid": process.pid,
                "returncode": process.returncode,
            }
        )

    def _log_event(self, fields: dict[str, object]) -> None:
        self.process_log.write(
            json.dumps({"at": datetime.now(UTC).isoformat()} | fields, separators=(",", ":")) + "\n"
        )
        self.process_log.flush()

    def _cleanup_event(self, name: str, detail: str) -> None:
        self._log_event({"action": "cleanup_failed", "name": name, "detail": detail})

    async def _release_sandboxes(self) -> None:
        """Release the Docker sandboxes this stack's serve created.

        A serve owns its conversations' containers for the life of the process and reclaims an idle
        one only from a later create, so a serve that exits leaves every container up and every
        per-conversation bridge subnet held. Nothing else ever releases them, and Docker's default
        address pool serves about thirty-one subnets — so a second stack starts with fewer free than
        it needs and its turns fail on `docker network create` before reaching a model, which reads
        as an arm that scored zero rather than one that never ran.

        Scoped by this stack's own workspace root, whose per-conversation directory names are
        exactly the container and network suffixes, so a stack can only ever release its own.

        Every step runs whatever the one before it answered. Teardown reaches here from a `finally`,
        so a raise would both abandon every container and subnet after the failing step and replace
        the run's own outcome — and docker refusing one step is ordinary: a network whose egress or
        sandbox endpoint still detaches answers `has active endpoints`, and a host with no `docker`
        on it fails the call outright. A refused step lands in `process.log` instead.
        """
        workspaces = self.config.sandbox.workspace_root
        if self.config.sandbox.backend != DOCKER_BACKEND or not workspaces.is_dir():
            return
        for path in sorted(workspaces.iterdir()):
            try:
                conversation = UUID(path.name)
            except ValueError:
                continue
            for argv in (
                ("rm", "-f", f"{SANDBOX_CONTAINER_PREFIX}{conversation}"),
                ("network", "rm", f"{SANDBOX_NETWORK_PREFIX}{conversation.hex}"),
            ):
                try:
                    failure = await _docker(*argv)
                except Exception as error:
                    failure = f"{type(error).__name__}: {error}"
                if failure is not None:
                    self._release_event(argv, failure)

    def _release_event(self, argv: tuple[str, ...], detail: str) -> None:
        self._log_event({"action": "release-failed", "name": " ".join(argv), "detail": detail})

    def _log_path(self, step: str) -> Path:
        return self.root / f"{step}.log"


async def _docker(*argv: str) -> str | None:
    command = f"docker {' '.join(argv)}"
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except OSError as error:
        return f"{command} failed: {error}"
    if process.returncode == 0:
        return None
    detail = (stderr or stdout).decode("utf-8", "replace").strip()
    target = argv[-1]
    missing_container = argv[:2] == ("rm", "-f") and f"No such container: {target}" in detail
    missing_network = argv[:2] == ("network", "rm") and f"network {target} not found" in detail
    if missing_container or missing_network:
        return None
    return f"{command} exited {process.returncode}: {detail}"


def _mint_egress_ca() -> tuple[str, str]:
    """The shared CA a stack's serve hands its sandbox and its `ufo-egress` signs leaves with — the
    dev rig's minted CA, per run. Returns (cert PEM, PKCS#8 key PEM): serve trusts the cert, the
    proxy holds the key, so the sandbox trusts the leaf the proxy mints for each host it MITMs."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ufo-egress-eval")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _egress_binary(repo_root: Path) -> Path:
    """The `ufo-egress` data-plane binary the stack runs beside serve so an in-sandbox fetch has a
    proxy to reach. It is the deleted in-process proxy's replacement, built from `servers/egress/`;
    a stack with no egress wire refuses every sandbox CONNECT, so `run` resolves the binary before
    touching a database — a missing build fails in seconds, never after a materialization and
    never as an unexplained connection-refused inside a suite that fetches over the network."""
    for profile in ("release", "debug"):
        candidate = repo_root / "servers" / "egress" / "target" / profile / "ufo-egress"
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "ufo-egress binary not found under servers/egress/target/{release,debug}/ — the eval "
        "sandbox routes egress through it. Build it: "
        "cargo build --manifest-path servers/egress/Cargo.toml"
    )


def materialize_readiness(returncode: int | None, stdout: bytes, seed_log: Path) -> Path:
    """The materialize contract: exit 0 and the readiness.json path as the last stdout line."""
    if returncode != 0:
        raise RuntimeError(f"materialize exited {returncode} — see {seed_log}")
    printed = stdout.decode().strip().splitlines()
    if not printed or not printed[-1].endswith("readiness.json"):
        raise RuntimeError(f"materialize did not print a readiness path — see {seed_log}")
    return Path(printed[-1])


def template_config(text: str) -> Config:
    """Validate a template ufo.toml, rejecting shapes the stack cannot isolate: a pinned DBOS
    store (the `_dbos` sibling derives per run) and non-filesystem blobs."""
    template = tomllib.loads(text)
    if template.get("database", {}).get("system_url"):
        raise ValueError("stack templates must not pin database.system_url — it derives per run")
    config = Config.model_validate(template)
    if config.blob.backend != "filesystem":
        raise ValueError("stack templates require the filesystem blob backend")
    return config


def derived_config(
    template: Config,
    root: Path,
    serve_port: int,
    proxy_port: int,
    otlp_port: int | None,
    database_name: str,
) -> Config:
    """Rewrite only the collision knobs of a validated template: database (a per-run SQLite file
    or a per-run database on the template's Postgres server, DBOS sibling re-derived), blob root,
    loopback serve host with probed serve/proxy ports, the matching OAuth callback base, and — when
    the template sets one — a private loopback OTLP endpoint. Every suite knob passes through
    untouched.

    The stack always serves shared: the shared fleet is the only runtime, so a stack seeds one
    workspace and serves it through the fleet path (per-request/per-turn workspace resolution, the
    owner engine for cross-workspace job sweeps) rather than pinning it at boot. `owner_url`
    defaults to the run's own `url` — the seeded role owns its per-run database, so the same DSN is
    the RLS-bypassing owner engine `owner_tx` opens."""
    if template.database.url.startswith("sqlite"):
        url = f"sqlite+aiosqlite:///{root / 'ufo.db'}"
    else:
        url = f"{template.database.url.rpartition('/')[0]}/{database_name}"
    return template.model_copy(
        update={
            "database": DatabaseConfig(url=url, owner_url=template.database.owner_url or url),
            "blob": template.blob.model_copy(update={"root": root / "blobs"}),
            "serve": template.serve.model_copy(update={"host": "127.0.0.1", "port": serve_port}),
            "connect": ConnectConfig(public_base_url=f"http://127.0.0.1:{serve_port}"),
            "sandbox": template.sandbox.model_copy(
                update={"proxy_port": proxy_port, "workspace_root": root / "workspaces"}
            ),
            **(
                {"o11y": O11yConfig(otlp_endpoint=f"http://127.0.0.1:{otlp_port}")}
                if otlp_port is not None
                else {}
            ),
        }
    )


async def drop_eval_database_pairs(
    admin_database_url: str | None, ownerships: tuple[DatabaseOwnership, ...]
) -> None:
    """Drop generated eval database pairs after their owning run artifacts are archived."""
    if admin_database_url is None:
        if ownerships:
            raise ValueError("owned Postgres eval databases require an admin database URL")
        return
    authority = _database_authority(admin_database_url)
    for ownership in ownerships:
        _validate_database_ownership(ownership)
        if ownership.authority != authority:
            raise ValueError("eval stack database ownership has a different admin authority")
    pairs = tuple((ownership.application, ownership.system) for ownership in ownerships)
    names = tuple(name for pair in pairs for name in reversed(pair))
    if len(names) != len(set(names)):
        raise ValueError("eval stack database pairs are not uniquely owned")
    if not names:
        return
    markers = {
        name: ownership.marker
        for ownership in ownerships
        for name in (ownership.application, ownership.system)
    }
    connection = await asyncpg.connect(_asyncpg_dsn(admin_database_url))
    try:
        rows = await connection.fetch(
            "select datname, shobj_description(oid, 'pg_database') as ownership "
            "from pg_database where datname = any($1::name[])",
            list(names),
        )
        mismatched = sorted(
            row["datname"] for row in rows if row["ownership"] != markers[row["datname"]]
        )
        if mismatched:
            raise RuntimeError(f"eval stack database ownership mismatch: {', '.join(mismatched)}")
        existing = {row["datname"] for row in rows}
        connected = sorted(
            row["datname"]
            for row in await connection.fetch(
                "select datname from pg_stat_activity where datname = any($1::name[])",
                list(existing),
            )
        )
        if connected:
            raise RuntimeError(
                f"eval stack databases still connected during cleanup: {', '.join(connected)}"
            )
        for name in names:
            if name in existing:
                await connection.execute(f'drop database if exists "{name}"')
    finally:
        await connection.close()


def database_cleanup_plan(
    root: Path, template: Config
) -> tuple[str | None, tuple[DatabaseOwnership, ...]]:
    """Load the exact database ownership records an ablation arm may clean up.

    The record is what grants the drop, so the plan asks the arm what it recorded and never how many
    records it owes. An arm worktree runs the `evals.stack` of the base its experiment pins, and a
    base older than these records writes none and marks no database: those databases stay where that
    run left them, exactly as they did before the records existed, rather than being dropped on a
    name alone or failing an arm that ran to the end."""
    paths = tuple(sorted((root / RUNS_ROOT).glob(f"*/*/{DATABASE_OWNERSHIP_FILE}")))
    admin_url = template.database.url if template.database.url.startswith("postgresql") else None
    if admin_url is None:
        if paths:
            raise RuntimeError("SQLite arm has Postgres database ownership records")
        return None, ()
    return admin_url, tuple(load_database_ownership(path) for path in paths)


def load_database_ownership(path: Path) -> DatabaseOwnership:
    """Load and verify the provision-time ownership record beside one eval stack config."""
    if path.name != DATABASE_OWNERSHIP_FILE:
        raise ValueError(f"database ownership record must be named {DATABASE_OWNERSHIP_FILE}")
    ownership = DatabaseOwnership.model_validate_json(path.read_text())
    if ownership.run_root != path.parent.resolve():
        raise ValueError("database ownership record belongs to a different run root")
    _validate_database_ownership(ownership)
    return ownership


def _validate_database_ownership(ownership: DatabaseOwnership) -> None:
    root = ownership.run_root.resolve()
    if ownership.run_root != root:
        raise ValueError("database ownership run root must be absolute and resolved")
    application = _database_name(root)
    system = f"{application}{POSTGRES_SYSTEM_SUFFIX}"
    if (ownership.application, ownership.system) != (application, system):
        raise ValueError("database ownership names do not match their provision-time run identity")


def _database_authority(database_url: str) -> DatabaseAuthority:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("database ownership authority must be Postgres")
    return DatabaseAuthority(
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
        options=tuple(
            (name, tuple(values)) for name, values in sorted(url.normalized_query.items())
        ),
    )


def _asyncpg_dsn(database_url: str) -> str:
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _database_name(root: Path) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", f"eval_{root.parent.name}_{root.name}".lower())
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:DATABASE_NAME_DIGEST_CHARS]
    prefix_length = POSTGRES_NAME_LIMIT - len(POSTGRES_SYSTEM_SUFFIX) - len(digest) - 1
    return f"{name[:prefix_length].rstrip('_')}_{digest}"


def _port_probe() -> tuple[socket.socket, int]:
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    return probe, probe.getsockname()[1]


if __name__ == "__main__":
    main()
