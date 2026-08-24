"""The Daytona carrier extension: a per-conversation cloud sandbox on Daytona's container runtime.

This extension registers `daytona` on the `carriers` Manifest point, so a deploy that sets
`[sandbox] backend = "daytona"` opens its sandboxes on Daytona without core naming the provider.
Every provider call is awaited on the SDK's async client. `create` opens a fresh sandbox from the
deploy's per-size snapshot or restarts the one `spec.resume_id` names off the conversation's
durable handle; `exec` runs a command through `process.exec`; `read` streams a produced file out
over the sandbox's presigned download URL. `/workspace` is the sandbox's own disk and the only copy
of the conversation's files: auto-delete is disabled at create, and nothing here can drop a
sandbox — a stopped one costs only its disk, an archived one less, and reclaiming either would
delete the workspace.

A remote sandbox runs off-cluster, so every command carries the egress env dialing the proxy's
public URL with the turn's run token and the model sentinels the proxy swaps on the wire. The
sandbox runs as one non-root OS user with no way to escalate, so the proxy's CA cannot enter
system trust: preparation writes it beside a merged bundle (system roots plus the proxy CA) under
`/tmp/.ufo-ca`, and the egress env points every TLS reader at that bundle instead of the system
one — the same merged-roots pattern the terminal carrier uses on a member's own machine.

What the provider's clock and runtime do — measured against the live service 2026-08-23 on SDK
0.205.1, three probe rounds. None of it is inferable from the SDK's types, and the decisions below
turn on it:

- `auto_stop_interval` is an idle timer in minutes, refreshed by activity. A command still running
  is activity (a 90s exec against a 1-minute interval completed and answered), and preview traffic
  through the provider's proxy is too (100s of traffic against a 1-minute interval held the box
  `started`; it stopped on schedule once the traffic ceased) — so no lease machinery exists here,
  nothing renews anything mid-turn, and `dial` never touches a timer.
- Idle expiry stops the container: the filesystem (including `/tmp`) survives, memory and
  processes do not. `pause` answers `DaytonaUnprocessableEntityError` on the container runtime —
  the lossless idle E2B's pause gave does not exist here, so a detached background task or a
  dialed dev server dies with the idle-stop and only its files remain.
- Nothing auto-resumes: a data-plane call against a stopped sandbox fails (observed as
  `DaytonaBadRequestError: failed to resolve container IP`), so this carrier checks state and
  starts explicitly. A start from stopped costs ~0.5s; a create ~0.3s.
- `process.exec` answers `ExecuteResponse` for every exit code — never an exception — and its
  `result` is the command's combined output, undivided, so an `ExecResult` here carries everything
  on stdout and an empty stderr. It runs the command through the sandbox user's login shell, so
  every command is normalized through `sh -c`; it takes no per-call user (`os_user` at create is
  ignored on a baked snapshot), so nothing here can escalate.
- The exec deadline severs the call and leaves the whole tree running (`sleep 600` survived its
  5s deadline), surfacing as `DaytonaProcessExecutionTimeoutError` or, racing the SDK's own
  request timeout of deadline+5s, `DaytonaConnectionTimeoutError` — both mean the deadline fired,
  and the group kill after either is what actually stops the work. `setsid` is present, makes the
  wrapped command a session leader (`/proc/pid/stat` session == pid), and `kill -9 -pid` reaps the
  group.
- A sandbox the provider no longer has answers `DaytonaNotFoundError` — the one clean absence
  signal, on `get` alone. A presigned `download_url` works with no auth header, survives restarts,
  and serves only while the sandbox runs."""

import os
import shlex
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID, uuid4

import httpx
from daytona import (
    AsyncDaytona,
    CreateSandboxFromSnapshotParams,
    DaytonaBadRequestError,
    DaytonaConnectionTimeoutError,
    DaytonaNotFoundError,
    DaytonaProcessExecutionTimeoutError,
    SandboxState,
)

from ufo.sdk.manifest import Manifest
from ufo.sdk.o11y import emit_metric, log
from ufo.sdk.sandbox import (
    SANDBOX_ENV,
    SANDBOX_SIZES,
    SYSTEM_CA_BUNDLE,
    WORKSPACE_DIR,
    CarrierSpec,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
    egress_proxy_env,
    ufo_fs_file_op,
)

