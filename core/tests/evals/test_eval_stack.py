import asyncio
import json
import signal
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from uuid import UUID

import asyncpg
import pytest
import sqlalchemy as sa
import tomli_w
from pydantic import ValidationError
from sqlalchemy import make_url
from ufo_testsupport.migrations import apply_cached_migrations
from ufo_testsupport.plugin import POSTGRES_TEST_URL, postgres_reachable

import evals.stack as eval_stack
from evals.registry import selected_run_tasks
from evals.sandbox_image import SandboxImagePlan
from evals.stack import (
    APPLICATION_BUILD_PRODUCTS,
    CREATION_DISABLED_JOBS,
    ISOLATED_EXTERNAL_BILLING_JOBS,
    STACK_OWNER_EMAIL,
    EvalStack,
    Matrix,
    RunSpec,
    _database_name,
    _docker,
    derived_config,
    drop_eval_database_pairs,
    materialize_readiness,
    template_config,
)
from evals.suites.ufo_app_prepare import (
    APP_PARENT_TOOLS,
    HOMEPAGE_SEED_PREFIX,
    SETTLED_MARKER,
    WEB_EXTENSION,
    prepare_app_eval,
    prepare_creation_eval,
)
from ufo.config import Config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.host.ext.loader import load_manifests
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.runtime.billing.accounting import BalanceGate, record_probe_egress_request
from ufo.runtime.billing.balance import credit, debit, set_reserve
from ufo.runtime.jobs import bindings_from
from ufo.schema import tables


class _ExitedProcess:
    """A serve or proxy that is already down, so teardown goes straight to what it left behind."""

    returncode = 0


