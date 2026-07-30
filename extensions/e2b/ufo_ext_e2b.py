"""The E2B carrier extension: a per-conversation cloud sandbox reached through the e2b SDK.

Docker is core's default carrier; this extension registers `e2b` on the `carriers` Manifest point,
so a deploy that sets `[sandbox] backend = "e2b"` runs its sandboxes on E2B without core naming the
provider. Every provider call is awaited on the SDK's async client, so a turn's sandbox I/O never
occupies a thread or the loop while it waits. `create` opens a fresh sandbox on the deploy's
template, resumes the conversation's in-process one, or — when this process holds none — reconnects
the sandbox a prior process left, from the id core seeds on `spec.resume_id` off the conversation's
durable handle; `exec` runs a command through `commands.run`; `read` streams a produced file out
through the filesystem API. `/workspace` is the sandbox's own disk and the only copy of the
conversation's files: the provider pauses an idle sandbox and keeps it indefinitely, and nothing
here can drop one — a paused sandbox costs nothing, and reclaiming it would delete the workspace.

A remote sandbox runs off-cluster, so it reaches the egress proxy at the proxy's externally-
reachable public URL (not a host-local address): every command runs with `HTTP(S)_PROXY` dialing
that URL, the turn's run token as the proxy basic-auth username so each metered request keys to the
turn, the model sentinels the proxy swaps for the real key on the wire, and the proxy CA written
into the sandbox so it terminates TLS the sandbox trusts.

What the provider's clock does — measured against the live service 2026-07-28 on SDK 2.30.0, with
the wider matrix on 2.35.0; transcripts in #826. None of it is inferable from the SDK's types, and
every lease decision below turns on it:

- `timeout` is a wall clock running from the moment it is set, not an idle timer, so it expires
  while a command is still running. Nothing a command does extends it.
- Expiry pauses the container, and `auto_resume` covers **inbound traffic only**: `commands.run`
  and `files.*` wake a paused sandbox, while `set_timeout` and `get_info` answer 404 against one.
  `connect` is the exception that makes this workable — it resumes a paused container *and* sets
  its span in one call, and 404s only for a sandbox the provider no longer has. Its span is a floor:
  it never brings an expiry nearer, so one replica renewing cannot cut a lease out from under
  another mid-command.
- So a lapse heals on a data-plane call by itself, but that resume leases e2b's own 300s default
  rather than the span the caller wanted. Only `connect` sets a lease, and only `connect` both
  resumes and leases — which is why renewal here is a connect and never a `set_timeout`.
- A pause is indefinite and lossless: processes, open fds and local disk all survive one, and a
  resume costs ~110ms after a short pause and ~200ms (360ms worst) after twelve minutes. A lapsed
  lease is a latency event rather than a lost turn, provided the call that meets it resumes rather
  than 404s.
- A command whose stream the pause severs keeps running inside the sandbox and completes; only the
  client's stream dies, and the error class belongs to the SDK's HTTP stack rather than its API, so
  it is version-dependent and cannot be named."""

import os
import shlex
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

from e2b import AsyncSandbox as E2BSdkSandbox
from e2b.exceptions import FileNotFoundException, SandboxNotFoundException, TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox.sandbox_api import SandboxLifecycle, SandboxNetworkOpts

from ufo.sdk.manifest import Manifest
from ufo.sdk.o11y import log
from ufo.sdk.sandbox import (
    NO_PROXY_HOSTS,
    SENTINEL_MODEL_KEY,
    WORKSPACE_DIR,
    CarrierSpec,
    DialTarget,
    ExecResult,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)

CARRIER_NAME = "e2b"
E2B_API_KEY_ENV = "E2B_API_KEY"
E2B_TEMPLATE_NAME = "ufo-sbx"
NODE_GLOBAL_MODULES = "/usr/local/lib/node_modules"
PLAYWRIGHT_BROWSERS_DIR = "/usr/local/lib/playwright"
TRAFFIC_ACCESS_HEADER = "e2b-traffic-access-token"
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
E2B_NETWORK: SandboxNetworkOpts = {"allow_public_traffic": False}
CA_STAGING_PATH = "/root/.ufo-egress-ca.pem"
CA_SANDBOX_PATH = "/usr/local/share/ca-certificates/ufo-egress-ca.crt"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
CA_INSTALL_TIMEOUT_SECONDS = 30
SANDBOX_USER = "user"
WORKSPACE_ENSURE_TIMEOUT_SECONDS = 30
ENSURE_WORKSPACE_COMMAND = (
    f"mkdir -p {WORKSPACE_DIR} && chown {SANDBOX_USER}:{SANDBOX_USER} {WORKSPACE_DIR}"
)
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


