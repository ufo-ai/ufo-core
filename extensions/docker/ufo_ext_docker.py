"""The Docker carrier extension: a per-conversation container reached only through the egress proxy.

Core's default is the local carrier; a deploy that sets `[sandbox] backend = "docker"` runs its
sandboxes as sibling containers. Create-or-attach keeps the container a disposable cache over
the durable workspace — a killed container is recreated on the next turn from the same bind-mounted
`workspace/` subtree, and the turn notices only latency. Every command runs through `docker exec`
under its turn's egress env: HTTP(S)_PROXY points at the egress proxy running on the host, reached
at `host.docker.internal`, and carries the turn's run token as its basic-auth username so the proxy
attributes each metered request to the turn; the proxy refuses any host its rules do not allow and
swaps the sentinel for the real key on the wire, so the raw credential never enters the sandbox.
The env is per-exec, never baked into the container — a container outlives its first turn, and a
later turn must not run under an earlier turn's token."""

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID

from ufo.sdk.manifest import Manifest
from ufo.sdk.sandbox import (
    MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
    MOUNT_TIMEOUT_SECONDS,
    SANDBOX_FS_CREDENTIAL_PATH,
    SANDBOX_FS_TOKEN_STAGING_PATH,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    BlobStore,
    CarrierSpec,
    ExecResult,
    MountSpec,
    SandboxHandle,
    SandboxSpec,
    install_token_command,
    mount_health_check,
    mount_scripts,
    prepare_token_staging_command,
    s3fs_command,
)

CARRIER_NAME = "docker"
CONTAINER_NAME_PREFIX = "ufo-sbx-"
CREATE_TIMEOUT_SECONDS = 120
WRITE_TIMEOUT_SECONDS = 30
DEFAULT_NETWORK = "ufo-sandbox"
HOST_GATEWAY_NAME = "host.docker.internal"
HOST_GATEWAY_MAPPING = f"{HOST_GATEWAY_NAME}:host-gateway"
DROP_NET_RAW_ARGS = ("--cap-drop", "NET_RAW")
# s3fs mounts the workspace prefix over FUSE, which needs the fuse device plus CAP_SYS_ADMIN and an
# unconfined apparmor profile to mount inside the container. Added only for an s3 mount — a
# filesystem bind mount (local dev) needs no FUSE and keeps the tighter default isolation.
FUSE_RUN_ARGS = (
    "--device",
    "/dev/fuse",
    "--cap-add",
    "SYS_ADMIN",
    "--security-opt",
    "apparmor=unconfined",
)


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
        return 124, b"", b"timed out"
    return process.returncode or 0, stdout, stderr