def _close(stack: EvalStack) -> None:
    for handle in (
        stack.serve_probe,
        stack.proxy_probe,
        stack.otlp_probe,
        stack.seed_log,
        stack.serve_log,
        stack.egress_log,
        stack.eval_log,
        stack.process_log,
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
DOCKER_TEMPLATE = (
    SQLITE_TEMPLATE
    + """
[sandbox]
backend = "docker"
"""
)
POSTGRES_TEMPLATE = """\
[database]
url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"

[blob]
backend = "filesystem"
root = "./blobs"
"""


def _postgres_stack(tmp_path: Path, name: str, template_text: str = POSTGRES_TEMPLATE) -> EvalStack:
    template = tmp_path / f"{name}.toml"
    template.write_text(template_text)
    stack = EvalStack.provision(
        RunSpec(label=name, config=template),
        root=tmp_path / name,
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    _close(stack)
    return stack


def _authority(port: int = 5541) -> eval_stack.DatabaseAuthority:
    return eval_stack.DatabaseAuthority(
        username="ufo", host="127.0.0.1", port=port, database="ufo", options=()
    )


def _ownership(root: Path, suffix: str = "") -> eval_stack.DatabaseOwnership:
    application = f"{_database_name(root.resolve())}{suffix}"
    return eval_stack.DatabaseOwnership(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        run_root=root.resolve(),
        authority=_authority(),
        application=application,
        system=f"{application}_dbos",
    )


async def test_isolated_stack_keeps_local_billing_and_omits_external_billing_workflows(
    db: None,
    tmp_path: Path,
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE.replace('name = "assistant"', 'name = "assistant_hosted"'))
    stack = EvalStack.provision(
        RunSpec(label="hosted", config=template),
        root=tmp_path / "hosted",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    manifests = load_manifests(stack.config.pack.name)
    enabled = bindings_from(
        manifests,
        (),
        disabled=frozenset(stack.config.serve.disabled_jobs),
    )
    _close(stack)

    assert stack.config.serve.disabled_jobs == ISOLATED_EXTERNAL_BILLING_JOBS
    assert "metronome" in {manifest.name for manifest in manifests}
    assert not frozenset(ISOLATED_EXTERNAL_BILLING_JOBS) & {binding.key for binding in enabled}

    workspace_id = UUID("11111111-2222-3333-4444-555555555555")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await credit(connection, workspace_id, 20_000_000, 0, "eval/test")
        await set_reserve(connection, workspace_id, 2_000_000)
        await record_probe_egress_request(connection, workspace_id)
        await debit(connection, workspace_id, 18_000_000)
        decision = await BalanceGate(workspace_id).admits(connection)
        ledger = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).one()
        balance = (
            await connection.execute(
                sa.select(
                    tables.workspace_balance.c.balance_micro_usd,
                    tables.workspace_balance.c.reserve_micro_usd,
                ).where(tables.workspace_balance.c.workspace_id == workspace_id)
            )
        ).one()

    assert ledger == ("egress", 1, 0)
    assert balance == (2_000_000, 2_000_000)
    assert decision.outcome == "reject"


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
    assert config.connect.public_base_url == "http://127.0.0.1:18710"
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


def test_provision_records_the_exact_postgres_database_ownership(tmp_path: Path) -> None:
    arm = tmp_path / "arm"
    stack_parent = arm / eval_stack.RUNS_ROOT / "stamp"
    stack_parent.mkdir(parents=True)
    stack = _postgres_stack(stack_parent, "run")
    root = stack.root
    ownership = eval_stack.DatabaseOwnership.model_validate_json(
        (root / eval_stack.DATABASE_OWNERSHIP_FILE).read_text()
    )

    assert stack.database_ownership == ownership
    assert ownership.run_root == root.resolve()
    assert ownership.authority == _authority()
    assert "password" not in (root / eval_stack.DATABASE_OWNERSHIP_FILE).read_text()
    assert ownership.application == stack.config.database.url.rpartition("/")[2]
    assert ownership.system == stack.config.database.system_url.rpartition("/")[2]
    assert eval_stack.database_cleanup_plan(arm, template_config(POSTGRES_TEMPLATE)) == (
        stack.admin_database_url,
        (ownership,),
    )


def test_database_cleanup_plans_nothing_for_a_base_that_records_no_ownership(
    tmp_path: Path,
) -> None:
    """An arm worktree runs the `evals.stack` of the base its experiment pins. A base older than the
    ownership record writes none and marks no database, so the arm owns nothing to drop and is not a
    failed arm."""
    arm = tmp_path / "arm"
    for label in ("ablate-control-0", "ablate-control-1"):
        run = arm / eval_stack.RUNS_ROOT / "20260821-224424" / label
        run.mkdir(parents=True)
        (run / "ufo.toml").write_text(POSTGRES_TEMPLATE)

    assert eval_stack.database_cleanup_plan(arm, template_config(POSTGRES_TEMPLATE)) == (
        "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo",
        (),
    )
    assert eval_stack.database_cleanup_plan(arm, template_config(SQLITE_TEMPLATE)) == (None, ())


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
    with pytest.raises(ValidationError, match="--memory-ingestion"):
        RunSpec(label="smoke", config=Path("ufo.toml"), args=("--memory-ingestion", "snap"))
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


async def test_shutdown_releases_the_docker_sandboxes_this_stack_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A serve owns its conversations' containers for its own life and reclaims an idle one only
    from a later create, so a stack that exits holds every bridge subnet it took. Docker's default
    pool is about thirty-one, so the next stack cannot create its own and its turns die on
    `docker network create` before reaching a model — an arm that reads as scoring zero when it
    never ran. Teardown releases them, scoped to this stack's own workspace root."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(DOCKER_TEMPLATE)
    spec = RunSpec(label="release", config=template, args=("--only", "basics"))
    stack = EvalStack.provision(
        spec, root=tmp_path / "run" / "release", out=tmp_path / "archive", repo_root=tmp_path
    )
    _close(stack)
    mine = UUID("11111111-2222-3333-4444-555555555555")
    workspaces = stack.config.sandbox.workspace_root
    (workspaces / str(mine)).mkdir(parents=True)
    (workspaces / "not-a-conversation").mkdir()
    calls: list[tuple[str, ...]] = []

    async def record(*argv: str) -> None:
        calls.append(argv)

    monkeypatch.setattr(eval_stack, "_docker", record)

    await stack._shutdown(_ExitedProcess(), _ExitedProcess())

    assert calls == [
        ("rm", "-f", f"ufo-sbx-{mine}"),
        ("network", "rm", f"ufo-sandbox-{mine.hex}"),
    ]


async def test_shutdown_finishes_its_release_list_after_docker_refuses_a_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(DOCKER_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="release-all", config=template, args=("--only", "basics")),
        root=tmp_path / "run" / "release-all",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    held = UUID("11111111-2222-3333-4444-555555555555")
    last = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
    workspaces = stack.config.sandbox.workspace_root
    for conversation in (held, last):
        (workspaces / str(conversation)).mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    async def refuse(*argv: str) -> None:
        calls.append(argv)
        if argv == ("network", "rm", f"ufo-sandbox-{held.hex}"):
            raise RuntimeError(f"docker {' '.join(argv)} exited 1: network has active endpoints")
        if argv == ("rm", "-f", f"ufo-sbx-{last}"):
            raise FileNotFoundError(2, "No such file or directory: 'docker'")

    monkeypatch.setattr(eval_stack, "_docker", refuse)

    await stack._shutdown(_ExitedProcess(), _ExitedProcess())
    events = [json.loads(line) for line in (stack.root / "process.log").read_text().splitlines()]
    _close(stack)

    assert calls == [
        ("rm", "-f", f"ufo-sbx-{held}"),
        ("network", "rm", f"ufo-sandbox-{held.hex}"),
        ("rm", "-f", f"ufo-sbx-{last}"),
        ("network", "rm", f"ufo-sandbox-{last.hex}"),
    ]
    assert [(event["action"], event["name"]) for event in events] == [
        ("release-failed", f"network rm ufo-sandbox-{held.hex}"),
        ("release-failed", f"rm -f ufo-sbx-{last}"),
    ]
    assert "active endpoints" in events[0]["detail"]


async def test_shutdown_leaves_sandboxes_alone_on_a_backend_that_owns_no_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the Docker carrier holds host containers and subnets. An off-cluster backend keeps its
    boxes on its own side, so a stack on one has nothing here to release and must not shell out
    guessing at names."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    spec = RunSpec(label="offcluster", config=template, args=("--only", "basics"))
    stack = EvalStack.provision(
        spec, root=tmp_path / "run" / "offcluster", out=tmp_path / "archive", repo_root=tmp_path
    )
    _close(stack)
    workspaces = stack.config.sandbox.workspace_root
    (workspaces / "11111111-2222-3333-4444-555555555555").mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    async def record(*argv: str) -> None:
        calls.append(argv)

    monkeypatch.setattr(eval_stack, "_docker", record)

    await stack._shutdown(_ExitedProcess())

    assert calls == []


async def test_shutdown_records_its_signal_before_the_process_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="trace", config=template, args=("--only", "basics")),
        root=tmp_path / "run" / "trace",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    process = await asyncio.create_subprocess_exec("sleep", "60")

    await stack._shutdown(process)
    events = [json.loads(line) for line in (stack.root / "process.log").read_text().splitlines()]
    _close(stack)

    assert [(event["action"], event["name"]) for event in events] == [
        ("terminate", "serve"),
        ("exited", "serve"),
    ]
    assert events[0]["pid"] == process.pid
    assert events[1]["returncode"] == -15


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
        env={
            "MCP_ATLAS_URL": "http://127.0.0.1:9000",
            "UFO_ANTHROPIC_API_KEY": "sk-ant-stack-member",
        },
        model="claude-haiku-4-5",
        reasoning="high",
        member_model_provider="anthropic",
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
    assert stack.serve_binary.is_absolute()
    assert stack.serve_binary.name == "eval-serve"
    assert stack.serve_binary.resolve() == Path(sys.executable).with_name("ufoctl")
    args = stack._child_args()
    assert args[:4] == ("--jobbench", "snap", "--only", "jobbench")
    assert ("--out", str(tmp_path / "archive")) == args[4:6]
    assert ("--label", "boundary") == args[6:8]
    assert args[8:] == ("--jobbench-submissions", str(root.resolve() / "submissions"))
    assert "--model" not in args
    assert stack._seed_args() == (
        "init",
        "--email",
        "evals@localhost",
        "--model",
        "claude-haiku-4-5",
        "--reasoning",
        "high",
        "--member-model-provider",
        "anthropic",
    )


def test_remote_child_args_carry_the_model_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(
            label="remote-model",
            config=template,
            args=("--remote", "--only", "basics"),
            model="z-ai/glm-5.3-flash",
        ),
        root=tmp_path / "stack",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )

    args = stack._child_args()
    _close(stack)

    assert args[args.index("--model") : args.index("--model") + 2] == (
        "--model",
        "z-ai/glm-5.3-flash",
    )


