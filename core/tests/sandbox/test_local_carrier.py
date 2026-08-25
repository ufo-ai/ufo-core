"""The core default carrier against real subprocesses in a temp-dir workspace.

The local carrier has no fake to stand in for — it runs the host's own shell, so these drive it
end to end: create the workspace, exec a command that writes into it, rewrite the logical
`/workspace` path, carry the egress environment, reach a service on the sandbox's own loopback,
stream a file out in bounded chunks, and confine that read to the workspace. The last test proves
the payoff — the file tools run through the default carrier against the `ufo` client on its command
PATH, no container."""

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from ufo.sandbox.containment import ContainmentError, LocationEscape, NotRegularFile
from ufo.sandbox.local import (
    EXEC_TIMEOUT_CODE,
    LOCAL_CONTAINER_ID,
    READ_CHUNK_BYTES,
    LocalCarrier,
)
from ufo.sandbox.session import (
    COPY_IN_PROG,
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)

ROOT = Path(__file__).parents[3]
RUN_TOKEN = "run-token-abc"
PROXY_PORT = 9999
PROBE_SYSTEM_HELPER = "probe-system-helper-that-never-runs"
PROBE_GLOBAL_HELPER = "probe-global-helper-that-never-runs"
PROBE_ENV_HELPER = "probe-env-helper-that-never-runs"
PROBE_PARAMS_HELPER = "probe-params-helper-that-never-runs"
PROBE_ASKPASS_USER = "probe-askpass-user"
PROMPTS_DISABLED = "terminal prompts disabled"
PRESIGNED_PUT = 'curl -sS --fail-with-body -T "$1" --url "$2"'
BARE_PATH = "/usr/bin:/bin"
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
    dockerfile = (ROOT / "dev" / "Dockerfile").read_text()

    assert "cargo build --manifest-path client/Cargo.toml --locked" in makefile
    assert "COPY --from=client /usr/local/bin/ufo /usr/local/bin/ufo" in dockerfile
    assert "cargo build --release --locked --bin ufo" in dockerfile
    assert "FROM golang:1.27.0-bookworm AS gh" in dockerfile
    assert "github.com/cli/cli/v2/cmd/gh@v2.97.0" in dockerfile
    assert "UFO_GH_ARCHIVE=/tmp/ufo-gh.gz" in dockerfile


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
        proxy=ProxyEndpoint(port=PROXY_PORT, ca_cert="CA-PEM-BYTES"),
        run_token=RUN_TOKEN,
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
    """A credential helper a command cannot answer blocks it forever — Apple's git names
    `osxkeychain`, whose store raises a keychain authorization UI and then waits on a reply nothing
    sends. Every level the host's configuration reaches a command by is dropped: the system and
    global config files, and `GIT_CONFIG_COUNT`, which carries config in the environment and
    outranks them both. `attach` reads it back too, because its handles run commands as well.

    Each level gets a control that reads it back with the guard lifted through `spec.env`, so a
    passing assertion is the guard working rather than a read that found nothing. The guarded read
    pins git's exit code for "no such key" too: an empty stdout alone would also be what a `git`
    that refused to start prints, which would leave this green while every sandbox command died.
    Writing the global config is exercised for the same reason — pointing the level at an unwritable
    path reads identically here and takes `git lfs install` and `gh auth setup-git` down.
    """
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
    answer nobody sends. `GIT_ASKPASS` names a host program git runs to get one — a GUI helper there
    blocks exactly as the keychain does — and a terminal prompt blocks on a terminal no reader is
    watching. Both are closed, so git fails fast instead.

    Each has its own control lifted through `spec.env`: with the host askpass restored git returns
    the username it printed, and with prompting restored git reaches for the terminal and reports
    that instead. `git credential fill` is the probe: obtaining a credential is its whole job.
    """
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


async def test_a_logical_path_inside_a_file_a_command_reads_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """Argv is the whole of the rewrite, so a `/workspace` path the agent wrote into a script or a
    REPL cell resolves against the host's own filesystem. That is why a path a command carries in
    its own text has to be workspace-relative under this carrier, and why a mounted skill's script
    is unreachable here by the logical path a load prints for it."""
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


async def test_exec_carries_the_egress_environment(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    result = await carrier.exec(
        handle,
        (
            "bash",
            "-lc",
            'printf "%s|%s|%s|%s" "$HTTPS_PROXY" "$ANTHROPIC_API_KEY" '
            '"$GIT_SSL_CAINFO" "$CARGO_HTTP_CAINFO"',
        ),
        30,
    )

    proxy, sentinel, git_ca, cargo_ca = result.stdout.split("|")
    assert proxy == f"http://{RUN_TOKEN}:@127.0.0.1:{PROXY_PORT}"
    assert sentinel == SENTINEL_MODEL_KEY
    # git and cargo (libcurl) ignore CURL_CA_BUNDLE, so they get their own CAINFO or a MITM'd host
    # fails their TLS. Both point at the same proxy CA the other consumers do.
    assert git_ca and git_ca == handle.egress_env["SSL_CERT_FILE"]
    assert cargo_ca == git_ca


async def test_the_serve_environment_does_not_reach_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local-carrier command is a shell on the serve host, and serve's own environment is the
    deploy's secrets — the token-signing secret, DSNs, cloud keys. The handle carries only what
    commands need, so a variable serve holds never reaches one. The control reads the same name
    back through `spec.env`, so a passing guard is the allowlist working rather than a read that
    found nothing; `attach` is read back too, because its handles run commands as well."""
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