CARRIER_NAME = "daytona"
DAYTONA_API_KEY_ENV = "DAYTONA_API_KEY"
DAYTONA_SNAPSHOTS_ENV = "DAYTONA_SNAPSHOTS"
CONVERSATION_LABEL = "ufo.conversation_id"
PREVIEW_TOKEN_HEADER = "x-daytona-preview-token"
AUTO_STOP_MINUTES = 5
"""The idle span before the provider stops a silent container, matching the five minutes an idle
e2b sandbox held its slot. The timer is the provider's and activity refreshes it — a running
command, preview traffic, any API call — so a working turn never renews anything; the trade is
that the stop kills processes where e2b's pause froze them."""
AUTO_ARCHIVE_MINUTES = 7 * 24 * 60
"""How long a stopped sandbox holds warm disk before the provider archives it to cold storage —
the provider's own default, stated here because a stopped box holds disk quota and an archived one
frees it, and because an unstated default is a lifecycle decision left to drift."""
AUTO_DELETE_NEVER = -1
"""Auto-delete disabled: `/workspace` is the only copy of the conversation's files, and 0 here
would delete the sandbox the moment it stops."""
CA_DIR = "/tmp/.ufo-ca"
CA_PEM_PATH = f"{CA_DIR}/proxy-ca.pem"
CA_BUNDLE_PATH = f"{CA_DIR}/bundle.pem"
PREPARE_COMMAND = (
    f"cat {SYSTEM_CA_BUNDLE} {CA_PEM_PATH} > {CA_BUNDLE_PATH} && "
    f"test -d {WORKSPACE_DIR} && test -w {WORKSPACE_DIR}"
)
"""Make the box usable, with no root to do it: merge the proxy CA into a trust bundle the egress
env points at, and prove the baked `/workspace` is there and writable. Idempotent, re-run on every
open, because the CA in hand is the only one the proxy will present and a resumed box may hold an
older one."""
PREPARE_TIMEOUT_SECONDS = 30
TRUST_ENV: dict[str, str] = {
    "SSL_CERT_FILE": CA_BUNDLE_PATH,
    "REQUESTS_CA_BUNDLE": CA_BUNDLE_PATH,
    "CURL_CA_BUNDLE": CA_BUNDLE_PATH,
    "GIT_SSL_CAINFO": CA_BUNDLE_PATH,
    "NODE_EXTRA_CA_CERTS": CA_PEM_PATH,
}
"""Where every TLS reader finds the proxy's CA, overriding the system-store paths
`egress_proxy_env` assumes: no root means no `update-ca-certificates`, so trust rides the env.
Node takes the bare CA because `NODE_EXTRA_CA_CERTS` adds to its defaults; everything else reads
the merged bundle so public roots keep working."""
EXEC_TIMEOUT_CODE = 124
EXEC_STOP_TIMEOUT_SECONDS = 15
RUN_PID_TEMPLATE = "/tmp/.ufo-run-{run_id}.pid"
TURN_PIDS_TEMPLATE = "/tmp/.ufo-turn-{turn_id}.pids"
STOP_RUN_TEMPLATE = """sh -c 'p=$(cat {pidfile}) && kill -9 -"$p"; rm -f {pidfile}'"""
STOP_TURN_TEMPLATE = (
    "sh -c '[ -f {pids} ] || exit 0; "
    'while read -r p; do [ "$(ps -o sess= -p "$p" 2>/dev/null | tr -d " ")" = "$p" ] '
    '&& kill -9 -"$p"; done < {pids}; rm -f {pids}'
    "'"
)
"""Signal every group the turn's pidfile names that is still a session leader, then drop the file.
The leader check is what makes the file safe to keep across completed commands: a finished
command's pid fails it, and a recycled pid is almost never again a session of its own — so the
sweep reaches exactly the groups still running, without a per-command bookkeeping round trip."""
STATE_FRESH_SECONDS = 5.0
"""How long one confirmation of `started` keeps `dial` from asking again. A dial's address is
worthless against a stopped box and the ingress dials per proxied request, so dial re-checks state
— but a burst of requests inside this window rides one check, and a box found stopped stays
misaddressed for at most this long."""


class DaytonaExecResult(Protocol):
    exit_code: int
    result: str


