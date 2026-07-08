"""The E2B carrier extension: a per-conversation cloud sandbox reached through the e2b SDK.

Docker is core's default carrier; this extension registers `e2b` on the `carriers` Manifest point,
so a deploy that sets `[sandbox] backend = "e2b"` runs its sandboxes on E2B without core naming the
provider. The e2b SDK is synchronous, so every provider call crosses `asyncio.to_thread` — the one
blocking boundary tolerated, kept to the named SDK callable it wraps. `create` opens a fresh sandbox
on the deploy's template, resumes the conversation's in-process one, or — when this process holds
none — reconnects the sandbox a prior process left, from the id core seeds on `spec.resume_id` off
the conversation's durable handle; `exec` runs a command through `commands.run`; `export` promotes a
produced file into the artifact store with a server-side copy inside the bucket; `destroy` pauses
the sandbox so its next turn resumes cheaply. The container stays a disposable cache over the
durable workspace.

A remote sandbox runs off-cluster, so it reaches the egress proxy at the proxy's externally-
reachable public URL (not a host-local address): every command runs with `HTTP(S)_PROXY` dialing
that URL, the turn's run token as the proxy basic-auth username so each metered request keys to the
turn, the model sentinels the proxy swaps for the real key on the wire, and the proxy CA written
into the sandbox so it terminates TLS the sandbox trusts. The s3fs mount step runs without that env
— it talks to S3 directly with its own prefix-scoped credential, never through the agent's egress
proxy. The reaper reclaims a sandbox a prior process created by reconnecting the stored id and
pausing it — the conversation's durable `sandbox_handle` is the map, so idle reclaim no longer
leans on the provider's own timeout alone."""

import asyncio
import base64
import os
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, cast
from uuid import UUID

from e2b import Sandbox as E2BSdkSandbox
from e2b.exceptions import TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox.sandbox_api import SandboxLifecycle

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
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    aws_credentials_file,
    mount_scripts,
    s3fs_command,
)

CARRIER_NAME = "e2b"
E2B_API_KEY_ENVS = ("E2B_API_KEY", "UFO_E2B_API_KEY")
E2B_TEMPLATE_ENV = "UFO_E2B_TEMPLATE"
DEFAULT_TIMEOUT_SECONDS = 300
EXEC_TIMEOUT_CODE = 124
CONVERSATION_METADATA_KEY = "ufo.conversation_id"
E2B_LIFECYCLE: SandboxLifecycle = {"on_timeout": "pause", "auto_resume": True}
CA_SANDBOX_PATH = "/home/user/.ufo-egress-ca.pem"


def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]:
    """The environment a sandbox command runs under so its every network call routes through the
    egress proxy: `HTTP(S)_PROXY` dial the proxy at its public base with the turn's run token as the
    basic-auth username (so the proxy attributes and meters the request to the turn), the model keys
    are the sentinels the proxy swaps for the real key on the wire, and the CA is the one written
    into the sandbox so the proxy can terminate TLS the sandbox trusts. The run token is base64url,
    so it drops into the URL's userinfo unescaped. Off-cluster means the proxy's public base is
    required — absent it (the guard `serve` applies at boot), the sandbox would have no metered
    route out, so this fails loud rather than build an open sandbox."""
    if proxy.public_url is None:
        raise RuntimeError(
            "the e2b carrier runs off-cluster and needs a reachable egress proxy; "
            "set [sandbox] proxy_public_url to the externally-reachable proxy URL"
        )
    scheme, _, authority = proxy.public_url.partition("://")
    proxy_url = f"{scheme}://{run_token}:@{authority}"
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "ANTHROPIC_API_KEY": SENTINEL_MODEL_KEY,
        "OPENAI_API_KEY": SENTINEL_MODEL_KEY,
        "SSL_CERT_FILE": CA_SANDBOX_PATH,
        "REQUESTS_CA_BUNDLE": CA_SANDBOX_PATH,
        "CURL_CA_BUNDLE": CA_SANDBOX_PATH,
        "NODE_EXTRA_CA_CERTS": CA_SANDBOX_PATH,
    }


class E2BCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int


class E2BCommands(Protocol):
    def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,
    ) -> E2BCommandResult: ...


class E2BFiles(Protocol):
    def make_dir(self, path: str, *, user: str | None = None) -> bool: ...

    def write(self, path: str, data: str | bytes) -> object: ...


class E2BSandbox(Protocol):
    sandbox_id: str
    traffic_access_token: str | None
    commands: E2BCommands
    files: E2BFiles

    def pause(self, **opts: object) -> bool: ...

    def get_host(self, port: int) -> str: ...


