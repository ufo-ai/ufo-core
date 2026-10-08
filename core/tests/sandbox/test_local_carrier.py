"""The core default carrier against real subprocesses in a temp-dir workspace.

The local carrier has no fake to stand in for — it runs the host's own shell, so these drive it
end to end: create the workspace, exec a command that writes into it, rewrite the logical
`/workspace` path, carry the spec's environment, reach a service on the sandbox's own loopback,
and stream a file out in bounded chunks. The last test proves the payoff — the file tools run
through the default carrier against the `ufo` client on its command PATH, no container."""

import asyncio
import json
import os
import shlex
import tempfile
from base64 import urlsafe_b64encode
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from ufo.harness.sandbox.local import (
    EXEC_TIMEOUT_CODE,
    LOCAL_CONTAINER_ID,
    READ_CHUNK_BYTES,
    LocalCarrier,
    provision_scratch,
)
from ufo.harness.sandbox.session import (
    PROXY_SESSION_ENV_NAMES,
    WORKSPACE_DIR,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.runtime.skills.runtime import RuntimeSkill, SystemSkillBundle

ROOT = Path(__file__).parents[3]
PROBE_SYSTEM_HELPER = "probe-system-helper-that-never-runs"
PROBE_GLOBAL_HELPER = "probe-global-helper-that-never-runs"
PROBE_ENV_HELPER = "probe-env-helper-that-never-runs"
PROBE_PARAMS_HELPER = "probe-params-helper-that-never-runs"
PROBE_ASKPASS_USER = "probe-askpass-user"
PROMPTS_DISABLED = "terminal prompts disabled"
PRESIGNED_PUT = 'curl -sS --fail-with-body -T "$1" --url "$2"'
PAGE = "<!doctype html><title>hello</title>"
LOOPBACK_PROBE = (
    "import urllib.request;"
    "print(urllib.request.urlopen('http://127.0.0.1:{port}/json/version', timeout=2).read())"
)


DESCENDANT_MARKER = "descendant.pid"
DESCENDANT_LIFETIME_SECONDS = 60
DESCENDANT_COMMAND = (
    f"sh -c 'echo $$ > {DESCENDANT_MARKER}; sleep {DESCENDANT_LIFETIME_SECONDS}' & "
    f"sleep {DESCENDANT_LIFETIME_SECONDS}"
)
EXEC_TIMEOUT_SECONDS = 1
EXEC_RETURN_BUDGET_SECONDS = 15
DESCENDANT_POLL_SECONDS = 0.05
DESCENDANT_POLL_ATTEMPTS = 100


def test_supported_local_builds_produce_the_client() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "cargo build --manifest-path client/Cargo.toml --locked" in makefile


def test_system_skills_seed_directly_and_preserve_user_skills(tmp_path: Path) -> None:
    carrier = LocalCarrier(_scratch=provision_scratch(tmp_path / "scratch"))
    first = RuntimeSkill(
        name="first",
        description="first",
        instructions="first",
        raw_skill_md="first",
    )
    carrier.seed_system_skills(SystemSkillBundle.from_skills((first,)).archive)
    user = carrier.ufo_home / "skills" / "user" / "SKILL.md"
    user.parent.mkdir()
    user.write_text("user")
    second = RuntimeSkill(
        name="second",
        description="second",
        instructions="second",
        raw_skill_md="second",
    )

    carrier.seed_system_skills(SystemSkillBundle.from_skills((second,)).archive)

    root = carrier.ufo_home / "skills"
    assert not (root / "first").exists()
    assert (root / "second" / "SKILL.md").read_text() == "second"
    assert user.read_text() == "user"
    assert (root / ".system-manifest.json").is_file()


async def test_a_user_skill_may_replace_an_inactive_seeded_skill(tmp_path: Path) -> None:
    carrier = LocalCarrier(_scratch=provision_scratch(tmp_path / "scratch"))
    system = RuntimeSkill(
        name="probe",
        description="system",
        instructions="system",
        raw_skill_md="system",
    )
    carrier.seed_system_skills(SystemSkillBundle.from_skills((system,)).archive)
    user = RuntimeSkill(
        name="probe",
        description="user",
        instructions="user",
        raw_skill_md="user",
    )

    result = await carrier.load_skills(
        SandboxHandle(conversation_id=uuid4(), container_id=LOCAL_CONTAINER_ID),
        {
            "system": {},
            "user": {
                "probe": {
                    "digest": user.content_digest(),
                    "files": {"SKILL.md": urlsafe_b64encode(b"user").decode()},
                }
            },
        },
    )

    assert result.exit_code == 0
    assert (carrier.ufo_home / "skills" / "probe" / "SKILL.md").read_text() == "user"


async def test_a_nested_system_skill_seeds_and_loads(tmp_path: Path) -> None:
    carrier = LocalCarrier(_scratch=provision_scratch(tmp_path / "scratch"))
    system = RuntimeSkill(
        name="website-building/webapp",
        description="system",
        instructions="system",
        raw_skill_md="system",
    )
    carrier.seed_system_skills(SystemSkillBundle.from_skills((system,)).archive)

    result = await carrier.load_skills(
        SandboxHandle(conversation_id=uuid4(), container_id=LOCAL_CONTAINER_ID),
        {"system": {system.name: system.content_digest()}, "user": {}},
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "roots": {system.name: str(carrier.ufo_home / "skills" / "website-building" / "webapp")}
    }


async def test_a_user_skill_cannot_replace_the_system_manifest(tmp_path: Path) -> None:
    carrier = LocalCarrier(_scratch=provision_scratch(tmp_path / "scratch"))
    system = RuntimeSkill(
        name="probe",
        description="system",
        instructions="system",
        raw_skill_md="system",
    )
    carrier.seed_system_skills(SystemSkillBundle.from_skills((system,)).archive)
    manifest = carrier.ufo_home / "skills" / ".system-manifest.json"
    before = manifest.read_bytes()
    user = RuntimeSkill(
        name=".system-manifest.json",
        description="user",
        instructions="user",
        raw_skill_md="user",
    )

    result = await carrier.load_skills(
        SandboxHandle(conversation_id=uuid4(), container_id=LOCAL_CONTAINER_ID),
        {
            "system": {},
            "user": {
                user.name: {
                    "digest": user.content_digest(),
                    "files": {"SKILL.md": urlsafe_b64encode(b"user").decode()},
                }
            },
        },
    )

    assert result.exit_code == 1
    assert manifest.read_bytes() == before


async def _descendant_pid(workspace: Path) -> int:
    marker = workspace / DESCENDANT_MARKER
    for _ in range(DESCENDANT_POLL_ATTEMPTS):
        recorded = marker.read_text().strip() if marker.exists() else ""
        if recorded:
            return int(recorded)
        await asyncio.sleep(DESCENDANT_POLL_SECONDS)
    raise AssertionError("the command never recorded its descendant")


async def _still_running(pid: int) -> bool:
    for _ in range(DESCENDANT_POLL_ATTEMPTS):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        await asyncio.sleep(DESCENDANT_POLL_SECONDS)
    return True


def _spec(workspace: Path) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path=str(workspace),
    )


