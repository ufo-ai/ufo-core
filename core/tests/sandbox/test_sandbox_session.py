import asyncio
import base64
import hashlib
import json
import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

import ufo.runtime.tools.tasks as tasks_module
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.auth.token_signing import sign_token
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DOCUMENT_READ_EXEC_TIMEOUT_SECONDS,
    SANDBOX_PYTHON_FLAG,
    SKILL_LOAD_PROG,
    SKILL_STAGING_DIRNAME,
    SYSTEM_SKILL_SYNC_PROG,
    SYSTEM_SKILLS_ROOT,
    WORKSPACE_DIR,
    WORKSPACE_SCOPE_HINT,
    ExecResult,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    host_argv,
    runtime_relative,
    shell_path,
    ufo_fs_file_op,
    workspace_path,
)
from ufo.host.tools.builtins import BashInput, bash_handler
from ufo.runtime.skills.runtime import RuntimeSkill, SystemSkillBundle
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.tasks import (
    BACKGROUND_DIRECTIVE,
    BACKGROUND_TASKS_DIR,
    DETACHED_LEAD,
    EXEC_TIMEOUT_COMMAND_MAX_CHARS,
    EXEC_TIMEOUT_VITALS_CMD,
    TASK_PROBE,
)
from ufo.runtime.turns.audience import conversation_audience
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

RUN_TOKENS = RunTokenCodec(b"run-token-test-secret")
LARGE_SKILL_BYTES = 1_000_000
LINUX_MAX_ARG_STRLEN = 131_072


def _check_shell_path_expands_only_the_runtime_home_prefix() -> None:
    assert shell_path("$UFO_HOME/runs/abc/repl/run file.mjs") == (
        "\"$UFO_HOME\"/'runs/abc/repl/run file.mjs'"
    )
    assert shell_path("/workspace/run file.mjs") == "'/workspace/run file.mjs'"


def _basic(username: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:ufo".encode()).decode()


def _check_run_token_round_trips_encode_then_proxy_auth() -> None:
    token = RunToken(workspace_id=uuid4(), turn_id=uuid4(), acts_for=uuid4())
    assert RUN_TOKENS.from_proxy_auth(_basic(RUN_TOKENS.encode(token))) == token


def _check_run_token_wire_carries_a_member_the_turn_or_nobody() -> None:
    workspace_id, turn_id, member_id = uuid4(), uuid4(), uuid4()
    for actor, expected in ((str(member_id), member_id), ("-", "turn"), ("~", "nobody")):
        payload = f"ufo-run/{workspace_id}/{turn_id}/{actor}".encode()
        decoded = RUN_TOKENS.from_proxy_auth(_basic(sign_token(RUN_TOKENS.secret, payload)))
        assert decoded == RunToken(workspace_id, turn_id, acts_for=expected)
        assert RUN_TOKENS.encode(decoded) == sign_token(RUN_TOKENS.secret, payload)
    with pytest.raises(ValueError):
        RUN_TOKENS.from_proxy_auth(
            _basic(sign_token(RUN_TOKENS.secret, f"ufo-run/{workspace_id}/{turn_id}".encode()))
        )


def _check_run_token_member_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        RunToken(uuid4(), uuid4(), uuid4())


def _check_encoded_token_is_url_safe_userinfo() -> None:
    encoded = RUN_TOKENS.encode(RunToken(workspace_id=uuid4(), turn_id=uuid4()))
    assert all(char.isalnum() or char in "-_." for char in encoded)


def _check_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("Bearer " + RUN_TOKENS.encode(RunToken(uuid4(), uuid4())))


def _check_from_proxy_auth_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="basic"):
        RUN_TOKENS.from_proxy_auth("")


def _check_from_proxy_auth_rejects_a_malformed_run_token() -> None:
    with pytest.raises(ValueError):
        RUN_TOKENS.from_proxy_auth(_basic("not-a-run-token"))


def _check_run_token_rejects_a_valid_shape_signed_by_another_deployment() -> None:
    run = RunToken(uuid4(), uuid4())
    forged = RunTokenCodec(b"other-deployment").encode(run)
    with pytest.raises(ValueError, match="signed"):
        RUN_TOKENS.from_proxy_auth(_basic(forged))


def _check_authorized_session_scopes_proxy_and_cli_environment_without_mutating_base() -> None:
    base_env = {
        "HTTPS_PROXY": "https://ufo-session-turn:ufo@proxy.test",
        "ALICE_KEY": "alice",
        "UNRELATED": "kept",
    }
    base = SandboxSession(
        carrier=_RecordingCarrier(),
        system_skill_archive=b"bundle",
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c", egress_env=base_env),
    )

    authorized = base.authorize(
        frozenset(("ALICE_KEY", "BOB_KEY")),
        {"BOB_KEY": "bob", "HTTPS_PROXY": "https://ufo-session-member:ufo@proxy.test"},
    )

    assert authorized.system_skill_archive == b"bundle"
    assert authorized.handle.egress_env == {
        "HTTPS_PROXY": "https://ufo-session-member:ufo@proxy.test",
        "UNRELATED": "kept",
        "BOB_KEY": "bob",
    }
    assert base.handle.egress_env == base_env


