"""The Docker carrier extension: a per-conversation container reached only through the egress proxy.

Core's default is the local carrier; a deploy that sets `[sandbox] backend = "docker"` runs its
sandboxes as sibling containers. Create-or-attach keeps the container a disposable cache over
the durable workspace — a killed container is recreated on the next turn from the same bind-mounted
`workspace/` subtree, and the turn notices only latency. Every command runs through `docker exec`.
The container's HTTP(S)_PROXY points at the egress proxy running on the host, reached at
`host.docker.internal`, and carries the turn's run token as its basic-auth username so the proxy
attributes each metered request to the turn; the proxy refuses any host its rules do not allow and
swaps the sentinel for the real key on the wire, so the raw credential never enters the sandbox."""

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ufo.sdk.manifest import Manifest
from ufo.sdk.sandbox import (
    AWS_CREDENTIALS_PATH,
    MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
    MOUNT_TIMEOUT_SECONDS,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    BlobStore,
    CarrierSpec,
    ExecResult,
    MountSpec,
    SandboxHandle,
    SandboxSpec,
    aws_credentials_file,
    mount_scripts,
    s3fs_command,
)

CARRIER_NAME = "docker"
CONTAINER_NAME_PREFIX = "ufo-sbx-"
CREATE_TIMEOUT_SECONDS = 120
DEFAULT_NETWORK = "ufo-sandbox"
HOST_GATEWAY_NAME = "host.docker.internal"
HOST_GATEWAY_MAPPING = f"{HOST_GATEWAY_NAME}:host-gateway"
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
# The s3fs daemon is framework infrastructure, not agent egress: it talks straight to the object
# store with the mount's prefix-scoped credential. The container-wide HTTP(S)_PROXY (set for the
# agent's metered egress) is default-deny and does not allow the S3 host, and libcurl — which s3fs
# uses — would honor it and fail the mount at CONNECT. So the mount exec clears the proxy env for
# the s3fs process; the scoped credential, not the proxy, confines it to the conversation workspace.
PROXY_CLEAR_ARGS = (
    "--env",
    "HTTP_PROXY=",
    "--env",
    "HTTPS_PROXY=",
    "--env",
    "http_proxy=",
    "--env",
    "https_proxy=",
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
        name = f"{CONTAINER_NAME_PREFIX}{spec.conversation_id}"
        running = await self._running_id(name)
        if running is not None:
            handle = SandboxHandle(
                conversation_id=spec.conversation_id, container_id=running, mount=spec.mount
            )
            await self._mount_s3(handle, spec.mount)
            return handle
        await self._ensure_network()
        proxy_url = f"http://{spec.run_token}:@{HOST_GATEWAY_NAME}:{spec.proxy.port}"
        argv = [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            self.network,
            "--add-host",
            HOST_GATEWAY_MAPPING,
            *(FUSE_RUN_ARGS if spec.mount.kind == "s3" else ()),
            "--env",
            f"HTTP_PROXY={proxy_url}",
            "--env",
            f"HTTPS_PROXY={proxy_url}",
            "--env",
            f"http_proxy={proxy_url}",
            "--env",
            f"https_proxy={proxy_url}",
            "--env",
            f"ANTHROPIC_API_KEY={SENTINEL_MODEL_KEY}",
            "--env",
            f"OPENAI_API_KEY={SENTINEL_MODEL_KEY}",
        ]
        if spec.mount.kind == "filesystem" and spec.mount.host_path is not None:
            argv += ["-v", f"{spec.mount.host_path}:/workspace"]
        argv += [spec.image_ref, "sleep", "infinity"]
        code, stdout, stderr = await _docker(*argv, timeout_s=CREATE_TIMEOUT_SECONDS)
        if code != 0:
            raise RuntimeError(f"docker run failed: {stderr.decode().strip()}")
        container_id = stdout.decode().strip()
        await self._install_ca(container_id, spec.proxy.ca_cert)
        handle = SandboxHandle(
            conversation_id=spec.conversation_id, container_id=container_id, mount=spec.mount
        )
        await self._mount_s3(handle, spec.mount)
        return handle

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        code, stdout, stderr = await _docker(
            "exec", "-i", handle.container_id, *argv, stdin=stdin, timeout_s=timeout_s
        )
        return ExecResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=code,
        )

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        """Copy a produced workspace file to `key` without the host process ever holding the bytes
        whole — the large-attachment path, distinct from the bounded read. A filesystem bind mount
        already has the file at `host_path/<rel>`, streamed in by the blob store. An s3 mount wrote
        it through s3fs to `<key_prefix>/<rel>` in the same bucket, so it streams object→object in
        bounded chunks."""
        mount = handle.mount
        if mount is None:
            raise RuntimeError("docker export requires a workspace mount")
        rel = PurePosixPath(path).relative_to(WORKSPACE_DIR)
        if mount.kind == "s3":
            if mount.key_prefix is None:
                raise RuntimeError("s3 workspace mount is missing its key prefix")
            await blob.put_stream(key, blob.get_stream(f"{mount.key_prefix}/{rel}"))
            return
        if mount.kind != "filesystem" or mount.host_path is None:
            raise RuntimeError("docker export requires a filesystem or s3 workspace mount")
        await blob.put_file(key, Path(mount.host_path) / rel)

    async def _mount_s3(self, handle: SandboxHandle, mount: MountSpec) -> None:
        """Bring the conversation's workspace S3 prefix up at /workspace over s3fs. Idempotent:
        skips a healthy mount, so a later turn attaching to a still-running container skips a
        redundant remount (same-conversation turns serialize under the queue partition, so no
        dispatch races another's in-flight reads). Writes the prefix-scoped credential, then runs
        the root `prepare` (open /dev/fuse, enable user_allow_other, detach any stale mount) and the
        agent `mount` (s3fs, with the proxy env cleared) through the two docker-exec users — the
        privileged prepare never runs as the agent."""
        if mount.kind != "s3":
            return
        if await self._mount_healthy(handle):
            return
        if (
            mount.credentials is None
            or mount.bucket is None
            or mount.key_prefix is None
            or mount.s3_url is None
            or mount.region is None
        ):
            raise RuntimeError("s3 workspace mount is missing its scoped credential or endpoint")
        creds = aws_credentials_file(mount.credentials).encode()
        parent = shlex.quote(str(PurePosixPath(AWS_CREDENTIALS_PATH).parent))
        write = f"mkdir -p {parent} && cat > {shlex.quote(AWS_CREDENTIALS_PATH)}"
        code, _, stderr = await _docker(
            "exec", "-i", handle.container_id, "sh", "-c", write, stdin=creds
        )
        if code != 0:
            raise RuntimeError(f"sandbox-fs credential write failed: {stderr.decode().strip()}")
        s3fs = s3fs_command(
            mount.bucket,
            mount.key_prefix,
            WORKSPACE_DIR,
            mount.s3_url,
            mount.region,
            mount.path_style,
        )
        prepare, mount_cmd = mount_scripts(WORKSPACE_DIR, s3fs)
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
            *PROXY_CLEAR_ARGS,
            handle.container_id,
            "sh",
            "-c",
            mount_cmd,
            timeout_s=MOUNT_TIMEOUT_SECONDS,
        )
        if code != 0:
            raise RuntimeError(f"sandbox-fs mount failed: {stderr.decode().strip()}")

    async def _mount_healthy(self, handle: SandboxHandle) -> bool:
        code, _, _ = await _docker(
            "exec",
            "-i",
            handle.container_id,
            "sh",
            "-c",
            f"mountpoint -q {shlex.quote(WORKSPACE_DIR)}",
            timeout_s=MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
        )
        return code == 0

    async def destroy(self, handle: SandboxHandle) -> None:
        """Reap by the conversation's container name, the same key `create` derives — the durable
        identity a reaper holds, not the ephemeral id a live handle also carries. `rm -f` on a name
        that no longer exists is a no-op, so destroying an already-gone or never-created sandbox
        never raises."""
        await _docker("rm", "-f", f"{CONTAINER_NAME_PREFIX}{handle.conversation_id}")

    async def _running_id(self, name: str) -> str | None:
        code, stdout, _ = await _docker(
            "ps", "-q", "--filter", f"name=^{name}$", "--filter", "status=running"
        )
        if code != 0:
            return None
        found = stdout.decode().strip()
        return found or None

    async def _ensure_network(self) -> None:
        code, _, _ = await _docker("network", "inspect", self.network)
        if code != 0:
            await _docker("network", "create", self.network)

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
