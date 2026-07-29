"""Provision isolated eval stacks and run a matrix of suites concurrently.

`python -m evals.stack matrix.toml` reads `[[run]]` blocks, provisions one isolated stack per
run — a run directory holding a derived ufo.toml (its own database, blob root, serve and proxy
ports), a freshly seeded workspace, and its own `ufoctl serve` — then drives a single-suite
`python -m evals` child against each stack and records every run into one shared archive. Run
directories and per-run databases are retained for reconstruction and post-mortems; the final
summary prints each stack's run dir and database URL."""

import argparse
import asyncio
import os
import re
import secrets
import socket
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self

import asyncpg
import tomli_w
from cryptography.fernet import Fernet
from httpx import AsyncClient, HTTPError
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from evals.harness.viewer import load_runs, write_viewer
from ufo.config import Config, DatabaseConfig, O11yConfig
from ufo.proxy_serve import OWNER_DSN_ENV

RUNS_ROOT = Path(".local/evals")
DEFAULT_OUT = Path("eval-reports")
STACK_OWNER_EMAIL = "evals@localhost"
POSTGRES_NAME_LIMIT = 63
READY_DEADLINE_SECONDS = 180.0
READY_POLL_SECONDS = 0.5
SHUTDOWN_GRACE_SECONDS = 30.0
ORCHESTRATOR_ARGS = (
    "--out",
    "--view",
    "--share",
    "--reconstruct",
    "--list",
    "--memory-100",
    "--memory-100-state",
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
    memory_100: Path | None = None
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
        if self.memory_100 is not None and self.model is not None:
            raise ValueError(
                f"run {self.label!r} sets model with memory_100 — materialization owns the agent"
            )
        if self.memory_100 is not None and self.issue_recall:
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
    admin_database_url: str | None
    env: dict[str, str]
    serve_probe: socket.socket
    proxy_probe: socket.socket
    otlp_probe: socket.socket | None
    seed_log: IO[bytes]
    serve_log: IO[bytes]
    eval_log: IO[bytes]

    @classmethod
    def provision(cls, spec: RunSpec, root: Path, out: Path, repo_root: Path) -> Self:
        template = template_config(spec.config.read_text())
        if spec.memory_100 is not None and not template.database.url.startswith("postgresql"):
            raise ValueError(f"run {spec.label!r}: memory_100 requires a Postgres template")
        if (
            spec.memory_100 is not None or spec.issue_recall
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
        config_file = root / "ufo.toml"
        config_file.write_text(tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)))
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
        return cls(
            spec=spec,
            root=root,
            out=out,
            repo_root=repo_root,
            config=config,
            config_file=config_file,
            admin_database_url=(
                template.database.url if template.database.url.startswith("postgresql") else None
            ),
            env=env,
            serve_probe=serve_probe,
            proxy_probe=proxy_probe,
            otlp_probe=otlp_probe,
            seed_log=(root / "seed.log").open("wb"),
            serve_log=(root / "serve.log").open("wb"),
            eval_log=(root / "eval.log").open("wb"),
        )

    async def run(self, creation: asyncio.Lock) -> StackResult:
        try:
            async with creation:
                await self._create_databases()
            readiness = await self._seed()
            serve = await self._start_serve()
            try:
                await self._ready(serve)
                exit_code = await self._drive(serve, readiness)
            finally:
                await self._shutdown(serve)
        finally:
            for handle in (
                self.serve_probe,
                self.proxy_probe,
                self.otlp_probe,
                self.seed_log,
                self.serve_log,
                self.eval_log,
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
            await self._checked(await self._ufoctl("migrate", log=self.seed_log), "seed")
            return await self._materialize(
                "evals.memory_100.materialize", "--snapshot", str(self.spec.memory_100.resolve())
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

    def _seed_args(self) -> tuple[str, ...]:
        argv = ["init", "--email", STACK_OWNER_EMAIL]
        if self.spec.model is not None:
            argv += ["--model", self.spec.model]
        return tuple(argv)

    async def _ufoctl(self, *argv: str, log: IO[bytes]) -> asyncio.subprocess.Process:
        binary = Path(sys.executable).with_name("ufoctl")
        if not binary.exists():
            raise RuntimeError(f"ufoctl not found beside the interpreter: {binary}")
        return await asyncio.create_subprocess_exec(
            str(binary), *argv, cwd=self.root, env=self.env, stdout=log, stderr=log
        )

    async def _checked(self, process: asyncio.subprocess.Process, step: str) -> None:
        if await process.wait() != 0:
            raise RuntimeError(f"{step} exited {process.returncode} — see {self._log_path(step)}")

    async def _start_serve(self) -> asyncio.subprocess.Process:
        self.serve_probe.close()
        self.proxy_probe.close()
        return await self._ufoctl("serve", log=self.serve_log)

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

    async def _drive(self, serve: asyncio.subprocess.Process, readiness: Path | None) -> int:
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
        child_wait = asyncio.ensure_future(child.wait())
        serve_wait = asyncio.ensure_future(serve.wait())
        done, pending = await asyncio.wait(
            (child_wait, serve_wait), return_when=asyncio.FIRST_COMPLETED
        )
        if serve_wait in done and child_wait not in done:
            child.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(child_wait), SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                child.kill()
                await child.wait()
            raise RuntimeError(
                f"serve exited {serve.returncode} mid-run — see {self._log_path('serve')}"
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
        if readiness is not None and self.spec.issue_recall:
            argv += ["--issue-recall", str(readiness)]
        return tuple(argv)

    async def _shutdown(self, serve: asyncio.subprocess.Process) -> None:
        if serve.returncode is None:
            serve.terminate()
            try:
                await asyncio.wait_for(serve.wait(), SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                serve.kill()
                await serve.wait()

    def _log_path(self, step: str) -> Path:
        return self.root / f"{step}.log"


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
    loopback serve host with probed serve/proxy ports, and — when the template sets one — a
    private loopback OTLP endpoint. Every suite knob passes through untouched.

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
