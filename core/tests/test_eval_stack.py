import asyncio
import tomllib
from pathlib import Path

import asyncpg
import pytest
import tomli_w
from pydantic import ValidationError
from sqlalchemy import make_url
from ufo_testsupport.plugin import POSTGRES_TEST_URL, postgres_reachable

from evals.stack import (
    STACK_OWNER_EMAIL,
    EvalStack,
    Matrix,
    RunSpec,
    _database_name,
    derived_config,
    materialize_readiness,
    template_config,
)
from ufo.config import Config
from ufo.proxy_serve import OWNER_DSN_ENV


def _close(stack: EvalStack) -> None:
    for handle in (
        stack.serve_probe,
        stack.proxy_probe,
        stack.otlp_probe,
        stack.seed_log,
        stack.serve_log,
        stack.eval_log,
    ):
        if handle is not None:
            handle.close()


SQLITE_TEMPLATE = """\
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"

[models]
auto_model = "claude-opus-4-8"

[pack]
name = "assistant"

[research]
search_provider = "perplexity"
"""
POSTGRES_TEMPLATE = """\
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"

[blob]
backend = "filesystem"
root = "./blobs"
"""


def test_derived_config_isolates_a_sqlite_template(tmp_path: Path) -> None:
    root = tmp_path / "20260717-000000" / "smoke"
    derived = derived_config(
        template_config(SQLITE_TEMPLATE),
        root=root,
        serve_port=18710,
        proxy_port=18888,
        otlp_port=None,
        database_name="unused",
    )

    payload = tomli_w.dumps(derived.model_dump(mode="json", exclude_none=True))
    config = Config.model_validate(tomllib.loads(payload))
    assert config == derived
    assert config.database.url == f"sqlite+aiosqlite:///{root / 'ufo.db'}"
    assert config.database.system_url == f"sqlite:///{root / 'ufo_dbos.db'}"
    assert config.blob.root == root / "blobs"
    assert config.serve.host == "127.0.0.1"
    assert config.serve.port == 18710
    assert config.sandbox.proxy_port == 18888
    assert config.o11y.otlp_endpoint is None
    assert config.pack.name == "assistant"
    assert config.models.auto_model == "claude-opus-4-8"
    assert config.research.search_provider == "perplexity"


def test_derived_config_forces_shared_serve_with_a_self_owner_dsn(tmp_path: Path) -> None:
    """Every stack serves shared — the only runtime — seeding one workspace behind the fleet path
    rather than pinning it at boot. `owner_url` defaults to the run's own `url` so `owner_tx` has an
    RLS-bypassing engine to enumerate through (the seeded role owns its per-run database)."""
    derived = derived_config(
        template_config(SQLITE_TEMPLATE),
        root=tmp_path,
        serve_port=18710,
        proxy_port=18888,
        otlp_port=None,
        database_name="unused",
    )

    assert derived.database.owner_url == derived.database.url


def test_derived_config_names_a_per_run_postgres_database(tmp_path: Path) -> None:
    derived = derived_config(
        template_config(POSTGRES_TEMPLATE),
        root=tmp_path,
        serve_port=18710,
        proxy_port=18888,
        otlp_port=None,
        database_name="eval_20260717_smoke",
    )

    assert derived.database.url == "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/eval_20260717_smoke"
    assert derived.database.system_url == (
        "postgresql+psycopg://ufo:ufo@127.0.0.1:5541/eval_20260717_smoke_dbos"
    )
    assert derived.database.owner_url == derived.database.url


def test_derived_config_reports_a_private_otlp_endpoint_only_when_the_template_sets_one(
    tmp_path: Path,
) -> None:
    template = SQLITE_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n'

    derived = derived_config(
        template_config(template),
        root=tmp_path,
        serve_port=18710,
        proxy_port=18888,
        otlp_port=14318,
        database_name="unused",
    )

    assert derived.o11y.otlp_endpoint == "http://127.0.0.1:14318"


def test_template_config_rejects_non_filesystem_blobs_and_a_pinned_system_url() -> None:
    s3 = SQLITE_TEMPLATE.replace(
        'backend = "filesystem"\nroot = "./blobs"', 'backend = "s3"\nbucket = "shared"'
    )
    with pytest.raises(ValueError, match="filesystem blob backend"):
        template_config(s3)

    pinned = POSTGRES_TEMPLATE.replace(
        "[blob]", 'system_url = "postgresql+psycopg://elsewhere/dbos"\n\n[blob]'
    )
    with pytest.raises(ValueError, match="system_url"):
        template_config(pinned)


def test_run_spec_rejects_orchestrator_owned_surface() -> None:
    with pytest.raises(ValidationError, match="--out"):
        RunSpec(label="smoke", config=Path("ufo.toml"), args=("--out=elsewhere",))
    with pytest.raises(ValidationError, match="--memory-100"):
        RunSpec(label="smoke", config=Path("ufo.toml"), args=("--memory-100", "snap"))
    with pytest.raises(ValidationError, match="UFO_CONFIG"):
        RunSpec(label="smoke", config=Path("ufo.toml"), env={"UFO_CONFIG": "x"})
    with pytest.raises(ValidationError, match="label"):
        RunSpec(label="Bad Label!", config=Path("ufo.toml"))