async def test_create_exec_and_read_in_a_temp_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    assert handle.container_id == LOCAL_CONTAINER_ID
    assert workspace.is_dir()

    result = await carrier.exec(handle, ("bash", "-lc", "echo hi > out.txt && printf done"), 30)
    assert result.exit_code == 0
    assert result.stdout == "done"
    assert (workspace / "out.txt").read_text() == "hi\n"

    out = [chunk async for chunk in carrier.read(handle, "/workspace/out.txt")]
    assert b"".join(out) == b"hi\n"


async def test_the_host_git_config_does_not_reach_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    system_config = tmp_path / "system-gitconfig"
    global_config = tmp_path / "global-gitconfig"
    system_config.write_text(f"[credential]\n\thelper = {PROBE_SYSTEM_HELPER}\n")
    global_config.write_text(f"[credential]\n\thelper = {PROBE_GLOBAL_HELPER}\n")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system_config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", PROBE_ENV_HELPER)
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'credential.helper={PROBE_PARAMS_HELPER}'")
    carrier = LocalCarrier()
    argv = ("git", "config", "--get-all", "credential.helper")

    created = await carrier.create(_spec(workspace))
    attached = await carrier.attach(_spec(workspace))
    assert attached is not None
    guarded = await carrier.exec(created, argv, 30)
    guarded_attach = await carrier.exec(attached, argv, 30)
    wrote = await carrier.exec(
        created, ("git", "config", "--global", "user.email", "probe@example.com"), 30
    )
    lifted = {
        name: await carrier.exec(await carrier.create(replace(_spec(workspace), env=env)), argv, 30)
        for name, env in {
            PROBE_SYSTEM_HELPER: {
                "GIT_CONFIG_NOSYSTEM": "0",
                "GIT_CONFIG_SYSTEM": str(system_config),
            },
            PROBE_GLOBAL_HELPER: {"GIT_CONFIG_GLOBAL": str(global_config)},
            PROBE_ENV_HELPER: {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "credential.helper",
                "GIT_CONFIG_VALUE_0": PROBE_ENV_HELPER,
            },
            PROBE_PARAMS_HELPER: {
                "GIT_CONFIG_PARAMETERS": f"'credential.helper={PROBE_PARAMS_HELPER}'"
            },
        }.items()
    }

    for helper, result in lifted.items():
        assert helper in result.stdout.split()
    for result in (guarded, guarded_attach):
        assert result.stdout == ""
        assert result.exit_code == 1
    assert wrote.exit_code == 0, wrote.stderr