def _check_an_authorized_session_keeps_the_turn_a_stop_is_scoped_to() -> None:
    """A tool runs commands through a member-authorized session while cancel uses the base one.
    Both name the same turn, so the stop reaches its command groups alone."""
    turn_id = uuid4()
    base = SandboxSession(
        carrier=_RecordingCarrier(),
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c", turn_id=turn_id),
    )

    authorized = base.authorize(frozenset(), {})

    assert authorized.handle.turn_id == turn_id


def _check_host_argv_names_a_logical_path_under_the_host_root() -> None:
    root = "/Users/member/proj"

    assert host_argv(("cat", f"{WORKSPACE_DIR}/hello/index.html"), root) == (
        "cat",
        "/Users/member/proj/hello/index.html",
    )
    assert host_argv(
        ("bash", "-lc", f'cd {WORKSPACE_DIR} && cat "{WORKSPACE_DIR}/a.txt"'), root
    ) == ("bash", "-lc", 'cd /Users/member/proj && cat "/Users/member/proj/a.txt"')
    assert host_argv(
        (f"PYTHONPATH=/lib:{WORKSPACE_DIR}/pkg", f"file://{WORKSPACE_DIR}/staged"), root
    ) == ("PYTHONPATH=/lib:/Users/member/proj/pkg", "file:///Users/member/proj/staged")


def _check_host_argv_leaves_the_substring_that_is_not_this_workspace() -> None:
    untouched = (
        "https://bucket.s3.amazonaws.com/workspaces/f795c197/sites/i.html?X-Amz-Signature=a",
        "sites/workspaces/nested",
        "$HOME/workspace",
        "${HOME}/workspace",
        "./workspace",
        "/workspace-old/index.html",
        "/workspaces",
    )

    assert host_argv(untouched, "/Users/member/proj") == untouched


class _RecordingCarrier:
    def __init__(
        self,
        result: ExecResult | None = None,
        probe: ExecResult | None = None,
        results: list[ExecResult] | None = None,
    ) -> None:
        self.timeouts: list[int] = []
        self.commands: list[str] = []
        self.argvs: list[tuple[str, ...]] = []
        self.writes: list[tuple[str, bytes]] = []
        self.result = result or ExecResult(stdout="", stderr="", exit_code=0)
        self.probe = probe
        self.results = list(results or ())

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise NotImplementedError

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.writes.append((path, content))

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        self.timeouts.append(timeout_s)
        self.commands.append(argv[-1] if argv else "")
        self.argvs.append(argv)
        if self.probe is not None and argv and argv[-1] == EXEC_TIMEOUT_VITALS_CMD:
            return self.probe
        if TASK_PROBE in argv:
            return ExecResult(stdout="", stderr="", exit_code=0)
        if self.results:
            return self.results.pop(0)
        return self.result

    async def exec_skill(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        return await self.exec(handle, argv, timeout_s)

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn unused")


def _bash_ctx(carrier: _RecordingCarrier, tmp_path: Path) -> ToolContext:
    session = SandboxSession(
        carrier=carrier, handle=SandboxHandle(conversation_id=uuid4(), container_id="c")
    )
    return _tool_ctx(session, tmp_path)


async def _live_ctx(
    tmp_path: Path,
    idempotency_key: str | None = None,
    conversation_id: UUID | None = None,
    turn: Turn | None = None,
) -> ToolContext:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=(
                turn.conversation_id if turn is not None else conversation_id or uuid4()
            ),
            image_ref=SANDBOX_IMAGE_REF,
            workspace_host_path=str(tmp_path / "my ws"),
        )
    )
    return _tool_ctx(
        SandboxSession(carrier=carrier, handle=handle), tmp_path, idempotency_key, turn=turn
    )


async def _seeded_turn() -> Turn:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=turn.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=turn.agent_id,
                workspace_id=turn.workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=turn.conversation_id,
                workspace_id=turn.workspace_id,
                agent_id=turn.agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn.id,
                workspace_id=turn.workspace_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                seq=turn.seq,
                status=turn.status,
                inbound=turn.inbound,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn


async def _detached_until(turn: Turn) -> datetime | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.detached_until).where(tables.turn.c.id == turn.id)
            )
        ).scalar_one()


async def _wait_for_file(session: SandboxSession, path: str) -> str:
    for _ in range(200):
        result = await session.bash(f'cat "{path}" 2>/dev/null || true')
        if result.stdout.strip():
            return result.stdout.strip()
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def _tool_ctx(
    session: SandboxSession,
    tmp_path: Path,
    idempotency_key: str | None = None,
    turn: Turn | None = None,
) -> ToolContext:
    return ToolContext(
        idempotency_key=idempotency_key,
        sandbox=session,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn
        or Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


async def _check_a_carrier_that_declares_no_stop_is_never_asked_for_one() -> None:
    session = SandboxSession(
        carrier=_RecordingCarrier(),
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )

    await session.stop_commands()

    assert not hasattr(session.carrier, "stop_commands")


async def _check_skills_load_from_one_staged_container_payload() -> None:
    carrier = _RecordingCarrier(
        result=ExecResult(
            stdout='{"roots":{"sandbox":"/home/user/.ufo/skills/sandbox"}}',
            stderr="",
            exit_code=0,
        )
    )
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )

    roots = await session.load_skills(
        {
            "system": {"sandbox": "sha256:aaa", "ufo-style": "sha256:bbb"},
            "user": {},
        }
    )

    assert roots == {"sandbox": "/home/user/.ufo/skills/sandbox"}
    [(staged, content)] = carrier.writes
    runtime_root = f"/home/user/.ufo/runs/{session.handle.conversation_id.hex}"
    assert staged.startswith(f"{runtime_root}/{SKILL_STAGING_DIRNAME}/skill-load-")
    assert staged.endswith(".json")
    assert json.loads(content) == {
        "system": {"sandbox": "sha256:aaa", "ufo-style": "sha256:bbb"},
        "user": {},
    }
    assert carrier.argvs == [
        (
            "python3",
            SANDBOX_PYTHON_FLAG,
            "-c",
            SKILL_LOAD_PROG,
            staged,
            SYSTEM_SKILLS_ROOT,
            hashlib.sha256(content).hexdigest(),
        )
    ]


