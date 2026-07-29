"""The Docker carrier extension's selection seam.

The container-integration proof lives in test_file_tools.py (Docker-gated). This asserts only the
seam: with `[sandbox] backend = "docker"` and the docker extension's Manifest present, `serve`
resolves exactly this extension's carrier by name — the deploy swaps to it without core naming
Docker. Constructing the carrier needs no daemon, so this is not Docker-gated."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_docker as docker_ext
from ufo_ext_docker import DockerCarrier

from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.db import workspace_tx
from ufo.sandbox.conversation import ConversationSandbox
from ufo.schema import tables
from ufo.sdk.sandbox import (
    NO_PROXY_HOSTS,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
)
from ufo.serve import _select_carrier
from ufo.workspace import ws


async def test_running_id_raises_on_docker_ps_failure_instead_of_reporting_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine no-match is exit 0 with empty stdout; a non-zero `docker ps` means the command
    itself failed (daemon hiccup, timeout). Swallowing that as "not running" would send `create`
    down the fresh-create path against a name a live container still holds, surfacing a misleading
    name conflict instead of the real transient fault."""

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        return 1, b"", b"error during connect: transient daemon error"

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)

    with pytest.raises(RuntimeError, match="docker ps failed"):
        await DockerCarrier()._running_id("ufo-sbx-x")


def test_config_backend_docker_resolves_the_extension_contributed_carrier() -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend="docker"),
    )
    carrier, off_cluster = _select_carrier(config, (docker_ext.manifest(),))
    assert isinstance(carrier, DockerCarrier)
    assert off_cluster is False


async def test_exec_carries_the_turn_env_and_run_bakes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run token and sentinel env belong to the turn, not the container: `docker run` bakes no
    egress env (a container outliving its first turn must not pin that turn's token) and always
    bind-mounts the conversation's host workspace directory at `/workspace`, and every
    `docker exec` carries its own handle's env — proxy URL with the turn's token, model sentinels,
    and the spec's per-turn sentinel entries."""
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        if argv[0] == "run":
            return 0, b"cid1\n", b""
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="turn-a",
        env={"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-1"},
    )
    carrier = DockerCarrier()
    handle = await carrier.create(spec)

    run_argv = next(argv for argv in calls if argv[0] == "run")
    assert "--env" not in run_argv
    assert run_argv[run_argv.index("-v") + 1] == f"/tmp/ws:{WORKSPACE_DIR}"
    assert run_argv[run_argv.index("--network") + 1] == (f"ufo-sandbox-{spec.conversation_id.hex}")
    assert run_argv[run_argv.index("--cap-drop") + 1] == "NET_RAW"

    await carrier.exec(handle, ("bash", "-lc", "gh api user"), 30)

    exec_argv = calls[-1]
    proxy_url = "http://turn-a:@host.docker.internal:8080"
    assert f"HTTPS_PROXY={proxy_url}" in exec_argv
    assert f"https_proxy={proxy_url}" in exec_argv
    assert f"NO_PROXY={NO_PROXY_HOSTS}" in exec_argv
    assert f"no_proxy={NO_PROXY_HOSTS}" in exec_argv
    assert f"ANTHROPIC_API_KEY={SENTINEL_MODEL_KEY}" in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-1" in exec_argv
    assert exec_argv.index("cid1") > exec_argv.index(f"HTTPS_PROXY={proxy_url}")


async def test_missing_conversation_network_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)

    await DockerCarrier()._ensure_network("ufo-sandbox-c1")

    assert calls == [
        ("network", "ls", "-q", "--filter", "name=^ufo-sandbox-c1$"),
        ("network", "create", "ufo-sandbox-c1"),
    ]


@pytest.mark.parametrize("failed_command", ["ls", "create"])
async def test_conversation_network_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, failed_command: str
) -> None:
    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        failed = argv[1] == failed_command
        return (1 if failed else 0), b"", (b"daemon unavailable" if failed else b"")

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)

    with pytest.raises(RuntimeError, match=f"network {failed_command} failed"):
        await DockerCarrier()._ensure_network("ufo-sandbox-c1")


