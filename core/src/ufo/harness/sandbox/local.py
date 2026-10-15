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

Every command runs under the kernel's own sandbox, `ufo sandbox` — Seatbelt on macOS, Landlock on
Linux — with writes confined to the workspace, the conversation's runtime root, the scratch home,
and the temp dir, and everything readable. The binary that applies it sits on the scratch PATH
outside every writable root, so no confined command can replace what confines the next. Egress is
not kernel-held: a command that ignores the proxy env reaches the host's network. Docker and E2B
are the carriers that hold both. A command's environment is built for it — the scratch HOME and
PATH, locale and tmp passthrough, the proxy exports, the spec's own env — never serve's own, whose
environment is the deploy's secrets."""

import asyncio
import hashlib
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import zipfile
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from signal import SIGKILL
from uuid import uuid4

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
SANDBOX_VERB = "sandbox"
SANDBOX_WRITE_FLAG = "--write"
DEFAULT_TMPDIR = "/tmp"
EXEC_TIMEOUT_CODE = 124
READ_CHUNK_BYTES = 1024 * 1024
STAGED_PREFIX = ".ufo-staged-"
SYSTEM_MANIFEST_NAME = ".system-manifest.json"


def provision_scratch(root: Path) -> Path:
    """`root` as a carrier's scratch: a `home` for tools that write under `$HOME`, and a `bin` with
    the command PATH's `ufo` client. That binary confines every command, so it sits outside every
    directory a command may write, and it is copied rather than linked so every command of a running
    process runs the one build resolved here. A checkout holding no build of the client leaves
    `bin` empty, and every command then fails naming the build to run, while the carrier still
    serves the reads and writes every other seam needs."""
    (root / "home").mkdir(parents=True, exist_ok=True)
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    try:
        source = client_binary()
    except RuntimeError as error:
        warn("sandbox.local.client_absent", reason=str(error))
        return root
    target = bin_dir / CLIENT_BINARY_NAME
    target.write_bytes(source.read_bytes())
    target.chmod(0o755)
    return root


@cache
def _provision_scratch() -> Path:
    return provision_scratch(Path(tempfile.mkdtemp(prefix="ufo-local-")))