async def _check_large_skill_payload_never_enters_a_container_command_argument() -> None:
    carrier = _RecordingCarrier(
        result=ExecResult(
            stdout='{"roots":{"large":"/home/user/.ufo/skills/large"}}',
            stderr="",
            exit_code=0,
        )
    )
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )
    encoded = base64.urlsafe_b64encode(b"x" * LARGE_SKILL_BYTES).decode()

    roots = await session.load_skills(
        {
            "system": {},
            "user": {
                "large": {
                    "digest": "sha256:aaa",
                    "files": {"reference.bin": encoded},
                }
            },
        }
    )

    assert roots == {"large": "/home/user/.ufo/skills/large"}
    assert len(carrier.writes[0][1]) > LARGE_SKILL_BYTES
    assert max(len(arg) for arg in carrier.argvs[0]) < LINUX_MAX_ARG_STRLEN


async def _check_skill_load_fails_when_the_container_loader_fails() -> None:
    carrier = _RecordingCarrier(result=ExecResult(stdout="", stderr="missing", exit_code=2))
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )

    with pytest.raises(OSError, match="missing"):
        await session.load_skills({"system": {"sandbox": "sha256:aaa"}, "user": {}})


async def _check_a_stale_sandbox_refreshes_before_loading_the_requested_system_skill() -> None:
    class _PrivilegedCarrier(_RecordingCarrier):
        async def exec_skill(
            self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
        ) -> ExecResult:
            return await super().exec(handle, argv, timeout_s)

        async def exec(
            self,
            handle: SandboxHandle,
            argv: tuple[str, ...],
            timeout_s: int,
            model_command: str | None = None,
        ) -> ExecResult:
            raise AssertionError("skill programs must use privileged execution")

    carrier = _PrivilegedCarrier(
        results=[
            ExecResult(stdout='{"roots":{}}', stderr="", exit_code=0),
            ExecResult(stdout="", stderr="", exit_code=0),
            ExecResult(
                stdout='{"roots":{"sandbox":"/home/user/.ufo/skills/sandbox"}}',
                stderr="",
                exit_code=0,
            ),
        ]
    )
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
        system_skill_archive=b"current bundle",
    )
    payload = {"system": {"sandbox": "sha256:current"}, "user": {}}

    roots = await session.load_skills(payload)

    assert roots == {"sandbox": "/home/user/.ufo/skills/sandbox"}
    assert len(carrier.argvs) == 3
    assert carrier.argvs[0][3] == SKILL_LOAD_PROG
    assert carrier.argvs[1][3] == SYSTEM_SKILL_SYNC_PROG
    assert carrier.argvs[2][3] == SKILL_LOAD_PROG
    assert all(argv[0] == "python3" for argv in carrier.argvs)
    runtime_root = f"/home/user/.ufo/runs/{session.handle.conversation_id.hex}"
    assert carrier.writes[0][0].startswith(f"{runtime_root}/{SKILL_STAGING_DIRNAME}/skill-load-")
    assert carrier.writes[1][0].startswith(f"{runtime_root}/{SKILL_STAGING_DIRNAME}/system-skills-")
    assert carrier.writes[1][1] == b"current bundle"
    assert carrier.writes[2][0].startswith(f"{runtime_root}/{SKILL_STAGING_DIRNAME}/skill-load-")


