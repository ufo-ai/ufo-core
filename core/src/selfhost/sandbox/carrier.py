"""The Docker carrier: a per-conversation container reached only through the egress proxy.

Create-or-attach keeps the container a disposable cache over the durable workspace — a killed
container is recreated on the next turn from the same bind-mounted `workspace/` subtree, and the
turn notices only latency. Every command runs through `docker exec`; the container joins an
internal network with no gateway, so its sole route out is the proxy named in the spec."""

import asyncio
from dataclasses import dataclass

from selfhost.sandbox.session import (
    ExecResult,
    SandboxHandle,
    SandboxSpec,
)

CONTAINER_NAME_PREFIX = "selfhost-sbx-"
CREATE_TIMEOUT_SECONDS = 120
DEFAULT_NETWORK = "selfhost-sandbox"


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
            return SandboxHandle(conversation_id=spec.conversation_id, container_id=running)
        await self._ensure_network()
        argv = [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            self.network,
            "--env",
            f"HTTP_PROXY={spec.proxy.url}",
            "--env",
            f"HTTPS_PROXY={spec.proxy.url}",
            "--env",
            f"http_proxy={spec.proxy.url}",
            "--env",
            f"https_proxy={spec.proxy.url}",
        ]
        if spec.mount.kind == "filesystem" and spec.mount.host_path is not None:
            argv += ["-v", f"{spec.mount.host_path}:/workspace"]
        argv += [spec.image_ref, "sleep", "infinity"]
        code, stdout, stderr = await _docker(*argv, timeout_s=CREATE_TIMEOUT_SECONDS)
        if code != 0:
            raise RuntimeError(f"docker run failed: {stderr.decode().strip()}")
        container_id = stdout.decode().strip()
        await self._install_ca(container_id, spec.proxy.ca_cert)
        return SandboxHandle(conversation_id=spec.conversation_id, container_id=container_id)

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

    async def route(self, handle: SandboxHandle, port: int) -> str:
        code, stdout, _ = await _docker(
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            handle.container_id,
        )
        if code != 0:
            raise RuntimeError(f"no route for {handle.container_id}")
        return f"http://{stdout.decode().strip()}:{port}"

    async def destroy(self, handle: SandboxHandle) -> None:
        await _docker("rm", "-f", handle.container_id)

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
            await _docker("network", "create", "--internal", self.network)

    async def _install_ca(self, container_id: str, ca_cert: str) -> None:
        write = await _docker(
            "exec",
            "-i",
            container_id,
            "sh",
            "-c",
            "cat > /usr/local/share/ca-certificates/selfhost-proxy.crt && update-ca-certificates",
            stdin=ca_cert.encode(),
        )
        if write[0] != 0:
            raise RuntimeError(f"CA install failed: {write[2].decode().strip()}")
