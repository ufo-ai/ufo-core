"""The local carrier: the per-conversation workspace as a host directory, commands as subprocesses.

Core's zero-dependency default — no container, no cloud. The conversation's `workspace/` subtree is
a real host directory (the same bind-mount the Docker carrier would use), commands run as host
subprocesses with cwd set there, and the `/workspace` paths tools pass are rewritten to it. Egress
still routes through the sandbox proxy: each command inherits `HTTP(S)_PROXY` pointing at the proxy
on localhost, carrying the turn's run token, plus the sentinel model keys and the proxy CA, so
sentinel-swap and metering hold exactly as they do in a container. The `ufo` client is installed on
the command PATH — the same binary the image bakes — so `ufo fs` serves the file tools and `ufo llm`
serves the in-sandbox egress CLI with no container present.

A command's git is the sandbox's, never the host's: Apple's git ships
`credential.helper=osxkeychain`, and storing a credential through it raises a keychain authorization
UI, then blocks on a synchronous XPC reply nothing can send — hanging `git`, and the turn awaiting
it, forever.

This is a development default, not an isolation boundary: a subprocess is confined to the workspace
only through the `workspace_path` guard on tool arguments, not by the kernel. Docker and E2B are the
carriers that add real isolation. A command's environment is built for it — the scratch HOME and
PATH, locale and tmp passthrough, the proxy exports, the spec's own env — never serve's own, whose
environment is the deploy's secrets."""

import asyncio
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from io import BufferedReader
from pathlib import Path, PurePosixPath
from signal import SIGKILL

from ufo.harness.containment import (
    PathNotFound,
    contained_file,
    contained_relative,
    contained_remove,
)
from ufo.harness.o11y import warn
from ufo.harness.sandbox.client_binary import CLIENT_BINARY_NAME, client_binary
from ufo.harness.sandbox.session import (
    NO_PROXY_HOSTS,
    PROXY_PASSWORD,
    RUNTIME_DIRNAME,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    WORKSPACE_WRITE_MODE,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    host_argv,
    ufo_fs_file_op,
)

LOCAL_CONTAINER_ID = "local"
LOCAL_PROXY_HOST = "127.0.0.1"
ENV_PASSTHROUGH = (
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NODE_PATH",
    "PLAYWRIGHT_BROWSERS_PATH",
)
CA_FILENAME = "egress-ca.pem"
EXEC_TIMEOUT_CODE = 124
READ_CHUNK_BYTES = 1024 * 1024


@cache
def _provision_scratch() -> Path:
    """A process-lifetime scratch dir holding the command PATH's `ufo` client and a home for tools
    that write under `$HOME` — created once per process, off the event loop at the first carrier's
    construction. The binary is copied rather than linked, so every command of a running process
    runs the one build resolved here, and nothing deletes the copy: one dir per construction would
    leak the binary's size per carrier. The workspace itself is never here: it is the durable
    bind-mount, kept clear of scaffolding.

    A checkout holding no build of the client warns and carries on. The carrier is still the shell,
    the reads and the writes every other seam needs, and only the file tools and `ufo llm` want the
    binary — so the failure belongs to the command that asks for it, named there, rather than to
    every conversation this process opens."""
    root = Path(tempfile.mkdtemp(prefix="ufo-local-"))
    (root / "home").mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    try:
        source = client_binary()
    except RuntimeError as error:
        warn("sandbox.local.client_absent", reason=str(error))
        return root
    target = bin_dir / CLIENT_BINARY_NAME
    target.write_bytes(source.read_bytes())
    target.chmod(0o755)
    return root