def test_current_loader_refreshes_a_stale_bundle_and_installs_an_inactive_user_skill(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    inactive = RuntimeSkill(
        name="inactive",
        description="inactive",
        instructions="inactive",
        raw_skill_md="inactive",
    )
    active = RuntimeSkill(
        name="active/child",
        description="active",
        instructions="active",
        raw_skill_md="active",
    )
    for index, bundle in enumerate(
        (
            SystemSkillBundle.from_skills((inactive,)),
            SystemSkillBundle.from_skills((active,)),
        )
    ):
        archive = tmp_path / f"bundle-{index}.zip"
        archive.write_bytes(bundle.archive)
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                SYSTEM_SKILL_SYNC_PROG,
                str(archive),
                str(root),
                hashlib.sha256(bundle.archive).hexdigest(),
            ],
            check=True,
        )
        if index == 0:
            (root / "bundles" / "old").mkdir(parents=True)
            (root / "current").write_text("old")
    user = RuntimeSkill(
        name="inactive",
        description="user",
        instructions="user",
        raw_skill_md="user",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "system": {active.name: active.content_digest()},
                "user": {
                    "inactive": {
                        "digest": user.content_digest(),
                        "files": {"SKILL.md": base64.urlsafe_b64encode(b"user").decode()},
                    }
                },
            }
        )
    )

    loaded = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            SKILL_LOAD_PROG,
            str(payload),
            str(root),
            hashlib.sha256(payload.read_bytes()).hexdigest(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(loaded.stdout) == {
        "roots": {
            "active/child": str(root / "active" / "child"),
            "inactive": str(root / "inactive"),
        }
    }
    assert (root / "active" / "child" / "SKILL.md").read_text() == "active"
    assert (root / "inactive" / "SKILL.md").read_text() == "user"
    assert not (root / "bundles").exists()
    assert not (root / "current").exists()
    assert not payload.exists()


def test_privileged_loader_refuses_a_changed_staged_payload(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    manifest = root / ".system-manifest.json"
    manifest.write_text('{"skills":{}}')
    payload = tmp_path / "payload.json"
    original = b'{"system":{},"user":{}}'
    payload.write_bytes(b'{"system":{},"user":{".system-manifest.json":{}}}')

    loaded = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            SKILL_LOAD_PROG,
            str(payload),
            str(root),
            hashlib.sha256(original).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )

    assert loaded.returncode != 0
    assert "skill load payload changed after staging" in loaded.stderr
    assert manifest.read_text() == '{"skills":{}}'


def test_privileged_loader_refuses_an_internal_skill_name(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    manifest = root / ".system-manifest.json"
    manifest.write_text('{"skills":{}}')
    user = RuntimeSkill(
        name=".system-manifest.json",
        description="user",
        instructions="user",
        raw_skill_md="user",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "system": {},
                "user": {
                    user.name: {
                        "digest": user.content_digest(),
                        "files": {"SKILL.md": base64.urlsafe_b64encode(b"user").decode()},
                    }
                },
            }
        )
    )

    loaded = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            SKILL_LOAD_PROG,
            str(payload),
            str(root),
            hashlib.sha256(payload.read_bytes()).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )

    assert loaded.returncode != 0
    assert "invalid user skill" in loaded.stderr
    assert manifest.read_text() == '{"skills":{}}'


def test_privileged_loader_refuses_a_nested_skill_name(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    manifest = root / ".system-manifest.json"
    manifest.write_text('{"skills":{}}')
    user = RuntimeSkill(
        name="pack/subskill",
        description="user",
        instructions="user",
        raw_skill_md="user",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "system": {},
                "user": {
                    user.name: {
                        "digest": user.content_digest(),
                        "files": {"SKILL.md": base64.urlsafe_b64encode(b"user").decode()},
                    }
                },
            }
        )
    )

    loaded = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            SKILL_LOAD_PROG,
            str(payload),
            str(root),
            hashlib.sha256(payload.read_bytes()).hexdigest(),
        ],
        capture_output=True,
        text=True,
    )

    assert loaded.returncode != 0
    assert "invalid user skill" in loaded.stderr
    assert not (root / "pack").exists()
    assert manifest.read_text() == '{"skills":{}}'


async def _check_read_glob_and_grep_accept_skill_paths_outside_the_workspace() -> None:
    class _FileCarrier(_RecordingCarrier):
        def __init__(self) -> None:
            super().__init__()
            self.file_ops: list[tuple[str, dict[str, object]]] = []

        async def file_op(
            self, handle: SandboxHandle, op: str, params: dict[str, object]
        ) -> dict[str, object]:
            self.file_ops.append((op, params))
            return {}

    carrier = _FileCarrier()
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )

    await session.run_ufo_fs("read", {"path": "/home/user/.ufo/skills/probe/SKILL.md"})
    await session.run_ufo_fs("glob", {"path": "$UFO_HOME/skills", "pattern": "**/*.md"})
    await session.run_ufo_fs(
        "grep", {"path": "/home/user/.ufo/skills/reference", "pattern": "probe"}
    )

    assert carrier.file_ops == [
        (
            "read",
            {
                "path": "/home/user/.ufo/skills/probe/SKILL.md",
                "workspace": "/home/user/.ufo/skills",
            },
        ),
        (
            "glob",
            {
                "path": "$UFO_HOME/skills",
                "pattern": "**/*.md",
                "workspace": "$UFO_HOME/skills",
            },
        ),
        (
            "grep",
            {
                "path": "/home/user/.ufo/skills/reference",
                "pattern": "probe",
                "workspace": "/home/user/.ufo/skills",
            },
        ),
    ]


async def _check_file_reads_accept_only_the_current_runtime_namespace() -> None:
    class _FileCarrier(_RecordingCarrier):
        def __init__(self) -> None:
            super().__init__()
            self.file_ops: list[tuple[str, dict[str, object]]] = []

        async def file_op(
            self, handle: SandboxHandle, op: str, params: dict[str, object]
        ) -> dict[str, object]:
            self.file_ops.append((op, params))
            return {}

    carrier = _FileCarrier()
    session = SandboxSession(
        carrier=carrier,
        handle=SandboxHandle(
            conversation_id=uuid4(),
            container_id="c",
            runtime_root="/home/user/.ufo/runs/current",
        ),
    )

    await session.run_ufo_fs("read", {"path": "$UFO_HOME/runs/current/tool-output/call.txt"})

    assert carrier.file_ops == [
        (
            "read",
            {
                "path": "/home/user/.ufo/runs/current/tool-output/call.txt",
                "workspace": "/home/user/.ufo/runs/current",
            },
        )
    ]
    with pytest.raises(ValueError, match="escapes"):
        await session.run_ufo_fs("read", {"path": "$UFO_HOME/runs/another/tool-output/call.txt"})
    with pytest.raises(ValueError, match="escapes"):
        await session.run_ufo_fs("write", {"path": "$UFO_HOME/runs/current/tool-output/call.txt"})