def test_child_args_carry_the_environment_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    document = tmp_path / "arm.yaml"
    document.write_text("main:\n  prompt:\n    text: OVERRIDDEN\n")
    stack = EvalStack.provision(
        RunSpec(
            label="local-environment",
            config=template,
            args=("--only", "basics"),
            environment=document,
        ),
        root=tmp_path / "stack",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )

    args = stack._child_args()
    _close(stack)

    assert args[args.index("--environment") : args.index("--environment") + 2] == (
        "--environment",
        str(document.resolve()),
    )


async def test_start_serve_uses_the_stack_private_executable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="serve-name", config=template),
        root=tmp_path / "run" / "serve-name",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    called: list[tuple[tuple[str, ...], Path | None]] = []

    class Process:
        pid = 123
        returncode = None

    async def ufoctl(
        self: EvalStack, *argv: str, log: IO[bytes], binary: Path | None = None
    ) -> Process:
        called.append((argv, binary))
        return Process()

    monkeypatch.setattr(EvalStack, "_ufoctl", ufoctl)

    await stack._start_serve()
    _close(stack)

    assert called == [(("serve",), stack.serve_binary)]
    assert "ufoctl serve" not in f"{stack.serve_binary} serve"


def test_app_eval_uses_the_template_parent_agent_and_rejects_matrix_model_knobs(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="app", config=template, args=("--only", "ufo-app-bench")),
        root=tmp_path / "app",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    _close(stack)

    assert stack._seed_args() == ("init", "--email", "evals@localhost")
    with pytest.raises(ValueError, match="app suites use the template parent agent"):
        RunSpec(
            label="varied",
            config=template,
            args=("--only=ufo-app-bench",),
            model="google/gemini-3.7-flash",
        )