class DaytonaProcessApi(Protocol):
    """The SDK's own signature, mirrored: `timeout` is Daytona's keyword — the deadline the
    provider severs the call at — not a timeout this repo offers."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> DaytonaExecResult: ...


class DaytonaFsApi(Protocol):
    async def upload_file(self, src: bytes, dst: str) -> None: ...


class DaytonaPreviewUrl(Protocol):
    url: str
    token: str | None


class DaytonaSandboxApi(Protocol):
    id: str
    state: SandboxState | None
    process: DaytonaProcessApi
    fs: DaytonaFsApi

    async def start(self) -> None: ...

    async def wait_for_sandbox_start(self) -> None: ...

    async def wait_for_sandbox_stop(self) -> None: ...

    async def refresh_data(self) -> None: ...

    async def get_preview_link(self, port: int) -> DaytonaPreviewUrl: ...

    async def download_url(self, path: str) -> str: ...


class DaytonaSdk(Protocol):
    async def create(self, params: CreateSandboxFromSnapshotParams) -> DaytonaSandboxApi: ...

    async def get(self, sandbox_id: str) -> DaytonaSandboxApi: ...


def _download_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))


@dataclass
class _Cached:
    api: DaytonaSandboxApi
    confirmed_at: float


def _launch_command(argv: tuple[str, ...], run_id: str, turn_id: UUID | None) -> str:
    """One provider command that makes the real command a session leader and records its pid on
    the box's own disk before handing over: the run file is what the deadline's kill names, the
    turn file is what a member's stop sweeps. The record lives in the sandbox rather than this
    process because the launch and the wait are one provider round trip — there is no moment the
    pid is known here before the command runs — and because a stop must reach commands a prior
    process launched."""
    record = f"echo $$ > {RUN_PID_TEMPLATE.format(run_id=run_id)}"
    if turn_id is not None:
        record += f"; echo $$ >> {TURN_PIDS_TEMPLATE.format(turn_id=turn_id)}"
    return f"setsid sh -c {shlex.quote(f'{record}; exec {shlex.join(argv)}')}"


@dataclass(frozen=True)
class DaytonaCarrier:
    sdk: DaytonaSdk
    snapshots: Mapping[str, str]
    """One published snapshot per sandbox size — the snapshot fixes cpu, memory and disk, so which
    snapshot a fresh sandbox is created from is what `SandboxSpec.size` decides."""
    http: httpx.AsyncClient = field(default_factory=_download_client)
    clock: Callable[[], float] = time.monotonic
    _live: dict[UUID, _Cached] = field(default_factory=dict)
    """Per conversation, the sandbox object this process last used and when it was last confirmed
    running. A reference cache, not a lease: the provider owns every deadline, so nothing here is
    trusted about state except inside `STATE_FRESH_SECONDS`, and every holder recovers when the
    provider answers for a box that idled out between calls."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Open the conversation's sandbox: the one `spec.resume_id` names, else the one this
        process holds, else a fresh one from the size's snapshot. The named id outranks the
        in-process cache because the row is the arbiter of concurrent opens — the loser of a
        double-create must converge on the persisted winner. A named id the provider no longer has
        means that sandbox and its workspace are gone — an explicit delete, or a provider fault —
        so a fresh one opens in its place and the loss is named in the log; refusing instead would
        wedge every later turn of the conversation on a sandbox nothing can bring back.

        Preparation — the proxy CA merged into the trust bundle, `/workspace` proven writable —
        runs strictly on every open: it is one idempotent command against a box this call just
        started, and a box that cannot answer it would fail the turn's first command anyway, so
        failing here names the fault where it happened."""
        egress_env = {**egress_proxy_env(spec.proxy, spec.run_token), **TRUST_ENV}
        cached = self._live.get(spec.conversation_id)
        resume_id = (
            spec.resume_id
            if spec.resume_id is not None
            else cached.api.id
            if cached is not None
            else None
        )
        api = await self._resume_or_open(spec, resume_id)
        await self._prepare(api, spec.proxy.ca_cert)
        self._live[spec.conversation_id] = _Cached(api, self.clock())
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=api.id,
            run_token=spec.run_token,
            egress_env={**egress_env, **spec.env},
            turn_id=spec.turn_id,
        )

    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None:
        """The sandbox `spec.resume_id` names — started if the provider stopped or archived it —
        or None when there is no id or the provider no longer has it. Always the provider's own
        answer, never the cache's, and never a fresh sandbox: a read of a conversation whose
        sandbox is gone answers absent rather than opening an empty one. No egress env — a read
        runs `ufo fs` and streams files, nothing that leaves the box."""
        if spec.resume_id is None:
            return None
        try:
            api = await self.sdk.get(spec.resume_id)
            await self._started(api)
        except DaytonaNotFoundError:
            self._live.pop(spec.conversation_id, None)
            return None
        self._live[spec.conversation_id] = _Cached(api, self.clock())
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id=api.id,
            run_token=spec.run_token,
            turn_id=spec.turn_id,
        )

    async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> DaytonaSandboxApi:
        if resume_id is not None:
            try:
                api = await self.sdk.get(resume_id)
                await self._started(api)
                return api
            except DaytonaNotFoundError:
                log(
                    "sandbox.daytona.resume_missed",
                    conversation_id=str(spec.conversation_id),
                    sandbox_id=resume_id,
                )
        snapshot = None if spec.size is None else self.snapshots.get(spec.size)
        if snapshot is None:
            raise RuntimeError(f"no daytona snapshot serves sandbox size {spec.size!r}")
        return await self.sdk.create(
            CreateSandboxFromSnapshotParams(
                snapshot=snapshot,
                labels={CONVERSATION_LABEL: str(spec.conversation_id)},
                auto_stop_interval=AUTO_STOP_MINUTES,
                auto_archive_interval=AUTO_ARCHIVE_MINUTES,
                auto_delete_interval=AUTO_DELETE_NEVER,
            )
        )

    async def _started(self, api: DaytonaSandboxApi) -> None:
        """The box running, spoken in the provider's own state machine: a box being destroyed is
        absent (the not-found every caller already maps), a transition already under way is awaited
        rather than raced — `start()` against a box mid-`stopping` answers 409 — and everything
        else is started. An `error` state reaches `start()` and fails with the provider's own
        message."""
        match api.state:
            case SandboxState.STARTED:
                return
            case SandboxState.DESTROYING | SandboxState.DESTROYED:
                raise DaytonaNotFoundError(f"sandbox {api.id} is deleted")
            case (
                SandboxState.STARTING
                | SandboxState.CREATING
                | SandboxState.RESTORING
                | SandboxState.RESUMING
                | SandboxState.PULLING_SNAPSHOT
            ):
                await api.wait_for_sandbox_start()
                return
            case SandboxState.STOPPING:
                await api.wait_for_sandbox_stop()
                await api.refresh_data()
                await self._started(api)
                return
            case _:
                await api.start()

    async def _prepare(self, api: DaytonaSandboxApi, ca_cert: str) -> None:
        await api.fs.upload_file(ca_cert.encode(), CA_PEM_PATH)
        result = await api.process.exec(
            f"sh -c {shlex.quote(PREPARE_COMMAND)}", timeout=PREPARE_TIMEOUT_SECONDS
        )
        if result.exit_code != 0:
            raise RuntimeError(f"sandbox prepare failed: {result.result.strip()}")

    async def _sandbox(self, handle: SandboxHandle) -> DaytonaSandboxApi:
        """The conversation's sandbox object, from the cache or the provider. A cache entry naming
        a different container is a miss however fresh: the handle is read from the conversation row
        per call, and a sandbox recreated since makes that row — not the cache — the truth. A cold
        resolve confirms the box is running; a warm one trusts nothing, which is why every
        data-plane caller recovers through `_recovered` when the box idled out in between."""
        cached = self._live.get(handle.conversation_id)
        if cached is not None and cached.api.id == handle.container_id:
            return cached.api
        api = await self.sdk.get(handle.container_id)
        await self._started(api)
        self._live[handle.conversation_id] = _Cached(api, self.clock())
        return api

    async def _recovered[T](
        self, api: DaytonaSandboxApi, call: Callable[[], Coroutine[object, object, T]]
    ) -> T:
        """One data-plane call, reissued once when the provider answers for a container that idled
        out between calls — its documented lifecycle, not a masked fault: the box is confirmed
        stopped before the start, and a second failure raises. The reissue is safe because every
        call routed here is idempotent from the box's point of view: a command that never started
        (the box had no IP to receive it), an upload of the same bytes, a preview address."""
        try:
            return await call()
        except DaytonaBadRequestError:
            await api.refresh_data()
            if api.state == SandboxState.STARTED:
                raise
            log("sandbox.daytona.restarted", sandbox_id=api.id, state=str(api.state))
            emit_metric("sandbox_restarted_total", carrier=CARRIER_NAME)
            await api.start()
            return await call()

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        """Run one command through the sandbox's `process.exec`. Daytona takes a shell string, so
        argv is quoted into one command — bytes reach the workspace through `write`, never here.
        It runs under the turn's egress env plus the baked-image env the provider's exec does not
        inherit, so its every network call routes through the proxy with the turn's run token. The
        provider's combined output comes back as stdout with stderr empty — Daytona keeps no
        division — and every exit code is a result, never an exception.

        The deadline stops the work, not just the reporting of it: Daytona's `timeout` severs the
        call and leaves the tree running, so the group the launch wrapper recorded is killed the
        moment either timeout class lands, and the member is told the deadline their own command
        hit. A cancelled step ends the wait without stopping anything — a member's stop and an
        executor preemption are indistinguishable here, and a replayed step that finds its command
        finished is the point of leaving it alone — so only `stop_commands` ever signals the
        turn's groups."""
        api = await self._sandbox(handle)
        run_id = uuid4().hex
        command = _launch_command(argv, run_id, handle.turn_id)
        try:
            result = await self._recovered(
                api,
                lambda: api.process.exec(
                    command,
                    cwd=WORKSPACE_DIR,
                    env={**SANDBOX_ENV, **handle.egress_env},
                    timeout=timeout_s,
                ),
            )
        except (DaytonaConnectionTimeoutError, DaytonaProcessExecutionTimeoutError) as error:
            emit_metric("sandbox_exec_timeout_total", carrier=CARRIER_NAME)
            await self._stop_run(api, run_id)
            return ExecResult(
                stdout="",
                stderr=str(error),
                exit_code=EXEC_TIMEOUT_CODE,
                timed_out_after_s=timeout_s,
            )
        return ExecResult(stdout=result.result, stderr="", exit_code=result.exit_code)

    async def stop_commands(self, handle: SandboxHandle) -> None:
        """Signal every group `handle.turn_id` launched that is still running in the container —
        the stop a cancelled `exec` deliberately did not issue. The turn is the whole reach: one
        container serves every turn of a conversation and every subagent turn that inherited it,
        so a stop keyed on the container would kill commands of turns nobody cancelled. The groups
        live in the turn's own pidfile on the box, so this is one command whoever launched them —
        this process, or one a deploy replaced."""
        if handle.turn_id is None:
            return
        api = await self._sandbox(handle)
        command = STOP_TURN_TEMPLATE.format(pids=TURN_PIDS_TEMPLATE.format(turn_id=handle.turn_id))
        try:
            await api.process.exec(command, timeout=EXEC_STOP_TIMEOUT_SECONDS)
        except Exception:
            emit_metric("sandbox_exec_stop_failed_total", carrier=CARRIER_NAME)

    async def _stop_run(self, api: DaytonaSandboxApi, run_id: str) -> None:
        """Kill the timed-out command's own group, named by the pidfile its launch wrapper wrote —
        never the turn's other commands, which are running under deadlines of their own. Swallowed,
        since what the caller must still be told is the deadline its own command hit."""
        pidfile = RUN_PID_TEMPLATE.format(run_id=run_id)
        try:
            await api.process.exec(
                STOP_RUN_TEMPLATE.format(pidfile=pidfile), timeout=EXEC_STOP_TIMEOUT_SECONDS
            )
        except Exception:
            emit_metric("sandbox_exec_stop_failed_total", carrier=CARRIER_NAME)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        """Upload through the sandbox's filesystem API, which creates the parent directories and
        carries the body as its own request — bytes never ride `exec`'s argv."""
        api = await self._sandbox(handle)
        await self._recovered(api, lambda: api.fs.upload_file(content, path))

    async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        """Stream a workspace file out over the sandbox's presigned download URL, in bounded
        chunks that never sit whole in this process. The URL serves only while the box runs, and
        it answers 404 both for a missing file and for some faults of a box that idled out — so a
        non-200 is believed about the file only after the box is confirmed running: refresh, start
        a stopped box, and re-issue once. The response is closed on every exit path, including a
        consumer that stops mid-file."""
        api = await self._sandbox(handle)
        response = await self._opened_download(api, path)
        if response.status_code != 200:
            await response.aclose()
            await api.refresh_data()
            if api.state != SandboxState.STARTED:
                await api.start()
            response = await self._opened_download(api, path)
        if response.status_code == 404:
            await response.aclose()
            raise FileNotFoundError(path)
        if response.status_code != 200:
            await response.aclose()
            raise RuntimeError(f"daytona download of {path!r} answered {response.status_code}")
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    async def _opened_download(self, api: DaytonaSandboxApi, path: str) -> httpx.Response:
        url = await api.download_url(path)
        return await self.http.send(self.http.build_request("GET", url), stream=True)

    async def file_op(
        self, handle: SandboxHandle, op: str, params: dict[str, object]
    ) -> dict[str, object]:
        """The snapshot bakes the `ufo` client, so a file op is `ufo fs` run through `exec` — under
        the same quoting and timeout mapping every other command gets."""
        return await ufo_fs_file_op(self, handle, op, params)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        """The sandbox's public per-port host: Daytona routes an in-sandbox port over a per-port
        subdomain, reached from outside with the preview token minted alongside it. Fetched per
        dial because the token resets when the sandbox restarts. The box is confirmed running
        first — an address to a stopped box fails every request sent to it, and the service's own
        traffic is what keeps the box up from then on — but a confirmation inside
        `STATE_FRESH_SECONDS` rides the last one, so a request burst pays one check. A sandbox the
        provider no longer has raises `SandboxUnreachable`, the one error every carrier's dial
        raises."""
        try:
            api = await self._sandbox(handle)
            cached = self._live[handle.conversation_id]
            if self.clock() - cached.confirmed_at >= STATE_FRESH_SECONDS:
                await api.refresh_data()
                await self._started(api)
                cached.confirmed_at = self.clock()
            link = await api.get_preview_link(port)
        except DaytonaNotFoundError as error:
            raise SandboxUnreachable(f"daytona sandbox {handle.container_id!r} is gone") from error
        token = link.token
        return DialTarget(
            host=httpx.URL(link.url).netloc.decode(),
            tls=True,
            headers={PREVIEW_TOKEN_HEADER: token} if token else {},
        )