def _check_workspace_path_refusal_names_the_tree_a_tool_may_name() -> None:
    """The path came from a model, which can only correct it by knowing which tree it may name,
    so the refusal carries the scope beside the rejected path."""
    for path in ("/etc/passwd", "../escape", f"{WORKSPACE_DIR}/../outside"):
        with pytest.raises(ValueError) as refused:
            workspace_path(path)
        assert str(refused.value) == f"path {path!r} escapes {WORKSPACE_DIR}{WORKSPACE_SCOPE_HINT}"
        assert WORKSPACE_DIR in WORKSPACE_SCOPE_HINT
    assert workspace_path("notes/summary.md") == f"{WORKSPACE_DIR}/notes/summary.md"


def _check_runtime_relative_refuses_an_escape() -> None:
    for path in ("", "../escape", "tasks/../escape", "/absolute"):
        with pytest.raises(ValueError, match="invalid runtime path"):
            runtime_relative(path)


async def _check_file_reads_outside_the_workspace_and_skill_tree_are_refused() -> None:
    for op, path in (
        ("read", "/etc/passwd"),
        ("glob", "/opt/reference"),
        ("grep", "$UFO_HOME/config.toml"),
        ("read", "$UFO_HOME/skills/../credentials"),
        ("glob", "/home/user/.ufo/skills/../../config.toml"),
    ):
        session = SandboxSession(
            carrier=_RecordingCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
        )

        with pytest.raises(ValueError, match="escapes"):
            await session.run_ufo_fs(op, {"path": path, "pattern": "probe"})


async def _check_write_and_edit_remain_workspace_confined() -> None:
    session = SandboxSession(
        carrier=_RecordingCarrier(),
        handle=SandboxHandle(conversation_id=uuid4(), container_id="c"),
    )

    with pytest.raises(ValueError, match="escapes /workspace"):
        await session.run_ufo_fs("write", {"path": "/home/user/outside.txt"})
    with pytest.raises(ValueError, match="escapes /workspace"):
        await session.run_ufo_fs("edit", {"path": "/home/user/outside.txt"})
    with pytest.raises(ValueError, match="escapes /workspace"):
        await session.run_ufo_fs("write", {"path": "$UFO_HOME/skills/probe/SKILL.md"})
    with pytest.raises(ValueError, match="escapes /workspace"):
        await session.run_ufo_fs("edit", {"path": "$UFO_HOME/skills/probe/SKILL.md"})


async def _check_document_file_ops_outlive_the_preview_request() -> None:
    carrier = _RecordingCarrier(result=ExecResult(stdout='{"type":"text"}', stderr="", exit_code=0))
    handle = SandboxHandle(conversation_id=uuid4(), container_id="c")

    await ufo_fs_file_op(
        carrier,
        handle,
        "read",
        {"path": "/workspace/report.xlsx", "offset": 1, "limit": 20},
    )
    await ufo_fs_file_op(
        carrier,
        handle,
        "read",
        {"path": "/workspace/report.txt", "offset": 1, "limit": 20},
    )

    assert carrier.timeouts == [DOCUMENT_READ_EXEC_TIMEOUT_SECONDS, DEFAULT_EXEC_TIMEOUT_SECONDS]


async def test_bash_timeout_is_milliseconds_capped_and_converted(tmp_path: Path) -> None:
    """Source-faithful: bash `timeout` is milliseconds (max 600000). The handler caps then converts
    to the carrier's seconds; an omitted timeout falls back to the default budget."""
    carrier = _RecordingCarrier()
    ctx = _bash_ctx(carrier, tmp_path)
    await bash_handler(ctx, BashInput(command="echo hi", timeout=5000))
    await bash_handler(ctx, BashInput(command="echo hi", timeout=9_000_000))
    await bash_handler(ctx, BashInput(command="echo hi"))
    assert carrier.timeouts == [5, 600, DEFAULT_EXEC_TIMEOUT_SECONDS]


def _task_payload(text: str) -> dict[str, str]:
    payload = json.loads(text.splitlines()[-1])
    assert isinstance(payload, dict)
    return payload


async def test_background_bash_detaches_and_signals_completion(tmp_path: Path, db: None) -> None:
    """The handler returns before the command ends, stamps the turn as holding detached work, and
    the exit file stays absent until it appears exactly once with the code."""
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    gate = "/workspace/release-the-background-task"
    result = await bash_handler(
        ctx,
        BashInput(
            command=f'while [ ! -f "{gate}" ]; do sleep 0.05; done; echo finished-marker',
            background=True,
        ),
    )
    assert not result.is_error
    task = _task_payload(result.content[0].text)
    probe = await ctx.sandbox.bash(f'cat "{task["exit_file"]}" || true')
    assert probe.exit_code == 0
    assert probe.stdout == ""
    stamped = await _detached_until(turn)
    assert stamped is not None
    assert stamped.replace(tzinfo=UTC) > datetime.now(UTC)
    await ctx.sandbox.bash(f'touch "{gate}"')
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert "finished-marker" in log.stdout