@pytest.mark.parametrize("suite", ("ufo-app-bench", "new_application", "red_after_green"))
def test_docker_eval_pins_the_current_sandbox_image_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite: str
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(DOCKER_TEMPLATE)
    plan = SandboxImagePlan(
        tmp_path / "sandbox.Dockerfile",
        "ufo-sandbox-eval:0123456789abcdef",
        "sha256:source",
    )
    monkeypatch.setattr(eval_stack, "sandbox_image_plan", lambda *_: plan)

    stack = EvalStack.provision(
        RunSpec(label="app-image", config=template, args=("--only", suite)),
        root=tmp_path / "app-image",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    persisted = Config.model_validate(tomllib.loads(stack.config_file.read_text()))
    _close(stack)

    assert stack.sandbox_image == plan
    assert stack.config.sandbox.image_ref == plan.reference
    assert persisted.sandbox.image_ref == plan.reference


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


async def test_create_and_drop_databases_owns_the_app_and_dbos_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not postgres_reachable():
        pytest.skip("postgres service not reachable")
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    url = make_url(POSTGRES_TEST_URL)
    admin_url = f"postgresql+asyncpg://{url.username}:{url.password}@{url.host}:{url.port}/ufo"
    stack = _postgres_stack(
        tmp_path,
        "dbpair",
        f'[database]\nurl = "{admin_url}"\n\n[blob]\nbackend = "filesystem"\nroot = "./blobs"\n',
    )
    app_name = stack.config.database.url.rpartition("/")[2]
    ownership = stack.database_ownership
    assert ownership is not None

    real_connect = asyncpg.connect
    mode = "create"
    interrupted = False
    created = asyncio.Event()
    resume = asyncio.Event()
    late_blocker: asyncpg.Connection | None = None

    class InterruptingConnection:
        def __init__(self, connection: asyncpg.Connection) -> None:
            self.connection = connection

        async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
            return await self.connection.fetch(query, *args)

        async def execute(self, query: str, *args: object) -> str:
            nonlocal interrupted, late_blocker
            result = await self.connection.execute(query, *args)
            if mode == "create" and query.startswith("create database") and not created.is_set():
                created.set()
                await resume.wait()
            if mode in {"cancel", "late-blocker"} and query.startswith("drop") and not interrupted:
                interrupted = True
                if mode == "cancel":
                    raise asyncio.CancelledError
                late_blocker = await real_connect(stack.config.database.url.replace("+asyncpg", ""))
            return result

        async def close(self) -> None:
            await self.connection.close()

    async def connect(*args: object, **kwargs: object) -> InterruptingConnection:
        return InterruptingConnection(await real_connect(*args, **kwargs))

    monkeypatch.setattr(eval_stack.asyncpg, "connect", connect)
    admin = await real_connect(admin_url.replace("+asyncpg", ""))
    running = asyncio.create_task(stack._create_databases())
    await created.wait()
    running.cancel()
    resume.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    mode = ""
    with pytest.raises(RuntimeError, match="already exist"):
        await stack._create_databases()
    blocker = await real_connect(stack.config.database.url.replace("+asyncpg", ""))

    try:
        rows = await admin.fetch(
            "select datname, shobj_description(oid, 'pg_database') as ownership "
            "from pg_database where datname = any($1::name[])",
            [app_name, f"{app_name}_dbos"],
        )
        assert sorted(row["datname"] for row in rows) == [app_name, f"{app_name}_dbos"]
        assert {row["ownership"] for row in rows} == {ownership.marker}
        await admin.execute(f"comment on database \"{ownership.application}\" is 'foreign'")
        with pytest.raises(RuntimeError, match="ownership"):
            await drop_eval_database_pairs(stack.admin_database_url, (ownership,))
        await admin.execute(
            f"comment on database \"{ownership.application}\" is '{ownership.marker}'"
        )
        with pytest.raises(RuntimeError, match="still connected"):
            await drop_eval_database_pairs(stack.admin_database_url, (ownership,))
        await blocker.close()
        await admin.execute(f'drop database "{ownership.system}"')
        await drop_eval_database_pairs(stack.admin_database_url, (ownership,))
        await drop_eval_database_pairs(stack.admin_database_url, (ownership,))
        assert not await admin.fetch(
            "select datname from pg_database where datname = any($1::name[])",
            [app_name, f"{app_name}_dbos"],
        )
        for mode in ("cancel", "late-blocker"):
            await stack._create_databases()
            interrupted = False
            expected = asyncio.CancelledError if mode == "cancel" else asyncpg.ObjectInUseError
            with pytest.raises(expected):
                await drop_eval_database_pairs(admin_url, (ownership,))
            assert {
                row["datname"]
                for row in await admin.fetch(
                    "select datname from pg_database where datname = any($1::name[])",
                    [ownership.application, ownership.system],
                )
            } == {ownership.application}
            if late_blocker is not None:
                await late_blocker.close()
            await drop_eval_database_pairs(admin_url, (ownership,))
            assert not await admin.fetch(
                "select 1 from pg_database where datname = any($1::name[])",
                [ownership.application, ownership.system],
            )
    finally:
        if late_blocker is not None and not late_blocker.is_closed():
            await late_blocker.close()
        if not blocker.is_closed():
            await blocker.close()
        for name in (app_name, f"{app_name}_dbos"):
            await admin.execute(f'drop database if exists "{name}"')
        await admin.close()


async def test_database_cleanup_refuses_a_different_identity_or_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ownership = _ownership(tmp_path / "owned")

    with pytest.raises(ValueError, match="authority"):
        await drop_eval_database_pairs(
            "postgresql+asyncpg://ufo:ufo@127.0.0.1:5542/ufo", (ownership,)
        )

    async def connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid ownership must fail before connecting")

    monkeypatch.setattr(eval_stack.asyncpg, "connect", connect)
    with pytest.raises(ValueError, match="run identity"):
        await drop_eval_database_pairs(
            "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo",
            (_ownership(tmp_path / "owned", "x"),),
        )


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
    with pytest.raises(ValidationError, match="materialization owns the agent"):
        RunSpec(label="memory", config=postgres_template, memory_100=snapshot, reasoning="high")
    with pytest.raises(ValidationError, match=r"models\.auto_model owns the recall model"):
        RunSpec(
            label="ingestion",
            config=postgres_template,
            memory_ingestion=snapshot,
            model="claude",
        )
    assert (
        RunSpec(
            label="ingestion",
            config=postgres_template,
            memory_ingestion=snapshot,
            reasoning="medium",
        ).reasoning
        == "medium"
    )
    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()


class _DoneProcess:
    """A finished subprocess: `_checked` reads its exit status and nothing else."""

    returncode = 0

    async def wait(self) -> int:
        return 0


async def test_app_eval_preparation_settles_each_agent_and_is_idempotent(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ufo.db'}"
    apply_cached_migrations(database_url)
    config = Config.model_validate(
        tomllib.loads(SQLITE_TEMPLATE.replace("sqlite+aiosqlite:///ufo.db", database_url))
    )
    workspace_id = UUID("11111111-2222-3333-4444-555555555555")
    agent_ids = (
        UUID("aaaaaaaa-1111-2222-3333-444444444444"),
        UUID("bbbbbbbb-1111-2222-3333-444444444444"),
    )
    init_db(database_url)
    try:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
            )
            await connection.execute(
                sa.insert(tables.agent),
                [
                    {
                        "id": agent_id,
                        "workspace_id": workspace_id,
                        "name": f"agent-{index}",
                        "prompt": "p",
                        "model": "m",
                        "is_main": index == 0,
                        "tools": None if index == 0 else ["read"],
                        "created_at": now,
                        "updated_at": now,
                    }
                    for index, agent_id in enumerate(agent_ids)
                ],
            )
    finally:
        await dispose_db()

    await prepare_app_eval(config)
    await prepare_app_eval(config)

    init_db(database_url)
    try:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.ext_store.c.extension,
                        tables.ext_store.c.key,
                        tables.ext_store.c.value,
                    ).order_by(tables.ext_store.c.key)
                )
            ).all()
            tool_rows = (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.tools).order_by(tables.agent.c.id)
                )
            ).all()
    finally:
        await dispose_db()
    assert rows == [
        (WEB_EXTENSION, f"{HOMEPAGE_SEED_PREFIX}{agent_id}", SETTLED_MARKER)
        for agent_id in agent_ids
    ]
    assert tool_rows == [(agent_ids[0], list(APP_PARENT_TOOLS)), (agent_ids[1], ["read"])]

    init_db(database_url)
    try:
        async with workspace_tx() as connection:
            await connection.execute(sa.delete(tables.ext_store))
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == agent_ids[0])
                .values(tools=["object_apply"])
            )
    finally:
        await dispose_db()

    await prepare_creation_eval(config)
    await prepare_creation_eval(config)

    init_db(database_url)
    try:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.ext_store.c.extension,
                        tables.ext_store.c.key,
                        tables.ext_store.c.value,
                    ).order_by(tables.ext_store.c.key)
                )
            ).all()
            tool_rows = (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.tools).order_by(tables.agent.c.id)
                )
            ).all()
    finally:
        await dispose_db()
    assert rows == [
        (WEB_EXTENSION, f"{HOMEPAGE_SEED_PREFIX}{agent_id}", SETTLED_MARKER)
        for agent_id in agent_ids
    ]
    assert tool_rows == [(agent_ids[0], ["object_apply"]), (agent_ids[1], ["read"])]