class E2BSdk(Protocol):
    def create(
        self,
        *,
        template: str,
        timeout: int,
        metadata: dict[str, str],
        lifecycle: SandboxLifecycle,
        api_key: str,
    ) -> E2BSandbox: ...

    def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox: ...


E2B_SDK = cast(E2BSdk, E2BSdkSandbox)


@dataclass(frozen=True)
class E2BCarrier:
    api_key: str
    template: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    sdk: E2BSdk = E2B_SDK
    _live: dict[UUID, E2BSandbox] = field(default_factory=dict)
    _egress: dict[UUID, dict[str, str]] = field(default_factory=dict)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        live = self._live.get(spec.conversation_id)
        resume_id = live.sandbox_id if live is not None else spec.resume_id
        if resume_id is not None:
            sandbox = await asyncio.to_thread(
                self.sdk.connect,
                resume_id,
                timeout=self.timeout_seconds,
                api_key=self.api_key,
            )
        else:
            sandbox = await asyncio.to_thread(
                self.sdk.create,
                template=self.template,
                timeout=self.timeout_seconds,
                metadata={CONVERSATION_METADATA_KEY: str(spec.conversation_id)},
                lifecycle=E2B_LIFECYCLE,
                api_key=self.api_key,
            )
            await asyncio.to_thread(sandbox.files.make_dir, WORKSPACE_DIR)
        self._live[spec.conversation_id] = sandbox
        # Write the proxy CA and (re)build the turn's egress env on every create — a resume (in this
        # process or a prior one's, reconnected from spec.resume_id) picks up this process's CA (the
        # proxy mints a fresh one at boot) and this turn's run token, so exec never reads a missing
        # _egress: create is the one place that seeds it and every turn opens through create.
        await asyncio.to_thread(sandbox.files.write, CA_SANDBOX_PATH, spec.proxy.ca_cert)
        self._egress[spec.conversation_id] = _egress_env(spec.proxy, spec.run_token)
        await self._mount_s3(sandbox, spec.mount)
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            mount=spec.mount,
            traffic_token=sandbox.traffic_access_token,
        )

    async def _mount_s3(self, sandbox: E2BSandbox, mount: MountSpec) -> None:
        """Bring the conversation's workspace S3 prefix up at /workspace over s3fs. Runs on every
        create/resume and is idempotent: skips a healthy mount so a subagent sharing the sandbox
        never remounts under an in-flight dispatch. Writes the prefix-scoped credential, then runs
        the root `prepare` and the agent `mount` through the sync SDK off the loop — the privileged
        prepare runs as root, the s3fs mount as the agent. These steps pass no egress env, so s3fs
        reaches S3 directly with its own scoped credential — never through the agent's egress proxy,
        whose default-deny rules would refuse the S3 host at CONNECT (docker's PROXY_CLEAR_ARGS)."""
        if mount.kind != "s3":
            return
        if await self._mount_healthy(sandbox):
            return
        if (
            mount.credentials is None
            or mount.bucket is None
            or mount.key_prefix is None
            or mount.s3_url is None
            or mount.region is None
        ):
            raise RuntimeError("s3 workspace mount is missing its scoped credential or endpoint")
        s3fs = s3fs_command(
            mount.bucket,
            mount.key_prefix,
            WORKSPACE_DIR,
            mount.s3_url,
            mount.region,
            mount.path_style,
        )
        prepare, mount_cmd = mount_scripts(WORKSPACE_DIR, s3fs)
        try:
            await asyncio.to_thread(
                sandbox.files.write, AWS_CREDENTIALS_PATH, aws_credentials_file(mount.credentials)
            )
            await asyncio.to_thread(
                sandbox.commands.run, prepare, user="root", timeout=MOUNT_TIMEOUT_SECONDS
            )
            await asyncio.to_thread(sandbox.commands.run, mount_cmd, timeout=MOUNT_TIMEOUT_SECONDS)
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox-fs mount failed: {detail}") from error

    async def _mount_healthy(self, sandbox: E2BSandbox) -> bool:
        try:
            await asyncio.to_thread(
                sandbox.commands.run,
                f"mountpoint -q {shlex.quote(WORKSPACE_DIR)}",
                timeout=MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
            )
            return True
        except CommandExitException:
            return False

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's synchronous `commands.run`. e2b takes a shell
        string with no stdin channel, so argv is quoted into one command and any stdin rides in
        base64 through the sandbox's own `base64 -d` — the write path the file tools depend on. A
        non-zero exit and a command timeout arrive as SDK exceptions, mapped to the ExecResult the
        session reads exactly as the shell's own exit code would. The command runs under the turn's
        egress env, so its every network call routes through the proxy with the turn's run token —
        the raw model key never enters the sandbox and every request is metered."""
        sandbox = await self._sandbox(handle)
        command = shlex.join(argv)
        if stdin:
            payload = shlex.quote(base64.b64encode(stdin).decode())
            command = f"printf %s {payload} | base64 -d | {command}"
        try:
            result = await asyncio.to_thread(
                sandbox.commands.run,
                command,
                cwd=WORKSPACE_DIR,
                envs=self._egress[handle.conversation_id],
                timeout=timeout_s,
            )
        except CommandExitException as error:
            return ExecResult(stdout=error.stdout, stderr=error.stderr, exit_code=error.exit_code)
        except TimeoutException as error:
            return ExecResult(stdout="", stderr=str(error), exit_code=EXEC_TIMEOUT_CODE)
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        """Promote a produced workspace file into the artifact store with a server-side copy inside
        the bucket — the bytes never leave S3, so nothing reads back through the remote pod. The
        file is already flushed to the workspace S3 prefix by the time export runs: a prior tool
        wrote and closed it through s3fs and `share_file`'s preflight has already streamed it — the
        same flush assumption docker's s3 export makes. `handle.mount` carries the `key_prefix`
        create set from `spec.mount`, so the source object is `<key_prefix>/<rel>` in the same
        bucket as the store."""
        mount = handle.mount
        if mount is None or mount.kind != "s3" or mount.key_prefix is None:
            raise RuntimeError("e2b export requires an s3 workspace mount")
        rel = PurePosixPath(path).relative_to(WORKSPACE_DIR)
        await blob.copy(f"{mount.key_prefix}/{rel}", key)

    async def host(self, handle: SandboxHandle, port: int) -> str:
        """The sandbox's public per-port host: e2b routes an in-sandbox port over a per-port
        subdomain (`{port}-{sandbox_id}.{domain}`), reached from outside with the sandbox's traffic
        token. The generic inbound path for any service the turn started inside the container (a
        browser's CDP endpoint, a site's dev-server preview). `get_host` is pure address formatting,
        no round trip."""
        sandbox = await self._sandbox(handle)
        return sandbox.get_host(port)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Pause and drop the conversation's sandbox. The reaper passes the stored id, so a sandbox
        this process still holds is paused directly, and one it never held (a prior process created
        it) is reconnected from `handle.container_id` and paused too — the durable handle lets idle
        reclaim reach a sandbox this process could not otherwise address. An empty id (the seam's
        no-container reap) connects to nothing and is a no-op that never raises."""
        self._egress.pop(handle.conversation_id, None)
        live = self._live.pop(handle.conversation_id, None)
        if live is None and handle.container_id:
            live = await asyncio.to_thread(
                self.sdk.connect,
                handle.container_id,
                timeout=self.timeout_seconds,
                api_key=self.api_key,
            )
        if live is not None:
            await asyncio.to_thread(live.pause, api_key=self.api_key)

    async def _sandbox(self, handle: SandboxHandle) -> E2BSandbox:
        live = self._live.get(handle.conversation_id)
        if live is not None:
            return live
        sandbox = await asyncio.to_thread(
            self.sdk.connect,
            handle.container_id,
            timeout=self.timeout_seconds,
            api_key=self.api_key,
        )
        self._live[handle.conversation_id] = sandbox
        return sandbox


def build_e2b_carrier() -> E2BCarrier:
    """Build the carrier the `[sandbox] backend = "e2b"` deploy selects: the template and API key
    come from the environment (`UFO_E2B_TEMPLATE`, and `E2B_API_KEY` / `UFO_E2B_API_KEY`).
    The template is the one `sandbox/build_template.py` publishes from the single image definition
    (`ufo-sbx`), so the E2B image and the Docker image never drift. An e2b backend with either
    unset fails loud at boot rather than on the first turn. `serve` calls this once, only when the
    deploy selects `e2b`."""
    template = os.environ.get(E2B_TEMPLATE_ENV)
    if not template:
        raise RuntimeError(f"e2b carrier selected but {E2B_TEMPLATE_ENV} is not set")
    key = next((os.environ[name] for name in E2B_API_KEY_ENVS if os.environ.get(name)), None)
    if not key:
        raise RuntimeError(
            f"e2b carrier selected but none of {E2B_API_KEY_ENVS} is set in the environment"
        )
    return E2BCarrier(api_key=key, template=template)


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=build_e2b_carrier, off_cluster=True),),
    )