async def test_failed_create_removes_the_conversation_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        if argv[0] == "run":
            return 1, b"", b"image unavailable"
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()
    spec = SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="turn-a",
    )

    with pytest.raises(RuntimeError, match="image unavailable"):
        await DockerCarrier().create(spec)

    assert calls[-1] == ("network", "rm", f"ufo-sandbox-{conversation.hex}")


async def test_failed_provision_removes_the_container_and_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append(argv)
        if argv[0] == "run":
            return 0, b"cid1\n", b""
        if argv[0] == "exec":
            return 1, b"", b"trust update failed"
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()
    spec = SandboxSpec(
        conversation_id=conversation,
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="turn-a",
    )

    with pytest.raises(RuntimeError, match="trust update failed"):
        await DockerCarrier().create(spec)

    assert calls[-2:] == [
        ("rm", "-f", "cid1"),
        ("network", "rm", f"ufo-sandbox-{conversation.hex}"),
    ]


async def test_attach_to_a_running_container_carries_the_second_turns_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #507 bug lived in the attach branch: a later turn reusing a still-running container ran
    under the first turn's baked env. Now the second `create` (which hits `running is not None`)
    returns a handle whose `exec` carries the second turn's token and sentinels, not the first's —
    the env lives on the per-turn handle, never on the container."""
    running: list[str] = []

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        if argv[0] == "run":
            running.append("cid1")
            return 0, b"cid1\n", b""
        if argv[0] == "ps":
            return 0, (b"cid1\n" if running else b""), b""
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    conversation = uuid4()

    def _spec(run_token: str, env: dict[str, str]) -> SandboxSpec:
        return SandboxSpec(
            conversation_id=conversation,
            image_ref="ufo-sandbox:latest",
            workspace_host_path="/tmp/ws",
            proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
            run_token=run_token,
            env=env,
        )

    carrier = DockerCarrier()
    await carrier.create(_spec("turn-a", {"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-a"}))
    second = await carrier.create(_spec("turn-b", {"GH_TOKEN": "UFO_SENTINEL_GRANT_acct-b"}))

    exec_calls: list[tuple[str, ...]] = []

    async def record_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        exec_calls.append(argv)
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", record_docker)
    await carrier.exec(second, ("bash", "-lc", "gh api user"), 30)

    exec_argv = exec_calls[-1]
    assert "HTTPS_PROXY=http://turn-b:@host.docker.internal:8080" in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-b" in exec_argv
    assert "HTTPS_PROXY=http://turn-a:@host.docker.internal:8080" not in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-a" not in exec_argv


async def test_write_streams_the_content_over_stdin_and_never_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant the write seam exists for: a payload reaches the container through `docker exec
    -i`'s stdin, never as an argv element. The real-container proof is the 26 MB write in
    test_file_tools.py; this pins the call shape, which no successful write can distinguish."""
    calls: list[tuple[tuple[str, ...], bytes]] = []

    async def record_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        calls.append((argv, stdin))
        return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", record_docker)
    carrier = DockerCarrier()
    handle = SandboxHandle(conversation_id=uuid4(), container_id="cid1")
    content = b"x" * (2 * 1024 * 1024)

    await carrier.write(handle, "/workspace/notes/report.txt", content)

    argv, stdin = calls[-1]
    assert stdin == content
    assert not any(content[:64].decode() in arg for arg in argv)
    assert argv[:3] == ("exec", "-i", "cid1")
    assert argv[-1] == "/workspace/notes/report.txt"
    assert 'mkdir -p "$(dirname "$1")" && cat > "$1"' in argv