class E2BFileStream(Protocol):
    """The SDK's streamed-read reader: an async byte iterator over an open connection, released
    only by an awaited close."""

    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class E2BFiles(Protocol):
    async def write(self, path: str, data: str | bytes, *, user: str | None = None) -> object: ...

    async def read(self, path: str, format: str) -> E2BFileStream: ...


class E2BSandbox(Protocol):
    sandbox_id: str
    traffic_access_token: str | None
    commands: E2BCommands
    files: E2BFiles

    def get_host(self, port: int) -> str: ...


class E2BSdk(Protocol):
    async def create(
        self,
        *,
        template: str,
        timeout: int,  # noqa: ASYNC109
        metadata: dict[str, str],
        lifecycle: SandboxLifecycle,
        network: SandboxNetworkOpts,
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
    """A live sandbox and the moment the provider's clock runs out on it, read before the call that
    sets it so the deadline held here always falls earlier than the real one — a lease believed
    shorter than it is costs a spare renewal, one believed longer loses the container."""

    sandbox: E2BSandbox
    expires_at: float


@dataclass(frozen=True)
class E2BCarrier:
    api_key: str
    template: str
    idle_seconds: int = DEFAULT_IDLE_SECONDS
    """The span every piece of work needs the container to survive regardless of its own length: it
    is the model's thinking between two tool calls, not the calls, that a lease has to outlast."""
    sdk: E2BSdk = E2B_SDK
    clock: Callable[[], float] = time.monotonic
    _live: dict[UUID, _Lease] = field(default_factory=dict)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Open the conversation's sandbox: the one `spec.resume_id` names, else the one this
        process holds, else a fresh one. The named id outranks the in-process cache because the row
        is the arbiter of concurrent opens — a caller retrying with the persisted winner must
        converge on it, and a cache-first read would hand back this process's losing sandbox and
        carry its id over the winner's row; the cache serves only the caller that names nothing,
        the one this process opened moments ago, before any id was persisted. A named id the
        provider no longer has means that sandbox and its workspace are gone — an explicit kill, or
        a provider fault — so a fresh one opens in its place and the loss is named in the log;
        refusing instead would wedge every later turn of the conversation on a sandbox nothing can
        bring back."""
        egress_env = _egress_env(spec.proxy, spec.run_token)
        live = self._leased(spec.conversation_id)
        resume_id = (
            spec.resume_id
            if spec.resume_id is not None
            else live.sandbox.sandbox_id
            if live is not None
            else None
        )
        opened = self.clock()
        sandbox = await self._resume_or_open(spec, resume_id)
        self._live[spec.conversation_id] = _Lease(sandbox, opened + SANDBOX_LEASE_SECONDS)
        await self._install_ca(sandbox, spec.proxy.ca_cert)
        await self._ensure_workspace(sandbox)
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            run_token=spec.run_token,
            egress_env={**egress_env, **spec.env},
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The sandbox `spec.resume_id` names — resumed if the provider paused it — or None when
        there is no id or the provider no longer has it. Always the provider's own answer, never the
        in-process cache's: a cached lease can outlive its sandbox, and a read that answered present
        off it would raise where absence was promised. Never a fresh sandbox either — a read of a
        conversation whose sandbox is gone answers absent rather than opening an empty one and
        persisting its id over the stored handle. No egress env — a read runs `sbxfs` and `cat`,
        nothing that leaves the box."""
        if spec.resume_id is None:
            return None
        opened = self.clock()
        try:
            sandbox = await self.sdk.connect(
                spec.resume_id, timeout=SANDBOX_LEASE_SECONDS, api_key=self.api_key
            )
        except SandboxNotFoundException:
            self._live.pop(spec.conversation_id, None)
            return None
        self._live[spec.conversation_id] = _Lease(sandbox, opened + SANDBOX_LEASE_SECONDS)
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            run_token=spec.run_token,
        )

    async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox:
        """Resume the conversation's sandbox, or open one on the deploy's template. `connect` both
        resumes a paused sandbox and sets its lease, so a resume is one call whatever state the
        container was left in. e2b holds a paused sandbox until something kills it, so an id that
        comes back not-found names one the provider no longer has: the container is cache over the
        durable workspace, so the turn opens a fresh one rather than failing every turn this
        conversation will ever admit against an id nothing can resurrect."""
        if resume_id is not None:
            try:
                return await self.sdk.connect(
                    resume_id, timeout=SANDBOX_LEASE_SECONDS, api_key=self.api_key
                )
            except SandboxNotFoundException:
                log(
                    "sandbox.e2b.resume_missed",
                    conversation_id=str(spec.conversation_id),
                    sandbox_id=resume_id,
                )
        sandbox = await self.sdk.create(
            template=self.template,
            timeout=SANDBOX_LEASE_SECONDS,
            metadata={CONVERSATION_METADATA_KEY: str(spec.conversation_id)},
            lifecycle=E2B_LIFECYCLE,
            network=E2B_NETWORK,
            api_key=self.api_key,
        )
        if not sandbox.traffic_access_token:
            raise RuntimeError(
                "e2b returned no traffic access token for a sandbox created with public "
                "traffic disabled — its ports would be unreachable through the ingress; "
                "check the template"
            )
        return sandbox

    def _leased(self, conversation_id: UUID) -> _Lease | None:
        """The conversation's lease, having dropped every lease whose deadline has passed. `destroy`
        and `_drop` are the map's only other exits and the ingress reaches neither — it dials, for
        every conversation it ever serves — so without this the map holds a sandbox reference for
        every conversation the process has ever touched. Sweeping on lookup rather than by key
        bounds it to the leases still running: a conversation served once and never returned to
        leaves nothing behind. Dropping the reference is the whole reclaim — the SDK caches one
        envd transport, and so one connection pool, per event loop rather than per sandbox, so
        closing an evicted sandbox's own client would close the pool every other sandbox shares.
        The caller's own lease is returned whether or not it lapsed, since a lapsed lease still
        names the container to reconnect to and is the one thing the renewal below reports."""
        lease = self._live.get(conversation_id)
        now = self.clock()
        for expired in [key for key, entry in self._live.items() if entry.expires_at <= now]:
            del self._live[expired]
        return lease

    async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None:
        await sandbox.files.write(CA_STAGING_PATH, ca_cert, user="root")
        try:
            await sandbox.commands.run(
                INSTALL_CA_COMMAND, user="root", timeout=CA_INSTALL_TIMEOUT_SECONDS
            )
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox CA install failed: {detail}") from error

    async def _ensure_workspace(self, sandbox: E2BSandbox) -> None:
        """Guarantee `/workspace` exists and is the sandbox user's, on every create-or-attach path
        — fresh, resumed, and reconnected alike, because the first process to touch a sandbox is not
        always the one that created it. Root, since `/` is root-owned and the sandbox user could
        neither create the directory nor own it."""
        try:
            await sandbox.commands.run(
                ENSURE_WORKSPACE_COMMAND, user="root", timeout=WORKSPACE_ENSURE_TIMEOUT_SECONDS
            )
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox workspace setup failed: {detail}") from error

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's `commands.run`. e2b takes a shell string, so argv
        is quoted into one command — bytes reach the workspace through `write`, never here. A
        non-zero exit and a command timeout arrive as SDK exceptions, mapped to the ExecResult the
        session reads exactly as the shell's own exit code would. It runs under the turn's egress
        env, so its every network call routes through the proxy with the turn's run token — the raw
        model key never enters the sandbox and every request is metered. What it asks of the lease
        is its own timeout with room for the provider to answer, so the container cannot pause
        mid-command, and never less than the idle span, which is what carries the container across
        the model's thinking between one command and the next.

        A stream severed while the command runs arrives as neither of those SDK exceptions, and as
        no class this can name. The command itself keeps running inside the sandbox and completes,
        so this is never retried — it is reported, and the lease is
        dropped so the next call reattaches rather than trust a deadline the provider abandoned."""
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
        except Exception:
            self._drop(handle.conversation_id, "exec")
            raise
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Upload through the sandbox's filesystem API, which creates the parent directories and
        streams the body as its own request — e2b's only channel that carries bytes off the command
        line, since `commands.run` takes a shell string with no stdin. A failed upload drops the
        lease for the same reason a severed command does: the provider answered for a container the
        deadline here still vouches for."""
        sandbox = await self._sandbox(handle, self.idle_seconds)
        try:
            await sandbox.files.write(path, content)
        except Exception:
            self._drop(handle.conversation_id, "write")
            raise

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """Stream a workspace file out through the sandbox's filesystem API, which serves the body
        as its own chunked response — so an arbitrarily large produced file crosses in bounded
        pieces and never sits whole in this process. The reader holds an open connection with no
        finalizer that can release it, so it is closed on every exit path, including a consumer
        that stops mid-file."""
        sandbox = await self._sandbox(handle, self.idle_seconds)
        try:
            stream = await sandbox.files.read(path, format="stream")
        except FileNotFoundException as error:
            raise FileNotFoundError(str(error)) from error
        except Exception:
            self._drop(handle.conversation_id, "read")
            raise
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The sandbox's public per-port host: e2b routes an in-sandbox port over a per-port
        subdomain (`{port}-{sandbox_id}.{domain}`), reached from outside with the sandbox's traffic
        token. The generic inbound path for any service the turn started inside the container (a
        browser's CDP endpoint, a site's dev-server preview). `get_host` is pure address formatting,
        no round trip — the lease is what the caller is really asking for, since an address is
        worthless if the container pauses before the dial. A sandbox the provider no longer has
        raises `SandboxNotFoundException` on reconnect — not this carrier's contract to leak — so it
        maps to `SandboxUnreachable`, the one error every carrier's `dial` raises."""
        try:
            sandbox = await self._sandbox(handle, self.idle_seconds)
        except SandboxNotFoundException as error:
            raise SandboxUnreachable(f"e2b sandbox {handle.container_id!r} is gone") from error
        token = sandbox.traffic_access_token
        return DialTarget(
            host=sandbox.get_host(port),
            tls=True,
            headers={TRAFFIC_ACCESS_HEADER: token} if token else {},
        )

    async def _sandbox(self, handle: SandboxHandle, needed_seconds: int) -> E2BSandbox:
        """The conversation's sandbox, leased past the work about to run on it. Every caller states
        the span it needs and a standing lease that already covers it is used as it stands: renewing
        per call would put a control-plane round trip in front of every tool call, and the lease is
        sized so a working turn buys one about every ten minutes instead of one per command.

        The deadline held here is a belief about a clock the provider owns, so what makes it safe to
        trust is that the only operation it gates is `connect`, which is correct whether or not the
        belief still holds. A renewal never asks for less than `SANDBOX_LEASE_SECONDS` and reads the
        clock before the call, so the deadline recorded is always earlier than the real one and the
        container is never worked on past what the provider agreed to. The entry is dropped before
        the call and restored only by a connect that returned, so a provider fault leaves nothing
        behind for the next call to trust.

        The lease is keyed by conversation but the caller names a container, so a lease naming a
        different one is a miss however fresh it is: the handle is read from the conversation row
        per call, and a sandbox recreated since the lease was taken makes that row — not the
        cache — the truth about where this conversation's work goes."""
        lease = self._leased(handle.conversation_id)
        renewed = self.clock()
        if (
            lease is not None
            and lease.sandbox.sandbox_id == handle.container_id
            and lease.expires_at - renewed >= needed_seconds
        ):
            return lease.sandbox
        self._live.pop(handle.conversation_id, None)
        span = max(SANDBOX_LEASE_SECONDS, needed_seconds)
        sandbox = await self.sdk.connect(handle.container_id, timeout=span, api_key=self.api_key)
        self._live[handle.conversation_id] = _Lease(sandbox, renewed + span)
        log(
            "sandbox.e2b.leased",
            conversation_id=str(handle.conversation_id),
            sandbox_id=handle.container_id,
            span=span,
            lapsed=lease is not None and lease.expires_at <= renewed,
        )
        return sandbox

    def _drop(self, conversation_id: UUID, during: str) -> None:
        """Forget the conversation's lease after a provider call failed on it. The deadline is only
        evidence while the container answers to it; a call that did not come back is the provider
        saying it no longer does, whatever the clock here still reads."""
        self._live.pop(conversation_id, None)
        log("sandbox.e2b.lease_dropped", conversation_id=str(conversation_id), during=during)


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
