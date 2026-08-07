"""The local carrier: the per-conversation workspace as a host directory, commands as subprocesses.

Core's zero-dependency default — no container, no cloud. The conversation's `workspace/` subtree is
a real host directory (the same bind-mount the Docker carrier would use), commands run as host
subprocesses with cwd set there, and the `/workspace` paths tools pass are rewritten to it. Egress
still routes through the sandbox proxy: each command inherits `HTTP(S)_PROXY` pointing at the proxy
on localhost, carrying the turn's run token, plus the sentinel model keys and the proxy CA, so
sentinel-swap and metering hold exactly as they do in a container. The in-sandbox `sbx` and `sbxfs`
helpers are installed onto the command PATH — the same binaries the image bakes — so the file tools
and the egress CLI work with only a Python interpreter present.

A command's git is the sandbox's, never the host's: Apple's git ships
`credential.helper=osxkeychain`, and storing a credential through it raises a keychain authorization
UI, then blocks on a synchronous XPC reply nothing can send — hanging `git`, and the turn awaiting
it, forever.

This is a development default, not an isolation boundary: a subprocess is confined to the workspace
only through the `workspace_path` guard on tool arguments, not by the kernel. Docker and E2B are the
carriers that add real isolation."""

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import BufferedReader
from pathlib import Path, PurePosixPath

from ufo.sandbox.containment import PathNotFound, contained_file
from ufo.sandbox.session import (
    NO_PROXY_HOSTS,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    WORKSPACE_WRITE_MODE,
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
# Modules the scripts import, installed beside them exactly as the image bakes them: a script's own
# directory is `sys.path[0]`, so this is how `sbxfs` reaches the containment guard under a carrier
# that has no installed `ufo` package inside the sandbox.
SANDBOX_MODULES = ("containment.py",)


def _git_without_host_config(scratch: Path) -> dict[str, str]:
    """Every level a host credential helper could reach a command through, closed. The command
    inherits the environment, so the two config files are not enough: `GIT_CONFIG_COUNT` and
    `GIT_CONFIG_PARAMETERS` carry config of their own and outrank both, and `GIT_ASKPASS` and a
    terminal prompt each ask a question no command can answer.

    The global level points at the scratch home rather than `os.devnull`, which reads the same and
    still writes: `git config --global` against `/dev/null` fails to lock it, taking `git lfs
    install` and `gh auth setup-git` down with it."""
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(scratch / "home" / ".gitconfig"),
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_PARAMETERS": "",
        "GIT_ASKPASS": "",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _provision_scratch() -> Path:
    """A process-lifetime scratch dir holding the command PATH's `sbx`/`sbxfs`, the modules they
    import, and a home for tools that write under `$HOME` — created once per carrier, off the event
    loop at construction. The workspace itself is never here: it is the durable bind-mount, kept
    clear of scaffolding."""
    root = Path(tempfile.mkdtemp(prefix="ufo-local-"))
    (root / "home").mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    source = Path(__file__).parent / "image"
    for name in SANDBOX_BINARIES:
        target = bin_dir / name
        target.write_bytes((source / name).read_bytes())
        target.chmod(0o755)
    for name in SANDBOX_MODULES:
        target = bin_dir / name
        target.write_bytes((Path(__file__).parent / name).read_bytes())
        target.chmod(0o644)
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
                **_git_without_host_config(self._scratch),
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
                **_git_without_host_config(self._scratch),
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
        loop, since the filesystem has no async API, and through the containment guard, since the
        path was built from a tool argument or a surface's inbound filename. Every directory on the
        way is created and re-opened `O_NOFOLLOW` as the descent reaches it, so a link planted at
        any component cannot redirect the copy-in, and the bytes are staged under a name created
        `O_CREAT|O_EXCL` beside the target and renamed onto it: a reader of the path sees the whole
        of one write or the whole of the one before, which is what two writers racing one path need,
        since a surface may deliver a file twice.

        The staged name is its own, not the target's with a suffix, so the longest filename a
        directory takes still fits, and it is removed on any failure, since the workspace listing is
        the member's own file list and an orphan would appear in it as a file they never made. A
        path resolving to the workspace root, or above it, holds no file to write and is refused
        before any byte is staged. A rename installs a new inode, so an overwrite carries the mode
        across and an executable a turn produced stays executable for the turn that runs it — probed
        once, without following a link, so a delete racing the write still ends in a created file.
        Permission bits only: setuid, setgid and sticky do not survive a copy-in through any other
        carrier. A file the copy-in creates lands 0o644 rather than under serve's umask, because the
        sandbox user is the one that reads it, and so does one landing on a name something other
        than a regular file holds — that name is replaced rather than refused, since the rename
        cannot write through a link and a link left at an inbox name would otherwise deny every
        later delivery to it."""
        await asyncio.to_thread(self._write_contained, handle, path, content)

    def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        with contained_file(_workspace_name(path), _root(handle), create_parent=True) as target:
            target.replace_bytes(content, target.mode(WORKSPACE_WRITE_MODE))

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """The workspace is a host directory, so the copy-out is a chunked host read under it — off
        the loop, since the filesystem has no async API. A copy-out is an ingress like any other:
        the path is confined before a byte is touched and the stream comes off an `O_NOFOLLOW` fd
        the descent proved regular, so a link the agent planted at the target — or at a directory on
        the way to it — is refused instead of read out of the workspace."""
        source = await asyncio.to_thread(self._contained_source, handle, path)
        try:
            while chunk := await asyncio.to_thread(source.read, READ_CHUNK_BYTES):
                yield chunk
        finally:
            await asyncio.to_thread(source.close)

    def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader:
        """The pinned parent is released once the file's own fd is open, so the stream that outlives
        this call names no path a later swap could redirect."""
        try:
            with contained_file(_workspace_name(path), _root(handle)) as target:
                if target.lstat() is None:
                    raise FileNotFoundError(str(target.path))
                return target.open_bytes()
        except PathNotFound as error:
            raise FileNotFoundError(str(error)) from error

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


def _workspace_name(path: str) -> PurePosixPath:
    """The name a logical `/workspace` path carries under the host workspace directory. Only the
    mapping — every containment check is the guard's, run against the real filesystem the name lands
    on rather than against the string."""
    return PurePosixPath(path).relative_to(WORKSPACE_DIR)
