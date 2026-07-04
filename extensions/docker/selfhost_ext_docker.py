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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from selfhost.sdk.manifest import Manifest
from selfhost.sdk.sandbox import (
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    BlobStore,
    CarrierSpec,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
)

CARRIER_NAME = "docker"
CONTAINER_NAME_PREFIX = "selfhost-sbx-"
CREATE_TIMEOUT_SECONDS = 120
DEFAULT_NETWORK = "selfhost-sandbox"
HOST_GATEWAY_NAME = "host.docker.internal"
HOST_GATEWAY_MAPPING = f"{HOST_GATEWAY_NAME}:host-gateway"


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
            return SandboxHandle(
                conversation_id=spec.conversation_id, container_id=running, mount=spec.mount
            )
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
        return SandboxHandle(
            conversation_id=spec.conversation_id, container_id=container_id, mount=spec.mount
        )

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
        """The workspace is a host bind mount, so the produced file already lives at
        `host_path/<rel>` — hand that path to the blob store, which streams it in (a filesystem
        copy, an S3 multipart upload) without the host process ever holding the bytes whole. No
        read cap applies: this is the large-attachment path, distinct from the bounded read."""
        mount = handle.mount
        if mount is None or mount.kind != "filesystem" or mount.host_path is None:
            raise RuntimeError("docker export requires a filesystem workspace mount")
        rel = PurePosixPath(path).relative_to(WORKSPACE_DIR)
        await blob.put_file(key, Path(mount.host_path) / rel)

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
            "cat > /usr/local/share/ca-certificates/selfhost-proxy.crt && update-ca-certificates",
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