async def test_a_command_is_never_asked_for_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same wedge: a command that is asked for a credential waits for an
    answer nobody sends."""
    askpass = tmp_path / "askpass"
    askpass.write_text(f"#!/bin/sh\necho {PROBE_ASKPASS_USER}\n")
    askpass.chmod(0o755)
    monkeypatch.setenv("GIT_ASKPASS", str(askpass))
    carrier = LocalCarrier()
    argv = (
        "bash",
        "-c",
        "printf 'protocol=https\\nhost=probe.invalid\\n\\n' | git credential fill 2>&1",
    )

    guarded = await carrier.exec(await carrier.create(_spec(tmp_path / "workspace")), argv, 30)
    askpass_lifted = await carrier.exec(
        await carrier.create(
            replace(_spec(tmp_path / "workspace"), env={"GIT_ASKPASS": str(askpass)})
        ),
        argv,
        30,
    )
    prompt_lifted = await carrier.exec(
        await carrier.create(
            replace(_spec(tmp_path / "workspace"), env={"GIT_TERMINAL_PROMPT": "1"})
        ),
        argv,
        30,
    )

    assert PROBE_ASKPASS_USER in askpass_lifted.stdout
    assert PROMPTS_DISABLED not in prompt_lifted.stdout
    assert PROBE_ASKPASS_USER not in guarded.stdout
    assert PROMPTS_DISABLED in guarded.stdout


async def test_read_streams_a_large_file_in_bounded_chunks(tmp_path: Path) -> None:
    """A workspace read streams through the carrier in bounded chunks, so an arbitrarily large
    produced file crosses without a whole-file buffer forming here."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    payload = bytes(range(256)) * (READ_CHUNK_BYTES // 128)
    (workspace / "big.bin").write_bytes(payload)

    chunks = [chunk async for chunk in carrier.read(handle, "/workspace/big.bin")]

    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= READ_CHUNK_BYTES
    assert b"".join(chunks) == payload


async def test_read_of_an_absent_file_raises(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in carrier.read(handle, "/workspace/absent.bin")]


async def test_exec_rewrites_the_logical_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    await carrier.exec(handle, ("bash", "-lc", "printf x > /workspace/w.txt"), 30)

    assert (workspace / "w.txt").read_text() == "x"