async def test_write_raises_with_the_container_error_on_a_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed copy-in is loud: the caller (the engine's offload) decides how to degrade, so the
    carrier must not swallow it into a silently truncated file."""

    async def failing_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        return 1, b"", b"cat: /workspace/x: No space left on device\n"

    monkeypatch.setattr(docker_ext, "_docker", failing_docker)
    carrier = DockerCarrier()
    handle = SandboxHandle(conversation_id=uuid4(), container_id="cid1")

    with pytest.raises(OSError, match="No space left on device"):
        await carrier.write(handle, "/workspace/x", b"payload")


class _FakeDaemon:
    """Stands in for the docker CLI alone: `run` registers a running container under its --name,
    `ps` answers from that registry (names for the reclaim scan, an id for a name filter, exited
    ids for the stopped lookup), `stop`/`start` move a container between the running and stopped
    sets, `rm -f` unregisters by id or name, and an exec against a stopped container answers the
    daemon's own is-not-running error. Every assertion is on the carrier's own calls and ordering —
    the daemon is the dependency, never the thing asserted. `on_call` lets a test interleave a
    concurrent touch at an exact await point."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.running: dict[str, str] = {}
        self.stopped: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.networks: set[str] = set()
        self.counter = 0
        self.on_call: Callable[[tuple[str, ...]], None] | None = None
        self.conflict_on_existing = False
        self.network_conflict = False
        self.hide_from_ps = False
        self.stop_fails = False

    def start(self, name: str) -> None:
        self.counter += 1
        container_id = f"cid-{self.counter}"
        self.running[name] = container_id
        self.ids[name] = container_id

    def _move(self, target: str, source: dict[str, str], into: dict[str, str]) -> bool:
        for name, cid in list(source.items()):
            if cid == target or name == target:
                del source[name]
                into[name] = cid
                return True
        return False

    async def __call__(self, *argv: str, stdin: bytes = b"", timeout_s: int = 60):
        await asyncio.sleep(0)
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv)
        match argv[0]:
            case "run":
                name = argv[argv.index("--name") + 1]
                if self.conflict_on_existing and (name in self.running or name in self.stopped):
                    detail = f'Conflict. The container name "/{name}" is already in use'
                    return 125, b"", detail.encode()
                self.start(name)
                return 0, f"{self.running[name]}\n".encode(), b""
            case "ps" if "--format" in argv:
                return 0, "\n".join(self.running).encode(), b""
            case "ps" if "-aq" in argv:
                wanted = argv[argv.index("--filter") + 1].removeprefix("name=^").removesuffix("$")
                found = self.stopped.get(wanted, "")
                return 0, f"{found}\n".encode() if found else b"", b""
            case "ps":
                wanted = argv[argv.index("--filter") + 1].removeprefix("name=^").removesuffix("$")
                if self.hide_from_ps:
                    self.hide_from_ps = False
                    return 0, b"", b""
                found = self.running.get(wanted, "")
                return 0, f"{found}\n".encode() if found else b"", b""
            case "stop":
                if self.stop_fails:
                    return 1, b"", b"Error response from daemon: cannot stop"
                self._move(argv[1], self.running, self.stopped)
                return 0, b"", b""
            case "start":
                if self._move(argv[1], self.stopped, self.running):
                    return 0, f"{argv[1]}\n".encode(), b""
                return 1, b"", f"Error: no such container: {argv[1]}".encode()
            case "exec":
                container = next(
                    arg for arg in argv[1:] if not arg.startswith("-") and "=" not in arg
                )
                if container in self.stopped.values():
                    detail = f"Error response from daemon: container {container} is not running"
                    return 1, b"", detail.encode()
                return 0, b"", b""
            case "rm":
                target = argv[-1]
                self._move(target, self.running, {})
                self._move(target, self.stopped, {})
                return 0, b"", b""
            case "network" if argv[1] == "create":
                if self.network_conflict and argv[2] in self.networks:
                    return 1, b"", f"network with name {argv[2]} already exists".encode()
                self.networks.add(argv[2])
                return 0, b"", b""
            case "network" if argv[1] == "rm":
                self.networks.discard(argv[2])
                return 0, b"", b""
            case "network" if argv[1] == "ls":
                wanted = argv[argv.index("--filter") + 1].removeprefix("name=^").removesuffix("$")
                return 0, (b"nid\n" if wanted in self.networks else b""), b""
            case _:
                return 0, b"", b""


def _reclaim_spec(conversation_id: UUID) -> SandboxSpec:
    return SandboxSpec(
        conversation_id=conversation_id,
        image_ref="ufo-sandbox:latest",
        workspace_host_path="/tmp/ws",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        run_token="turn-a",
    )