async def test_only_app_and_creation_suites_run_the_homepage_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    calls: list[tuple[str, ...]] = []
    disabled_jobs: list[tuple[str, ...]] = []

    async def create(*argv: str, **_: object) -> _DoneProcess:
        calls.append(argv)
        return _DoneProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    for label, args in (
        ("copy", ("--only", "ufo-app-copy", "--case", "copy-meeting-tasks")),
        ("bench", ("--only=ufo-app-bench",)),
        ("creation", ("--only", "new_application", "--case", "A05-guided-build")),
        ("other", ("--only", "basics")),
    ):
        stack = EvalStack.provision(
            RunSpec(label=label, config=template, args=args),
            root=tmp_path / label,
            out=tmp_path / "archive",
            repo_root=tmp_path,
        )
        disabled_jobs.append(stack.config.serve.disabled_jobs)
        await stack._prepare_app_eval()
        _close(stack)

    assert [call[2] for call in calls] == [
        "evals.suites.ufo_app_prepare",
        "evals.suites.ufo_app_prepare",
        "evals.suites.ufo_app_prepare",
    ]
    assert calls[2][-1] == "--creation"
    assert disabled_jobs == [(), (), CREATION_DISABLED_JOBS, ()]


@pytest.mark.parametrize("other_suite", ("new_application", "basics"))
def test_app_suites_require_separate_stacks(tmp_path: Path, other_suite: str) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)

    with pytest.raises(ValueError, match="app suites require a separate eval stack"):
        EvalStack.provision(
            RunSpec(
                label="mixed-apps",
                config=template,
                args=("--only", "ufo-app-bench", other_suite),
            ),
            root=tmp_path / "mixed-apps",
            out=tmp_path / "archive",
            repo_root=tmp_path,
        )


def test_app_eval_preparation_matches_the_web_homepage_job() -> None:
    from ufo_ext_web import surface as web_surface

    assert WEB_EXTENSION == "web"
    assert HOMEPAGE_SEED_PREFIX == web_surface.HOMEPAGE_SEED_PREFIX