async def test_a_command_writes_only_where_the_carrier_names(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    outside = ROOT / f".ufo-sandbox-probe-{uuid4().hex}"

    try:
        refused = await carrier.exec(
            handle, ("bash", "-lc", f"printf no > {shlex.quote(str(outside))}"), 30
        )
        landed = await carrier.exec(
            handle,
            (
                "bash",
                "-lc",
                'printf ok > /workspace/in.txt && printf t > "${TMPDIR:-/tmp}/ufo-sandbox-probe" '
                '&& printf h > "$HOME/ufo-sandbox-probe" && rm "${TMPDIR:-/tmp}/ufo-sandbox-probe"',
            ),
            30,
        )
        assert refused.exit_code != 0
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)
    assert landed.exit_code == 0, landed.stderr
    assert (workspace / "in.txt").read_text() == "ok"
    assert (carrier._scratch / "home" / "ufo-sandbox-probe").read_text() == "h"


async def test_a_scratch_without_the_client_refuses_every_command(tmp_path: Path) -> None:
    carrier = LocalCarrier(_scratch=tmp_path / "scratch")
    (tmp_path / "scratch" / "home").mkdir(parents=True)
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(RuntimeError, match="cargo build"):
        await carrier.exec(handle, ("bash", "-lc", "true"), 30)


async def test_a_logical_path_inside_a_file_a_command_reads_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """Argv is the whole of the rewrite, so a `/workspace` path the agent wrote into a script or
    a REPL cell resolves against the host's own filesystem."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    (workspace / "probe.py").write_text(
        "from pathlib import Path\n"
        "print(Path('/workspace/probe.py').resolve() == Path('probe.py').resolve())\n"
    )

    result = await carrier.exec(handle, ("python3", "/workspace/probe.py"), 30)

    assert result.exit_code == 0
    assert result.stdout.strip() == "False"


async def test_exec_carries_the_spec_env_and_no_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in PROXY_SESSION_ENV_NAMES:
        monkeypatch.setenv(name, "http://serve-proxy.test")
    carrier = LocalCarrier()
    handle = await carrier.create(
        replace(_spec(tmp_path / "workspace"), env={"ACME_KEY": "spec-carried"})
    )
    names = sorted(PROXY_SESSION_ENV_NAMES)

    result = await carrier.exec(
        handle,
        (
            "bash",
            "-lc",
            'printf "%s|" "$ACME_KEY" "${SSL_CERT_FILE:-none}" '
            + " ".join(f'"${{{name}:-none}}"' for name in names),
        ),
        30,
    )

    assert result.stdout.split("|")[:-1] == ["spec-carried", "none", *["none"] * len(names)]
    assert PROXY_SESSION_ENV_NAMES.isdisjoint(handle.egress_env)
    assert "SSL_CERT_FILE" not in handle.egress_env


async def test_the_serve_environment_does_not_reach_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local-carrier command is a shell on the serve host, and serve's own environment is the
    deploy's secrets — the token-signing secret, DSNs, cloud keys."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", "serve-only")
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    argv = ("bash", "-lc", 'printf "%s" "${UFO_TOKEN_SECRET-absent}"')

    created = await carrier.exec(await carrier.create(_spec(workspace)), argv, 30)
    attached_handle = await carrier.attach(_spec(workspace))
    assert attached_handle is not None
    attached = await carrier.exec(attached_handle, argv, 30)
    lifted = await carrier.exec(
        await carrier.create(replace(_spec(workspace), env={"UFO_TOKEN_SECRET": "spec-carried"})),
        argv,
        30,
    )

    assert created.stdout == "absent"
    assert attached.stdout == "absent"
    assert lifted.stdout == "spec-carried"


async def test_the_local_runtime_environment_reaches_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NODE_PATH", "/runtime/node_modules")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/runtime/playwright")
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    argv = ("bash", "-lc", 'printf "%s|%s" "$NODE_PATH" "$PLAYWRIGHT_BROWSERS_PATH"')

    created = await carrier.exec(await carrier.create(_spec(workspace)), argv, 30)
    attached_handle = await carrier.attach(_spec(workspace))
    assert attached_handle is not None
    attached = await carrier.exec(attached_handle, argv, 30)

    assert created.stdout == "/runtime/node_modules|/runtime/playwright"
    assert attached.stdout == created.stdout


async def test_exec_reaches_a_service_on_the_sandbox_loopback(tmp_path: Path) -> None:
    """A proxy-aware client in the sandbox reaches a service the turn started on the sandbox's
    own loopback."""
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nlive!")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await carrier.exec(handle, ("python3", "-c", LOOPBACK_PROBE.format(port=port)), 30)

    assert result.exit_code == 0, result.stderr
    assert "live!" in result.stdout


async def test_an_upload_reaches_the_key_its_url_was_signed_for(tmp_path: Path) -> None:
    """The upload leg of hosted publishing and file sharing: one `curl -T` over a workspace path
    and a presigned URL, each its own argv element."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    (workspace / "index.html").write_text(PAGE)
    stored: list[tuple[str, bytes]] = []

    async def store(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in head[1:] if ": " in line)
        body = await reader.readexactly(int(headers["Content-Length"]))
        stored.append((head[0].split(" ")[1], body))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(store, "127.0.0.1", 0)
    key = f"workspaces/{uuid4()}/sites/hello/index.html"
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/{key}?X-Amz-Signature=abc"
    async with server:
        result = await carrier.exec(
            handle,
            ("sh", "-c", PRESIGNED_PUT, "sh", f"{WORKSPACE_DIR}/index.html", url),
            30,
        )

    assert result.exit_code == 0, result.stderr
    assert stored == [(f"/{key}?X-Amz-Signature=abc", PAGE.encode())]