async def test_create_reclaims_a_container_idle_past_the_ttl_and_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reclaim is this carrier's own, fired by the create that proves the process is live. The
    container whose conversation went untouched for the idle span is stopped by its resolved id —
    never removed, so the container and its bind-mounted workspace persist for the next touch to
    start again; a container touched inside the span, and the one the create is opening, are
    not."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    idle, busy, opening = uuid4(), uuid4(), uuid4()
    carrier = DockerCarrier(clock=lambda: now)

    await carrier.create(_reclaim_spec(idle))
    now = docker_ext.IDLE_RECLAIM_SECONDS - 1
    await carrier.create(_reclaim_spec(busy))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    daemon.calls.clear()

    await carrier.create(_reclaim_spec(opening))

    idle_name = f"{docker_ext.CONTAINER_NAME_PREFIX}{idle}"
    assert ("stop", daemon.ids[idle_name]) in daemon.calls
    assert idle_name in daemon.stopped
    assert not any(argv[:2] == ("rm", "-f") for argv in daemon.calls)
    stopped = [argv for argv in daemon.calls if argv[0] == "stop"]
    assert stopped == [("stop", daemon.ids[idle_name])]


async def test_reclaim_adopts_a_prior_process_container_and_takes_it_one_span_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running `ufo-sbx-*` container this process has no touch for — left by a process that
    restarted — must not run forever, and must not be stopped on sight either: the first create
    adopts it at the current clock, and only a create one full idle span later stops it."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    orphan = uuid4()
    daemon.start(f"{docker_ext.CONTAINER_NAME_PREFIX}{orphan}")
    orphan_id = daemon.ids[f"{docker_ext.CONTAINER_NAME_PREFIX}{orphan}"]
    carrier = DockerCarrier(clock=lambda: now)

    await carrier.create(_reclaim_spec(uuid4()))
    assert ("stop", orphan_id) not in daemon.calls

    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    assert ("stop", orphan_id) in daemon.calls


async def test_concurrent_creates_reclaim_a_stale_container_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two creates racing one stale candidate must not double-stop or raise: the snapshot is
    advisory, the stop re-checks the touch after every await, and the loser of the race finds the
    entry already gone and moves on."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    stale = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(stale))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    stale_id = daemon.ids[f"{docker_ext.CONTAINER_NAME_PREFIX}{stale}"]
    daemon.calls.clear()

    await asyncio.gather(
        carrier.create(_reclaim_spec(uuid4())), carrier.create(_reclaim_spec(uuid4()))
    )

    assert [argv for argv in daemon.calls if argv == ("stop", stale_id)] == [("stop", stale_id)]


async def test_a_touch_landing_during_the_reclaim_scan_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversation reopened while the scan is mid-flight is not stopped on the snapshot's
    word: the staleness re-check after the id resolve sees the fresh touch and skips it."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    contested = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(contested))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    contested_name = f"{docker_ext.CONTAINER_NAME_PREFIX}{contested}"

    def touch_during_resolve(argv: tuple[str, ...]) -> None:
        if argv[:2] == ("ps", "-q") and contested_name in argv[3]:
            carrier._touched[contested] = now

    daemon.on_call = touch_during_resolve
    daemon.calls.clear()
    await carrier.create(_reclaim_spec(uuid4()))

    assert ("stop", daemon.ids[contested_name]) not in daemon.calls
    assert contested in carrier._touched


async def test_an_exec_keeps_a_working_conversations_container_out_of_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idleness is measured from the last touch, not from create, so a long conversation whose only
    activity is tool calls is never reclaimed underneath itself."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    working, opening = uuid4(), uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(working))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.exec(handle, ("bash", "-lc", "true"), 30)
    daemon.calls.clear()

    await carrier.create(_reclaim_spec(opening))

    working_id = daemon.ids[f"{docker_ext.CONTAINER_NAME_PREFIX}{working}"]
    assert ("stop", working_id) not in daemon.calls