def test_matrix_requires_labels_unique_as_database_names() -> None:
    spec = {"label": "smoke", "config": "ufo.toml"}
    with pytest.raises(ValidationError, match="no \\[\\[run\\]\\]"):
        Matrix.model_validate({"run": []})
    with pytest.raises(ValidationError, match="labels collide"):
        Matrix.model_validate({"run": [spec, spec]})
    with pytest.raises(ValidationError, match="smoke_a"):
        Matrix.model_validate(
            {
                "run": [
                    {"label": "smoke-a", "config": "ufo.toml"},
                    {"label": "smoke_a", "config": "ufo.toml"},
                ]
            }
        )


def test_provision_writes_the_derived_config_and_owns_the_child_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    spec = RunSpec(
        label="boundary",
        config=template,
        args=("--jobbench", "snap", "--only", "jobbench"),
        env={"MCP_ATLAS_URL": "http://127.0.0.1:9000"},
        model="claude-haiku-4-5",
    )
    root = tmp_path / "20260717-000000" / "boundary"

    stack = EvalStack.provision(spec, root=root, out=tmp_path / "archive", repo_root=tmp_path)
    _close(stack)

    written = Config.model_validate(tomllib.loads(stack.config_file.read_text()))
    assert written == stack.config
    assert stack.config.serve.port != stack.config.sandbox.proxy_port
    assert stack.env["UFO_CONFIG"] == str(stack.config_file.resolve())
    assert stack.env["UFOCTL_DIR"] == str((root / ".ufoctl").resolve())
    assert stack.env["MCP_ATLAS_URL"] == "http://127.0.0.1:9000"
    assert stack.env["UFO_CREDENTIAL_KEY"]
    assert stack.env["UFO_ARTIFACT_TOKEN_SECRET"]
    assert stack.admin_database_url is None
    args = stack._child_args()
    assert args[:4] == ("--jobbench", "snap", "--only", "jobbench")
    assert ("--out", str(tmp_path / "archive")) == args[4:6]
    assert ("--label", "boundary") == args[6:8]
    assert args[8:] == ("--jobbench-submissions", str(root.resolve() / "submissions"))
    assert stack._seed_args() == (
        "init",
        "--email",
        "evals@localhost",
        "--model",
        "claude-haiku-4-5",
    )