async def test_write_creates_parents_for_a_payload_too_large_for_a_command_line(
    tmp_path: Path,
) -> None:
    carrier = LocalCarrier()
    workspace = tmp_path / "workspace"
    handle = await carrier.create(_spec(workspace))
    content = b"x" * (2 * 1024 * 1024)

    await carrier.write(handle, "/workspace/nested/deeper/big.txt", content)

    assert (workspace / "nested" / "deeper" / "big.txt").read_bytes() == content


async def test_exec_maps_a_timeout_to_the_timeout_code(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(handle, ("bash", "-lc", "sleep 5"), 1)

    assert result.exit_code == EXEC_TIMEOUT_CODE


async def test_a_timed_out_command_takes_its_descendants_with_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    started = monotonic()
    result = await carrier.exec(handle, ("bash", "-lc", DESCENDANT_COMMAND), EXEC_TIMEOUT_SECONDS)
    elapsed = monotonic() - started
    descendant = await _descendant_pid(workspace)

    assert result.exit_code == EXEC_TIMEOUT_CODE
    assert elapsed < EXEC_RETURN_BUDGET_SECONDS
    assert not await _still_running(descendant)


async def test_a_cancelled_exec_takes_its_descendants_with_it(tmp_path: Path) -> None:
    """A turn cancelled at its deadline unwinds the exec awaiting it, which is the path that leaks
    a whole tree: the timeout never fires, so nothing signals the command on the way out."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    running = asyncio.create_task(carrier.exec(handle, ("bash", "-lc", DESCENDANT_COMMAND), 60))
    descendant = await _descendant_pid(workspace)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert not await _still_running(descendant)


async def test_read_refuses_a_path_outside_the_sandbox_roots(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(ValueError):
        [chunk async for chunk in carrier.read(handle, "/etc/passwd")]


@pytest.mark.integration
async def test_file_tools_run_through_ufo_fs_locally(tmp_path: Path, sandbox_client: Path) -> None:
    """The default carrier installs the `ufo` client on the command PATH, so the file tools work
    with no container: a write lands in the workspace and the `ufo fs` read reflects it."""
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))
    session = SandboxSession(carrier=carrier, handle=handle)

    await session.write_file("notes.txt", b"alpha\nbeta\n")
    assert await session.file_exists("notes.txt")

    read = await session.run_ufo_fs("read", {"path": "notes.txt"})
    assert "alpha" in json.dumps(read)


def test_every_carrier_in_a_process_shares_one_scratch() -> None:
    first = LocalCarrier()._scratch
    settled = set(Path(tempfile.gettempdir()).glob("ufo-local-*"))

    carriers = [LocalCarrier() for _ in range(3)]

    assert {carrier._scratch for carrier in carriers} == {first}
    assert set(Path(tempfile.gettempdir()).glob("ufo-local-*")) == settled


@pytest.mark.integration
async def test_file_op_is_the_carrier_seam_a_session_runs_a_file_op_through(
    tmp_path: Path, sandbox_client: Path
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    await carrier.write(handle, f"{WORKSPACE_DIR}/notes.txt", b"alpha\nbeta\n")

    read = await carrier.file_op(
        handle, "read", {"path": f"{WORKSPACE_DIR}/notes.txt", "workspace": WORKSPACE_DIR}
    )
    assert read["content"] == "1\talpha\n2\tbeta"

    with pytest.raises(ValueError, match="not found"):
        await carrier.file_op(
            handle, "read", {"path": f"{WORKSPACE_DIR}/absent.txt", "workspace": WORKSPACE_DIR}
        )


async def test_ensure_tool_output_dir_creates_the_directory_when_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (Path(handle.runtime_root) / "tool-output").is_dir()
    assert not (workspace / ".tool-output").exists()


async def test_ensure_tool_output_dir_reclaims_a_file_squatting_the_name(tmp_path: Path) -> None:
    """A regular file occupying the engine's offload directory is reclaimed."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_runtime_file("tool-output", b"squatter")
    output = Path(handle.runtime_root) / "tool-output"
    assert output.is_file()

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is True
    assert output.is_dir()
    assert not (workspace / ".tool-output").exists()


async def test_ensure_tool_output_dir_leaves_an_existing_directory_and_its_contents(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_runtime_file("tool-output/kept.txt", b"keep me")

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (Path(handle.runtime_root) / "tool-output" / "kept.txt").read_text() == "keep me"
    assert not (workspace / ".tool-output").exists()


async def test_exec_env_rides_the_handle_not_the_conversation(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    conversation = uuid4()
    base = replace(_spec(tmp_path / "workspace"), conversation_id=conversation)
    first = await carrier.create(base)
    second = await carrier.create(replace(base, env={"GH_TOKEN": "sent-b"}))

    probe = ("bash", "-lc", 'printf "%s" "${GH_TOKEN:-none}"')
    result_a = await carrier.exec(first, probe, 30)
    result_b = await carrier.exec(second, probe, 30)

    assert result_a.stdout == "none"
    assert result_b.stdout == "sent-b"


async def test_background_descendant_keeps_its_authority_across_later_execs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    base = SandboxSession(carrier=carrier, handle=await carrier.create(_spec(workspace)))
    first = await base.authorize(frozenset(("GH_TOKEN",)), {"GH_TOKEN": "sent-a"})
    second = await base.authorize(frozenset(("GH_TOKEN",)), {"GH_TOKEN": "sent-b"})

    launched = await first.bash(
        "nohup sh -c 'while [ ! -f release ]; do :; done; "
        'printf "%s" "$GH_TOKEN" > first.tmp && mv first.tmp first.txt\' '
        ">/dev/null 2>&1 &"
    )
    assert launched.exit_code == 0
    written = await second.bash(
        'printf "%s" "$GH_TOKEN" > second.txt; touch release; while [ ! -f first.txt ]; do :; done'
    )
    assert written.exit_code == 0

    assert (workspace / "first.txt").read_text() == "sent-a"
    assert (workspace / "second.txt").read_text() == "sent-b"


async def test_a_second_write_never_shows_a_reader_a_half_written_file(tmp_path: Path) -> None:
    """Two writers race one path whenever a surface delivers the same file twice — Slack sends a
    channel mention as two events, and both build their turn before the duplicate is dropped."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    path = "/workspace/inbox/data.csv"
    first = b"a" * (READ_CHUNK_BYTES * 4)
    second = b"b" * (READ_CHUNK_BYTES * 4)
    await carrier.write(handle, path, first)

    async def rewrite() -> None:
        for _ in range(20):
            await carrier.write(handle, path, second)
            await carrier.write(handle, path, first)

    async def reads() -> list[bytes]:
        seen = []
        for _ in range(20):
            seen.append(b"".join([chunk async for chunk in carrier.read(handle, path)]))
            await asyncio.sleep(0)
        return seen

    rewriting = asyncio.ensure_future(rewrite())
    seen = await reads()
    await rewriting
    assert set(seen) <= {first, second}
    assert [entry.name for entry in (workspace / "inbox").iterdir()] == ["data.csv"]


async def test_the_longest_filename_a_directory_takes_still_writes(tmp_path: Path) -> None:
    """The sidecar is named for itself, not the target, so the longest name a directory accepts
    is still writable."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    longest = "n" * os.pathconf(tmp_path, "PC_NAME_MAX")

    await carrier.write(handle, f"/workspace/{longest}", b"fits")

    assert (workspace / longest).read_bytes() == b"fits"


async def test_a_write_that_fails_leaves_nothing_behind(tmp_path: Path) -> None:
    """The workspace listing is the member's own file list, so a half-written sidecar would show up
    in it as a file they never made — in the portal, in the agent's glob, and in the prune count."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    await carrier.write(handle, "/workspace/inbox/keep.txt", b"kept")

    with pytest.raises(IsADirectoryError):
        await carrier.write(handle, "/workspace/inbox", b"onto the directory itself")

    assert [entry.name for entry in workspace.iterdir()] == ["inbox"]
    assert [entry.name for entry in (workspace / "inbox").iterdir()] == ["keep.txt"]


async def test_an_overwrite_keeps_the_mode_the_file_already_had(tmp_path: Path) -> None:
    """A rename installs a new inode under the umask, so a script a turn made executable would come
    back 0o644 and the next turn's `./run.sh` would exit 126. The mode crosses with the bytes."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    script = workspace / "run.sh"
    await carrier.write(handle, "/workspace/run.sh", b"#!/bin/sh\necho first\n")
    script.chmod(0o755)

    await carrier.write(handle, "/workspace/run.sh", b"#!/bin/sh\necho second\n")

    assert script.stat().st_mode & 0o7777 == 0o755
    result = await carrier.exec(handle, ("bash", "-lc", "./run.sh"), 30)
    assert result.exit_code == 0
    assert result.stdout.strip() == "second"


async def test_an_overwrite_carries_permission_bits_and_not_the_others(tmp_path: Path) -> None:
    """Docker's `cat >` and an in-place `edit` both strip setuid, setgid and sticky, so a copy-in
    that carried them would make one `Carrier.write` mean two things by carrier."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    target = workspace / "tool"
    await carrier.write(handle, "/workspace/tool", b"first")
    target.chmod(0o6755)

    await carrier.write(handle, "/workspace/tool", b"second")

    assert target.stat().st_mode & 0o7777 == 0o755


async def test_a_target_deleted_before_the_write_is_still_created(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    target = workspace / "gone.txt"
    await carrier.write(handle, "/workspace/gone.txt", b"first")
    target.unlink()

    await carrier.write(handle, "/workspace/gone.txt", b"second")

    assert target.read_bytes() == b"second"


async def test_dial_targets_the_loopback_port() -> None:
    carrier = LocalCarrier()
    handle = SandboxHandle(conversation_id=uuid4(), container_id=LOCAL_CONTAINER_ID)
    target = await carrier.dial(handle, 8000)
    assert target.host == "127.0.0.1:8000"
    assert target.tls is False