async def test_background_bash_records_a_failing_exit_code(tmp_path: Path, db: None) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    result = await bash_handler(
        ctx,
        BashInput(command="echo boom >&2; exit 7", background=True),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "7"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert "boom" in log.stdout


async def test_background_bash_carries_the_commands_own_quoting(tmp_path: Path, db: None) -> None:
    """The command travels as its own argv element: one containing the launcher's delimiter must run
    verbatim rather than break out of the wrapper."""
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    result = await bash_handler(
        ctx,
        BashInput(command='echo "x\'y"', background=True),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    log = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert log.stdout.strip() == "x'y"


async def test_background_bash_rewrites_workspace_paths_inside_the_command(
    tmp_path: Path, db: None
) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    result = await bash_handler(
        ctx,
        BashInput(
            command=f'echo rewritten > "{WORKSPACE_DIR}/out.txt"',
            background=True,
        ),
    )
    task = _task_payload(result.content[0].text)
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    written = await ctx.sandbox.bash(f'cat "{WORKSPACE_DIR}/out.txt"')
    assert written.stdout.strip() == "rewritten"


async def test_the_advertised_stop_line_ends_the_command_and_signals(
    tmp_path: Path, db: None
) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    result = await bash_handler(
        ctx,
        BashInput(command="sleep 30", background=True),
    )
    task = _task_payload(result.content[0].text)
    killed = await ctx.sandbox.bash(task["stop"])
    assert killed.exit_code == 0
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) != "0"


async def test_the_stop_line_reaches_the_commands_descendants(tmp_path: Path, db: None) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    result = await bash_handler(
        ctx,
        BashInput(
            command=f'sleep 30 & echo $! > "{WORKSPACE_DIR}/desc.pid"; wait',
            background=True,
        ),
    )
    task = _task_payload(result.content[0].text)
    descendant = await _wait_for_file(ctx.sandbox, f"{WORKSPACE_DIR}/desc.pid")
    killed = await ctx.sandbox.bash(task["stop"])
    assert killed.exit_code == 0
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) != "0"
    for _ in range(200):
        alive = await ctx.sandbox.bash(f'kill -0 "{descendant}" 2>/dev/null && echo alive || true')
        if not alive.stdout.strip():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"descendant {descendant} survived the stop")


async def test_a_command_inside_its_budget_answers_as_itself(tmp_path: Path) -> None:
    ctx = await _live_ctx(tmp_path)

    passed = await bash_handler(
        ctx,
        BashInput(command="echo out-line; echo err-line >&2"),
    )
    failed = await bash_handler(
        ctx,
        BashInput(command="echo out-line; exit 7"),
    )

    assert not passed.is_error
    assert passed.content[0].text == "out-line\nerr-line\n"
    assert failed.is_error
    assert failed.content[0].text == "out-line\n\nexit code: 7"


async def test_a_finished_task_is_swept_a_window_after_it_ended(tmp_path: Path, db: None) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)
    tasks_dir = await ctx.sandbox.runtime_path(BACKGROUND_TASKS_DIR)
    await bash_handler(ctx, BashInput(command="echo done"))
    finished = (await ctx.sandbox.bash(f'ls "{tasks_dir}"')).stdout.split()
    assert len(finished) == 3
    running = await bash_handler(ctx, BashInput(command="sleep 30", background=True))
    await ctx.sandbox.bash(f'touch -t 202001010000 "{tasks_dir}"/*')
    await bash_handler(ctx, BashInput(command="echo next"))
    remaining = (await ctx.sandbox.bash(f'ls "{tasks_dir}"')).stdout.split()
    assert not set(finished) & set(remaining)
    live = _task_payload(running.content[0].text)["task"]
    assert {name for name in remaining if name.startswith(live)} == {
        f"{live}.log",
        f"{live}.pid",
    }
    await ctx.sandbox.bash(_task_payload(running.content[0].text)["stop"])


async def test_a_replayed_call_reattaches_and_reads_the_first_run(tmp_path: Path) -> None:
    """A dispatch step that crashes after its command launched re-runs its whole body on
    recovery."""
    key = f"{uuid4()}/bash/call_1"
    conversation_id = uuid4()
    runs = f"{WORKSPACE_DIR}/runs.txt"
    command = f'echo ran >> "{runs}"; cat "{runs}"'
    first = await bash_handler(
        await _live_ctx(tmp_path, idempotency_key=key, conversation_id=conversation_id),
        BashInput(command=command),
    )
    replayed = await bash_handler(
        await _live_ctx(tmp_path, idempotency_key=key, conversation_id=conversation_id),
        BashInput(command=command),
    )
    assert first.content[0].text == "ran\n"
    assert replayed.content[0].text == "ran\n"


async def test_a_replayed_call_picks_up_a_command_still_running(tmp_path: Path, db: None) -> None:
    key = f"{uuid4()}/bash/call_2"
    turn = await _seeded_turn()
    command = "sleep 1; echo finished"
    await bash_handler(
        await _live_ctx(tmp_path, idempotency_key=key, turn=turn),
        BashInput(command=command, background=True),
    )
    picked = await bash_handler(
        await _live_ctx(tmp_path, idempotency_key=key, turn=turn),
        BashInput(command=command),
    )
    assert not picked.is_error
    assert picked.content[0].text == "finished\n"