@dataclass(frozen=True)
class DockerCarrier:
    network: str = DEFAULT_NETWORK

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Create-or-attach the conversation's container. The egress env is never baked into the
        container — a container outliving its first turn must not pin that turn's run token — it
        rides the returned handle and every exec carries it, so each turn's commands run under its
        own token and sentinel entries."""
        name = f"{CONTAINER_NAME_PREFIX}{spec.conversation_id}"
        proxy_url = f"http://{spec.run_token}:@{HOST_GATEWAY_NAME}:{spec.proxy.port}"
        egress_env = {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
            "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
            **spec.env,
        }
        running = await self._running_id(name)
        if running is not None:
            handle = SandboxHandle(
                conversation_id=spec.conversation_id,
                container_id=running,
                mount=spec.mount,
                egress_env=egress_env,
            )
            await self._mount_s3(handle, spec.mount, self._credential_url(spec))
            return handle
        network = self._network_name(spec.conversation_id)
        await self._ensure_network(network)
        argv = [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--add-host",
            HOST_GATEWAY_MAPPING,
            *DROP_NET_RAW_ARGS,
            *(FUSE_RUN_ARGS if spec.mount.kind == "s3" else ()),
        ]
        if spec.mount.kind == "filesystem" and spec.mount.host_path is not None:
            argv += ["-v", f"{spec.mount.host_path}:/workspace"]
        argv += [spec.image_ref, "sleep", "infinity"]
        container_id: str | None = None
        try:
            code, stdout, stderr = await _docker(*argv, timeout_s=CREATE_TIMEOUT_SECONDS)
            if code != 0:
                raise RuntimeError(f"docker run failed: {stderr.decode().strip()}")
            container_id = stdout.decode().strip()
            await self._install_ca(container_id, spec.proxy.ca_cert)
            handle = SandboxHandle(
                conversation_id=spec.conversation_id,
                container_id=container_id,
                mount=spec.mount,
                egress_env=egress_env,
            )
            await self._mount_s3(handle, spec.mount, self._credential_url(spec))
            return handle
        except BaseException:
            if container_id is not None:
                await _docker("rm", "-f", container_id)
            await _docker("network", "rm", network)
            raise

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        env_args = tuple(
            arg for name, value in handle.egress_env.items() for arg in ("--env", f"{name}={value}")
        )
        code, stdout, stderr = await _docker(
            "exec", *env_args, handle.container_id, *argv, timeout_s=timeout_s
        )
        return ExecResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=code,
        )

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """`docker exec -i` gives the container a real stdin, so the bytes stream in over it and
        never enter the command line."""
        code, _, stderr = await _docker(
            "exec",
            "-i",
            handle.container_id,
            "sh",
            "-c",
            'mkdir -p "$(dirname "$1")" && cat > "$1"',
            "sh",
            path,
            stdin=content,
            timeout_s=WRITE_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise OSError(stderr.decode(errors="replace").strip() or f"write failed: {path}")

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        """Copy a produced workspace file to `key` without the host process ever holding the bytes
        whole — the large-attachment path, distinct from the bounded read. A filesystem bind mount
        already has the file at `host_path/<rel>`, streamed in by the blob store. An s3 mount wrote
        it through s3fs to `<key_prefix>/<rel>` in the same bucket, so it copies server-side within
        the store — the bytes never leave S3."""
        mount = handle.mount
        if mount is None:
            raise RuntimeError("docker export requires a workspace mount")
        rel = PurePosixPath(path).relative_to(WORKSPACE_DIR)
        if mount.kind == "s3":
            if mount.key_prefix is None:
                raise RuntimeError("s3 workspace mount is missing its key prefix")
            await blob.copy(f"{mount.key_prefix}/{rel}", key)
            return
        if mount.kind != "filesystem" or mount.host_path is None:
            raise RuntimeError("docker export requires a filesystem or s3 workspace mount")
        await blob.put_file(key, Path(mount.host_path) / rel)

    async def _mount_s3(self, handle: SandboxHandle, mount: MountSpec, credential_url: str) -> None:
        """Bring the conversation's workspace S3 prefix up at /workspace over s3fs. Idempotent:
        skips a mount the health probe passes, so a later turn attaching to a live container never
        remounts under an in-flight dispatch. Writes the private endpoint token, then runs mount
        orchestration as root. The s3fs daemon drops to its dedicated user, refreshes scoped
        credentials through the local relay, and talks directly to S3; neither path carries the
        agent's egress environment."""
        if mount.kind != "s3":
            return
        if (
            mount.credential_token is None
            or mount.bucket is None
            or mount.key_prefix is None
            or mount.s3_url is None
            or mount.region is None
        ):
            raise RuntimeError("s3 workspace mount is missing its credential token or endpoint")
        token = mount.credential_token.encode()
        staging = shlex.quote(SANDBOX_FS_TOKEN_STAGING_PATH)
        write = (
            f"{prepare_token_staging_command()} && umask 077 && cat > {staging} "
            f"&& {install_token_command()}"
        )
        code, _, stderr = await _docker(
            "exec", "-i", "-u", "root", handle.container_id, "sh", "-c", write, stdin=token
        )
        if code != 0:
            raise RuntimeError(f"sandbox-fs token write failed: {stderr.decode().strip()}")
        if await self._mount_healthy(handle):
            return
        s3fs = s3fs_command(
            mount.bucket,
            mount.key_prefix,
            WORKSPACE_DIR,
            mount.s3_url,
            mount.region,
            mount.path_style,
        )
        prepare, mount_cmd = mount_scripts(WORKSPACE_DIR, s3fs, credential_url)
        code, _, stderr = await _docker(
            "exec",
            "-i",
            "-u",
            "root",
            handle.container_id,
            "sh",
            "-c",
            prepare,
            timeout_s=MOUNT_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise RuntimeError(f"sandbox-fs mount prepare failed: {stderr.decode().strip()}")
        code, _, stderr = await _docker(
            "exec",
            "-i",
            "-u",
            "root",
            handle.container_id,
            "sh",
            "-c",
            mount_cmd,
            timeout_s=MOUNT_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise RuntimeError(f"sandbox-fs mount failed: {stderr.decode().strip()}")
        if not await self._mount_healthy(handle):
            raise RuntimeError("sandbox-fs mount failed its health check")

    def _credential_url(self, spec: SandboxSpec) -> str:
        if spec.proxy.public_url is not None:
            parsed = urlsplit(spec.proxy.public_url)
            if parsed.scheme != "https" or parsed.hostname is None:
                raise RuntimeError(
                    "the docker carrier requires an HTTPS [sandbox] proxy_public_url so mount "
                    "credentials are encrypted in transit"
                )
            return f"{spec.proxy.public_url.rstrip('/')}{SANDBOX_FS_CREDENTIAL_PATH.rstrip('/')}"
        return (
            f"http://{HOST_GATEWAY_NAME}:{spec.proxy.port}{SANDBOX_FS_CREDENTIAL_PATH.rstrip('/')}"
        )

    async def _mount_healthy(self, handle: SandboxHandle) -> bool:
        code, _, _ = await _docker(
            "exec",
            "-i",
            "-u",
            "root",
            handle.container_id,
            "sh",
            "-c",
            mount_health_check(WORKSPACE_DIR),
            timeout_s=MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
        )
        return code == 0

    async def destroy(self, handle: SandboxHandle) -> None:
        """Reap by the conversation's container name, the same key `create` derives — the durable
        identity a reaper holds, not the ephemeral id a live handle also carries. `rm -f` on a name
        that no longer exists is a no-op, so destroying an already-gone or never-created sandbox
        never raises."""
        await _docker("rm", "-f", f"{CONTAINER_NAME_PREFIX}{handle.conversation_id}")
        await _docker("network", "rm", self._network_name(handle.conversation_id))

    async def host(self, handle: SandboxHandle, port: int) -> str:
        """The docker carrier publishes no per-port host, so an in-sandbox service (a browser's CDP
        endpoint, a site's dev-server preview) has no external route here — a deploy that reaches an
        in-sandbox port needs a remote carrier (e2b)."""
        raise RuntimeError(
            "the docker carrier exposes no external per-port host; reach an in-sandbox service "
            "through a remote carrier (e2b)"
        )

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
        code, stdout, stderr = await _docker("network", "ls", "-q", "--filter", f"name=^{network}$")
        if code != 0:
            raise RuntimeError(f"docker network ls failed: {stderr.decode().strip()}")
        if stdout.strip():
            return
        code, _, stderr = await _docker("network", "create", network)
        if code != 0:
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


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=DockerCarrier),),
    )