def snapshot_map(value: str) -> dict[str, str]:
    """The size→snapshot map off the `DAYTONA_SNAPSHOTS` wire form (`small=ref,medium=ref,
    large=ref`, the line `sandbox/build_template.py --daytona` prints). Every declared size must
    name a snapshot — a map missing one would boot a deploy whose portal offers a size no sandbox
    can be created at."""
    entries: dict[str, str] = {}
    for item in value.split(","):
        size, sep, reference = item.partition("=")
        if not sep or not size or not reference:
            raise RuntimeError(f"{DAYTONA_SNAPSHOTS_ENV} entry {item!r} is not <size>=<snapshot>")
        entries[size] = reference
    if set(entries) != set(SANDBOX_SIZES):
        raise RuntimeError(
            f"{DAYTONA_SNAPSHOTS_ENV} names sizes {sorted(entries)}, "
            f"expected {sorted(SANDBOX_SIZES)}"
        )
    return entries


def build_daytona_carrier() -> DaytonaCarrier:
    key = os.environ.get(DAYTONA_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"daytona carrier selected but {DAYTONA_API_KEY_ENV} is not set")
    snapshots = os.environ.get(DAYTONA_SNAPSHOTS_ENV)
    if not snapshots:
        raise RuntimeError(f"daytona carrier selected but {DAYTONA_SNAPSHOTS_ENV} is not set")
    return DaytonaCarrier(sdk=cast(DaytonaSdk, AsyncDaytona()), snapshots=snapshot_map(snapshots))


def manifest() -> Manifest:
    return Manifest(
        name=CARRIER_NAME,
        version="0.1.0",
        carriers=(
            CarrierSpec(
                name=CARRIER_NAME,
                factory=build_daytona_carrier,
                off_cluster=True,
                sizes=SANDBOX_SIZES,
            ),
        ),
    )