async def test_without_a_resume_identity_every_call_is_a_fresh_task(tmp_path: Path) -> None:
    """No idempotency key means no earlier attempt to reattach to: two identical calls are two
    runs, never one run answering twice."""
    ctx = await _live_ctx(tmp_path)
    runs = f"{WORKSPACE_DIR}/runs.txt"
    command = f'echo ran >> "{runs}"; cat "{runs}"'
    await bash_handler(ctx, BashInput(command=command))
    second = await bash_handler(ctx, BashInput(command=command))
    assert second.content[0].text == "ran\nran\n"


async def test_a_foreground_command_carries_its_quoting_and_workspace_paths(
    tmp_path: Path,
) -> None:
    """The command rides argv through every hop of the launch, so one holding the delimiter runs
    verbatim and an absolute workspace path inside it lands under the real root."""
    ctx = await _live_ctx(tmp_path)

    quoted = f"{WORKSPACE_DIR}/quoted.txt"
    result = await bash_handler(
        ctx,
        BashInput(
            command=f"""echo "x'y" > "{quoted}"; cat "{quoted}" """,
        ),
    )

    assert not result.is_error
    assert result.content[0].text.strip() == "x'y"


async def test_a_command_that_outgrows_its_budget_keeps_running(tmp_path: Path, db: None) -> None:
    """The budget is how long the caller waits, not how long the work may take."""
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)

    result = await bash_handler(
        ctx,
        BashInput(
            command='for i in 1 2 3 4 5 6 7 8; do echo "tick $i"; sleep 0.4; done',
            timeout=1000,
        ),
    )

    assert not result.is_error
    assert "did not complete within its 1s timeout" in result.content[0].text
    task = _task_payload(result.content[0].text)
    at_return = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) == "0"
    after = await ctx.sandbox.bash(f'cat "{task["log"]}"')
    assert after.stdout != at_return.stdout
    assert "tick 8" in after.stdout


async def test_a_moved_command_is_reported_as_any_detached_one(tmp_path: Path, db: None) -> None:
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)

    asked = await bash_handler(ctx, BashInput(command="sleep 30", background=True))
    moved = await bash_handler(
        ctx, BashInput(command='python3 -c "import time; time.sleep(30)"', timeout=1000)
    )

    assert not asked.is_error and not moved.is_error
    assert (
        _task_payload(asked.content[0].text).keys() == _task_payload(moved.content[0].text).keys()
    )
    assert BACKGROUND_DIRECTIVE in asked.content[0].text
    assert BACKGROUND_DIRECTIVE in moved.content[0].text
    assert DETACHED_LEAD in asked.content[0].text
    assert "1s" not in asked.content[0].text.split("\n")[0]
    await ctx.sandbox.bash(_task_payload(asked.content[0].text)["stop"])
    await ctx.sandbox.bash(_task_payload(moved.content[0].text)["stop"])


async def test_a_moved_commands_stop_line_reaches_its_descendants(tmp_path: Path, db: None) -> None:
    """A moved command is a task like any other: the stop it is handed back with ends the work
    itself, not just the shell in front of it."""
    turn = await _seeded_turn()
    ctx = await _live_ctx(tmp_path, turn=turn)

    result = await bash_handler(
        ctx,
        BashInput(
            command=f'python3 -c "import time; time.sleep(30)" & '
            f'echo $! > "{WORKSPACE_DIR}/moved.pid"; wait',
            timeout=1000,
        ),
    )

    task = _task_payload(result.content[0].text)
    descendant = await _wait_for_file(ctx.sandbox, f"{WORKSPACE_DIR}/moved.pid")
    killed = await ctx.sandbox.bash(task["stop"])
    assert killed.exit_code == 0
    assert await _wait_for_file(ctx.sandbox, task["exit_file"]) != "0"
    for _ in range(200):
        alive = await ctx.sandbox.bash(f'kill -0 "{descendant}" 2>/dev/null && echo alive || true')
        if not alive.stdout.strip():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"descendant {descendant} survived the stop")


async def test_a_stopped_command_names_the_deadline_that_stopped_it(tmp_path: Path) -> None:
    """A budget that expires over a sandbox holding nothing — no wrapper alive, no exit code
    coming — has no task to hand back, and reports the deadline that fired."""
    default_stop = _RecordingCarrier(
        ExecResult(
            stdout="",
            stderr="",
            exit_code=124,
            timed_out_after_s=DEFAULT_EXEC_TIMEOUT_SECONDS,
        )
    )
    capped = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=600)
    )
    exact = _RecordingCarrier(ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=5))

    unset = await bash_handler(
        _bash_ctx(default_stop, tmp_path),
        BashInput(command="pytest -q"),
    )
    reduced = await bash_handler(
        _bash_ctx(capped, tmp_path),
        BashInput(command="pytest -q", timeout=9_000_000),
    )
    honoured = await bash_handler(
        _bash_ctx(exact, tmp_path),
        BashInput(command="pytest -q", timeout=5000),
    )

    assert unset.is_error and reduced.is_error and honoured.is_error
    assert f"{DEFAULT_EXEC_TIMEOUT_SECONDS}s" in unset.content[0].text
    assert "no timeout" in unset.content[0].text
    assert (
        "600s" in reduced.content[0].text
        and "9000s requested was capped" in reduced.content[0].text
    )
    assert "5s" in honoured.content[0].text and "capped" not in honoured.content[0].text