async def test_exec_reaches_a_service_on_the_sandbox_loopback(tmp_path: Path) -> None:
    """A proxy-aware client in the sandbox reaches a service the turn started on the sandbox's own
    loopback. The proxy admits only globally routable destinations, so a loopback request routed to
    it can only be refused — without the exemption an in-sandbox service (Chrome's DevTools port,
    a dev-server preview) is unreachable from inside the box that runs it."""
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
    and a presigned URL, each its own argv element. Every blob key begins `workspaces/`, so the
    logical root's name rides inside the signed URL — the path lands under the host root and the
    URL arrives byte for byte, or the store gets a PUT for a key no signature covers and answers
    403 with nothing wrong at the path end."""
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
    """Signalling the shell alone leaves everything it forked running, reparented to init and
    holding the host's CPU for as long as it lives — one command that forks then outlives the turn
    that could still name it. The descendant inherits the pipes the exec reads, so it also holds
    the call open long past the timeout that was supposed to bound it: the elapsed budget is what
    separates a killed group from a shell that merely died first."""
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


async def test_read_confines_to_the_workspace(tmp_path: Path) -> None:
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(tmp_path / "workspace"))

    with pytest.raises(ValueError):
        [chunk async for chunk in carrier.read(handle, "/etc/passwd")]


async def test_read_of_a_traversal_path_is_refused(tmp_path: Path) -> None:
    """A name that climbs out of the workspace is refused where the path becomes a real one: the
    lexical guard upstream contains at the workspace root, and a copy-out is what reads."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    (workspace / "sub").mkdir()
    (tmp_path / "outside.txt").write_bytes(b"host secret")

    with pytest.raises(LocationEscape):
        [chunk async for chunk in carrier.read(handle, "/workspace/sub/../../outside.txt")]


async def test_read_of_a_planted_symlink_is_refused(tmp_path: Path) -> None:
    """The copy-out is CVE-2026-56692's shape: a link the agent plants in its own workspace, then a
    share or a browse that resolves it. The host file's bytes never cross."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    (workspace / "linked.txt").symlink_to(outside)

    with pytest.raises(NotRegularFile):
        [chunk async for chunk in carrier.read(handle, "/workspace/linked.txt")]


async def test_read_through_a_symlinked_directory_out_of_the_workspace_is_refused(
    tmp_path: Path,
) -> None:
    """The link need not be at the target: a directory whose link leaves the workspace is enough,
    and canonicalizing the parent is what turns that into a refusal instead of a read of the link's
    target — check 2's work, not the descent's, which walks components already canonical."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"host secret")
    (workspace / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LocationEscape):
        [chunk async for chunk in carrier.read(handle, "/workspace/dir/secret.txt")]


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