async def test_stack_prepares_the_app_eval_after_seed_and_before_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(DOCKER_TEMPLATE)
    image = SandboxImagePlan(
        tmp_path / "sandbox.Dockerfile",
        "ufo-sandbox-eval:0123456789abcdef",
        "sha256:source",
    )
    monkeypatch.setattr(eval_stack, "sandbox_image_plan", lambda *_: image)
    stack = EvalStack.provision(
        RunSpec(label="app", config=template, args=("--only", "ufo-app-copy")),
        root=tmp_path / "app",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    for path in APPLICATION_BUILD_PRODUCTS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("built")
    events: list[str] = []

    async def prepare_image(self: EvalStack) -> None:
        events.append("image")

    async def no_databases(self: EvalStack) -> None:
        return None

    async def done_ufoctl(self: EvalStack, *argv: str, log: object) -> _DoneProcess:
        return _DoneProcess()

    async def preflight(self: EvalStack) -> None:
        return None

    async def seed(self: EvalStack) -> None:
        events.append("seed")

    async def prepare(self: EvalStack) -> None:
        events.append("prepare")

    async def serve(self: EvalStack) -> _DoneProcess:
        events.append("serve")
        return _DoneProcess()

    async def egress(self: EvalStack, binary: Path) -> _DoneProcess:
        return _DoneProcess()

    async def ready(self: EvalStack, process: _DoneProcess) -> None:
        return None

    async def drive(
        self: EvalStack,
        serve_process: _DoneProcess,
        egress_process: _DoneProcess,
        readiness: Path | None,
    ) -> int:
        return 0

    async def shutdown(
        self: EvalStack,
        serve_process: _DoneProcess,
        egress_process: _DoneProcess | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(eval_stack, "_egress_binary", lambda _: tmp_path / "ufo-egress")
    monkeypatch.setattr(EvalStack, "_prepare_sandbox_image", prepare_image)
    monkeypatch.setattr(EvalStack, "_create_databases", no_databases)
    monkeypatch.setattr(EvalStack, "_ufoctl", done_ufoctl)
    monkeypatch.setattr(EvalStack, "_preflight", preflight)
    monkeypatch.setattr(EvalStack, "_seed", seed)
    monkeypatch.setattr(EvalStack, "_prepare_app_eval", prepare)
    monkeypatch.setattr(EvalStack, "_start_serve", serve)
    monkeypatch.setattr(EvalStack, "_start_egress", egress)
    monkeypatch.setattr(EvalStack, "_ready", ready)
    monkeypatch.setattr(EvalStack, "_egress_ready", ready)
    monkeypatch.setattr(EvalStack, "_drive", drive)
    monkeypatch.setattr(EvalStack, "_shutdown", shutdown)

    result = await stack.run(asyncio.Lock())

    assert result.passed
    assert events == ["image", "seed", "prepare", "serve"]


async def test_cancelled_drive_reaps_eval_before_sandbox_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="cancel", config=template),
        root=tmp_path / "cancel",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    events: list[str] = []
    eval_waiting = asyncio.Event()
    eval_signalled = asyncio.Event()
    eval_may_exit = asyncio.Event()
    watcher_started = {"serve": asyncio.Event(), "egress": asyncio.Event()}
    watcher_cancelled: set[str] = set()
    session: bool | None = None

    class Service:
        def __init__(self, name: str, pid: int) -> None:
            self.name = name
            self.pid = pid
            self.returncode: int | None = None
            self.exited = asyncio.Event()

        async def wait(self) -> int:
            watcher_started[self.name].set()
            try:
                await self.exited.wait()
            except asyncio.CancelledError:
                watcher_cancelled.add(self.name)
                raise
            return self.returncode or 0

        def terminate(self) -> None:
            self.returncode = 0
            self.exited.set()

        def kill(self) -> None:
            raise AssertionError("service did not need SIGKILL")

    class EvalProcess:
        pid = 900
        returncode: int | None = None

        async def wait(self) -> int:
            eval_waiting.set()
            await eval_may_exit.wait()
            self.returncode = -signal.SIGTERM
            events.append("eval-exited")
            return self.returncode

    serve = Service("serve", 901)
    egress = Service("egress", 902)
    child = EvalProcess()

    async def create(*_argv: str, **kwargs: object) -> EvalProcess:
        nonlocal session
        session = bool(kwargs["start_new_session"])
        return child

    async def nothing(*_args: object, **_kwargs: object) -> None:
        return None

    async def done_ufoctl(*_args: object, **_kwargs: object) -> object:
        return object()

    async def seed(*_args: object, **_kwargs: object) -> None:
        return None

    async def start_serve(*_args: object, **_kwargs: object) -> Service:
        return serve

    async def start_egress(*_args: object, **_kwargs: object) -> Service:
        return egress

    async def release(*_args: object, **_kwargs: object) -> None:
        events.append("sandboxes-released")

    def kill_group(pid: int, sent: signal.Signals) -> None:
        assert (pid, sent) == (child.pid, signal.SIGTERM)
        events.append("eval-terminated")
        eval_signalled.set()

    monkeypatch.setattr(eval_stack, "_egress_binary", lambda _: tmp_path / "ufo-egress")
    monkeypatch.setattr(eval_stack.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(eval_stack.os, "killpg", kill_group)
    monkeypatch.setattr(EvalStack, "_prepare_sandbox_image", nothing)
    monkeypatch.setattr(EvalStack, "_create_databases", nothing)
    monkeypatch.setattr(EvalStack, "_ufoctl", done_ufoctl)
    monkeypatch.setattr(EvalStack, "_checked", nothing)
    monkeypatch.setattr(EvalStack, "_preflight", nothing)
    monkeypatch.setattr(EvalStack, "_seed", seed)
    monkeypatch.setattr(EvalStack, "_prepare_app_eval", nothing)
    monkeypatch.setattr(EvalStack, "_start_serve", start_serve)
    monkeypatch.setattr(EvalStack, "_start_egress", start_egress)
    monkeypatch.setattr(EvalStack, "_ready", nothing)
    monkeypatch.setattr(EvalStack, "_egress_ready", nothing)
    monkeypatch.setattr(EvalStack, "_release_sandboxes", release)

    task = asyncio.create_task(stack.run(asyncio.Lock()))
    await eval_waiting.wait()
    await asyncio.gather(*(event.wait() for event in watcher_started.values()))
    task.cancel()
    await eval_signalled.wait()

    assert events == ["eval-terminated"]

    eval_may_exit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session is True
    assert watcher_cancelled == {"serve", "egress"}
    assert events.index("eval-terminated") < events.index("eval-exited")
    assert events.index("eval-exited") < events.index("sandboxes-released")


async def test_docker_cleanup_reports_real_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            (1, b"", b"Error response from daemon: No such container: ufo-sbx-missing"),
            (1, b"", b"Error response from daemon: network ufo-sandbox-missing not found"),
            (1, b"", b"Cannot connect to the Docker daemon"),
            (1, b"", b"network has active endpoints"),
        )
    )

    class Process:
        def __init__(self, response: tuple[int, bytes, bytes]) -> None:
            self.returncode, self.stdout, self.stderr = response

        async def communicate(self) -> tuple[bytes, bytes]:
            return self.stdout, self.stderr

    async def create(*_argv: str, **_kwargs: object) -> Process:
        return Process(next(responses))

    monkeypatch.setattr(eval_stack.asyncio, "create_subprocess_exec", create)

    assert await _docker("rm", "-f", "ufo-sbx-missing") is None
    assert await _docker("network", "rm", "ufo-sandbox-missing") is None
    assert await _docker("rm", "-f", "ufo-sbx-live") == (
        "docker rm -f ufo-sbx-live exited 1: Cannot connect to the Docker daemon"
    )
    assert await _docker("network", "rm", "ufo-sandbox-live") == (
        "docker network rm ufo-sandbox-live exited 1: network has active endpoints"
    )


