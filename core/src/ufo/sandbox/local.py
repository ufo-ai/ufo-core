"""The local carrier: the per-conversation workspace as a host directory, commands as subprocesses.

Core's zero-dependency default — no container, no cloud. The conversation's `workspace/` subtree is
a real host directory (the same bind-mount the Docker carrier would use), commands run as host
subprocesses with cwd set there, and the `/workspace` paths tools pass are rewritten to it. Egress
still routes through the sandbox proxy: each command inherits `HTTP(S)_PROXY` pointing at the proxy
on localhost, carrying the turn's run token, plus the sentinel model keys and the proxy CA, so
sentinel-swap and metering hold exactly as they do in a container. The in-sandbox `sbx` and `sbxfs`
helpers are installed onto the command PATH — the same binaries the image bakes — so the file tools
and the egress CLI work with only a Python interpreter present.

This is a development default, not an isolation boundary: a subprocess is confined to the workspace
only through the `workspace_path` guard on tool arguments, not by the kernel. Docker and E2B are the
carriers that add real isolation."""

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ufo.sandbox.session import (
    NO_PROXY_HOSTS,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)

LOCAL_CONTAINER_ID = "local"
LOCAL_PROXY_HOST = "127.0.0.1"
CA_FILENAME = "egress-ca.pem"
EXEC_TIMEOUT_CODE = 124
READ_CHUNK_BYTES = 1024 * 1024
SANDBOX_BINARIES = ("sbx", "sbxfs")


def _provision_scratch() -> Path:
    """A process-lifetime scratch dir holding the command PATH's `sbx`/`sbxfs` and a home for tools
    that write under `$HOME` — created once per carrier, off the event loop at construction. The
    workspace itself is never here: it is the durable bind-mount, kept clear of scaffolding."""
    root = Path(tempfile.mkdtemp(prefix="ufo-local-"))
    (root / "home").mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    source = Path(__file__).parent / "image"
    for name in SANDBOX_BINARIES:
        target = bin_dir / name
        target.write_bytes((source / name).read_bytes())
        target.chmod(0o755)
    return root


@dataclass(frozen=True)
class LocalCarrier:
    _scratch: Path = field(default_factory=_provision_scratch)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        root = Path(spec.workspace_host_path)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        ca_path = self._scratch / CA_FILENAME
        await asyncio.to_thread(ca_path.write_bytes, spec.proxy.ca_cert.encode())
        proxy_url = f"http://{spec.run_token}:@{LOCAL_PROXY_HOST}:{spec.proxy.port}"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=LOCAL_CONTAINER_ID,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            egress_env={
                **os.environ,
                "HOME": str(self._scratch / "home"),
                "PATH": (
                    f"{self._scratch / 'bin'}:{Path(sys.executable).parent}:{os.environ['PATH']}"
                ),
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "NO_PROXY": NO_PROXY_HOSTS,
                "no_proxy": NO_PROXY_HOSTS,
                "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
                "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
                "SSL_CERT_FILE": str(ca_path),
                "REQUESTS_CA_BUNDLE": str(ca_path),
                "CURL_CA_BUNDLE": str(ca_path),
                "NODE_EXTRA_CA_CERTS": str(ca_path),
                **spec.env,
            },
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The read-only shape of `create`: the same handle over the same host directory, minus the
        directory's creation — a browse of a conversation that never grew a workspace answers empty
        through the reads, never by making one. Commands are host subprocesses, so the handle still
        carries the scratch PATH the `sbxfs` reads run under."""
        if not await asyncio.to_thread(Path(spec.workspace_host_path).is_dir):
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=LOCAL_CONTAINER_ID,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            egress_env={
                **os.environ,
                "HOME": str(self._scratch / "home"),
                "PATH": (
                    f"{self._scratch / 'bin'}:{Path(sys.executable).parent}:{os.environ['PATH']}"
                ),
            },
        )

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command as a host subprocess in the workspace. The `/workspace` paths the tools
        pass are logical, so each argv element is rewritten to the host workspace directory before
        the subprocess sees it, and the command inherits the turn's egress environment."""
        root = _root(handle)
        rewritten = tuple(arg.replace(WORKSPACE_DIR, str(root)) for arg in argv)
        process = await asyncio.create_subprocess_exec(
            *rewritten,
            cwd=str(root),
            env=dict(handle.egress_env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ExecResult(stdout="", stderr="timed out", exit_code=EXEC_TIMEOUT_CODE)
        return ExecResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=process.returncode or 0,
        )

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """The workspace is a host directory, so the copy-in is a host write under it — off the
        loop, since the filesystem has no async API."""
        target = _host_path(handle, path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, content)

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """The workspace is a host directory, so the copy-out is a chunked host read under it — off
        the loop, since the filesystem has no async API."""
        source = await asyncio.to_thread(_host_path(handle, path).open, "rb")
        try:
            while chunk := await asyncio.to_thread(source.read, READ_CHUNK_BYTES):
                yield chunk
        finally:
            await asyncio.to_thread(source.close)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The local carrier runs commands as host subprocesses, not a network-addressable sandbox,
        so an in-sandbox service (a browser's CDP endpoint, a site's dev-server preview) has no
        external per-port host — a deploy that reaches an in-sandbox port needs a remote carrier
        (e2b)."""
        raise SandboxUnreachable(
            "the local carrier exposes no external per-port host; reach an in-sandbox service "
            "through a remote carrier (e2b)"
        )


def _root(handle: SandboxHandle) -> Path:
    if handle.workspace_host_path is None:
        raise RuntimeError("the local carrier serves /workspace from a host directory; none is set")
    return Path(handle.workspace_host_path)


def _host_path(handle: SandboxHandle, path: str) -> Path:
    """The host location of a logical `/workspace` path — the workspace is a host directory, so
    every copy-in and copy-out lands here."""
    return _root(handle) / PurePosixPath(path).relative_to(WORKSPACE_DIR)
