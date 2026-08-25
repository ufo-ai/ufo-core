"""Provision isolated eval stacks and run a matrix of suites concurrently.

`python -m evals.stack matrix.toml` reads `[[run]]` blocks, provisions one isolated stack per
run — a run directory holding a derived ufo.toml (its own database, blob root, serve and proxy
ports), a freshly seeded workspace, and its own `ufoctl serve` — then drives a single-suite
`python -m evals` child against each stack and records every run into one shared archive. Run
directories and per-run databases are retained for reconstruction and post-mortems; the final
summary prints each stack's run dir and database URL."""

import argparse
import asyncio
import json
import os
import re
import secrets
import socket
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Self
from uuid import UUID

import asyncpg
import tomli_w
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import AsyncClient, HTTPError
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from evals.harness.viewer import load_runs, write_viewer
from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.config import Config, ConnectConfig, DatabaseConfig, O11yConfig
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    EGRESS_CA_KEY_ENV,
    EGRESS_CONTROL_TOKEN_ENV,
)
from ufo.schema.records import ReasoningEffort

RUNS_ROOT = Path(".local/evals")
DEFAULT_OUT = Path("eval-reports")
STACK_OWNER_EMAIL = "evals@localhost"
APP_SUITES = frozenset({"ufo-app-bench", "ufo-app-copy"})
CREATION_SUITES = frozenset({"new_application"})
CREATION_DISABLED_JOBS = ("web:seed_homepages",)
POSTGRES_NAME_LIMIT = 63
READY_DEADLINE_SECONDS = 180.0
READY_POLL_SECONDS = 0.5
SHUTDOWN_GRACE_SECONDS = 30.0
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
)
ORCHESTRATOR_ENV = ("UFO_CONFIG", "UFOCTL_DIR")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.stack")
    parser.add_argument("matrix", type=Path, help="TOML file of [[run]] blocks")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="shared run archive")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    matrix = Matrix.model_validate(tomllib.loads(args.matrix.read_text()))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
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