async def test_docker_cleanup_reports_an_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create(*_argv: str, **_kwargs: object) -> object:
        raise OSError("docker is unavailable")

    monkeypatch.setattr(eval_stack.asyncio, "create_subprocess_exec", create)

    assert await _docker("rm", "-f", "ufo-sbx-live") == (
        "docker rm -f ufo-sbx-live failed: docker is unavailable"
    )


async def test_cleanup_failures_preserve_the_child_outcome_and_release_later_sandboxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(DOCKER_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="cleanup-outcome", config=template),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    first = UUID("11111111-2222-3333-4444-555555555555")
    second = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
    workspaces = stack.config.sandbox.workspace_root
    (workspaces / str(first)).mkdir(parents=True)
    (workspaces / str(second)).mkdir()
    calls: list[tuple[str, ...]] = []

    async def nothing(*_args: object, **_kwargs: object) -> None:
        return None

    async def done_ufoctl(*_args: object, **_kwargs: object) -> _DoneProcess:
        return _DoneProcess()

    async def start(*_args: object, **_kwargs: object) -> _DoneProcess:
        return _DoneProcess()

    async def drive(*_args: object, **_kwargs: object) -> int:
        return 7

    async def docker(*argv: str) -> str | None:
        calls.append(argv)
        if len(calls) == 1:
            raise RuntimeError("docker daemon unavailable")
        return None

    monkeypatch.setattr(eval_stack, "_egress_binary", lambda _: tmp_path / "ufo-egress")
    monkeypatch.setattr(eval_stack, "_docker", docker)
    monkeypatch.setattr(EvalStack, "_prepare_sandbox_image", nothing)
    monkeypatch.setattr(EvalStack, "_create_databases", nothing)
    monkeypatch.setattr(EvalStack, "_ufoctl", done_ufoctl)
    monkeypatch.setattr(EvalStack, "_preflight", nothing)
    monkeypatch.setattr(EvalStack, "_seed", nothing)
    monkeypatch.setattr(EvalStack, "_prepare_app_eval", nothing)
    monkeypatch.setattr(EvalStack, "_start_serve", start)
    monkeypatch.setattr(EvalStack, "_start_egress", start)
    monkeypatch.setattr(EvalStack, "_ready", nothing)
    monkeypatch.setattr(EvalStack, "_egress_ready", nothing)
    monkeypatch.setattr(EvalStack, "_drive", drive)

    result = await stack.run(asyncio.Lock())
    events = [json.loads(line) for line in (stack.root / "process.log").read_text().splitlines()]

    assert result.exit_code == 7
    assert not result.passed
    assert calls == [
        ("rm", "-f", f"ufo-sbx-{first}"),
        ("network", "rm", f"ufo-sandbox-{first.hex}"),
        ("rm", "-f", f"ufo-sbx-{second}"),
        ("network", "rm", f"ufo-sandbox-{second.hex}"),
    ]
    assert [event for event in events if event["action"] == "release-failed"] == [
        {
            "at": events[0]["at"],
            "action": "release-failed",
            "name": f"rm -f ufo-sbx-{first}",
            "detail": "RuntimeError: docker daemon unavailable",
        }
    ]