@dataclass(frozen=True)
class LocalCarrier:
    _scratch: Path = field(default_factory=_provision_scratch)

    @property
    def ufo_home(self) -> Path:
        """The local runtime's `$UFO_HOME`."""
        return self._scratch / "home" / ".ufo"

    def seed_system_skills(self, archive: bytes) -> None:
        root = self.ufo_home / "skills"
        root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            manifest_bytes = bundle.read("manifest.json")
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, Mapping):
                raise TypeError("invalid system skill manifest")
            old = self._system_manifest(root)
            old_skills = old.get("skills")
            new_skills = manifest.get("skills")
            if not isinstance(old_skills, Mapping) or not isinstance(new_skills, Mapping):
                raise TypeError("invalid system skill manifest")
            if any(not isinstance(name, str) for name in (*old_skills, *new_skills)):
                raise TypeError("invalid system skill name")
            top_levels = {
                Path(contained_relative(name, str(root))).relative_to(root).parts[0]
                for name in (*old_skills, *new_skills)
            }
            for name in top_levels:
                contained_remove(root / name, root)
            for entry in bundle.infolist():
                if entry.is_dir() or entry.filename == "manifest.json":
                    continue
                target = contained_relative(entry.filename, str(root))
                with contained_file(target, root, create_parent=True) as output:
                    output.replace_bytes(bundle.read(entry), 0o644)
            with contained_file(root / ".system-manifest.json", root) as output:
                output.replace_bytes(manifest_bytes, 0o644)

    async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult:
        """Resolve system and user skills under the local runtime's `$UFO_HOME/skills`."""
        try:
            roots = await asyncio.to_thread(self._load_skills, payload)
        except (KeyError, OSError, TypeError, ValueError) as error:
            return ExecResult(stdout="", stderr=str(error), exit_code=1)
        return ExecResult(
            stdout=json.dumps({"roots": roots}, sort_keys=True),
            stderr="",
            exit_code=0,
        )

    def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]:
        root = self.ufo_home / "skills"
        root.mkdir(parents=True, exist_ok=True)
        manifest = self._system_manifest(root)
        manifest_skills = manifest["skills"]
        system = payload["system"]
        user = payload["user"]
        if not isinstance(manifest_skills, Mapping):
            raise TypeError("invalid system skill manifest")
        if any(not isinstance(name, str) for name in manifest_skills):
            raise TypeError("invalid system skill name")
        if not isinstance(system, Mapping) or not isinstance(user, Mapping):
            raise TypeError("invalid skill load payload")
        roots: dict[str, str] = {}
        for name, digest in system.items():
            if not isinstance(name, str) or not isinstance(digest, str):
                raise TypeError("invalid system skill")
            contained_relative(name, str(root))
            declared = manifest_skills.get(name)
            if not isinstance(declared, Mapping) or declared.get("digest") != digest:
                continue
            files = declared.get("files")
            if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
                raise TypeError(f"invalid system skill manifest: {name}")
            contents = self._read_skill_files(root, name, files)
            if self._skill_digest(contents) == digest:
                roots[name] = contained_relative(name, str(root))
        system_names = tuple(system)
        for name, encoded in user.items():
            if not isinstance(name, str) or not isinstance(encoded, Mapping):
                raise TypeError("invalid user skill")
            self._validate_user_skill_name(name, root)
            if any(
                system_name == name
                or system_name.startswith(f"{name}/")
                or name.startswith(f"{system_name}/")
                for system_name in system_names
            ):
                raise ValueError(f"user skill conflicts with system skill: {name}")
            digest = encoded.get("digest")
            files = encoded.get("files")
            if not isinstance(digest, str) or not isinstance(files, Mapping):
                raise TypeError(f"invalid user skill: {name}")
            contents = []
            for path, content in files.items():
                if not isinstance(path, str) or not isinstance(content, str):
                    raise TypeError(f"invalid user skill file: {name}")
                contained_relative(path, f"/{name}")
                contents.append((path, urlsafe_b64decode(content)))
            contents.sort()
            if self._skill_digest(contents) != digest:
                raise ValueError(f"user skill does not match its digest: {name}")
            self._install_user_skill(root, name, contents)
            roots[name] = contained_relative(name, str(root))
        return roots

    @staticmethod
    def _system_manifest(root: Path) -> Mapping[str, object]:
        with contained_file(root / ".system-manifest.json", root) as source:
            if source.lstat() is None:
                return {"skills": {}}
            with source.open_bytes() as contents:
                manifest = json.load(contents)
        if not isinstance(manifest, Mapping):
            raise TypeError("invalid system skill manifest")
        return manifest

    @staticmethod
    def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]:
        contents = []
        for path in files:
            target_path = contained_relative(f"{name}/{path}", str(root))
            with contained_file(target_path, root) as target:
                with target.open_bytes() as source:
                    contents.append((path, source.read()))
        return contents

    @staticmethod
    def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None:
        destination = contained_relative(name, str(root))
        contained_remove(destination, root)
        for path, content in files:
            target = contained_relative(f"{name}/{path}", str(root))
            with contained_file(target, root, create_parent=True) as output:
                output.replace_bytes(content, 0o644)

    @staticmethod
    def _validate_user_skill_name(name: str, root: Path) -> None:
        relative = Path(contained_relative(name, str(root))).relative_to(root)
        if len(relative.parts) != 1 or relative.parts[0].startswith("."):
            raise ValueError(f"invalid skill name: {name}")

    @staticmethod
    def _skill_digest(files: list[tuple[str, bytes]]) -> str:
        digest = hashlib.sha256()
        for path, content in files:
            digest.update(hashlib.sha256(path.encode()).digest())
            digest.update(hashlib.sha256(content).digest())
        return f"sha256:{digest.hexdigest()}"

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        root = Path(spec.workspace_host_path)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        runtime_root = self.ufo_home / RUNTIME_DIRNAME / spec.conversation_id.hex
        await asyncio.to_thread(runtime_root.mkdir, parents=True, exist_ok=True)
        ca_path = self._scratch / CA_FILENAME
        await asyncio.to_thread(ca_path.write_bytes, spec.proxy.ca_cert.encode())
        proxy_url = f"http://{spec.run_token}:{PROXY_PASSWORD}@{LOCAL_PROXY_HOST}:{spec.proxy.port}"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=LOCAL_CONTAINER_ID,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            runtime_root=str(runtime_root),
            egress_env={
                **self._base_env(),
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
                # git and cargo (libcurl) ignore CURL_CA_BUNDLE when they set their own CAINFO, so a
                # MITM'd host (a cache-fronted registry, or git rewritten to the cache) needs these.
                "GIT_SSL_CAINFO": str(ca_path),
                "CARGO_HTTP_CAINFO": str(ca_path),
                **spec.env,
            },
        )

    def _base_env(self) -> dict[str, str]:
        """What every command gets and nothing more. Serve's own environment is the deploy's
        secrets — the token-signing secret, DSNs, cloud keys — and a local-carrier command is a
        shell on the same host, so the environment is built rather than inherited: the scratch HOME
        and PATH, the locale and tmp names tools break without, and every level a host git
        credential helper could reach a command through, closed — `GIT_CONFIG_COUNT` and
        `GIT_CONFIG_PARAMETERS` carry config of their own and outrank the two config files, and
        `GIT_ASKPASS` and a terminal prompt each ask a question no command can answer. The global
        level points at the scratch home rather than `os.devnull`, which reads the same and still
        writes: `git config --global` against `/dev/null` fails to lock it, taking `git lfs
        install` and `gh auth setup-git` down with it."""
        passed = {name: os.environ[name] for name in ENV_PASSTHROUGH if name in os.environ}
        return {
            **passed,
            "HOME": str(self._scratch / "home"),
            "UFO_HOME": str(self.ufo_home),
            "PATH": f"{self._scratch / 'bin'}:{Path(sys.executable).parent}:{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self._scratch / "home" / ".gitconfig"),
            "GIT_CONFIG_COUNT": "0",
            "GIT_CONFIG_PARAMETERS": "",
            "GIT_ASKPASS": "",
            "GIT_TERMINAL_PROMPT": "0",
        }

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The read-only shape of `create`: the same handle over the same host directory, minus the
        directory's creation — a browse of a conversation that never grew a workspace answers empty
        through the reads, never by making one. Commands are host subprocesses, so the handle still
        carries the scratch PATH the `ufo fs` reads run under."""
        if not await asyncio.to_thread(Path(spec.workspace_host_path).is_dir):
            return None
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=LOCAL_CONTAINER_ID,
            workspace_host_path=spec.workspace_host_path,
            run_token=spec.run_token,
            runtime_root=str(self.ufo_home / RUNTIME_DIRNAME / spec.conversation_id.hex),
            egress_env=self._base_env(),
        )

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command as a host subprocess in the workspace. The `/workspace` paths the tools
        pass are logical, so each argv element is rewritten to the host workspace directory before
        the subprocess sees it, and the command inherits the turn's egress environment.

        Argv is the whole of the rewrite. A logical path inside a file the command reads — a script
        the agent wrote, a REPL cell — resolves against the host's own filesystem, where
        `/workspace` is a different directory or none, so a mounted skill's own script is
        unreachable by its logical path under this carrier. A carrier whose sandbox holds
        `/workspace` itself (docker, e2b) has no such seam. A path a command carries in its own text
        has to be workspace-relative, the way the REPL prelude keeps its emit path.

        The command leads its own process group, and an exec that ends without the command's
        consent — its timeout, or a cancelled turn — kills that group rather than the shell alone.
        A signal to the direct child leaves its descendants running, reparented to init and holding
        the host's CPU for as long as it lives, which is how one `bash -lc` that forks outlives
        every turn, conversation and process that could still name it. A command that exits on its
        own leaves its group alone: a backgrounded descendant outliving the exec that launched it
        is how a turn starts a server."""
        root = _root(handle)
        rewritten = host_argv(argv, str(root))
        process = await asyncio.create_subprocess_exec(
            *rewritten,
            cwd=str(root),
            env=dict(handle.egress_env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            await _kill_process_group(process)
            return ExecResult(
                stdout="",
                stderr="timed out",
                exit_code=EXEC_TIMEOUT_CODE,
                timed_out_after_s=timeout_s,
            )
        except BaseException:
            await _kill_process_group(process)
            raise
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
        name, root = _contained_name(handle, path)
        with contained_file(name, root, create_parent=True) as target:
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
            name, root = _contained_name(handle, path)
            with contained_file(name, root) as target:
                if target.lstat() is None:
                    raise FileNotFoundError(str(target.path))
                return target.open_bytes()
        except PathNotFound as error:
            raise FileNotFoundError(str(error)) from error

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """The scratch PATH every command runs under holds the `ufo` client, so a file op is
        `ufo fs` run as a host subprocess against the workspace directory."""
        return await ufo_fs_file_op(self, handle, op, params)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The local carrier runs commands as host subprocesses sharing the host network, so an
        in-sandbox port is the same port on the loopback — the dial target is `127.0.0.1:port`,
        plain HTTP. The cost of that sharing is one port namespace for every conversation: two
        conversations serving the same port contend, and the newer deploy's server owns it. A
        deploy needing per-conversation port isolation takes a container carrier (Docker, e2b)."""
        return DialTarget(host=f"127.0.0.1:{port}", tls=False)


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


def _root(handle: SandboxHandle) -> Path:
    if handle.workspace_host_path is None:
        raise RuntimeError("the local carrier serves /workspace from a host directory; none is set")
    return Path(handle.workspace_host_path)


def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]:
    candidate = PurePosixPath(path)
    if candidate.is_relative_to(WORKSPACE_DIR):
        return candidate.relative_to(WORKSPACE_DIR), _root(handle)
    if handle.runtime_root and candidate.is_relative_to(handle.runtime_root):
        return candidate.relative_to(handle.runtime_root), Path(handle.runtime_root)
    raise ValueError(f"path {path!r} is outside the sandbox roots")
