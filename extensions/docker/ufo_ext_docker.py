"""The Docker carrier extension: a per-conversation container reached only through the egress proxy.

Core's default is the local carrier; a deploy that sets `[sandbox] backend = "docker"` runs its
sandboxes as sibling containers. `/workspace` is a host bind mount, so this carrier reclaims its
own idle containers by stopping them on each create — memory and bridge subnets are the contended
resources, so the stop releases the conversation's network with it, and a stopped container with
its workspace persists for any later touch to reconnect and start again: a conversation whose
turn was merely quiet survives its own reclaim at the cost of one restart. Core
reclaims nothing, because for a carrier whose `/workspace` lives inside its sandbox the container
*is* the workspace. Every command runs through `docker exec`
under its turn's egress env: HTTP(S)_PROXY points at the egress proxy running on the host, reached
at `host.docker.internal`, and carries the turn's run token as its basic-auth username so the proxy
attributes each metered request to the turn; the proxy refuses any host its rules do not allow and
swaps the sentinel for the real key on the wire, so the raw credential never enters the sandbox.
The env is per-exec, never baked into the container — a container outlives its first turn, and a
later turn must not run under an earlier turn's token."""

import asyncio
import errno
import os
import time
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from uuid import UUID

from ufo.sdk.manifest import Manifest
from ufo.sdk.sandbox import (
    COPY_IN_PROG,
    NO_PROXY_HOSTS,
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    CarrierSpec,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
    sandbox_runtime_root,
    ufo_fs_file_op,
)

CARRIER_NAME = "docker"
CONTAINER_NAME_PREFIX = "ufo-sbx-"
CREATE_TIMEOUT_SECONDS = 120
WRITE_TIMEOUT_SECONDS = 30
RUNTIME_ROOT_TIMEOUT_SECONDS = 30
READ_CHUNK_BYTES = 1024 * 1024
IDLE_RECLAIM_SECONDS = 1800
NAME_CONFLICT_MARKER = "is already in use"
NOT_RUNNING_MARKER = "is not running"
REASON_ERRNO: dict[str, int] = {os.strerror(code): code for code in errno.errorcode}
NETWORK_EXISTS_MARKER = "already exists"
INSPECT_TIMEOUT_SECONDS = 10
NO_SUCH_NETWORK_MARKER = "not found"
STOP_TIMEOUT_SECONDS = 30
START_TIMEOUT_SECONDS = 30
UUID_NAME_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
DEFAULT_NETWORK = "ufo-sandbox"
HOST_GATEWAY_NAME = "host.docker.internal"
HOST_GATEWAY_MAPPING = f"{HOST_GATEWAY_NAME}:host-gateway"
DROP_NET_RAW_ARGS = ("--cap-drop", "NET_RAW")
CA_SANDBOX_PATH = "/usr/local/share/ca-certificates/ufo-proxy.crt"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
EXEC_TIMEOUT_CODE = 124
TIMED_OUT_CODE = -1000
"""`_docker`'s own deadline, told apart from every code a process can report: an exit status is
0-255 and a signal death is negative down to the highest signal number, so no command reaches it.
A caller that only asks whether the code is zero reads a timeout as the failure it is; `exec`
translates it to the shell's own timeout code and names the deadline that produced it."""


async def _docker(*argv: str, stdin: bytes = b"", timeout_s: int = 60) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        "docker",
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        return TIMED_OUT_CODE, b"", b"timed out"
    return process.returncode or 0, stdout, stderr


