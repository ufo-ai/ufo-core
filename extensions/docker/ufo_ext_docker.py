"""The Docker carrier extension: a per-conversation container reached only through the egress proxy.

Core's default is the local carrier. Docker containers use host-mounted workspaces and isolated
conversation networks. The carrier stops unpinned containers to bound its warm network cache and
release idle subnets. A later operation reconnects and starts the retained container.
Every command runs through `docker exec`
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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from uuid import UUID

from ufo.sdk.manifest import Manifest
from ufo.sdk.sandbox import (
    NO_PROXY_HOSTS,
    PROXY_PASSWORD,
    SANDBOX_GID,
    SANDBOX_UID,
    SENTINEL_MODEL_KEY,
    SYSTEM_CA_BUNDLE,
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
COPY_IN_SCRIPT = (
    "set -e\n"
    'd="$(dirname "$1")"\n'
    'mkdir -p "$d"\n'
    't="$d/.ufo-staged-$$"\n'
    "trap 'rm -f \"$t\"' EXIT\n"
    'cat > "$t"\n'
    'if [ -e "$1" ]; then chmod --reference="$1" "$t"; else chmod 644 "$t"; fi\n'
    'mv -f -T "$t" "$1"\n'
)
RUNTIME_ROOT_TIMEOUT_SECONDS = 30
READ_CHUNK_BYTES = 1024 * 1024
IDLE_RECLAIM_SECONDS = 1800
WARM_NETWORK_LIMIT = 16
NAME_CONFLICT_MARKER = "is already in use"
NOT_RUNNING_MARKER = "is not running"
REASON_ERRNO: dict[str, int] = {os.strerror(code): code for code in errno.errorcode}
NETWORK_EXISTS_MARKER = "already exists"
INSPECT_TIMEOUT_SECONDS = 10
NO_SUCH_NETWORK_MARKER = "not found"
STOP_TIMEOUT_SECONDS = 30
START_TIMEOUT_SECONDS = 30
DEFAULT_NETWORK = "ufo-sandbox"
HOST_GATEWAY_NAME = "host.docker.internal"
HOST_GATEWAY_MAPPING = f"{HOST_GATEWAY_NAME}:host-gateway"
DROP_NET_RAW_ARGS = ("--cap-drop", "NET_RAW")
DOCKER_CA_PATH = "/usr/local/share/ca-certificates/ufo-proxy.crt"
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
    _allocation: asyncio.Lock = field(default_factory=asyncio.Lock)
    _lifecycle: defaultdict[UUID, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Create or attach the conversation's container and install the proxy CA.

        Egress credentials stay in the returned handle. Each exec uses its turn's token and
        sentinel entries. Installing the CA on reused containers keeps TLS bound to this proxy.
        Docker's deterministic container name resolves concurrent creates to one container."""
        async with self._pin(spec.conversation_id):
            return await self._create(spec)

    async def _create(self, spec: SandboxSpec) -> SandboxHandle:
        name = f"{CONTAINER_NAME_PREFIX}{spec.conversation_id}"
        proxy_url = (
            f"http://{spec.run_token}:{PROXY_PASSWORD}@{HOST_GATEWAY_NAME}:{spec.proxy.port}"
        )
        egress_env = {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": NO_PROXY_HOSTS,
            "no_proxy": NO_PROXY_HOSTS,
            "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
            "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
            "SSL_CERT_FILE": SYSTEM_CA_BUNDLE,
            "REQUESTS_CA_BUNDLE": SYSTEM_CA_BUNDLE,
            "CURL_CA_BUNDLE": SYSTEM_CA_BUNDLE,
            "NODE_EXTRA_CA_CERTS": DOCKER_CA_PATH,
            **spec.env,
        }
        running = await self._running_id(name)
        if running is not None:
            await self._install_ca(running, spec.proxy.ca_cert)
            await self._prepare_mounts(running, spec.conversation_id)
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
            await self._prepare_mounts(stopped, spec.conversation_id)
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
        await self._allocate_network(spec.conversation_id)
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
                        await self._prepare_mounts(winner, spec.conversation_id)
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
            await self._prepare_mounts(container_id, spec.conversation_id)
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
        async with self._pin(spec.conversation_id):
            return await self._attach(spec)

    async def _attach(self, spec: SandboxSpec) -> SandboxHandle | None:
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
            await self._prepare_mounts(running, spec.conversation_id)
        except RuntimeError:
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=running,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            runtime_root=sandbox_runtime_root(spec.conversation_id),
        )

    @asynccontextmanager
    async def _pin(self, conversation_id: UUID) -> AsyncIterator[None]:
        async with self._lifecycle[conversation_id]:
            self._inflight[conversation_id] += 1
            self._touched[conversation_id] = self.clock()
        try:
            yield
        finally:
            self._inflight[conversation_id] -= 1
            self._touched[conversation_id] = self.clock()

    async def _allocate_network(self, conversation_id: UUID) -> None:
        async with self._allocation:
            await self._reclaim_idle(conversation_id)
            await self._ensure_network(self._network_name(conversation_id))

    async def _reclaim_idle(self, opening: UUID) -> None:
        self._touched[opening] = self.clock()
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
        networks = {UUID(name.removeprefix(f"{self.network}-")) for name in stdout.decode().split()}
        owned = networks & self._touched.keys()
        candidates = sorted(owned - {opening}, key=self._touched.__getitem__)
        held = len(owned | {opening})
        for conversation_id in candidates:
            async with self._lifecycle[conversation_id]:
                touched = self._touched[conversation_id]
                if self._inflight[conversation_id] or (
                    held <= WARM_NETWORK_LIMIT and self.clock() - touched < IDLE_RECLAIM_SECONDS
                ):
                    continue
                container_id = await self._held_id(f"{CONTAINER_NAME_PREFIX}{conversation_id}")
                if not await self._release(conversation_id, container_id):
                    continue
                del self._touched[conversation_id]
                held -= 1

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        """A call in flight pins its container: the in-flight count parks the conversation outside
        reclaim's reach for exactly the call's duration, whatever that duration is, and the
        completion stamp sets its place in the warm network cache. Reclaim stops a container only
        between calls."""
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
        async with self._pin(handle.conversation_id):
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

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """`docker exec -i` gives the container a real stdin, so the bytes stream in over it and
        never enter the command line. They land under a staged name beside the target and are
        renamed onto it, so a reader sees the whole of one write or the whole of the one before,
        a copy-in that stops part-way leaves the target as it was, and an overwrite keeps the
        file's mode."""
        async with self._pin(handle.conversation_id):
            code, stderr = await self._write_started(handle, path, content)
            if code != 0 and NOT_RUNNING_MARKER in stderr.decode(errors="replace"):
                if await self._revive(handle.conversation_id, handle.container_id):
                    code, stderr = await self._write_started(handle, path, content)
            if code != 0:
                raise OSError(stderr.decode(errors="replace").strip() or f"write failed: {path}")

    async def _write_started(
        self, handle: SandboxHandle, path: str, content: bytes
    ) -> tuple[int, bytes]:
        code, _, stderr = await _docker(
            "exec",
            "-i",
            handle.container_id,
            "sh",
            "-c",
            COPY_IN_SCRIPT,
            "sh",
            path,
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
        async with self._pin(handle.conversation_id):
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

    def _read_started(
        self, handle: SandboxHandle, path: str
    ) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]:
        """After stdout's EOF the exit status can be unreaped; signalling then reaps it from the
        loop's watcher (`Popen.send_signal` polls first) and reports asyncio's 255 placeholder."""
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
        """A dead exec leaves no state of its own, so the reported fields are the container's."""
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
        """The daemon's default pools hold ~30 bridge subnets. It removes a network out from under a
        stopped container, even one a revive connected while stopped (measured)."""
        if container_id is not None:
            code, _, _ = await _docker("stop", container_id, timeout_s=STOP_TIMEOUT_SECONDS)
            if code != 0:
                return False
        code, _, stderr = await _docker("network", "rm", self._network_name(conversation_id))
        return code == 0 or NO_SUCH_NETWORK_MARKER in stderr.decode()

    async def _revive(self, conversation_id: UUID, container_id: str) -> bool:
        """A daemon that cannot give the network back raises; only `attach` converts that to
        absence, because only the read path promises None over an error."""
        await self._allocate_network(conversation_id)
        async with self._lifecycle[conversation_id]:
            self._touched[conversation_id] = self.clock()
            network = self._network_name(conversation_id)
            code, _, stderr = await _docker("network", "connect", network, container_id)
            if code != 0 and NETWORK_EXISTS_MARKER not in stderr.decode():
                return False
            code, _, _ = await _docker("start", container_id, timeout_s=START_TIMEOUT_SECONDS)
            return code == 0

    async def _held_id(self, name: str) -> str | None:
        """`docker stop` handles a paused container (measured)."""
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
        """`docker ps` exits 0 with empty stdout on no match, and non-zero only when the command
        itself failed."""
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
        """The daemon arbitrates the network name, so a racing create's already-exists answer is
        success."""
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

    async def _prepare_mounts(self, container_id: str, conversation_id: UUID) -> None:
        runtime_root = sandbox_runtime_root(conversation_id)
        home_root = str(PurePosixPath(runtime_root).parents[1])
        code, _, stderr = await _docker(
            "exec",
            "-u",
            "root",
            container_id,
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
            home_root,
            runtime_root,
            str(SANDBOX_UID),
            str(SANDBOX_GID),
            WORKSPACE_DIR,
            timeout_s=RUNTIME_ROOT_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise RuntimeError(f"sandbox mount preparation failed: {stderr.decode().strip()}")


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=DockerCarrier),),
    )
