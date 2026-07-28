"""The E2B carrier extension: a per-conversation cloud sandbox reached through the e2b SDK.

Docker is core's default carrier; this extension registers `e2b` on the `carriers` Manifest point,
so a deploy that sets `[sandbox] backend = "e2b"` runs its sandboxes on E2B without core naming the
provider. Every provider call is awaited on the SDK's async client, so a turn's sandbox I/O never
occupies a thread or the loop while it waits. `create` opens a fresh sandbox
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
— it refreshes a prefix-scoped credential through the proxy's credential endpoint, then talks to S3
directly. The reaper reclaims a sandbox a prior process created by reconnecting the stored id and
pausing it — the conversation's durable `sandbox_handle` is the map, so idle reclaim no longer
leans on the provider's own timeout alone."""

import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

from e2b import AsyncSandbox as E2BSdkSandbox
from e2b.exceptions import SandboxNotFoundException, TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox.sandbox_api import SandboxLifecycle

from ufo.sdk.manifest import Manifest
from ufo.sdk.sandbox import (
    MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
    MOUNT_TIMEOUT_SECONDS,
    NO_PROXY_HOSTS,
    SANDBOX_FS_CREDENTIAL_PATH,
    SANDBOX_FS_TOKEN_STAGING_PATH,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    BlobStore,
    CarrierSpec,
    ExecResult,
    MountSpec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    install_token_command,
    mount_health_check,
    mount_scripts,
    prepare_token_staging_command,
    s3fs_command,
)

CARRIER_NAME = "e2b"
E2B_API_KEY_ENV = "E2B_API_KEY"
E2B_TEMPLATE_NAME = "ufo-sbx"
NODE_GLOBAL_MODULES = "/usr/local/lib/node_modules"
PLAYWRIGHT_BROWSERS_DIR = "/usr/local/lib/playwright"
SANDBOX_ENV: dict[str, str] = {
    "NODE_PATH": NODE_GLOBAL_MODULES,
    "PLAYWRIGHT_BROWSERS_PATH": PLAYWRIGHT_BROWSERS_DIR,
}
DEFAULT_IDLE_SECONDS = 300
SANDBOX_LEASE_SECONDS = 900
EXEC_LEASE_MARGIN_SECONDS = 60
EXEC_TIMEOUT_CODE = 124
CONVERSATION_METADATA_KEY = "ufo.conversation_id"
E2B_LIFECYCLE: SandboxLifecycle = {"on_timeout": "pause", "auto_resume": True}
CA_STAGING_PATH = "/root/.ufo-egress-ca.pem"
CA_SANDBOX_PATH = "/usr/local/share/ca-certificates/ufo-egress-ca.crt"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
CA_INSTALL_TIMEOUT_SECONDS = 30
INSTALL_CA_COMMAND = (
    f"cmp -s {CA_STAGING_PATH} {CA_SANDBOX_PATH} || {{ "
    f"install -m 0644 {CA_STAGING_PATH} {CA_SANDBOX_PATH} && "
    f"/usr/sbin/update-ca-certificates; }} || {{ rm -f {CA_SANDBOX_PATH}; exit 1; }}"
)