async def test_entries_lists_container_paths_workspace_relative(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The file browser's listing over this carrier: the in-container walk answers `/workspace/…`
    paths — not the host bind-mount paths the local carrier's argv rewrite produces — and each must
    come back relative to the workspace root. The daemon is faked at the CLI seam with the shapes
    the real one answers; the mapping under test is the carrier's and the seam's own."""
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=None,
                sandbox_handle="docker:cid-listing",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    listing = json.dumps(
        {
            "files": [
                {"path": "/workspace/sub/b.txt", "size": 5, "modified": 1700000000.0},
                {"path": "/workspace/a.txt", "size": 2, "modified": 1700000001.0},
            ],
            "count": 2,
            "truncated": False,
        }
    )

    async def fake_docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60):
        match argv[0]:
            case "ps":
                return 0, b"cid-listing\n", b""
            case "exec" if "sbxfs" in argv:
                return 0, listing.encode(), b""
            case _:
                return 0, b"", b""

    monkeypatch.setattr(docker_ext, "_docker", fake_docker)
    sandboxes = ConversationSandbox(
        carrier=DockerCarrier(),
        backend="docker",
        off_cluster=False,
        image_ref="ufo-sandbox:latest",
        proxy=ProxyEndpoint(port=8080, ca_cert="ca-pem"),
        workspace_root=tmp_path / "workspaces",
    )

    with ws(workspace_id):
        entries = await sandboxes.entries(conversation_id)

    assert [(entry.path, entry.size_bytes) for entry in entries] == [
        ("a.txt", 2),
        ("sub/b.txt", 5),
    ]


async def test_a_create_losing_the_name_race_attaches_to_the_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent creates for one conversation race `docker run` under the same deterministic name;
    the daemon arbitrates, and the loser attaches to the winner's container instead of failing the
    caller — no lock held here, no second container ever made."""
    daemon = _FakeDaemon()
    daemon.conflict_on_existing = True
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    conversation = uuid4()
    carrier = DockerCarrier()

    winner = await carrier.create(_reclaim_spec(conversation))
    daemon.hide_from_ps = True

    loser = await carrier.create(_reclaim_spec(conversation))

    assert daemon.counter == 1
    assert loser.container_id == winner.container_id


async def test_a_mid_turn_command_revives_a_container_reclaim_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scenario reclaim must survive: conversation A's turn goes quiet past the idle span with
    no way for this carrier to know the turn is live, B's create stops A's container — and A's next
    command starts it again and runs, so the wrong reclaim costs one restart, never a failed tool
    call."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet, opening = uuid4(), uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(quiet))
    quiet_name = f"{docker_ext.CONTAINER_NAME_PREFIX}{quiet}"

    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(opening))
    assert quiet_name in daemon.stopped

    result = await carrier.exec(handle, ("bash", "-lc", "true"), 30)

    assert result.exit_code == 0
    assert quiet_name in daemon.running


async def test_create_starts_a_stopped_container_instead_of_running_a_fresh_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next turn of a reclaimed conversation reopens by starting the stopped container — same
    id, same workspace, no `docker run` — the docker analog of resuming a provider-paused
    sandbox."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    conversation = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    first = await carrier.create(_reclaim_spec(conversation))
    name = f"{docker_ext.CONTAINER_NAME_PREFIX}{conversation}"
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    assert name in daemon.stopped

    reopened = await carrier.create(_reclaim_spec(conversation))

    assert reopened.container_id == first.container_id
    assert name in daemon.running
    assert daemon.counter == 2


async def test_attach_starts_a_stopped_container_for_a_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browse of a reclaimed conversation shows its live files: attach starts the stopped
    container — the same shape as resuming a provider-paused sandbox — and still answers None when
    no container exists at all."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    conversation = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    created = await carrier.create(_reclaim_spec(conversation))
    name = f"{docker_ext.CONTAINER_NAME_PREFIX}{conversation}"
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    assert name in daemon.stopped

    attached = await carrier.attach(_reclaim_spec(conversation))

    assert attached is not None
    assert attached.container_id == created.container_id
    assert name in daemon.running
    assert await carrier.attach(_reclaim_spec(uuid4())) is None


async def test_a_command_in_flight_pins_its_container_against_reclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-flight guard, exercised mid-command: while conversation A's exec is suspended inside
    the daemon call, the clock jumps past the idle TTL and an unrelated create fires reclaim — A's
    container must not be stopped, however stale its last touch reads, because the command is still
    running. The stop happens only once A is genuinely between calls."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet, opening = uuid4(), uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(quiet))
    quiet_name = f"{docker_ext.CONTAINER_NAME_PREFIX}{quiet}"
    quiet_id = daemon.ids[quiet_name]

    release = asyncio.Event()
    original = daemon.__class__.__call__

    async def stall_exec(self, *argv, stdin=b"", timeout_s=60):
        if argv[0] == "exec" and quiet_id in argv:
            await release.wait()
        return await original(self, *argv, stdin=stdin, timeout_s=timeout_s)

    monkeypatch.setattr(daemon.__class__, "__call__", stall_exec)
    inflight = asyncio.ensure_future(carrier.exec(handle, ("bash", "-lc", "slow"), 30))
    await asyncio.sleep(0)
    now = docker_ext.IDLE_RECLAIM_SECONDS + 1

    await carrier.create(_reclaim_spec(opening))
    assert ("stop", quiet_id) not in daemon.calls
    assert quiet_name in daemon.running

    release.set()
    result = await inflight
    assert result.exit_code == 0

    now += docker_ext.IDLE_RECLAIM_SECONDS + 1
    await carrier.create(_reclaim_spec(uuid4()))
    assert ("stop", quiet_id) in daemon.calls