@dataclass(frozen=True)
class LocalCarrier:
    _scratch: Path = field(default_factory=_provision_scratch)

    @property
    def ufo_home(self) -> Path:
        """The local runtime's `$UFO_HOME`, beside the home rather than under it: a command may
        write its home, while `skills/` here stays outside every writable root and only the
        conversation's `runs/<id>` is named one."""
        return self._scratch / "ufo"

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
            top_levels = {PurePosixPath(name).parts[0] for name in (*old_skills, *new_skills)}
            for name in top_levels:
                with suppress(FileNotFoundError):
                    shutil.rmtree(root / name)
            for entry in bundle.infolist():
                if entry.is_dir() or entry.filename == "manifest.json":
                    continue
                target = root / entry.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(entry))
            (root / SYSTEM_MANIFEST_NAME).write_bytes(manifest_bytes)

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
            loaded = self._load_system_skill(root, manifest_skills, name, digest)
            if loaded is not None:
                loaded_name, loaded_root = loaded
                roots[loaded_name] = loaded_root
        system_names = tuple(system)
        for name, encoded in user.items():
            loaded_name, loaded_root = self._load_user_skill(root, system_names, name, encoded)
            roots[loaded_name] = loaded_root
        return roots

    def _load_system_skill(
        self,
        root: Path,
        manifest_skills: Mapping[object, object],
        name: object,
        digest: object,
    ) -> tuple[str, str] | None:
        if not isinstance(name, str) or not isinstance(digest, str):
            raise TypeError("invalid system skill")
        declared = manifest_skills.get(name)
        if not isinstance(declared, Mapping) or declared.get("digest") != digest:
            return None
        files = declared.get("files")
        if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            raise TypeError(f"invalid system skill manifest: {name}")
        contents = [(path, (root / name / path).read_bytes()) for path in files]
        if self._skill_digest(contents) != digest:
            return None
        return name, str(root / name)

    def _load_user_skill(
        self,
        root: Path,
        system_names: tuple[object, ...],
        name: object,
        encoded: object,
    ) -> tuple[str, str]:
        if not isinstance(name, str) or not isinstance(encoded, Mapping):
            raise TypeError("invalid user skill")
        self._validate_user_skill_name(name)
        if any(
            system_name == name
            or (
                isinstance(system_name, str)
                and (system_name.startswith(f"{name}/") or name.startswith(f"{system_name}/"))
            )
            for system_name in system_names
        ):
            raise ValueError(f"user skill conflicts with system skill: {name}")
        digest = encoded.get("digest")
        files = encoded.get("files")
        if not isinstance(digest, str) or not isinstance(files, Mapping):
            raise TypeError(f"invalid user skill: {name}")
        contents: list[tuple[str, bytes]] = []
        for path, content in files.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise TypeError(f"invalid user skill file: {name}")
            contents.append((path, urlsafe_b64decode(content)))
        contents.sort()
        if self._skill_digest(contents) != digest:
            raise ValueError(f"user skill does not match its digest: {name}")
        destination = root / name
        with suppress(FileNotFoundError):
            shutil.rmtree(destination)
        for path, content in contents:
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return name, str(destination)

    @staticmethod
    def _system_manifest(root: Path) -> Mapping[str, object]:
        try:
            manifest = json.loads((root / SYSTEM_MANIFEST_NAME).read_bytes())
        except FileNotFoundError:
            return {"skills": {}}
        if not isinstance(manifest, Mapping):
            raise TypeError("invalid system skill manifest")
        return manifest

    @staticmethod
    def _validate_user_skill_name(name: str) -> None:
        parts = PurePosixPath(name).parts
        if len(parts) != 1 or parts[0].startswith("."):
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
            turn_id=spec.turn_id,
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
                # MITM'd host needs these.
                "GIT_SSL_CAINFO": str(ca_path),
                "CARGO_HTTP_CAINFO": str(ca_path),
                **spec.env,
            },
        )

    def _base_env(self) -> dict[str, str]:
        """`GIT_CONFIG_COUNT`/`GIT_CONFIG_PARAMETERS` outrank the config files. `git config
        --global` on `/dev/null` fails to lock, breaking `git lfs install`."""
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
            turn_id=spec.turn_id,
            runtime_root=str(self.ufo_home / RUNTIME_DIRNAME / spec.conversation_id.hex),
            egress_env=self._base_env(),
        )

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
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
        is how a turn starts a server.

        The subprocess is `ufo sandbox` around the command: the kernel confines its writes to the
        workspace, the runtime root, the scratch home, and `TMPDIR`, and every descendant inherits
        the confinement."""
        root = _root(handle)
        rewritten = host_argv(argv, str(root))
        if len(rewritten) >= 3 and rewritten[-3:-1] == ("bash", "-lc"):
            rewritten = (
                *rewritten[:-1],
                f"export PATH={shlex.quote(handle.egress_env['PATH'])}\n{rewritten[-1]}",
            )
        writes = (
            str(root),
            handle.runtime_root,
            str(self._scratch / "home"),
            handle.egress_env.get("TMPDIR", DEFAULT_TMPDIR),
        )
        confined = (
            str(self._scratch / "bin" / CLIENT_BINARY_NAME),
            SANDBOX_VERB,
            *(flag for path in writes if path for flag in (SANDBOX_WRITE_FLAG, path)),
            "--",
            *rewritten,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *confined,
                cwd=str(root),
                env=dict(handle.egress_env),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"no {CLIENT_BINARY_NAME} client to confine the command; build one with "
                "`cargo build --release` in client/"
            ) from error
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
        loop, since the filesystem has no async API. The bytes are staged beside the target and
        renamed onto it: a reader of the path sees the whole of one write or the whole of the one
        before, which is what two writers racing one path need, since a surface may deliver a file
        twice. The staged name is its own, not the target's with a suffix, so the longest filename
        a directory takes still fits, and it is removed on any failure, since the workspace listing
        is the member's own file list and an orphan would appear in it as a file they never made.

        A rename installs a new inode, so an overwrite carries the mode across and an executable a
        turn produced stays executable for the turn that runs it — probed once, so a delete racing
        the write still ends in a created file. Permission bits only: setuid, setgid and sticky do
        not survive a copy-in through any other carrier. A file the copy-in creates lands
        `WORKSPACE_WRITE_MODE` rather than under serve's umask, because the sandbox user is the one
        that reads it."""
        await asyncio.to_thread(self._write, handle, path, content)

    def _write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        target = _host_path(handle, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = target.stat().st_mode & 0o777
        except FileNotFoundError:
            mode = WORKSPACE_WRITE_MODE
        staged = target.parent / f"{STAGED_PREFIX}{uuid4().hex}"
        try:
            staged.write_bytes(content)
            staged.chmod(mode)
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """The workspace is a host directory, so the copy-out is a chunked host read under it — off
        the loop, since the filesystem has no async API."""
        source = await asyncio.to_thread(_host_path(handle, path).open, "rb")
        try:
            while chunk := await asyncio.to_thread(source.read, READ_CHUNK_BYTES):
                yield chunk
        finally:
            await asyncio.to_thread(source.close)

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


def _host_path(handle: SandboxHandle, path: str) -> Path:
    candidate = PurePosixPath(path)
    if candidate.is_relative_to(WORKSPACE_DIR):
        return _root(handle) / candidate.relative_to(WORKSPACE_DIR)
    if handle.runtime_root and candidate.is_relative_to(handle.runtime_root):
        return Path(handle.runtime_root) / candidate.relative_to(handle.runtime_root)
    raise ValueError(f"path {path!r} is outside the sandbox roots")
