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

import asyncio
import os
import shlex
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, overload
from uuid import UUID

import httpx
from e2b import AsyncSandbox as E2BSdkSandbox
from e2b.exceptions import FileNotFoundException, SandboxNotFoundException, TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox.sandbox_api import SandboxLifecycle, SandboxNetworkOpts

from ufo.sdk.manifest import Manifest
from ufo.sdk.o11y import emit_metric, log
from ufo.sdk.sandbox import (
    CA_SANDBOX_PATH,
    CA_STAGING_PATH,
    SANDBOX_ENV,
    SANDBOX_SIZES,
    WORKSPACE_DIR,
    CarrierSpec,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
    egress_proxy_env,
    sbxfs_file_op,
)

CARRIER_NAME = "e2b"
E2B_API_KEY_ENV = "E2B_API_KEY"
E2B_TEMPLATES_ENV = "E2B_TEMPLATES"
TRAFFIC_ACCESS_HEADER = "e2b-traffic-access-token"
SANDBOX_LEASE_SECONDS = 300
"""The autosuspend span: every create, resume, and renewal leases at least this much, so a sandbox
pauses — and releases its concurrency slot — within five minutes of its last leased call. Work that
outruns it never pauses mid-run, because each call leases past its own need; a turn that thinks past
it pays a sub-second auto-resume on its next call rather than holding a slot through the silence."""
LEASE_MARGIN_SECONDS = 60
"""Room past a call's own span for the provider to answer — what `exec` adds to a command's timeout
and the whole span a bounded upload asks of the lease. A `read` is the exception: its stream cannot
renew mid-flight, so it asks for the full autosuspend span instead."""
DIAL_LEASE_SECONDS = 900
"""What a dial's renewal leases. The address a dial hands out is consumed off-carrier — a turn's
CDP session, a site request's stream — so no carrier call renews while the exchange runs, and a
pause severs it with nothing upstream that reconnects. The dial guarantees the autosuspend span
ahead and renews to this, so a conversation that dialed holds its slot up to fifteen minutes;
everything else frees at the autosuspend span."""
EXEC_TIMEOUT_CODE = 124
SESSION_LEADER_CMD = "setsid"
"""What makes a command its own process group leader, so its pid names the whole tree it forks.
envd runs every command in one shared group of its own, so the group a command is born into is the
carrier's other commands — signalling that would stop the very call doing the signalling."""
EXEC_STOP_TIMEOUT_SECONDS = 15
SILENT_PROBE_CMD = "true"
SILENT_PROBE_SECONDS = 10
SILENT_MARK_SECONDS = 60
"""How long one container's silence stays evidence about it. Inside the span a marked box is asked
one bounded word before a command commits its whole deadline, and a box that cannot answer that word
fails the command at once. Past the span the mark is gone and the next command runs as any other
would, because silence is also what a merely saturated box does — load 16 on four cores with memory
exhausted answers nothing for a while and then answers everything — and a mark with no end condemns
that box for the whole life of the process. A minute is longer than the stop and the probe that set
the mark, and shorter than a turn, so a box that was busy comes back inside the turn that met it and
one that is really gone is re-marked by the next unanswered call rather than remembered forever."""
CONVERSATION_METADATA_KEY = "ufo.conversation_id"
E2B_LIFECYCLE: SandboxLifecycle = {"on_timeout": "pause", "auto_resume": True}
E2B_NETWORK: SandboxNetworkOpts = {"allow_public_traffic": False}
CA_INSTALL_TIMEOUT_SECONDS = 30
SANDBOX_USER = "user"
WORKSPACE_ENSURE_TIMEOUT_SECONDS = 30
RESUME_PREPARE_TIMEOUT_SECONDS = 5
PREPARE_ATTEMPTS = 3
PREPARE_RETRY_SECONDS = 1.0
RESUME_TRANSPORT_RETRIES = 2
RESUME_RETRY_DELAY_SECONDS = 1.0
RESUME_TOTAL_TIMEOUT_SECONDS = 90.0
"""The whole retried resume, bounded here rather than left to the SDK's per-request default.

Attempts alone bound nothing a member feels: three of them against a control plane that answers
only by timing out is three times whatever `request_timeout` happens to be, a third-party default
this repo neither sets nor asserts and which `ConnectionConfig` maps to no timeout at all when a
caller passes 0. A turn's setup runs before its first round, so the span is the member's whole wait
with no answer at the end of it."""
ENSURE_WORKSPACE_COMMAND = (
    f"mkdir -p {WORKSPACE_DIR} && chown {SANDBOX_USER}:{SANDBOX_USER} {WORKSPACE_DIR}"
)
INSTALL_CA_COMMAND = (
    f"cmp -s {CA_STAGING_PATH} {CA_SANDBOX_PATH} || {{ "
    f"install -m 0644 {CA_STAGING_PATH} {CA_SANDBOX_PATH} && "
    f"/usr/sbin/update-ca-certificates; }} || {{ rm -f {CA_SANDBOX_PATH}; exit 1; }}"
)
WORKLOAD_CGROUPS = ("/sys/fs/cgroup/user", "/sys/fs/cgroup/ptys")
"""Where envd runs everything a turn does: `commands.run` processes land in `user`, PTY sessions
in `ptys`. envd itself runs apart in `system.slice/envd.service`, so a ceiling on these two bounds
the workload's whole reach without touching the daemon that runs it."""
WORKLOAD_MEMORY_RESERVE_KB = 512 * 1024
"""Guest memory the workload's ceiling leaves for envd and the kernel. A workload that exhausts
the guest takes envd with it, and a wedged envd frozen into a pause snapshot is a sandbox that can
never resume — the OOM killer must find its victim inside the workload's cgroup, never outside."""
WORKLOAD_PIDS_MAX = 2048
CAP_WORKLOAD_COMMAND = (
    "total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo) && "
    f"max_bytes=$(( (total_kb - {WORKLOAD_MEMORY_RESERVE_KB}) * 1024 )) && "
    '[ "$max_bytes" -gt 0 ] && '
    f"for cg in {' '.join(WORKLOAD_CGROUPS)}; do "
    'echo "$max_bytes" > "$cg/memory.max" && '
    f'echo {WORKLOAD_PIDS_MAX} > "$cg/pids.max" || exit 1; done'
)
WORKLOAD_CAP_TIMEOUT_SECONDS = 30