async def test_file_op_runs_in_a_resumed_sandbox(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    (scratch / "home").mkdir(parents=True)
    bin_dir = scratch / "bin"
    bin_dir.mkdir()
    command = bin_dir / "sbxfs"
    command.write_text('#!/bin/sh\nprintf \'{"op":"%s"}\' "$1"\n')
    command.chmod(0o755)
    carrier = LocalCarrier(_scratch=scratch)
    spec = replace(_spec(tmp_path / "workspace"), env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    handle = await carrier.create(spec)

    assert await carrier.file_op(handle, "glob", {"pattern": "*"}) == {"op": "glob"}


@pytest.mark.integration
async def test_file_op_is_the_carrier_seam_a_session_runs_a_file_op_through(
    tmp_path: Path, sandbox_client: Path
) -> None:
    """The op the session hands down reaches the files through the carrier alone: the local one
    answers it with the `ufo fs` on its command PATH, returning the parsed object, and a handled
    refusal — a path holding no file — arrives as the ValueError a tool reports to the model."""
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


GUARD_PROBE_PROG = """
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[2]) as target:
        target.lstat()
except ContainmentError as error:
    raise SystemExit(str(error))
print("accepted")
"""


def _plant_fake_guard(workspace: Path) -> None:
    """What the agent can do with its own `write` tool: leave modules named after the ones the
    bootstrap imports in the directory the carrier runs commands in."""
    (workspace / "shutil.py").write_text("def which(name):\n    return '/nonexistent/ufo'\n")
    (workspace / "containment.py").write_text(
        "class ContainmentError(Exception):\n    pass\n"
        "from contextlib import contextmanager\n"
        "@contextmanager\n"
        "def contained_file(path, root, **kw):\n"
        "    import os\n"
        "    class T:\n"
        "        def lstat(self):\n            return os.stat(path)\n"
        "    yield T()\n"
    )


async def test_an_in_sandbox_program_cannot_be_pointed_at_a_planted_guard(tmp_path: Path) -> None:
    """Every in-sandbox program — the share preflight, the prune, a connector's claim — decides its
    verdict with the module the bootstrap imports, and commands run with cwd inside the workspace
    the agent writes to. A plain `python3 -c` puts that cwd at `sys.path[0]`, so `shutil` and then
    `containment` itself resolve to whatever the agent left there, and the guard's verdict becomes
    the agent's to choose. The interpreter runs isolated for exactly that reason."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "host-secret.txt"
    outside.write_bytes(b"host secret")
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    (workspace / "link.txt").symlink_to(outside)
    (workspace / "real.txt").write_bytes(b"the workspace's own file")
    _plant_fake_guard(workspace)

    refused = await session.python(GUARD_PROBE_PROG, "/workspace/link.txt", "/workspace")

    assert refused.exit_code == 1
    assert "not a regular file" in refused.stderr
    assert "accepted" not in refused.stdout

    reachable = await session.python(GUARD_PROBE_PROG, "/workspace/real.txt", "/workspace")

    assert reachable.exit_code == 0 and "accepted" in reachable.stdout


async def test_an_in_sandbox_program_carries_the_guard_it_runs(tmp_path: Path) -> None:
    """A carrier whose sandbox is the member's own machine provisions nothing onto its PATH — the
    terminal carrier installs no client there, since the file ops that would need one are the
    member's own client's already. The guard travels inside the program instead, so a program
    decides its verdict with the module this process ships wherever it runs, and a sandbox holding
    no helper at all still guards the paths it is handed. The system interpreter is the one a
    member's machine answers `python3` with, which is what the program has to run under."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "host-secret.txt"
    outside.write_bytes(b"host secret")
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    bare = replace(handle, egress_env={**handle.egress_env, "PATH": BARE_PATH})
    session = SandboxSession(carrier=carrier, handle=bare)
    (workspace / "link.txt").symlink_to(outside)
    (workspace / "real.txt").write_bytes(b"the workspace's own file")
    _plant_fake_guard(workspace)

    assert shutil.which("ufo", path=BARE_PATH) is None

    refused = await session.python(GUARD_PROBE_PROG, "/workspace/link.txt", "/workspace")
    reachable = await session.python(GUARD_PROBE_PROG, "/workspace/real.txt", "/workspace")

    assert refused.exit_code == 1
    assert "not a regular file" in refused.stderr
    assert "accepted" not in refused.stdout
    assert reachable.exit_code == 0 and "accepted" in reachable.stdout


async def test_system_skill_mount_falls_back_without_a_local_client(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    bare = replace(handle, egress_env={**handle.egress_env, "PATH": BARE_PATH})
    session = SandboxSession(carrier=carrier, handle=bare)

    assert shutil.which("ufo", path=BARE_PATH) is None
    assert await session.mount_system_skills({"sandbox": "sha256:" + "a" * 64}) == frozenset()


async def test_the_container_copy_in_program_replaces_a_planted_symlink(tmp_path: Path) -> None:
    """The program the container carriers stream a copy-in into, run with a real stdin the way
    `docker exec -i` gives it one. `mkdir -p && cat > "$1"` truncated through a link planted at the
    name and followed a symlinked ancestor; this stages an inode and renames it onto the name, so
    the host file is untouched and the delivery still lands."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    target = workspace / "inbox" / "report.pdf"
    target.parent.mkdir()
    target.symlink_to(outside)
    _plant_fake_guard(workspace)

    landed = await asyncio.to_thread(
        subprocess.run,
        [
            "python3",
            SANDBOX_PYTHON_FLAG,
            "-c",
            f"{SANDBOX_MODULE_BOOTSTRAP}{COPY_IN_PROG}",
            str(target),
            str(workspace),
        ],
        input=b"delivered bytes",
        capture_output=True,
        cwd=str(workspace),
        env={"PATH": handle.egress_env["PATH"]},
    )

    assert landed.returncode == 0, landed.stderr
    assert not target.is_symlink()
    assert target.read_bytes() == b"delivered bytes"
    assert target.stat().st_mode & 0o777 == 0o644
    assert outside.read_bytes() == b"host secret"


async def test_the_container_copy_in_program_refuses_a_path_out_of_the_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))

    refused = await asyncio.to_thread(
        subprocess.run,
        [
            "python3",
            SANDBOX_PYTHON_FLAG,
            "-c",
            f"{SANDBOX_MODULE_BOOTSTRAP}{COPY_IN_PROG}",
            str(outside),
            str(workspace),
        ],
        input=b"delivered bytes",
        capture_output=True,
        cwd=str(workspace),
        env={"PATH": handle.egress_env["PATH"]},
    )

    assert refused.returncode == 1
    assert "escapes" in refused.stderr.decode()
    assert outside.read_bytes() == b"host secret"


async def test_ensure_tool_output_dir_creates_the_directory_when_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (workspace / ".tool-output").is_dir()


async def test_ensure_tool_output_dir_reclaims_a_file_squatting_the_name(tmp_path: Path) -> None:
    """The poison-pill case: a member write left a regular file where the engine's offload
    directory must be, so a bare `mkdir -p` would fail `File exists` on every later offload. The
    ensure reclaims it to a directory and reports the reclaim."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file(".tool-output", b"squatter")
    assert (workspace / ".tool-output").is_file()

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is True
    assert (workspace / ".tool-output").is_dir()
    await session.write_file(".tool-output/call.txt", b"offloaded")
    assert (workspace / ".tool-output" / "call.txt").read_text() == "offloaded"


async def test_ensure_tool_output_dir_leaves_an_existing_directory_and_its_contents(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    session = SandboxSession(carrier=carrier, handle=handle)
    await session.write_file(".tool-output/kept.txt", b"keep me")

    reclaimed = await session.ensure_tool_output_dir()

    assert reclaimed is False
    assert (workspace / ".tool-output" / "kept.txt").read_text() == "keep me"


async def test_exec_env_rides_the_handle_not_the_conversation(tmp_path: Path) -> None:
    """Two turns can hold one conversation's workspace at once (a subagent beside its parent): each
    exec runs under its own handle's run token and the spec's per-turn sentinel entries, so a later
    create never re-points an earlier turn's egress attribution."""
    carrier = LocalCarrier()
    conversation = uuid4()
    base = replace(_spec(tmp_path / "workspace"), conversation_id=conversation)
    first = await carrier.create(replace(base, run_token="turn-a"))
    second = await carrier.create(replace(base, run_token="turn-b", env={"GH_TOKEN": "sent-b"}))

    probe = ("bash", "-lc", 'printf "%s|%s" "$HTTPS_PROXY" "${GH_TOKEN:-none}"')
    result_a = await carrier.exec(first, probe, 30)
    result_b = await carrier.exec(second, probe, 30)

    assert result_a.stdout == f"http://turn-a:@127.0.0.1:{PROXY_PORT}|none"
    assert result_b.stdout == f"http://turn-b:@127.0.0.1:{PROXY_PORT}|sent-b"


async def test_background_descendant_keeps_its_authority_across_later_execs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    base = SandboxSession(carrier=carrier, handle=await carrier.create(_spec(workspace)))
    first = base.authorize(
        "member-a",
        frozenset(("GH_TOKEN",)),
        {"GH_TOKEN": "sent-a"},
    )
    second = base.authorize(
        "member-b",
        frozenset(("GH_TOKEN",)),
        {"GH_TOKEN": "sent-b"},
    )

    launched = await first.bash(
        "nohup sh -c 'while [ ! -f release ]; do :; done; "
        'printf "%s|%s" "$HTTPS_PROXY" "$GH_TOKEN" > first.tmp && mv first.tmp first.txt\' '
        ">/dev/null 2>&1 &"
    )
    assert launched.exit_code == 0
    written = await second.bash(
        'printf "%s|%s" "$HTTPS_PROXY" "$GH_TOKEN" > second.txt; '
        "touch release; while [ ! -f first.txt ]; do :; done"
    )
    assert written.exit_code == 0

    assert (workspace / "first.txt").read_text() == (
        f"http://member-a:@127.0.0.1:{PROXY_PORT}|sent-a"
    )
    assert (workspace / "second.txt").read_text() == (
        f"http://member-b:@127.0.0.1:{PROXY_PORT}|sent-b"
    )


async def test_a_second_write_never_shows_a_reader_a_half_written_file(tmp_path: Path) -> None:
    """Two writers race one path whenever a surface delivers the same file twice — Slack sends a
    channel mention as two events, and both build their turn before the duplicate is dropped. The
    write lands beside the target and is renamed onto it, so every read returns one whole write."""
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
    """The sidecar is named for itself, not the target, so the longest name a directory accepts is
    still writable. A name built from the target plus a suffix fails here with ENAMETOOLONG —
    reached from the agent's own write tool and from an inbound attachment the member named."""
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

    with pytest.raises(NotRegularFile):
        await carrier.write(handle, "/workspace/inbox", b"onto the directory itself")

    assert [entry.name for entry in workspace.iterdir()] == ["inbox"]
    assert [entry.name for entry in (workspace / "inbox").iterdir()] == ["keep.txt"]


@pytest.mark.parametrize("path", ("/workspace", "/workspace/.", "/workspace/sub/.."))
async def test_a_write_at_the_workspace_root_is_refused_before_any_byte_lands(
    tmp_path: Path, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged name is the target's sibling, and the workspace root's sibling is another
    conversation's workspace — so a path resolving to the root is refused before it is staged, not
    staged and cleaned up. Nothing is asserted created at all, since the cleanup would hide a staged
    file from the directory listing and leave it behind only when the process dies mid-write."""
    workspace = tmp_path / "conversations" / "one" / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    created: list[str] = []
    unpatched = os.open

    def spy(target, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            created.append(str(target))
        return unpatched(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)

    with pytest.raises(ContainmentError):
        await carrier.write(handle, path, b"payload that must not land beside the workspace")

    assert created == []
    assert [entry.name for entry in workspace.parent.iterdir()] == ["workspace"]
    assert list(workspace.iterdir()) == []


async def test_a_write_at_a_traversal_path_is_refused(tmp_path: Path) -> None:
    """A surface's inbound filename and a tool's path both arrive here as a `/workspace` string, so
    the copy-in is where a name climbing out of the workspace has to be refused."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    (workspace / "inbox").mkdir()

    with pytest.raises(LocationEscape):
        await carrier.write(handle, "/workspace/inbox/../../escape.txt", b"landed outside")

    assert not (tmp_path / "escape.txt").exists()


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


async def test_a_write_onto_a_planted_symlink_replaces_the_link(tmp_path: Path) -> None:
    """A copy-in stages under a name created `O_CREAT|O_EXCL` and renames it onto the target, so a
    link the agent planted there is replaced rather than written through: neither the host file's
    bytes nor its mode is reachable from the workspace, and the delivery still lands.

    Replacing rather than refusing is the point. The name is a surface's to choose — `slack-inbox`,
    an offload sidecar — so one link left at it would otherwise deny every later delivery under that
    name, and the rename already cannot follow it."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.sh"
    outside.write_bytes(b"outside")
    outside.chmod(0o777)
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    link = workspace / "linked.sh"
    link.symlink_to(outside)

    await carrier.write(handle, "/workspace/linked.sh", b"replaced")

    assert not link.is_symlink()
    assert link.read_bytes() == b"replaced"
    assert link.stat().st_mode & 0o777 == 0o644
    assert outside.read_bytes() == b"outside"
    assert [entry.name for entry in workspace.iterdir()] == ["linked.sh"]


async def test_a_write_through_a_symlinked_directory_out_of_the_workspace_is_refused(
    tmp_path: Path,
) -> None:
    """A copy-in creates the directories it needs, so a link already holding one of their names
    would otherwise carry the bytes out of the workspace — the parent is canonicalized and the
    escape refused before a byte is staged."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    (workspace / "inbox").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LocationEscape):
        await carrier.write(handle, "/workspace/inbox/note.txt", b"landed outside")

    assert list(outside.iterdir()) == []


async def test_the_mode_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One probe, so there is no window between two of them for a delete to land in — a second probe
    turns a write into FileNotFoundError on a path the prune sweep, a bash `rm`, or a concurrent
    delivery removed, where the copy-in would have recreated it."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(_spec(workspace))
    await carrier.write(handle, "/workspace/probed.txt", b"first")
    target = workspace / "probed.txt"
    probes: list[tuple[str, bool]] = []

    for name in ("lstat", "stat"):
        unpatched = getattr(os, name)

        def counted(path, *args, _name=name, _unpatched=unpatched, **kwargs):
            if Path(path).name == target.name:
                probes.append((_name, kwargs.get("follow_symlinks", True)))
            return _unpatched(path, *args, **kwargs)

        monkeypatch.setattr(os, name, counted)

    await carrier.write(handle, "/workspace/probed.txt", b"second")

    assert probes == [("stat", False)]
    assert target.read_bytes() == b"second"


async def test_a_target_deleted_before_the_write_is_still_created(tmp_path: Path) -> None:
    """The mode is read once, so a delete that lands between the read and the rename ends in a
    created file — where a second probe would have raised FileNotFoundError on a path the prune
    sweep, a bash `rm`, or a concurrent delivery had just removed."""
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