@pytest.mark.parametrize("suite", ("ufo-app-bench", "new_application"))
async def test_app_page_stack_rejects_missing_build_products_before_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite: str
) -> None:
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="app-products", config=template, args=("--only", suite)),
        root=tmp_path / "app-products",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    seeded = False

    async def create_databases(self: EvalStack) -> None:
        nonlocal seeded
        seeded = True

    monkeypatch.setattr(EvalStack, "_create_databases", create_databases)

    with pytest.raises(RuntimeError, match="application eval requires generated build products"):
        await stack.run(asyncio.Lock())

    _close(stack)
    assert not seeded


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
    for log in (
        stack.seed_log,
        stack.serve_log,
        stack.egress_log,
        stack.eval_log,
        stack.process_log,
    ):
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
    for log in (stack.seed_log, stack.serve_log, stack.eval_log, stack.process_log):
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
    for log in (stack.seed_log, stack.serve_log, stack.eval_log, stack.process_log):
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


def test_memory_ingestion_seeds_and_passes_snapshot_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(POSTGRES_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n')
    snapshot = tmp_path / "snapshot"
    stack = EvalStack.provision(
        RunSpec(
            label="ingestion",
            config=template,
            memory_ingestion=snapshot,
            reasoning="medium",
        ),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    for log in (
        stack.seed_log,
        stack.serve_log,
        stack.egress_log,
        stack.eval_log,
        stack.process_log,
    ):
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

    assert asyncio.run(stack._seed()) == readiness
    assert ufoctl == [("migrate",)]
    assert materialized == [
        (
            "evals.memory_ingestion.materialize",
            "--agent-reasoning",
            "medium",
            "--snapshot",
            str(snapshot.resolve()),
        )
    ]
    assert stack._child_args(readiness)[-4:] == (
        "--memory-ingestion",
        str(snapshot.resolve()),
        "--memory-ingestion-state",
        str(readiness),
    )


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


async def test_corpus_materializer_and_eval_child_share_one_run_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE + '\n[o11y]\notlp_endpoint = "http://127.0.0.1:4318"\n')
    stack = EvalStack.provision(
        RunSpec(
            label="budgeted",
            config=template,
            args=("--remote", "--budget-usd", "1.25"),
            issue_recall=True,
        ),
        root=tmp_path / "run",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )
    readiness = tmp_path / "run" / "state" / "abc" / "readiness.json"
    spawned: list[tuple[str, ...]] = []

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return f"{readiness}\n".encode(), b""

    async def create(*argv: str, **_kwargs: object) -> Process:
        spawned.append(argv)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    assert await stack._materialize("evals.issue_recall.materialize") == readiness
    child = stack._child_args(readiness)
    _close(stack)

    assert stack.run_budget is not None
    assert spawned == [
        (
            sys.executable,
            "-m",
            "evals.issue_recall.materialize",
            "--run-id",
            str(stack.run_budget.run_id),
            "--budget-micro-usd",
            "1250000",
            "--state",
            str(tmp_path / "run" / "state"),
        )
    ]
    assert child[child.index("--run-id") + 1] == str(stack.run_budget.run_id)
    assert child[child.index("--budget-usd") + 1] == "1.25"


def test_database_name_is_postgres_safe() -> None:
    root = Path(".local/evals/20260717-141530/An-Odd.Label")
    name = _database_name(root)

    assert name.startswith("eval_20260717_141530_an_odd_label_")
    assert len(f"{_database_name(Path('x' * 80) / ('y' * 80))}_dbos") == 63


def test_database_name_owns_the_absolute_run_root(tmp_path: Path) -> None:
    first = _database_name(tmp_path / "first" / "same-stamp" / "same-label")
    second = _database_name(tmp_path / "second" / "same-stamp" / "same-label")

    assert first != second


def test_run_names_the_missing_egress_binary_before_any_seed_subprocess(tmp_path: Path) -> None:
    """The egress-binary stat is the cheapest gate the stack has and materialization its most
    expensive step, so a run fails on the missing binary before a single seed subprocess starts —
    not after minutes of corpus embedding, which is where the lookup used to sit."""
    template = tmp_path / "template.toml"
    template.write_text(SQLITE_TEMPLATE)
    stack = EvalStack.provision(
        RunSpec(label="nobinary", config=template),
        root=tmp_path / "stamp" / "nobinary",
        out=tmp_path / "archive",
        repo_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="cargo build"):
        asyncio.run(stack.run(asyncio.Lock()))

    assert (stack.root / "seed.log").read_bytes() == b""


def test_ufo_app_suites_are_explicit() -> None:
    assert not {"ufo-app-bench", "ufo-app-copy"} & {task.name for task in selected_run_tasks()}
    assert [task.name for task in selected_run_tasks(("ufo-app-bench",))] == ["ufo-app-bench"]
    assert [task.name for task in selected_run_tasks(("ufo-app-copy",))] == ["ufo-app-copy"]