class E2BCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int


class E2BCommandHandle(Protocol):
    """A launched command still running: its pid, and the wait that yields what `run` would have
    returned. The pid is the reason the launch is a background one — a foreground `run` names the
    command only once it is over, which is exactly too late to stop one that is not."""

    pid: int

    async def wait(self) -> E2BCommandResult: ...


class E2BCommands(Protocol):
    """The SDK's own signatures, mirrored: `timeout` is e2b's keyword, not a timeout this repo
    offers, so it cannot become an `asyncio.timeout` around the call. `background` selects which of
    the two things `run` returns: the command's outcome, or the running command itself."""

    @overload
    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        background: Literal[True],
    ) -> E2BCommandHandle: ...

    @overload
    async def run(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        background: Literal[False] = False,
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
    templates: Mapping[str, str]
    """One published template per sandbox size — the build allocates cpu and memory per tier, so
    which template a fresh sandbox is created from is what `SandboxSpec.size` decides."""
    sdk: E2BSdk = E2B_SDK
    clock: Callable[[], float] = time.monotonic
    resume_prepare_seconds: float = RESUME_PREPARE_TIMEOUT_SECONDS
    """How long a resumed box's `envd` is given to re-assert preparation before the turn goes on
    without it."""
    prepare_retry_seconds: float = PREPARE_RETRY_SECONDS
    """How long a fresh box is left alone after its `envd` connection dropped mid-preparation."""
    resume_retry_delay_seconds: float = RESUME_RETRY_DELAY_SECONDS
    """How long to wait before re-issuing a resume the provider's control plane left unanswered."""
    resume_total_timeout_seconds: float = RESUME_TOTAL_TIMEOUT_SECONDS
    """The ceiling on a whole retried resume, attempts and backoff together."""
    silent_mark_seconds: float = SILENT_MARK_SECONDS
    """How long a container's silence keeps refusing commands before the box is tried again."""
    _live: dict[UUID, _Lease] = field(default_factory=dict)
    _silent: dict[str, float] = field(default_factory=dict)
    """Containers whose command channel did not answer the stop after their own deadline fired, each
    against the clock reading its silence stops being evidence at. A box that reaches this has
    already failed to answer twice, and every later command on it would otherwise pay its whole
    deadline before saying so — but only until the mark runs out, since the same silence is what a
    saturated box gives while it thrashes."""
    _launched: dict[tuple[str, UUID | None], set[int]] = field(default_factory=dict)
    """Per container and turn, the process groups this carrier launched and has not seen end — what
    a member's cancel stops through `stop_commands`. The container is in the key because a pid means
    nothing outside the box that issued it; the turn is, because one box serves every turn of a
    conversation and every subagent turn that inherited it, several of them running commands at
    once, so a stop keyed on the container alone would signal the groups of turns nobody cancelled.
    Emptied as each command ends, so no group is ever signalled twice or after the pid moved on."""

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
        bring back.

        Preparing the box — the CA into system trust, `/workspace` into place — runs on every open,
        because the CA in hand is the only one the proxy will present and a resumed box may hold an
        older one. On a box this call just opened it must succeed: nothing is installed yet, so an
        unprepared box reaches no host and holds no workspace. On a resumed box it is already true
        and this is a re-assertion, so it is bounded here and both its silence and a dropped
        connection are tolerated: preparation runs through `envd`, which stops answering while a
        loaded container thrashes and drops the connection when it comes back. The bound is this
        repo's own because the SDK has none to offer — `request_timeout` covers a stream's setup and
        send, never its read, so a silent `envd` holds a command open with nothing to expire. A
        deferred preparation costs nothing: the trust store still holds the CA installed when the
        box was made, the install command replaces it the first turn `envd` answers, and a command
        meeting a container still too busy to talk gets the exit code `exec` maps for it.

        What makes deferring safe is which box it is allowed for: the one `spec.resume_id` names,
        and only that one. That id comes off the conversation's durable handle, which is written
        only once this call has returned — so an id being there at all is proof some `create`
        prepared that container. A box reached any other way carries no such proof: this process's
        own cache can name one whose preparation just failed, and a fresh container has nothing on
        it yet. Both prepare strictly.

        A strict preparation retries a transport fault — the provider dropping its own connection to
        a container it just started, the one uncertainty here that is external — because both steps
        behind it are idempotent. A non-zero install exit is the box's own deterministic answer and
        is reported on the first attempt.

        A strict preparation that fails drops the lease and raises. The lease goes because the
        deadline it holds is only evidence while the container answers to it, and this one just
        did not — so the next open reattaches rather than re-failing against the same box for the
        rest of the span. The container itself is left standing: it may hold a whole conversation's
        `/workspace`, and this carrier disposes of nothing that might.

        The lease is published only once preparation has succeeded, so what a concurrent open can
        adopt is a box already known good rather than one still being made ready."""
        egress_env = egress_proxy_env(spec.proxy, spec.run_token)
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
        if sandbox.sandbox_id == spec.resume_id:
            try:
                async with asyncio.timeout(self.resume_prepare_seconds):
                    await self._prepare(sandbox, spec.proxy.ca_cert)
            except (TimeoutError, httpx.TransportError):
                log(
                    "sandbox.e2b.prepare_deferred",
                    conversation_id=str(spec.conversation_id),
                    sandbox_id=sandbox.sandbox_id,
                )
                emit_metric("sandbox_prepare_deferred_total", carrier=CARRIER_NAME)
        else:
            await self._prepare_strictly(sandbox, spec)
        self._live[spec.conversation_id] = _Lease(sandbox, opened + SANDBOX_LEASE_SECONDS)
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            run_token=spec.run_token,
            egress_env={**egress_env, **spec.env},
            turn_id=spec.turn_id,
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
            sandbox = await self._connected(
                spec.conversation_id, spec.resume_id, SANDBOX_LEASE_SECONDS
            )
        except SandboxNotFoundException:
            self._live.pop(spec.conversation_id, None)
            return None
        self._live[spec.conversation_id] = _Lease(sandbox, opened + SANDBOX_LEASE_SECONDS)
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=sandbox.sandbox_id,
            run_token=spec.run_token,
            turn_id=spec.turn_id,
        )

    async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox:
        """Resume the conversation's sandbox, or open one on the deploy's template. `connect` both
        resumes a paused sandbox and sets its lease, so a resume is one call whatever state the
        container was left in. e2b holds a paused sandbox until something kills it, so an id that
        comes back not-found names one the provider no longer has: the container is cache over the
        durable workspace, so the turn opens a fresh one rather than failing every turn this
        conversation will ever admit against an id nothing can resurrect.

        The resume is retried on a transport error and the create is not, and the asymmetry is the
        same fact read twice: a call whose answer never arrived leaves this process unable to say
        whether it landed. For `connect` that is harmless — it opens nothing, and its span is a
        floor — so re-issuing it converges on the one container the id names. For `create` it is
        not: the lost answer may have carried the id of a sandbox now running with nothing able to
        name it, and a second create would strand that one for good."""
        if resume_id is not None:
            try:
                return await self._connected(spec.conversation_id, resume_id, SANDBOX_LEASE_SECONDS)
            except SandboxNotFoundException:
                log(
                    "sandbox.e2b.resume_missed",
                    conversation_id=str(spec.conversation_id),
                    sandbox_id=resume_id,
                )
        template = None if spec.size is None else self.templates.get(spec.size)
        if template is None:
            raise RuntimeError(f"no e2b template serves sandbox size {spec.size!r}")
        sandbox = await self.sdk.create(
            template=template,
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

    async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None:
        for remaining in reversed(range(PREPARE_ATTEMPTS)):
            try:
                await self._prepare(sandbox, spec.proxy.ca_cert)
                return
            except BaseException as error:
                if remaining == 0 or not isinstance(error, httpx.TransportError):
                    self._drop(spec.conversation_id, "create")
                    raise
                log(
                    "sandbox.e2b.prepare_retried",
                    conversation_id=str(spec.conversation_id),
                    sandbox_id=sandbox.sandbox_id,
                    error_class=type(error).__name__,
                )
                emit_metric("sandbox_prepare_retried_total", carrier=CARRIER_NAME)
                await asyncio.sleep(self.prepare_retry_seconds)

    async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox:
        """`connect` on the sandbox `sandbox_id` names, re-issued up to RESUME_TRANSPORT_RETRIES
        times when the provider's control plane leaves the request unanswered, and bounded whole by
        RESUME_TOTAL_TIMEOUT_SECONDS. That control plane is a network call off this cluster, so an
        unanswered one is uncertainty about the provider rather than a fault here — the one case
        this repo retries. Only a transport error does: a not-found is the provider answering, and
        the caller decides what that means.

        Every `connect` in this carrier comes through here, because they are one endpoint and one
        uncertainty: turn setup resuming a conversation's box, the mid-turn lease renewal, and the
        read path. A stall that kills a turn at setup kills it just as dead ten minutes in.

        The ceiling is what makes the retry bounded in the units a member waits in. Attempts bound
        only how many times this asks; the wall clock bounds how long it asks for, and a timeout
        raises the transport fault the last attempt saw rather than a bare TimeoutError, so the
        caller reads the provider's failure and not this one's."""
        attempt = 0
        delay = self.resume_retry_delay_seconds
        last: httpx.TransportError | None = None
        try:
            async with asyncio.timeout(self.resume_total_timeout_seconds):
                while True:
                    try:
                        return await self.sdk.connect(
                            sandbox_id, timeout=span, api_key=self.api_key
                        )
                    except httpx.TransportError as error:
                        last = error
                        attempt += 1
                        if attempt > RESUME_TRANSPORT_RETRIES:
                            log(
                                "sandbox.e2b.resume_unanswered",
                                conversation_id=str(conversation_id),
                                sandbox_id=sandbox_id,
                                attempts=attempt,
                                error_class=type(error).__name__,
                            )
                            raise
                        log(
                            "sandbox.e2b.resume_retried",
                            conversation_id=str(conversation_id),
                            sandbox_id=sandbox_id,
                            attempt=attempt,
                            error_class=type(error).__name__,
                        )
                        await asyncio.sleep(delay)
                        delay *= 2
        except TimeoutError:
            log(
                "sandbox.e2b.resume_timed_out",
                conversation_id=str(conversation_id),
                sandbox_id=sandbox_id,
                attempts=attempt,
                seconds=self.resume_total_timeout_seconds,
            )
            raise (
                last
                if last is not None
                else httpx.ReadTimeout(
                    f"e2b never answered a resume of {sandbox_id} within "
                    f"{self.resume_total_timeout_seconds}s"
                )
            ) from None

    async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None:
        """Make the box usable: the proxy's CA in system trust, `/workspace` in place and owned by
        the sandbox user, and the workload cgroups capped below the guest's memory so no command
        can starve `envd` itself. Every step reaches `envd`, and none can be bounded from the
        SDK — the command runs over a stream the SDK gives no read timeout at all, so a silent
        `envd` holds it open with nothing to expire. The upload is bounded, but raises a class the
        SDK leaves unmapped. A caller that needs this to end bounds the whole of it, in one
        place."""
        await self._install_ca(sandbox, ca_cert)
        await self._ensure_workspace(sandbox)
        await self._cap_workload(sandbox)

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
        """`/workspace`, existing and owned by the sandbox user. Root, since `/` is root-owned and
        the sandbox user could neither create the directory nor own it."""
        try:
            await sandbox.commands.run(
                ENSURE_WORKSPACE_COMMAND, user="root", timeout=WORKSPACE_ENSURE_TIMEOUT_SECONDS
            )
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox workspace setup failed: {detail}") from error

    async def _cap_workload(self, sandbox: E2BSandbox) -> None:
        """Ceilings on the cgroups the workload runs in — memory at the guest's total minus the
        reserve, pids against fork exhaustion. Kernel state, so a pause carries it into the
        snapshot and a resume restores it; re-asserting on every preparation is idempotent."""
        try:
            await sandbox.commands.run(
                CAP_WORKLOAD_COMMAND, user="root", timeout=WORKLOAD_CAP_TIMEOUT_SECONDS
            )
        except CommandExitException as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"sandbox workload cap failed: {detail}") from error

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's `commands.run`. e2b takes a shell string, so argv
        is quoted into one command — bytes reach the workspace through `write`, never here. A
        non-zero exit and a command timeout arrive as SDK exceptions, mapped to the ExecResult the
        session reads exactly as the shell's own exit code would. It runs under the turn's egress
        env, so its every network call routes through the proxy with the turn's run token — the raw
        model key never enters the sandbox and every request is metered. What it asks of the lease
        is its own timeout with room for the provider to answer, so however long the command, the
        container cannot pause mid-run; the model's thinking between commands rides whatever the
        standing lease still holds, and a gap that outruns it costs a sub-second resume, never a
        lost turn.

        The deadline stops the work, not just the reporting of it. e2b's own `timeout` severs the
        client's stream and leaves the command running, so a stopped command would otherwise keep
        the container's CPU for as long as it lives — and the caller, told only that its deadline
        fired, meets a box already consumed by the run it thinks it stopped. `setsid` makes the
        command lead its own process group so the whole tree is nameable by one signal, and the
        launch is a background one purely to learn that leader's pid before the deadline can fire.
        The group is the unit because a signal to the shell alone leaves the descendants doing the
        real work — a suite's parallel workers, a build's compilers — reparented and running.
        The group is also the stop's whole reach: work a command detaches into a group of its own
        survives it by construction, which is how a bash call whose budget expires continues as
        the background task the tool hands back instead of dying with the launcher that waited on
        it — the bash tool's launch script holds the other half of this contract. Nothing but this
        ever stops what the group holds: no later call can name a group whose pid it never saw.
        A deadline that fires before the launch answers has no pid to name and nothing yet running
        behind it — and is the plainest reading of a gone channel there is, since detaching returns
        as soon as the command has a pid and cannot legitimately outlast a caller's whole budget.
        That box is remembered here for a bounded span, because a container silent enough to swallow
        its own launch never reaches the stop that would otherwise have noticed. The stop is
        best-effort and swallowed, since what the caller must still be told is the deadline its own
        command hit.

        A stream severed while the command runs arrives as neither of those SDK exceptions, and as
        no class this can name. The command itself keeps running inside the sandbox and completes,
        so this is never retried — it is reported, and the lease is
        dropped so the next call reattaches rather than trust a deadline the provider abandoned.

        A cancelled step ends the wait without ever reaching those paths, and what the command must
        do then depends on which cancel it was: a member's stop ends the turn for good, while an
        executor preemption replays the step, and a replay that finds its command finished is the
        whole point of leaving it alone. This call site cannot tell the two apart — DBOS cancels the
        step's task for both and names the difference only once the body has unwound — so the group
        is left running and only `stop_commands` ever signals it. The lease still goes, since
        dropping the reference cannot be interrupted and a lease the next call trusts is worse than
        one it re-leases: the cancel says nothing about how long this container still answers."""
        sandbox = await self._sandbox(handle, timeout_s + LEASE_MARGIN_SECONDS)
        await self._still_there(sandbox, handle.container_id)
        command = f"{SESSION_LEADER_CMD} {shlex.join(argv)}"
        running: E2BCommandHandle | None = None
        left_running = False
        try:
            running = await sandbox.commands.run(
                command,
                cwd=WORKSPACE_DIR,
                envs={**SANDBOX_ENV, **handle.egress_env},
                timeout=timeout_s,
                background=True,
            )
            self._launched.setdefault((handle.container_id, handle.turn_id), set()).add(running.pid)
            result = await running.wait()
        except CommandExitException as error:
            return ExecResult(stdout=error.stdout, stderr=error.stderr, exit_code=error.exit_code)
        except TimeoutException as error:
            emit_metric("sandbox_exec_timeout_total", carrier=CARRIER_NAME)
            if running is None:
                self._mark_silent(handle.container_id)
            else:
                await self._stop_group(sandbox, handle.container_id, running.pid)
            return ExecResult(
                stdout="",
                stderr=str(error),
                exit_code=EXEC_TIMEOUT_CODE,
                timed_out_after_s=timeout_s,
            )
        except asyncio.CancelledError:
            left_running = True
            self._drop(handle.conversation_id, "cancel")
            raise
        except Exception:
            self._drop(handle.conversation_id, "exec")
            raise
        finally:
            if running is not None and not left_running:
                self._forget_group(handle, running.pid)
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def stop_commands(self, handle: SandboxHandle) -> None:
        """Signal every group `handle.turn_id` still has running in the container — the stop a
        cancelled `exec` deliberately did not issue. It runs from the one place that knows a cancel
        was a member's: the step's own body sees a member's stop and an executor preemption as the
        same bare `asyncio.CancelledError`, and a preempted step is replayed, so stopping there
        destroyed work the replay would have found done.

        The turn is the whole reach. A subagent inherits the sandbox of the turn that spawned it, so
        the parent, its children and every later turn of the conversation run their commands in one
        container: a stop that took the container as its unit would kill the build, clone or push a
        sibling turn nobody cancelled is holding, and destroy the work in its workspace.

        A turn with nothing running costs no provider call: a cancel with no command in flight is
        the common one, and leasing a box to signal nothing would put a control-plane round trip on
        every stop. What the signal reaches is what the deadline's stop reaches — whatever the
        command detached into a group of its own keeps running, to be reattached by the task files
        it keeps."""
        pids = self._launched.pop((handle.container_id, handle.turn_id), set())
        if not pids:
            return
        sandbox = await self._sandbox(handle, LEASE_MARGIN_SECONDS)
        for pid in sorted(pids):
            await self._stop_group(sandbox, handle.container_id, pid)

    def _forget_group(self, handle: SandboxHandle, pid: int) -> None:
        """Drop a group that ended on its own or under the deadline's stop, and the turn's entry
        with the last of them — the map holds the groups still running, not every turn this process
        has ever run a command for."""
        key = (handle.container_id, handle.turn_id)
        groups = self._launched.get(key)
        if groups is None:
            return
        groups.discard(pid)
        if not groups:
            del self._launched[key]

    async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int) -> None:
        """Signal the stopped command's whole process group, which `setsid` made the pid's own —
        the negation is what reaches the descendants rather than the leader alone. It carries no
        `--`: the signal already took the option slot, so the negative pid is unambiguous without
        one, and dash's builtin `kill` — `/bin/sh` on Debian, and whichever shell a sandbox image
        happens to give a command — rejects the separator outright (`Illegal number: -`), which
        would leave every descendant running behind a stop that reported nothing. A sandbox that
        cannot answer leaves the group running and says so in the counter; raising here would
        replace the caller's timeout with an error about the cleanup after it.

        A stop the box never answers is also the reading that outlives this call. The command is
        one word against a container that has just failed to finish another, so a deadline here is
        the channel being gone rather than the work being slow — the two the vitals probe was
        written to separate, and the counter alone separates nowhere. That container is remembered
        for a bounded span, so the next command inside it asks whether the box is there before
        agreeing to wait for it."""
        try:
            await sandbox.commands.run(f"kill -9 -{pid}", timeout=EXEC_STOP_TIMEOUT_SECONDS)
        except TimeoutException:
            self._mark_silent(container_id)
            emit_metric("sandbox_exec_stop_failed_total", carrier=CARRIER_NAME)
        except Exception:
            emit_metric("sandbox_exec_stop_failed_total", carrier=CARRIER_NAME)

    def _mark_silent(self, container_id: str) -> None:
        """Remember a container that did not answer, and until when. The span is measured from this
        silence and never from the probes that follow it, so a box no probe can reach still gets a
        real command a bounded time after the fault instead of one refusal renewing the next."""
        self._silent[container_id] = self.clock() + self.silent_mark_seconds

    async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None:
        """One bounded word to a container that stopped answering, before another command commits
        its whole deadline to it. A box wedged past its own kernel keeps its provider record — the
        control plane answers `running` for one that has not run anything in twenty minutes — so
        the container itself is the only thing worth asking, and what it costs to ask is seconds
        against the ten minutes a caller would otherwise wait to learn the same thing.

        Answering clears the mark: a channel that came back is a working box, and nothing here
        should outlive the fault it recorded.

        Neither does the mark itself. A box saturated enough to miss a deadline can be too busy to
        answer even this word, and refusing on that reading alone made one loaded minute condemn a
        live container for the rest of the process — the member's turn ended on `stopped answering
        commands` while the box was thrashing, not gone. So the mark expires: past its span the
        container is tried with the command it was given, and a box that really is gone earns the
        mark again through the deadline that command hits."""
        marked_until = self._silent.get(container_id)
        if marked_until is None:
            return
        if self.clock() >= marked_until:
            del self._silent[container_id]
            log("sandbox.e2b.silent_mark_expired", sandbox_id=container_id)
            return
        try:
            await sandbox.commands.run(SILENT_PROBE_CMD, timeout=SILENT_PROBE_SECONDS)
        except Exception as error:
            emit_metric("sandbox_unreachable_total", carrier=CARRIER_NAME)
            raise SandboxUnreachable(
                f"sandbox {container_id} stopped answering commands"
            ) from error
        self._silent.pop(container_id, None)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Upload through the sandbox's filesystem API, which creates the parent directories and
        streams the body as its own request — e2b's only channel that carries bytes off the command
        line, since `commands.run` takes a shell string with no stdin. A failed upload drops the
        lease for the same reason a severed command does: the provider answered for a container the
        deadline here still vouches for."""
        sandbox = await self._sandbox(handle, LEASE_MARGIN_SECONDS)
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
        that stops mid-file. The stream cannot renew mid-flight and a pause severs it with no
        self-heal, so it asks the lease for the whole autosuspend span up front — a margin would
        pause the container under a transfer the margin underestimates."""
        sandbox = await self._sandbox(handle, SANDBOX_LEASE_SECONDS)
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

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """The template bakes `sbxfs`, so a file op is that CLI run through `exec` — under the same
        lease, quoting and timeout mapping every other command gets."""
        return await sbxfs_file_op(self, handle, op, params)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The sandbox's public per-port host: e2b routes an in-sandbox port over a per-port
        subdomain (`{port}-{sandbox_id}.{domain}`), reached from outside with the sandbox's traffic
        token. The generic inbound path for any service the turn started inside the container (a
        browser's CDP endpoint, a site's dev-server preview). `get_host` is pure address formatting,
        no round trip — the lease is what the caller is really asking for, since an address is
        worthless if the container pauses before the exchange, and the exchange itself runs where
        no carrier call can renew it. A sandbox the provider no longer has
        raises `SandboxNotFoundException` on reconnect — not this carrier's contract to leak — so it
        maps to `SandboxUnreachable`, the one error every carrier's `dial` raises."""
        try:
            sandbox = await self._sandbox(
                handle, SANDBOX_LEASE_SECONDS, span_floor=DIAL_LEASE_SECONDS
            )
        except SandboxNotFoundException as error:
            raise SandboxUnreachable(f"e2b sandbox {handle.container_id!r} is gone") from error
        token = sandbox.traffic_access_token
        return DialTarget(
            host=sandbox.get_host(port),
            tls=True,
            headers={TRAFFIC_ACCESS_HEADER: token} if token else {},
        )

    async def _sandbox(
        self,
        handle: SandboxHandle,
        needed_seconds: int,
        span_floor: int = SANDBOX_LEASE_SECONDS,
    ) -> E2BSandbox:
        """The conversation's sandbox, leased past the work about to run on it. Every caller states
        the span it needs and a standing lease that already covers it is used as it stands: renewing
        per call would put a control-plane round trip in front of every tool call, and the lease is
        sized so a working turn buys one every few minutes instead of one per command.

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
        span = max(span_floor, needed_seconds)
        sandbox = await self._connected(handle.conversation_id, handle.container_id, span)
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


def sandbox_templates(value: str) -> dict[str, str]:
    """The size→template map off the `E2B_TEMPLATES` wire form (`small=ref,medium=ref,large=ref`,
    the line `sandbox/build_template.py` prints). Every declared size must name a template — a map
    missing one would boot a deploy whose portal offers a size no sandbox can be created at — and a
    size no carrier declares is config drift, not a tier to serve quietly."""
    entries: dict[str, str] = {}
    for item in value.split(","):
        size, sep, reference = item.partition("=")
        if not sep or not size or not reference:
            raise RuntimeError(
                f"{E2B_TEMPLATES_ENV} entry {item!r} is not <size>=<template reference>"
            )
        entries[size] = reference
    if set(entries) != set(SANDBOX_SIZES):
        raise RuntimeError(
            f"{E2B_TEMPLATES_ENV} names sizes {sorted(entries)}, expected {sorted(SANDBOX_SIZES)}"
        )
    return entries


def build_e2b_carrier() -> E2BCarrier:
    key = os.environ.get(E2B_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"e2b carrier selected but {E2B_API_KEY_ENV} is not set")
    templates = os.environ.get(E2B_TEMPLATES_ENV)
    if not templates:
        raise RuntimeError(f"e2b carrier selected but {E2B_TEMPLATES_ENV} is not set")
    return E2BCarrier(api_key=key, templates=sandbox_templates(templates))


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(
            CarrierSpec(
                name=CARRIER_NAME,
                factory=build_e2b_carrier,
                off_cluster=True,
                sizes=SANDBOX_SIZES,
            ),
        ),
    )