def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]:
    """The environment a sandbox command runs under so its every call off the box routes through the
    egress proxy: `HTTP(S)_PROXY` dial the proxy at its public base with the turn's run token as the
    basic-auth username (so the proxy attributes and meters the request to the turn), `NO_PROXY`
    exempts the sandbox's own loopback (reaching a service this turn started inside the box is not
    egress), the model keys are the sentinels the proxy swaps for the real key on the wire, and the
    CA is the one written into the sandbox so the proxy can terminate TLS the sandbox trusts. The
    signed run token is URL-safe, so it drops into the URL's userinfo unescaped. Off-cluster means
    the public base is required — absent it (the guard `serve` applies at boot), the sandbox would
    have no metered route out, so this fails loud rather than build an open sandbox."""
    if proxy.public_url is None:
        raise RuntimeError(
            "the e2b carrier runs off-cluster and needs a reachable egress proxy; "
            "set [sandbox] proxy_public_url to the externally-reachable proxy URL"
        )
    parsed = urlsplit(proxy.public_url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise RuntimeError(
            "the e2b carrier requires an HTTPS [sandbox] proxy_public_url so its run token is "
            "encrypted in transit"
        )
    proxy_url = f"https://{run_token}:@{parsed.netloc}"
    return {
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
        "NODE_EXTRA_CA_CERTS": CA_SANDBOX_PATH,
    }


class E2BCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int


class E2BCommands(Protocol):
    """The SDK's own signatures, mirrored: `timeout` is e2b's keyword, not a timeout this repo
    offers, so it cannot become an `asyncio.timeout` around the call."""

    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> E2BCommandResult: ...


class E2BFiles(Protocol):
    async def make_dir(self, path: str, *, user: str | None = None) -> bool: ...

    async def write(self, path: str, data: str | bytes, *, user: str | None = None) -> object: ...


class E2BSandbox(Protocol):
    sandbox_id: str
    traffic_access_token: str | None
    commands: E2BCommands
    files: E2BFiles

    async def pause(self, **opts: object) -> bool: ...

    async def set_timeout(
        self,
        timeout: int,  # noqa: ASYNC109
        **opts: object,
    ) -> None: ...

    def get_host(self, port: int) -> str: ...


class E2BSdk(Protocol):
    async def create(
        self,
        *,
        template: str,
        timeout: int,  # noqa: ASYNC109
        metadata: dict[str, str],
        lifecycle: SandboxLifecycle,
        api_key: str,
    ) -> E2BSandbox: ...

    async def connect(
        self,
        sandbox_id: str,
        *,
        timeout: int,  # noqa: ASYNC109
        api_key: str,
    ) -> E2BSandbox: ...


E2B_SDK = cast(E2BSdk, E2BSdkSandbox)


@dataclass(frozen=True, slots=True)
class _Lease:
    """A live sandbox and the moment its provider clock runs out, measured before the call that set
    it so the deadline held here is always earlier than the real one — a lease believed shorter than
    it is costs a spare renewal, one believed longer loses the container mid-command."""

    sandbox: E2BSandbox
    expires_at: float


@dataclass(frozen=True)
class E2BCarrier:
    api_key: str
    template: str
    idle_seconds: int = DEFAULT_IDLE_SECONDS
    """The span every piece of work needs the container to survive regardless of its own length: it
    is the model's thinking between two tool calls, not the calls, a lease has to outlast."""
    sdk: E2BSdk = E2B_SDK
    clock: Callable[[], float] = time.monotonic
    _live: dict[UUID, _Lease] = field(default_factory=dict)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        egress_env = _egress_env(spec.proxy, spec.run_token)
        public_url = cast(str, spec.proxy.public_url)
        live = self._live.get(spec.conversation_id)
        resume_id = live.sandbox.sandbox_id if live is not None else spec.resume_id
        opened = self.clock()
        if resume_id is not None:
            sandbox = await self.sdk.connect(
                resume_id, timeout=SANDBOX_LEASE_SECONDS, api_key=self.api_key
            )
        else:
            sandbox = await self.sdk.create(
                template=self.template,
                timeout=SANDBOX_LEASE_SECONDS,
                metadata={CONVERSATION_METADATA_KEY: str(spec.conversation_id)},
                lifecycle=E2B_LIFECYCLE,
                api_key=self.api_key,
            )
            await sandbox.files.make_dir(WORKSPACE_DIR)
        self._live[spec.conversation_id] = _Lease(sandbox, opened + SANDBOX_LEASE_SECONDS)
        await self._install_ca(sandbox, spec.proxy.ca_cert)
        await self._mount_s3(
            sandbox,
            spec.mount,
            f"{public_url.rstrip('/')}{SANDBOX_FS_CREDENTIAL_PATH.rstrip('/')}",
        )
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            mount=spec.mount,
            traffic_token=sandbox.traffic_access_token,
            run_token=spec.run_token,
            egress_env={**egress_env, **spec.env},
        )

    async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None:
        await sandbox.files.write(CA_STAGING_PATH, ca_cert, user="root")
        try:
            await sandbox.commands.run(
                INSTALL_CA_COMMAND, user="root", timeout=CA_INSTALL_TIMEOUT_SECONDS
            )
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox CA install failed: {detail}") from error

    async def _mount_s3(self, sandbox: E2BSandbox, mount: MountSpec, credential_url: str) -> None:
        """Bring the conversation's workspace S3 prefix up at /workspace over s3fs. Runs on every
        create/resume and skips a mount the health probe passes, so a shared sandbox never remounts
        under an in-flight dispatch. Writes the private endpoint token, then runs mount
        orchestration as root. The s3fs daemon drops to its
        dedicated user, refreshes scoped credentials through the local relay, and reaches S3
        directly; neither path carries the agent's egress env."""
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
        await sandbox.commands.run(
            prepare_token_staging_command(), user="root", timeout=MOUNT_TIMEOUT_SECONDS
        )
        await sandbox.files.write(
            SANDBOX_FS_TOKEN_STAGING_PATH, mount.credential_token, user="root"
        )
        await sandbox.commands.run(
            install_token_command(), user="root", timeout=MOUNT_TIMEOUT_SECONDS
        )
        if await self._mount_healthy(sandbox):
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
        try:
            await sandbox.commands.run(prepare, user="root", timeout=MOUNT_TIMEOUT_SECONDS)
            await sandbox.commands.run(mount_cmd, user="root", timeout=MOUNT_TIMEOUT_SECONDS)
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox-fs mount failed: {detail}") from error
        if not await self._mount_healthy(sandbox):
            raise RuntimeError("sandbox-fs mount failed its health check")

    async def _mount_healthy(self, sandbox: E2BSandbox) -> bool:
        try:
            await sandbox.commands.run(
                mount_health_check(WORKSPACE_DIR),
                user="root",
                timeout=MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS,
            )
            return True
        except (CommandExitException, TimeoutException):
            return False

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's `commands.run`. e2b takes a shell string, so argv
        is quoted into one command — bytes reach the workspace through `write`, never here. A
        non-zero exit and a command timeout arrive as SDK exceptions, mapped to the ExecResult the
        session reads exactly as the shell's own exit code would. It runs under the turn's egress
        env, so its every network call routes through the proxy with the turn's run token — the raw
        model key never enters the sandbox and every request is metered. What it needs of the lease
        is its own timeout with room for the provider to return, so the sandbox cannot expire
        mid-command, and never less than the idle span, which is what carries the container across
        the model's thinking between one command and the next."""
        sandbox = await self._sandbox(
            handle, max(self.idle_seconds, timeout_s + EXEC_LEASE_MARGIN_SECONDS)
        )
        command = shlex.join(argv)
        try:
            result = await sandbox.commands.run(
                command,
                cwd=WORKSPACE_DIR,
                envs={**SANDBOX_ENV, **handle.egress_env},
                timeout=timeout_s,
            )
        except CommandExitException as error:
            return ExecResult(stdout=error.stdout, stderr=error.stderr, exit_code=error.exit_code)
        except TimeoutException as error:
            return ExecResult(stdout="", stderr=str(error), exit_code=EXEC_TIMEOUT_CODE)
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Upload through the sandbox's filesystem API, which creates the parent directories and
        streams the body as its own request — e2b's only channel that carries bytes off the command
        line, since `commands.run` takes a shell string with no stdin."""
        sandbox = await self._sandbox(handle, self.idle_seconds)
        await sandbox.files.write(path, content)

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
        sandbox = await self._sandbox(handle, self.idle_seconds)
        return sandbox.get_host(port)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Pause and drop the conversation's sandbox. The reaper passes the stored id, so a sandbox
        this process still holds is paused directly, and one it never held (a prior process created
        it) is reconnected from `handle.container_id` and paused too — the durable handle lets idle
        reclaim reach a sandbox this process could not otherwise address. An empty id (the seam's
        no-container reap) connects to nothing and is a no-op that never raises. A sandbox already
        gone (paused past e2b's own retention, or reaped by a concurrent process) raises
        SandboxNotFoundException on reconnect — also a no-op, since nothing is left to pause."""
        live = self._live.pop(handle.conversation_id, None)
        sandbox = live.sandbox if live is not None else None
        if sandbox is None and handle.container_id:
            try:
                sandbox = await self.sdk.connect(
                    handle.container_id, timeout=self.idle_seconds, api_key=self.api_key
                )
            except SandboxNotFoundException:
                return
        if sandbox is not None:
            await sandbox.pause(api_key=self.api_key)

    async def _sandbox(self, handle: SandboxHandle, needed_seconds: int) -> E2BSandbox:
        """The conversation's sandbox, guaranteed to outlive the work about to run on it. e2b's
        timeout is a wall clock the provider enforces from the moment it is set, not an idle timer:
        it expires while a command is still running and `E2B_LIFECYCLE` pauses the container out
        from under it, tearing down the command's own stream. So every caller states the span it
        needs, and a lease that already covers it is used as it stands — renewal is a control-plane
        round trip on the critical path of every tool call, and the standing lease is sized so a
        working turn buys one about every ten minutes instead of one per command.

        Reading the deadline locally is safe because a renewal never asks for less than
        `SANDBOX_LEASE_SECONDS` and nothing the tool surface can ask for reaches it — `bash`'s
        ceiling is the largest need and lands well under, which the suite pins — so every renewal
        buys that same span, and another replica can only push the real expiry further out than the
        deadline recorded here, never nearer. The `max` below guards a need that outgrew the lease
        rather than a branch taken today, and stays sound because the turn queue is partitioned by
        conversation: one sandbox has one process working it at a time. The lease is opened by
        `create`, or by the reconnect below when a process's first touch of a handle is the work
        itself, and ended by `destroy`."""
        live = self._live.get(handle.conversation_id)
        renewed = self.clock()
        if live is not None and live.expires_at - renewed >= needed_seconds:
            return live.sandbox
        span = max(SANDBOX_LEASE_SECONDS, needed_seconds)
        if live is None:
            sandbox = await self.sdk.connect(
                handle.container_id, timeout=span, api_key=self.api_key
            )
        else:
            sandbox = live.sandbox
            await sandbox.set_timeout(span, api_key=self.api_key)
        self._live[handle.conversation_id] = _Lease(sandbox, renewed + span)
        return sandbox


def build_e2b_carrier() -> E2BCarrier:
    """Build the carrier the `[sandbox] backend = "e2b"` deploy selects: the template and API key
    are `E2B_TEMPLATE_NAME` and `E2B_API_KEY`. `sandbox/build_template.py` publishes that same
    constant from the shared image definition, so the E2B image and Docker image never drift. A
    missing key fails loud at boot rather than on the first turn. `serve` calls this once, only
    when the deploy selects `e2b`."""
    key = os.environ.get(E2B_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"e2b carrier selected but {E2B_API_KEY_ENV} is not set")
    return E2BCarrier(api_key=key, template=E2B_TEMPLATE_NAME)


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=build_e2b_carrier, off_cluster=True),),
    )