@dataclass(frozen=True)
class DockerCarrier:
    network: str = DEFAULT_NETWORK
    clock: Callable[[], float] = time.monotonic
    _touched: dict[UUID, float] = field(default_factory=dict)
    _inflight: Counter[UUID] = field(default_factory=Counter)
    _lifecycle: defaultdict[UUID, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Create-or-attach the conversation's container. The egress env is never baked into the
        container — a container outliving its first turn must not pin that turn's run token — it
        rides the returned handle and every exec carries it, so each turn's commands run under its
        own token and sentinel entries.

        The daemon arbitrates concurrent creates for one conversation: both race `docker run` under
        the same deterministic name, the loser's run answers a name conflict, and the loser attaches
        to the winner's container — nothing here holds a lock, and no second container ever exists.
        The CA installs on every path out, not only the fresh run: a container outliving a serve
        restart still trusts the dead process's proxy CA, and the local proxy mints a fresh one per
        process, so every request from a reused container would fail TLS until the new CA lands."""
        await self._reclaim_idle(spec.conversation_id)
        name = f"{CONTAINER_NAME_PREFIX}{spec.conversation_id}"
        proxy_url = f"http://{spec.run_token}:@{HOST_GATEWAY_NAME}:{spec.proxy.port}"
        egress_env = {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": NO_PROXY_HOSTS,
            "no_proxy": NO_PROXY_HOSTS,
            "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
            "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
            # Point the toolchains that ignore the system trust store at the CA `_install_ca` merges
            # in, so a MITM'd host (a cache-fronted registry included) is trusted by pip, requests,
            # curl, and Node alike — not only by the tools that read `/etc/ssl`.
            "SSL_CERT_FILE": SYSTEM_CA_BUNDLE,
            "REQUESTS_CA_BUNDLE": SYSTEM_CA_BUNDLE,
            "CURL_CA_BUNDLE": SYSTEM_CA_BUNDLE,
            "NODE_EXTRA_CA_CERTS": CA_SANDBOX_PATH,
            **spec.env,
        }
        running = await self._running_id(name)
        if running is not None:
            await self._install_ca(running, spec.proxy.ca_cert)
            await self._ensure_runtime_root(running, spec.conversation_id)
            return SandboxHandle(
                conversation_id=spec.conversation_id,
                container_id=running,
                workspace_host_path=spec.workspace_host_path,
                run_token=spec.run_token,
                runtime_root=sandbox_runtime_root(spec.conversation_id),
                egress_env=egress_env,
            )
        stopped = await self._stopped_id(name)
        if stopped is not None and await self._revive(spec.conversation_id, stopped):
            await self._install_ca(stopped, spec.proxy.ca_cert)
            await self._ensure_runtime_root(stopped, spec.conversation_id)
            return SandboxHandle(
                conversation_id=spec.conversation_id,
                container_id=stopped,
                workspace_host_path=spec.workspace_host_path,
                run_token=spec.run_token,
                runtime_root=sandbox_runtime_root(spec.conversation_id),
                egress_env=egress_env,
            )
        if stopped is not None:
            await _docker("rm", "-f", stopped)
        network = self._network_name(spec.conversation_id)
        await self._ensure_network(network)
        argv = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--add-host",
            HOST_GATEWAY_MAPPING,
            *DROP_NET_RAW_ARGS,
            "-v",
            f"{spec.workspace_host_path}:{WORKSPACE_DIR}",
            spec.image_ref,
            "sleep",
            "infinity",
        ]
        container_id: str | None = None
        try:
            code, stdout, stderr = await _docker(*argv, timeout_s=CREATE_TIMEOUT_SECONDS)
            if code != 0:
                detail = stderr.decode().strip()
                if NAME_CONFLICT_MARKER in detail:
                    winner = await self._running_id(name)
                    if winner is not None:
                        await self._install_ca(winner, spec.proxy.ca_cert)
                        await self._ensure_runtime_root(winner, spec.conversation_id)
                        return SandboxHandle(
                            conversation_id=spec.conversation_id,
                            container_id=winner,
                            workspace_host_path=spec.workspace_host_path,
                            run_token=spec.run_token,
                            runtime_root=sandbox_runtime_root(spec.conversation_id),
                            egress_env=egress_env,
                        )
                raise RuntimeError(f"docker run failed: {detail}")
            container_id = stdout.decode().strip()
            await self._install_ca(container_id, spec.proxy.ca_cert)
            await self._ensure_runtime_root(container_id, spec.conversation_id)
            return SandboxHandle(
                conversation_id=spec.conversation_id,
                container_id=container_id,
                workspace_host_path=spec.workspace_host_path,
                run_token=spec.run_token,
                runtime_root=sandbox_runtime_root(spec.conversation_id),
                egress_env=egress_env,
            )
        except BaseException:
            if container_id is not None:
                await _docker("rm", "-f", container_id)
            await _docker("network", "rm", network)
            raise

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The conversation's container when one exists — started again if reclaim stopped it, the
        same shape as resuming a provider-paused sandbox — else None, never a fresh one, and None
        again whenever the revive cannot deliver a running container, refused or raising: a read
        promises absence, never an error. No egress env: a read runs `ufo fs` and `cat`, nothing
        that leaves the box."""
        name = f"{CONTAINER_NAME_PREFIX}{spec.conversation_id}"
        running = await self._running_id(name)
        if running is None:
            stopped = await self._stopped_id(name)
            if stopped is None:
                return None
            try:
                revived = await self._revive(spec.conversation_id, stopped)
            except RuntimeError:
                return None
            if not revived:
                return None
            running = stopped
        try:
            await self._ensure_runtime_root(running, spec.conversation_id)
        except RuntimeError:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=running,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            runtime_root=sandbox_runtime_root(spec.conversation_id),
        )

    async def _reclaim_idle(self, opening: UUID) -> None:
        """Release the conversations not touched for `IDLE_RECLAIM_SECONDS`, adopting any this
        process does not know: the `ufo-sbx-*` containers the daemon lists as up — running or
        paused, both holding memory — and the per-conversation networks (the bridge subnets), all
        left by a process that restarted or by this one's own crash. Release is terminal — a
        stopped container holds neither resource and appears in neither scan, so nothing is ever
        adopted twice — and each conversation's touch entry leaves with its release, so the map
        stays bounded by what actually holds a resource.

        A stop, never a removal: the carrier has no view of turn liveness, so a conversation whose
        turn is merely quiet — long reasoning, a round of subagent orchestration — must survive its
        own reclaim. The container and its bind-mounted workspace persist, and every later touch —
        the next create, a mid-turn exec/write/read, a browse — starts it again and continues. The
        worst a wrong reclaim can cost is one restart, never a failed tool call.

        A create is the trigger rather than an interval because the unit of idleness here is a
        resource on this host, not a workspace — the shape a scheduled job fans out over. It is a
        safe trigger: the conversation being opened is touched before anything else, so this never
        fires on its own work. Concurrent creates race this scan, so each candidate's staleness is
        re-checked after every await (a touch that landed meanwhile wins), the synchronous delete
        before the release is the reservation that makes the release exactly-once, the lifecycle
        lock orders it whole against any revive, and a release that failed anywhere puts the stale
        entry back so the next create retries it instead of leaving a subnet held with no
        record."""
        self._touched[opening] = self.clock()
        code, stdout, stderr = await _docker(
            "ps",
            "--filter",
            f"name=^{CONTAINER_NAME_PREFIX}{UUID_NAME_PATTERN}$",
            "--format",
            "{{.Names}}",
        )
        if code != 0:
            raise RuntimeError(f"docker ps failed: {stderr.decode().strip()}")
        held = [(stdout, CONTAINER_NAME_PREFIX)]
        code, stdout, stderr = await _docker(
            "network",
            "ls",
            "--filter",
            f"name=^{self.network}-[0-9a-f]{{32}}$",
            "--format",
            "{{.Name}}",
        )
        if code != 0:
            raise RuntimeError(f"docker network ls failed: {stderr.decode().strip()}")
        held.append((stdout, f"{self.network}-"))
        for listing, prefix in held:
            for name in listing.decode().split():
                self._touched.setdefault(UUID(name.removeprefix(prefix)), self.clock())
        stale = [
            conversation_id
            for conversation_id, touched in self._touched.items()
            if not self._inflight[conversation_id]
            and self.clock() - touched >= IDLE_RECLAIM_SECONDS
        ]
        for conversation_id in stale:
            container_id = await self._held_id(f"{CONTAINER_NAME_PREFIX}{conversation_id}")
            touched = self._touched.get(conversation_id)
            if (
                touched is None
                or self._inflight[conversation_id]
                or self.clock() - touched < IDLE_RECLAIM_SECONDS
            ):
                continue
            del self._touched[conversation_id]
            async with self._lifecycle[conversation_id]:
                released = await self._release(conversation_id, container_id)
            if not released:
                self._touched.setdefault(conversation_id, touched)

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """A call in flight pins its container: the in-flight count parks the conversation outside
        reclaim's reach for exactly the call's duration, whatever that duration is, and the
        completion stamp then grants a full idle span after it — so a container is stoppable only
        when genuinely between calls."""
        env_args = tuple(
            arg for name, value in handle.egress_env.items() for arg in ("--env", f"{name}={value}")
        )
        return await self._exec_with(handle, argv, timeout_s, env_args)

    async def exec_skill(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run a server-carried skill load or sync as root."""
        return await self._exec_with(handle, argv, timeout_s, ("--user", "root"))

    async def _exec_with(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        options: tuple[str, ...],
    ) -> ExecResult:
        self._inflight[handle.conversation_id] += 1
        self._touched[handle.conversation_id] = self.clock()
        try:
            code, stdout, stderr = await _docker(
                "exec",
                "--workdir",
                WORKSPACE_DIR,
                *options,
                handle.container_id,
                *argv,
                timeout_s=timeout_s,
            )
            if code != 0 and NOT_RUNNING_MARKER in stderr.decode(errors="replace"):
                if await self._revive(handle.conversation_id, handle.container_id):
                    code, stdout, stderr = await _docker(
                        "exec",
                        "--workdir",
                        WORKSPACE_DIR,
                        *options,
                        handle.container_id,
                        *argv,
                        timeout_s=timeout_s,
                    )
            timed_out = code == TIMED_OUT_CODE
            return ExecResult(
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                exit_code=EXEC_TIMEOUT_CODE if timed_out else code,
                timed_out_after_s=timeout_s if timed_out else None,
            )
        finally:
            self._inflight[handle.conversation_id] -= 1
            self._touched[handle.conversation_id] = self.clock()

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """`docker exec -i` gives the container a real stdin, so the bytes stream in over it and
        never enter the command line — into the guard's own copy-in program, which the image bakes
        the containment module beside. A shell `mkdir -p && cat > "$1"` follows a symlinked ancestor
        and truncates through a planted link, and the container is the only thing scoping the path:
        the name comes from a tool argument or an inbound attachment, and the agent writes the
        directory it lands in."""
        self._inflight[handle.conversation_id] += 1
        self._touched[handle.conversation_id] = self.clock()
        try:
            code, stderr = await self._write_started(handle, path, content)
            if code != 0 and NOT_RUNNING_MARKER in stderr.decode(errors="replace"):
                if await self._revive(handle.conversation_id, handle.container_id):
                    code, stderr = await self._write_started(handle, path, content)
            if code != 0:
                raise OSError(stderr.decode(errors="replace").strip() or f"write failed: {path}")
        finally:
            self._inflight[handle.conversation_id] -= 1
            self._touched[handle.conversation_id] = self.clock()

    async def _write_started(
        self, handle: SandboxHandle, path: str, content: bytes
    ) -> tuple[int, bytes]:
        root = (
            handle.runtime_root
            if handle.runtime_root and PurePosixPath(path).is_relative_to(handle.runtime_root)
            else WORKSPACE_DIR
        )
        code, _, stderr = await _docker(
            "exec",
            "-i",
            handle.container_id,
            "python3",
            SANDBOX_PYTHON_FLAG,
            "-c",
            f"{SANDBOX_MODULE_BOOTSTRAP}{COPY_IN_PROG}",
            path,
            root,
            stdin=content,
            timeout_s=WRITE_TIMEOUT_SECONDS,
        )
        return code, stderr

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """`docker exec` gives the container a real stdout, so the bytes stream out over it in
        bounded chunks and the host process never holds the file whole. `cat` is run through the
        container rather than off the bind mount so a caller reads what the sandbox sees, whatever
        the carrier's storage happens to be. A failure names its true cause: cat's reason segment
        (stderr's last line after its final separator, never the whole message, so a caller-chosen
        filename cannot impersonate a reason) resolves through strerror to the OSError its errno
        names — the same class and errno the local carrier's open() raises for the same path — a
        container stopped before the first byte revives and re-streams from the start, and
        anything else — including a `cat` killed mid-stream, which exits non-zero with empty
        stderr — raises with the exit code and the container's own reported state rather than
        masquerading as a filesystem refusal."""
        self._inflight[handle.conversation_id] += 1
        self._touched[handle.conversation_id] = self.clock()
        chunks, detail = self._read_started(handle, path)
        try:
            async for chunk in chunks:
                yield chunk
            if detail and NOT_RUNNING_MARKER in detail[0][1]:
                if await self._revive(handle.conversation_id, handle.container_id):
                    chunks, detail = self._read_started(handle, path)
                    async for chunk in chunks:
                        yield chunk
            if detail:
                code, stderr_text = detail[0]
                reason = stderr_text.splitlines()[-1].rsplit(": ", 1)[-1] if stderr_text else ""
                refused = REASON_ERRNO.get(reason)
                if refused is not None:
                    raise OSError(refused, stderr_text)
                cause = f": {stderr_text}" if stderr_text else await self._death_report(handle)
                raise RuntimeError(f"read of {path} died: cat exited {code}{cause}")
        finally:
            await chunks.aclose()
            self._inflight[handle.conversation_id] -= 1
            self._touched[handle.conversation_id] = self.clock()

    def _read_started(
        self, handle: SandboxHandle, path: str
    ) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]:
        """One `cat` attempt: the chunk stream, and a failure list one `(exit_code, stderr)` lands
        in after the stream is drained — empty on success. Split so `read` can revive a stopped
        container and re-stream without an async generator ever crossing its own retry. The drained
        path only ever WAITS: after stdout's EOF the child's exit status can be unreaped, and
        signalling it there reaps the real status out from under the loop's watcher
        (`Popen.send_signal` polls first) and reports asyncio's 255 placeholder — a successful
        read classified as a death. The kill belongs solely to the abandoned path, where a caller
        broke off mid-stream and the exec must not outlive its generator; the reap there is
        `communicate`, which drains both pipes to EOF first — a paused stdout transport never
        disconnects, and `wait` alone would wait on that disconnect forever."""
        failure: list[tuple[int, str]] = []

        async def stream() -> AsyncGenerator[bytes]:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                handle.container_id,
                "cat",
                path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout = process.stdout
            stderr = process.stderr
            if stdout is None or stderr is None:
                raise RuntimeError("docker exec opened no pipes")
            try:
                while chunk := await stdout.read(READ_CHUNK_BYTES):
                    yield chunk
                detail = (await stderr.read()).decode(errors="replace").strip()
                code = await process.wait()
                if code != 0:
                    failure.append((code, detail))
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()

        return stream(), failure

    async def _death_report(self, handle: SandboxHandle) -> str:
        """The container's own reported state, appended to an empty-stderr read death so the next
        investigation starts from facts. The fields are the CONTAINER's — a dead exec leaves no
        state of its own, so a running container here means the exec alone was killed or its exit
        status was lost, while an exited or absent container names a stop, an OOM of the main
        process, or a removal — the report states provenance and lets the fields speak. A failed
        inspect reports its own exit and stderr rather than answering a lifecycle claim it could
        not establish."""
        code, stdout, stderr = await _docker(
            "inspect",
            "--format",
            "{{.State.Status}} exit={{.State.ExitCode}} oom-killed={{.State.OOMKilled}}",
            handle.container_id,
            timeout_s=INSPECT_TIMEOUT_SECONDS,
        )
        if code != 0:
            inspected = stderr.decode(errors="replace").strip()
            return f" with no stderr — docker inspect exited {code}: {inspected}"
        return f" with no stderr — its container reports: {stdout.decode().strip()}"

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """The image bakes the `ufo` client, so a file op is `ufo fs` run through `exec` — which
        pins the container for the op's duration and revives a stopped one, exactly as a bash
        command."""
        return await ufo_fs_file_op(self, handle, op, params)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The docker carrier publishes no per-port host, so an in-sandbox service (a browser's CDP
        endpoint, a site's dev-server preview) has no external route here — a deploy that reaches an
        in-sandbox port needs a remote carrier (e2b)."""
        raise SandboxUnreachable(
            "the docker carrier exposes no external per-port host; reach an in-sandbox service "
            "through a remote carrier (e2b)"
        )

    async def _release(self, conversation_id: UUID, container_id: str | None) -> bool:
        """Stop the container and free its network, reporting whether everything released — bridge
        subnets are the host's finite resource (the daemon's default pools hold ~30), so a release
        that failed anywhere is retried by the next create rather than leaving a subnet held with
        no record. Runs under the conversation's lifecycle lock, so a revive can never interleave
        between the stop and the removal and be left running with no network. The stop is what
        frees the endpoint: the daemon removes a network out from under a stopped container, even
        one a revive connected while stopped (measured), so no disconnect is needed, and a network
        already gone is the state the removal was after."""
        if container_id is not None:
            code, _, _ = await _docker("stop", container_id, timeout_s=STOP_TIMEOUT_SECONDS)
            if code != 0:
                return False
        code, _, stderr = await _docker("network", "rm", self._network_name(conversation_id))
        return code == 0 or NO_SUCH_NETWORK_MARKER in stderr.decode()

    async def _revive(self, conversation_id: UUID, container_id: str) -> bool:
        """Start a container reclaim stopped, reporting whether it runs again — the recovery every
        touch of a stopped container shares: the next create, a mid-turn exec/write/read whose
        container was stopped underneath it, a browse. Reclaim released the conversation's network
        with the stop, so the revive re-ensures it and reconnects before starting; a connect
        answering already-connected is the state it was after. False — the connect or the start
        refused, the shape of a container removed or corrupted out of band — leaves the caller's
        own failure to surface; a daemon that cannot give the network back raises instead, and
        `attach` alone converts that to absence, because only the read path promises None over an
        error. The conversation's lifecycle lock orders
        this whole sequence against reclaim's stop-and-release, so neither ever interleaves inside
        the other."""
        async with self._lifecycle[conversation_id]:
            self._touched[conversation_id] = self.clock()
            network = self._network_name(conversation_id)
            await self._ensure_network(network)
            code, _, stderr = await _docker("network", "connect", network, container_id)
            if code != 0 and NETWORK_EXISTS_MARKER not in stderr.decode():
                return False
            code, _, _ = await _docker("start", container_id, timeout_s=START_TIMEOUT_SECONDS)
            return code == 0

    async def _held_id(self, name: str) -> str | None:
        """The id holding `name` in any state — running, paused, exited — else None: what a release
        must stop before its network can go, whatever state reclaim finds it in (`docker stop`
        handles a paused container, measured)."""
        code, stdout, stderr = await _docker("ps", "-aq", "--filter", f"name=^{name}$")
        if code != 0:
            raise RuntimeError(f"docker ps failed: {stderr.decode().strip()}")
        found = stdout.decode().strip()
        return found or None

    async def _stopped_id(self, name: str) -> str | None:
        """The id of `name`'s exited container, else None — what reclaim leaves behind, and what a
        later open starts again."""
        code, stdout, stderr = await _docker(
            "ps", "-aq", "--filter", f"name=^{name}$", "--filter", "status=exited"
        )
        if code != 0:
            raise RuntimeError(f"docker ps failed: {stderr.decode().strip()}")
        found = stdout.decode().strip()
        return found or None

    async def _running_id(self, name: str) -> str | None:
        """A genuine no-match is exit 0 with empty stdout — `docker ps` only returns non-zero when
        the command itself failed (daemon hiccup, timeout). Treating that failure as "not running"
        would send `create` down the fresh-create path against a name a live container still holds,
        surfacing as a misleading name conflict instead of the real transient fault."""
        code, stdout, stderr = await _docker(
            "ps", "-q", "--filter", f"name=^{name}$", "--filter", "status=running"
        )
        if code != 0:
            raise RuntimeError(f"docker ps failed: {stderr.decode().strip()}")
        found = stdout.decode().strip()
        return found or None

    def _network_name(self, conversation_id: UUID) -> str:
        return f"{self.network}-{conversation_id.hex}"

    async def _ensure_network(self, network: str) -> None:
        """Idempotent under the same race the container name is: two creates for one conversation
        both miss the ls and both run `network create`; the daemon arbitrates the name, and the
        loser's already-exists answer is the success it was after."""
        code, stdout, stderr = await _docker("network", "ls", "-q", "--filter", f"name=^{network}$")
        if code != 0:
            raise RuntimeError(f"docker network ls failed: {stderr.decode().strip()}")
        if stdout.strip():
            return
        code, _, stderr = await _docker("network", "create", network)
        if code != 0 and NETWORK_EXISTS_MARKER not in stderr.decode():
            raise RuntimeError(f"docker network create failed: {stderr.decode().strip()}")

    async def _install_ca(self, container_id: str, ca_cert: str) -> None:
        write = await _docker(
            "exec",
            "-i",
            "-u",
            "root",
            container_id,
            "sh",
            "-c",
            "cat > /usr/local/share/ca-certificates/ufo-proxy.crt && update-ca-certificates",
            stdin=ca_cert.encode(),
        )
        if write[0] != 0:
            raise RuntimeError(f"CA install failed: {write[2].decode().strip()}")

    async def _ensure_runtime_root(self, container_id: str, conversation_id: UUID) -> None:
        code, _, stderr = await _docker(
            "exec",
            container_id,
            "mkdir",
            "-p",
            sandbox_runtime_root(conversation_id),
            timeout_s=RUNTIME_ROOT_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise RuntimeError(f"runtime root creation failed: {stderr.decode().strip()}")


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=DockerCarrier),),
    )