async def test_reclaim_releases_the_conversations_network_and_revive_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge subnets are the host's finite resource (the daemon's default pools hold ~30), so a
    stop that kept its network would wedge every new conversation past that count. Reclaim releases
    the network with the stop, and the revive re-ensures it and reconnects the container before
    starting — so the reopened conversation runs on a fresh subnet and nothing leaks."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(quiet))
    network = f"ufo-sandbox-{quiet.hex}"
    assert network in daemon.networks

    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))

    assert ("network", "disconnect", network, handle.container_id) in daemon.calls
    assert network not in daemon.networks

    result = await carrier.exec(handle, ("bash", "-lc", "true"), 30)

    assert result.exit_code == 0
    assert network in daemon.networks
    connect_at = daemon.calls.index(("network", "connect", network, handle.container_id))
    start_at = daemon.calls.index(("start", handle.container_id))
    assert connect_at < start_at


async def test_a_failed_stop_keeps_the_stale_entry_for_the_next_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop the daemon refuses must not grant a fresh idle span: the stale entry goes back, the
    network stays, and the next create retries the stop."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    stuck = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(stuck))
    stuck_id = daemon.ids[f"{docker_ext.CONTAINER_NAME_PREFIX}{stuck}"]
    now = docker_ext.IDLE_RECLAIM_SECONDS
    daemon.stop_fails = True

    await carrier.create(_reclaim_spec(uuid4()))
    assert stuck in carrier._touched
    assert f"ufo-sandbox-{stuck.hex}" in daemon.networks

    daemon.stop_fails = False
    daemon.calls.clear()
    await carrier.create(_reclaim_spec(uuid4()))
    assert ("stop", stuck_id) in daemon.calls


async def test_concurrent_network_creates_converge_like_the_container_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two creates for one brand-new conversation can both miss the network ls and both run
    `network create`; the daemon's already-exists answer is the success the loser was after, never
    a failed sandbox open."""
    daemon = _FakeDaemon()
    daemon.network_conflict = True
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    conversation = uuid4()
    carrier = DockerCarrier()
    network = f"ufo-sandbox-{conversation.hex}"
    daemon.networks.add(network)
    original = daemon.__class__.__call__

    async def force_ls_miss(self, *argv, stdin=b"", timeout_s=60):
        if argv[:2] == ("network", "ls"):
            self.calls.append(argv)
            return 0, b"", b""
        return await original(self, *argv, stdin=stdin, timeout_s=timeout_s)

    monkeypatch.setattr(daemon.__class__, "__call__", force_ls_miss)

    handle = await carrier.create(_reclaim_spec(conversation))

    assert handle.container_id
    assert ("network", "create", network) in daemon.calls


async def test_a_name_race_loser_installs_the_ca_before_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser attaches to the winner's container, and the CA still lands before any command runs
    — a loser that skipped it would exec against a container whose trust store the winner may not
    have finished writing."""
    daemon = _FakeDaemon()
    daemon.conflict_on_existing = True
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    conversation = uuid4()
    carrier = DockerCarrier()
    winner = await carrier.create(_reclaim_spec(conversation))
    daemon.hide_from_ps = True
    daemon.calls.clear()

    loser = await carrier.create(_reclaim_spec(conversation))

    assert loser.container_id == winner.container_id
    ca_installs = [
        argv
        for argv in daemon.calls
        if argv[0] == "exec" and winner.container_id in argv and "ca-certificates" in argv[-1]
    ]
    assert ca_installs