class RunSpec(BaseModel):
    """One `[[run]]` block: a label, the template ufo.toml carrying the suite's knobs (pack,
    models, research), the `python -m evals` arguments, and extra environment to pass through."""

    model_config = ConfigDict(extra="forbid")
    label: str
    config: Path
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}
    model: str | None = None
    reasoning: ReasoningEffort | None = None
    memory_100: Path | None = None
    memory_ingestion: Path | None = None
    issue_recall: bool = False

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
        if (self.memory_100 is not None or self.memory_ingestion is not None) and (
            self.model is not None or self.reasoning is not None
        ):
            raise ValueError(
                f"run {self.label!r} sets model or reasoning with a memory corpus — "
                "materialization owns the agent"
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
    env: dict[str, str]
    serve_probe: socket.socket
    proxy_probe: socket.socket
    otlp_probe: socket.socket | None
    seed_log: IO[bytes]
    serve_log: IO[bytes]
    egress_log: IO[bytes]
    eval_log: IO[bytes]
    process_log: IO[str]

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
        selected, _ = selected_parser.parse_known_args(spec.args)
        selected_suites = frozenset(selected.only)
        if not APP_SUITES.isdisjoint(selected_suites) and not selected_suites <= APP_SUITES:
            raise ValueError("app suites require a separate eval stack")
        if not CREATION_SUITES.isdisjoint(selected.only):
            config = config.model_copy(
                update={
                    "serve": config.serve.model_copy(
                        update={"disabled_jobs": CREATION_DISABLED_JOBS}
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
            admin_database_url=(
                template.database.url if template.database.url.startswith("postgresql") else None
            ),
            env=env,
            serve_probe=serve_probe,
            proxy_probe=proxy_probe,
            otlp_probe=otlp_probe,
            seed_log=(root / "seed.log").open("wb"),
            serve_log=(root / "serve.log").open("wb"),
            egress_log=(root / "egress.log").open("wb"),
            eval_log=(root / "eval.log").open("wb"),
            process_log=(root / "process.log").open("w"),
        )

    async def run(self, creation: asyncio.Lock) -> StackResult:
        try:
            egress_binary = _egress_binary(self.repo_root)
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

    async def _create_databases(self) -> None:
        if self.admin_database_url is None:
            return
        admin_dsn = self.admin_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        app_name = self.config.database.url.rpartition("/")[2]
        system_name = self.config.database.system_url.rpartition("/")[2]
        connection = await asyncpg.connect(admin_dsn)
        try:
            for name in (app_name, system_name):
                exists = await connection.fetchrow(
                    "select 1 from pg_database where datname = $1", name
                )
                if exists is None:
                    await connection.execute(f'create database "{name}"')
        finally:
            await connection.close()

    async def _seed(self) -> Path | None:
        if self.spec.memory_100 is not None:
            return await self._materialize(
                "evals.memory_100.materialize", "--snapshot", str(self.spec.memory_100.resolve())
            )
        if self.spec.memory_ingestion is not None:
            await self._checked(await self._ufoctl("migrate", log=self.seed_log), "seed")
            return await self._materialize(
                "evals.memory_ingestion.materialize",
                "--snapshot",
                str(self.spec.memory_ingestion.resolve()),
            )
        await self._checked(await self._ufoctl(*self._seed_args(), log=self.seed_log), "seed")
        if not self.spec.issue_recall:
            return None
        return await self._materialize("evals.issue_recall.materialize")

    async def _materialize(self, module: str, *corpus_args: str) -> Path:
        materialize = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module,
            *corpus_args,
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
        )
        self._process_event("started", "eval", child)
        child_wait = asyncio.ensure_future(child.wait())
        infra = {
            asyncio.ensure_future(serve.wait()): ("serve", serve),
            asyncio.ensure_future(egress.wait()): ("egress", egress),
        }
        done, pending = await asyncio.wait(
            (child_wait, *infra), return_when=asyncio.FIRST_COMPLETED
        )
        for future in done:
            if future is child_wait:
                self._process_event("exited", "eval", child)
            elif future in infra:
                name, process = infra[future]
                self._process_event("exited", name, process)
        dead = next((infra[future] for future in infra if future in done), None)
        if dead is not None and child_wait not in done:
            name, process = dead
            self._process_event("terminate", "eval", child)
            child.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(child_wait), SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                self._process_event("kill", "eval", child)
                child.kill()
                await child.wait()
            self._process_event("exited", "eval", child)
            raise RuntimeError(
                f"{name} exited {process.returncode} mid-run — see {self._log_path(name)}"
            )
        for future in pending:
            future.cancel()
        return await child_wait

    def _child_args(self, readiness: Path | None = None) -> tuple[str, ...]:
        argv = list(self.spec.args)
        argv += ["--out", str(self.out)]
        flags = {token.partition("=")[0] for token in argv}
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
            self._process_event("terminate", name, process)
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                self._process_event("kill", name, process)
                process.kill()
                await process.wait()
            self._process_event("exited", name, process)
        await self._release_sandboxes()

    def _process_event(self, action: str, name: str, process: asyncio.subprocess.Process) -> None:
        self.process_log.write(
            json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "action": action,
                    "name": name,
                    "pid": process.pid,
                    "returncode": process.returncode,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        self.process_log.flush()

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
        """
        workspaces = self.config.sandbox.workspace_root
        if self.config.sandbox.backend != DOCKER_BACKEND or not workspaces.is_dir():
            return
        for path in sorted(workspaces.iterdir()):
            try:
                conversation = UUID(path.name)
            except ValueError:
                continue
            await _docker("rm", "-f", f"{SANDBOX_CONTAINER_PREFIX}{conversation}")
            await _docker("network", "rm", f"{SANDBOX_NETWORK_PREFIX}{conversation.hex}")

    def _log_path(self, step: str) -> Path:
        return self.root / f"{step}.log"


async def _docker(*argv: str) -> None:
    """One best-effort `docker` call. A stack tears down whatever it can and never fails a recorded
    run over cleanup: a box already gone, a network still held by another container, or no Docker on
    this host are all the same non-event here."""
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return
    await process.wait()


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


def _database_name(root: Path) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", f"eval_{root.parent.name}_{root.name}".lower())
    return name[:POSTGRES_NAME_LIMIT]


def _port_probe() -> tuple[socket.socket, int]:
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    return probe, probe.getsockname()[1]


if __name__ == "__main__":
    main()