async def test_a_commands_own_timeout_is_not_reported_as_the_sandboxs(tmp_path: Path) -> None:
    """`timeout` inside the command exits 124 exactly as a carrier-stopped command does."""
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=None)
    )

    result = await bash_handler(
        _bash_ctx(carrier, tmp_path),
        BashInput(command="timeout 3000 pytest -q", timeout=600_000),
    )

    assert result.is_error
    assert result.content[0].text == "exit code: 124"


async def test_a_stopped_command_records_the_container_it_was_stopped_in(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=600),
        probe=ExecResult(stdout="1.90 1.20 0.80 3/210 900\n", stderr="", exit_code=0),
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        result = await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(
                command="uv run pytest -n auto " + "x" * 500,
                timeout=9_000_000,
            ),
        )

    logged = next(r for r in caplog.records if r.message == "sandbox.exec_timeout")
    assert logged.ufo["applied_seconds"] == 600
    assert logged.ufo["requested_seconds"] == 9000
    assert logged.ufo["vitals_reached"] is True
    assert "1.90 1.20 0.80" in logged.ufo["vitals"]
    assert logged.ufo["command"].startswith("uv run pytest -n auto")
    assert len(logged.ufo["command"]) == EXEC_TIMEOUT_COMMAND_MAX_CHARS
    assert result.is_error and "600s" in result.content[0].text


async def test_a_container_that_cannot_answer_is_the_reading_that_matters(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy container answers `cat /proc/loadavg` in milliseconds, so a probe that never
    returns says the command channel stopped answering rather than the work being slow."""
    monkeypatch.setattr(tasks_module, "EXEC_TIMEOUT_VITALS_SECONDS", 0.05)

    class _Unanswering(_RecordingCarrier):
        async def exec(
            self,
            handle: SandboxHandle,
            argv: tuple[str, ...],
            timeout_s: int,
            model_command: str | None = None,
        ) -> ExecResult:
            if argv and argv[-1] == EXEC_TIMEOUT_VITALS_CMD:
                await asyncio.sleep(5)
            return await super().exec(handle, argv, timeout_s)

    carrier = _Unanswering(
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=120),
        probe=ExecResult(stdout="0.10 0.05 0.01 1/80 900\n", stderr="", exit_code=0),
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        result = await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(command="echo alive"),
        )

    logged = next(r for r in caplog.records if r.message == "sandbox.exec_timeout")
    assert logged.ufo["vitals_reached"] is False
    assert logged.ufo["vitals"] == ""
    assert result.is_error and "120s" in result.content[0].text


async def test_a_command_that_failed_on_its_own_records_no_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the carrier's own deadline is a sandbox timeout."""
    carrier = _RecordingCarrier(
        ExecResult(stdout="", stderr="boom", exit_code=124, timed_out_after_s=None)
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        await bash_handler(
            _bash_ctx(carrier, tmp_path),
            BashInput(command="timeout 3000 pytest"),
        )

    assert not [r for r in caplog.records if r.message == "sandbox.exec_timeout"]
    assert EXEC_TIMEOUT_VITALS_CMD not in carrier.commands


def test_sandbox_session_sync_contract() -> None:
    for check in (
        _check_shell_path_expands_only_the_runtime_home_prefix,
        _check_run_token_round_trips_encode_then_proxy_auth,
        _check_run_token_wire_carries_a_member_the_turn_or_nobody,
        _check_run_token_member_is_keyword_only,
        _check_encoded_token_is_url_safe_userinfo,
        _check_from_proxy_auth_rejects_non_basic_scheme,
        _check_from_proxy_auth_rejects_missing_header,
        _check_from_proxy_auth_rejects_a_malformed_run_token,
        _check_run_token_rejects_a_valid_shape_signed_by_another_deployment,
        _check_authorized_session_scopes_proxy_and_cli_environment_without_mutating_base,
        _check_an_authorized_session_keeps_the_turn_a_stop_is_scoped_to,
        _check_host_argv_names_a_logical_path_under_the_host_root,
        _check_host_argv_leaves_the_substring_that_is_not_this_workspace,
        _check_runtime_relative_refuses_an_escape,
        _check_workspace_path_refusal_names_the_tree_a_tool_may_name,
    ):
        check()


async def test_sandbox_session_async_contract() -> None:
    for check in (
        _check_a_carrier_that_declares_no_stop_is_never_asked_for_one,
        _check_skills_load_from_one_staged_container_payload,
        _check_large_skill_payload_never_enters_a_container_command_argument,
        _check_skill_load_fails_when_the_container_loader_fails,
        _check_a_stale_sandbox_refreshes_before_loading_the_requested_system_skill,
        _check_read_glob_and_grep_accept_skill_paths_outside_the_workspace,
        _check_file_reads_accept_only_the_current_runtime_namespace,
        _check_file_reads_outside_the_workspace_and_skill_tree_are_refused,
        _check_write_and_edit_remain_workspace_confined,
        _check_document_file_ops_outlive_the_preview_request,
    ):
        await check()
