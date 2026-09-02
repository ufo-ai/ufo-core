"""The Docker carrier extension's selection seam.

The container-integration proof lives in test_file_tools.py (Docker-gated). This asserts only the
seam: with `[sandbox] backend = "docker"` and the docker extension's Manifest present, `serve`
resolves exactly this extension's carrier by name — the deploy swaps to it without core naming
Docker. Constructing the carrier needs no daemon, so this is not Docker-gated."""

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_docker as docker_ext
from ufo_ext_docker import DockerCarrier

from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import ConversationSandbox
from ufo.harness.sandbox.select import select_carriers
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.sandbox import (
    NO_PROXY_HOSTS,
    SANDBOX_GID,
    SANDBOX_UID,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    sandbox_runtime_root,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


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
    selected = select_carriers(config, (docker_ext.manifest(),))
    assert isinstance(selected.carrier, DockerCarrier)
    assert selected.spec.off_cluster is False
    assert selected.spec.sizes == ()


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
    runtime_call = next(argv for argv in calls if len(argv) > 6 and "chown -R -h" in argv[6])
    runtime_root = sandbox_runtime_root(spec.conversation_id)
    assert runtime_call == (
        "exec",
        "-u",
        "root",
        "cid1",
        "sh",
        "-c",
        'if [ "$(stat -c %u:%g "$5")" != "$3:$4" ]; then '
        'chown -R -h "$3:$4" "$5"; fi && '
        'install -d -o 0 -g 0 -m 0755 "$1" && '
        'if [ -f "$1/session" ] && [ ! -L "$1/session" ]; then '
        'chown "$3:$4" "$1/session" && chmod 0600 "$1/session"; '
        'else rm -f "$1/session" && '
        'install -o "$3" -g "$4" -m 0600 /dev/null "$1/session"; fi && '
        'install -d -o "$3" -g "$4" -m 0700 "$2"',
        "sh",
        str(Path(runtime_root).parents[1]),
        runtime_root,
        str(SANDBOX_UID),
        str(SANDBOX_GID),
        WORKSPACE_DIR,
    )

    await carrier.exec(handle, ("bash", "-lc", "gh api user"), 30)

    exec_argv = calls[-1]
    proxy_url = "http://turn-a:ufo@host.docker.internal:8080"
    assert f"HTTPS_PROXY={proxy_url}" in exec_argv
    assert f"https_proxy={proxy_url}" in exec_argv
    assert f"NO_PROXY={NO_PROXY_HOSTS}" in exec_argv
    assert f"no_proxy={NO_PROXY_HOSTS}" in exec_argv
    assert f"ANTHROPIC_API_KEY={SENTINEL_MODEL_KEY}" in exec_argv
    assert "GH_TOKEN=UFO_SENTINEL_GRANT_acct-1" in exec_argv
    assert exec_argv.index("cid1") > exec_argv.index(f"HTTPS_PROXY={proxy_url}")
    assert exec_argv[exec_argv.index("--workdir") + 1] == WORKSPACE_DIR
    # The toolchains that ignore the system trust store are pointed at the proxy CA, so a MITM'd
    # host (a cache-fronted registry included) is trusted by pip and Node, not only by /etc/ssl.
    assert "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" in exec_argv
    assert "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in exec_argv
    assert "NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/ufo-proxy.crt" in exec_argv


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


async def test_write_streams_the_content_over_stdin_and_never_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant the write seam exists for: a payload reaches the container through `docker exec
    -i`'s stdin, never as an argv element, and the name it lands on goes through the containment
    guard rather than a shell redirect that would truncate through a planted link. The real proof of
    the stream is the 26 MB write in test_file_tools.py; this pins the call shape, which no
    successful write can tell apart."""
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
    assert argv[:5] == ("exec", "-i", "cid1", "python3", docker_ext.SANDBOX_PYTHON_FLAG)
    assert argv[-2:] == ("/workspace/notes/report.txt", "/workspace")
    assert docker_ext.COPY_IN_PROG in argv[-3]
    assert "from containment import" in argv[-3]
    assert not any("cat > " in arg for arg in argv)


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
    """Stands in for the docker CLI alone: `run` registers a running container under its --name;
    `ps` answers from three pools — running, paused, stopped — the way the daemon does (`--format`
    lists up containers' names, running and paused, honouring `--filter name=` and narrowing under
    `status=running`, plus stopped under `-a`; `-q status=running` answers a running id; `-aq`
    answers any state's id, or only an exited one under `status=exited`); `stop`/`start` move a
    container between pools, `rm -f` unregisters by id or name, and an exec against a stopped
    container answers the daemon's own is-not-running error. Every assertion is on the carrier's
    own calls and ordering — the daemon is the dependency, never the thing asserted. `on_call`
    lets a test interleave a concurrent touch at an exact await point."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.running: dict[str, str] = {}
        self.paused: dict[str, str] = {}
        self.stopped: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.networks: set[str] = set()
        self.counter = 0
        self.on_call: Callable[[tuple[str, ...]], None] | None = None
        self.conflict_on_existing = False
        self.network_conflict = False
        self.connect_conflict = False
        self.subnets_exhausted = False
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
                pattern = next(arg for arg in argv if arg.startswith("name=")).removeprefix("name=")
                narrowed = "status=running" in argv
                listed = dict(self.running) if narrowed else {**self.running, **self.paused}
                if "-a" in argv:
                    listed = {**listed, **self.stopped}
                matched = sorted(name for name in listed if re.search(pattern, name))
                return 0, "\n".join(matched).encode(), b""
            case "ps" if "-aq" in argv:
                wanted = argv[argv.index("--filter") + 1].removeprefix("name=^").removesuffix("$")
                pools = (
                    (self.stopped,)
                    if "status=exited" in argv
                    else (self.running, self.paused, self.stopped)
                )
                found = next((pool[wanted] for pool in pools if wanted in pool), "")
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
                self._move(argv[1], self.paused, self.stopped)
                return 0, b"", b""
            case "start":
                if self._move(argv[1], self.stopped, self.running):
                    return 0, f"{argv[1]}\n".encode(), b""
                return 1, b"", f"Error: no such container: {argv[1]}".encode()
            case "exec":
                offset = 1
                while argv[offset].startswith("-"):
                    offset += 2 if argv[offset] in {"--env", "--workdir"} else 1
                container = argv[offset]
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
                if self.subnets_exhausted:
                    return 1, b"", b"all predefined address pools have been fully subnetted"
                self.networks.add(argv[2])
                return 0, b"", b""
            case "network" if argv[1] == "connect":
                if self.connect_conflict:
                    detail = f"endpoint with name x already exists in network {argv[2]}"
                    return 1, b"", detail.encode()
                return 0, b"", b""
            case "network" if argv[1] == "rm":
                if argv[2] not in self.networks:
                    return 1, b"", f"network {argv[2]} not found".encode()
                self.networks.discard(argv[2])
                return 0, b"", b""
            case "network" if argv[1] == "ls":
                pattern = argv[argv.index("--filter") + 1].removeprefix("name=")
                matched = sorted(name for name in self.networks if re.search(pattern, name))
                return 0, "\n".join(matched).encode(), b""
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
    the real one answers; the mapping under test is the carrier's and the seam's own. The workspace
    directory is made first, as a turn's own open would: a read attaches only to a conversation
    directory it can prove under the root, and never provisions one."""
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
            case "exec" if any("command -v ufo" in arg for arg in argv):
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
    (tmp_path / "workspaces" / str(conversation_id)).mkdir(parents=True)

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


async def test_a_revive_never_interleaves_inside_a_reclaims_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifecycle lock orders reclaim's release whole against every revive: a mid-turn exec
    arriving while the release sits between its stop and its network removal waits, then
    reconnects and starts — the interleaving that would leave a running container with no
    network."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(quiet))
    network = f"ufo-sandbox-{quiet.hex}"
    now = docker_ext.IDLE_RECLAIM_SECONDS

    mid_stop = asyncio.Event()
    release = asyncio.Event()
    original = daemon.__class__.__call__

    async def stall_stop(self, *argv, stdin=b"", timeout_s=60):
        result = await original(self, *argv, stdin=stdin, timeout_s=timeout_s)
        if argv[0] == "stop":
            mid_stop.set()
            await release.wait()
        return result

    monkeypatch.setattr(daemon.__class__, "__call__", stall_stop)
    reclaiming = asyncio.ensure_future(carrier.create(_reclaim_spec(uuid4())))
    await mid_stop.wait()
    reviving = asyncio.ensure_future(carrier.exec(handle, ("bash", "-lc", "true"), 30))
    await asyncio.sleep(0)
    release.set()

    result = await reviving
    await reclaiming

    assert result.exit_code == 0
    assert f"{docker_ext.CONTAINER_NAME_PREFIX}{quiet}" in daemon.running
    assert network in daemon.networks
    removed_at = max(
        index for index, argv in enumerate(daemon.calls) if argv[:3] == ("network", "rm", network)
    )
    connect_at = max(
        index for index, argv in enumerate(daemon.calls) if argv[:2] == ("network", "connect")
    )
    start_at = max(index for index, argv in enumerate(daemon.calls) if argv[0] == "start")
    assert removed_at < connect_at < start_at


async def test_attach_answers_none_when_the_daemon_cannot_give_the_network_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read path promises absence, never an error: a revive whose network cannot come back —
    the daemon's pools exhausted — raises, and attach alone converts that to None."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(quiet))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    daemon.subnets_exhausted = True

    assert await carrier.attach(_reclaim_spec(quiet)) is None


async def test_a_failed_network_release_is_retried_by_the_next_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that stopped the container but could not free the network keeps the stale entry,
    so the next create retries the whole release instead of leaving the subnet held forever."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    stuck = uuid4()
    network = f"ufo-sandbox-{stuck.hex}"
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(stuck))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    original = daemon.__class__.__call__

    async def rm_fails(self, *argv, stdin=b"", timeout_s=60):
        if argv[:2] == ("network", "rm") and argv[2] == network:
            return 1, b"", b"network has active endpoints"
        return await original(self, *argv, stdin=stdin, timeout_s=timeout_s)

    monkeypatch.setattr(daemon.__class__, "__call__", rm_fails)
    await carrier.create(_reclaim_spec(uuid4()))
    assert stuck in carrier._touched
    assert network in daemon.networks

    monkeypatch.setattr(daemon.__class__, "__call__", original)
    await carrier.create(_reclaim_spec(uuid4()))
    assert network not in daemon.networks


class _FakeCat:
    """One `docker exec … cat` child as CPython's subprocess layer actually behaves: real
    StreamReaders, a returncode that appears on exit, a kill flag the early-close test reads, and
    `lingering` models the child whose pipes closed a beat before its exit was observed."""

    def __init__(
        self, out: bytes, err: bytes, code: int, exited: bool = True, lingering: bool = False
    ) -> None:
        self.stdout = asyncio.StreamReader()
        if out:
            self.stdout.feed_data(out)
        if exited or lingering:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        if err:
            self.stderr.feed_data(err)
        self.stderr.feed_eof()
        self._code = code
        self.returncode: int | None = code if exited else None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        self.stdout.feed_eof()

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        out = await self.stdout.read()
        err = await self.stderr.read()
        await self.wait()
        return out, err


async def test_a_release_is_terminal_never_readopted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A released conversation holds neither memory nor a subnet, appears in neither scan, and so
    is never adopted or released twice — the touch map stays bounded by what actually holds a
    resource."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    network = f"ufo-sandbox-{quiet.hex}"
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(quiet))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    assert quiet not in carrier._touched

    now = docker_ext.IDLE_RECLAIM_SECONDS * 3
    await carrier.create(_reclaim_spec(uuid4()))

    assert quiet not in carrier._touched
    removals = [argv for argv in daemon.calls if argv[:3] == ("network", "rm", network)]
    assert len(removals) == 1


async def test_a_mid_turn_revive_surfaces_the_daemon_fault_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exec whose revive meets a daemon that cannot give the network back fails with that
    fault, never with the stopped container's own is-not-running answer standing in for it."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(quiet))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    daemon.subnets_exhausted = True

    with pytest.raises(RuntimeError, match="subnetted"):
        await carrier.exec(handle, ("bash", "-lc", "true"), 30)


async def test_create_keeps_the_stopped_container_on_a_daemon_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create whose revive meets a daemon fault raises without removing the container — the
    fault is the daemon's, and a fresh run would fail on the same exhausted pools, so destroying
    the workspace's container buys nothing."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    quiet = uuid4()
    name = f"{docker_ext.CONTAINER_NAME_PREFIX}{quiet}"
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(quiet))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))
    daemon.subnets_exhausted = True

    with pytest.raises(RuntimeError, match="subnetted"):
        await carrier.create(_reclaim_spec(quiet))

    assert name in daemon.stopped


async def test_a_drained_read_never_kills_the_cat(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker exec` closes its pipes a beat before it exits, so a fully drained stream can find
    the exit status not yet observed. Killing there reaps the status away from the loop's watcher
    and the wait answers 255 — a successful read turned into a phantom FileNotFoundError, the CI
    failure this pins. The kill belongs to abandonment alone."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    carrier = DockerCarrier(clock=lambda: now)
    handle = await carrier.create(_reclaim_spec(uuid4()))
    content = b"whole and intact"
    cat = _FakeCat(content, b"", 0, exited=False, lingering=True)

    async def fake_cat(*argv, stdin=None, stdout=None, stderr=None):
        return cat

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_cat)
    streamed = b"".join([chunk async for chunk in carrier.read(handle, "/workspace/report.txt")])

    assert streamed == content
    assert not cat.killed


async def test_the_scan_admits_only_exact_uuid_container_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon-side filter is the whole tolerance for foreign names: a container whose name is
    36 hex-and-dash characters but no UUID must never reach `UUID()` — reaching it would fail
    every create on the host — and is never adopted or stopped."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    imposter = f"{docker_ext.CONTAINER_NAME_PREFIX}0123456789abcdef0123456789abcdef0---"
    daemon.start(imposter)
    carrier = DockerCarrier(clock=lambda: now)

    await carrier.create(_reclaim_spec(uuid4()))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    await carrier.create(_reclaim_spec(uuid4()))

    assert imposter in daemon.running


def _scan_ps(argv: tuple[str, ...]) -> bool:
    return argv[0] == "ps" and "--format" in argv


def _scan_network_ls(argv: tuple[str, ...]) -> bool:
    return argv[:2] == ("network", "ls") and "--format" in argv


def _resolve_any_state(argv: tuple[str, ...]) -> bool:
    return argv[0] == "ps" and "-aq" in argv and "status=exited" not in argv


def _resolve_exited(argv: tuple[str, ...]) -> bool:
    return argv[0] == "ps" and "-aq" in argv and "status=exited" in argv


@pytest.mark.parametrize(
    ("fails", "message"),
    [
        (_scan_ps, "docker ps failed"),
        (_scan_network_ls, "docker network ls failed"),
        (_resolve_any_state, "docker ps failed"),
        (_resolve_exited, "docker ps failed"),
    ],
)
async def test_a_failed_daemon_listing_fails_the_create_loudly(
    monkeypatch: pytest.MonkeyPatch,
    fails: Callable[[tuple[str, ...]], bool],
    message: str,
) -> None:
    """Every daemon listing a create leans on fails loud. Reclaim cannot tell idle from invisible
    when a scan fails, a swallowed stale resolve would release the network out from under a
    running container and drop its entry, and a swallowed exited resolve would run a fresh
    container over a name a stopped one still holds."""
    now = 0.0
    daemon = _FakeDaemon()
    monkeypatch.setattr(docker_ext, "_docker", daemon)
    carrier = DockerCarrier(clock=lambda: now)
    await carrier.create(_reclaim_spec(uuid4()))
    now = docker_ext.IDLE_RECLAIM_SECONDS
    original = daemon.__class__.__call__

    async def listing_fails(self, *argv, stdin=b"", timeout_s=60):
        if fails(argv):
            return 1, b"", b"daemon wedged"
        return await original(self, *argv, stdin=stdin, timeout_s=timeout_s)

    monkeypatch.setattr(daemon.__class__, "__call__", listing_fails)

    with pytest.raises(RuntimeError, match=message):
        await carrier.create(_reclaim_spec(uuid4()))