def test_provision_strips_an_ambient_owner_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A UFO_OWNER_DSN in the shell must not reach a stack's serve. Shared serve prefers it over the
    config's owner_url, so an inherited one would point the run's cross-workspace admin engine at a
    foreign (possibly production) database instead of the derived per-run one."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql://ufo_owner@prod/ufo")
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)

    stack = EvalStack.provision(
        RunSpec(label="isolated", config=template),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    _close(stack)

    assert OWNER_DSN_ENV not in stack.env
    assert stack.config.database.owner_url == stack.config.database.url


async def test_create_databases_creates_the_app_and_dbos_pair_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not postgres_reachable():
        pytest.skip("postgres service not reachable")
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    url = make_url(POSTGRES_TEST_URL)
    admin_url = f"postgresql+asyncpg://{url.username}:{url.password}@{url.host}:{url.port}/ufo"
    template = tmp_path / "template.toml"
    template.write_text(
        f'[database]\nurl = "{admin_url}"\n\n[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
    )
    stack = EvalStack.provision(
        RunSpec(label="dbpair", config=template),
        root=tmp_path / "dbpair",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    _close(stack)
    app_name = stack.config.database.url.rpartition("/")[2]

    await stack._create_databases()
    await stack._create_databases()

    admin = await asyncpg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, database="ufo"
    )
    try:
        rows = await admin.fetch(
            "select datname from pg_database where datname = any($1::name[])",
            [app_name, f"{app_name}_dbos"],
        )
        assert sorted(row["datname"] for row in rows) == [app_name, f"{app_name}_dbos"]
    finally:
        for name in (app_name, f"{app_name}_dbos"):
            await admin.execute(f'drop database if exists "{name}"')
        await admin.close()


def test_memory_100_spec_requires_postgres_and_a_collector_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    sqlite_template = tmp_path / "sqlite.toml"
    sqlite_template.write_text(SQLITE_TEMPLATE)
    postgres_template = tmp_path / "postgres.toml"
    postgres_template.write_text(POSTGRES_TEMPLATE)
    snapshot = tmp_path / "snapshot"

    with pytest.raises(ValueError, match="Postgres template"):
        EvalStack.provision(
            RunSpec(label="memory", config=sqlite_template, memory_100=snapshot),
            root=tmp_path / "a",
            out=tmp_path / "archive",
            repo_root=tmp_path,
        )
    with pytest.raises(ValueError, match="otlp_endpoint"):
        EvalStack.provision(
            RunSpec(label="memory", config=postgres_template, memory_100=snapshot),
            root=tmp_path / "b",
            out=tmp_path / "archive",
            repo_root=tmp_path,
        )
    with pytest.raises(ValidationError, match="materialization owns the agent"):
        RunSpec(label="memory", config=postgres_template, memory_100=snapshot, model="claude")
    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()


class _DoneProcess:
    """A finished subprocess: `_checked` reads its exit status and nothing else."""

    returncode = 0

    async def wait(self) -> int:
        return 0


def test_issue_recall_spec_needs_a_collector_endpoint_but_no_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture is 23 pages, so SQLite serves it — unlike memory_100, which needs Postgres. The
    collector endpoint is still required: the leaf is graded on the recall event."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    sqlite_template = tmp_path / "sqlite.toml"
    sqlite_template.write_text(SQLITE_TEMPLATE)
    observed = tmp_path / "observed.toml"
    observed.write_text(SQLITE_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n')

    with pytest.raises(ValueError, match="otlp_endpoint"):
        EvalStack.provision(
            RunSpec(label="issues", config=sqlite_template, issue_recall=True),
            root=tmp_path / "a",
            out=tmp_path / "archive",
            repo_root=tmp_path,
        )
    with pytest.raises(ValidationError, match="two corpora"):
        RunSpec(
            label="issues",
            config=observed,
            issue_recall=True,
            memory_100=tmp_path / "snapshot",
        )
    stack = EvalStack.provision(
        RunSpec(label="issues", config=observed, issue_recall=True),
        root=tmp_path / "b",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    for log in (stack.seed_log, stack.serve_log, stack.eval_log):
        log.close()

    assert not (tmp_path / "a").exists()
    assert stack.config.o11y.otlp_endpoint != "http://127.0.0.1:4318"


def test_issue_recall_seeds_a_workspace_then_materializes_and_passes_the_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`issue_recall` materializes into an initialized workspace, so seeding runs `ufoctl init` and
    then the corpus materializer — where memory_100 runs `migrate` and owns the workspace itself."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n')
    stack = EvalStack.provision(
        RunSpec(label="issues", config=template, issue_recall=True),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    for log in (stack.seed_log, stack.serve_log, stack.eval_log):
        log.close()
    ufoctl: list[tuple[str, ...]] = []
    materialized: list[tuple[str, ...]] = []
    readiness = tmp_path / "run" / "state" / "abc" / "readiness.json"

    async def fake_ufoctl(self: EvalStack, *argv: str, log: object) -> object:
        ufoctl.append(argv)
        return _DoneProcess()

    async def fake_materialize(self: EvalStack, module: str, *corpus_args: str) -> Path:
        materialized.append((module, *corpus_args))
        return readiness

    monkeypatch.setattr(EvalStack, "_ufoctl", fake_ufoctl)
    monkeypatch.setattr(EvalStack, "_materialize", fake_materialize)

    seeded = asyncio.run(stack._seed())

    assert seeded == readiness
    assert ufoctl == [("init", "--email", STACK_OWNER_EMAIL)]
    assert materialized == [("evals.issue_recall.materialize",)]
    assert stack._child_args(readiness)[-2:] == ("--issue-recall", str(readiness))
    assert "--issue-recall" not in stack._child_args(None)


def test_memory_100_child_args_carry_the_snapshot_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(POSTGRES_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n')
    snapshot = tmp_path / "snapshot"

    stack = EvalStack.provision(
        RunSpec(label="memory", config=template, memory_100=snapshot),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    for log in (stack.seed_log, stack.serve_log, stack.eval_log):
        log.close()

    readiness = tmp_path / "run" / "state" / "abc" / "readiness.json"
    assert stack._child_args(readiness)[-4:] == (
        "--memory-100",
        str(snapshot.resolve()),
        "--memory-100-state",
        str(readiness),
    )
    assert "--memory-100" not in stack._child_args(None)
    assert stack.config.o11y.otlp_endpoint != "http://127.0.0.1:4318"
    assert stack.admin_database_url == "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"


def test_materialize_readiness_parses_the_last_line_and_fails_loud(tmp_path: Path) -> None:
    seed_log = tmp_path / "seed.log"
    stdout = b"validated 12239 pages\n/state/abc/readiness.json\n"

    assert materialize_readiness(0, stdout, seed_log) == Path("/state/abc/readiness.json")
    with pytest.raises(RuntimeError, match="exited 3"):
        materialize_readiness(3, stdout, seed_log)
    with pytest.raises(RuntimeError, match="readiness path"):
        materialize_readiness(0, b"", seed_log)
    with pytest.raises(RuntimeError, match="readiness path"):
        materialize_readiness(0, b"no path printed\n", seed_log)


def test_database_name_is_postgres_safe() -> None:
    root = Path(".local/evals/20260717-141530/An-Odd.Label")
    name = _database_name(root)

    assert name == "eval_20260717_141530_an_odd_label"
    assert len(_database_name(Path("x" * 80) / ("y" * 80))) == 63
