"""The task environment: upstream's own container, serving its seeded Slack, mail, calendar, Jira,
and storefront over one Streamable-HTTP MCP endpoint the `mcp` extension reaches in-process.

Upstream's file tools stay off (`UpstreamPin.service_tool_sets`) — the agent's file surface is ufo's
sandbox, and the conversation's workspace directory is copied into the container at grading time so
upstream's own verifier scores exactly the bytes the turn left on disk. Each case runs its own
container on a Docker-assigned port and writes that endpoint into its own workspace's `mcp_servers`
slot, so eval lanes run concurrently without ever touching each other's services."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from httpx import AsyncClient, HTTPError
from pydantic import BaseModel, ConfigDict, Field

from evals.handbook.corpus import HandbookTask, UpstreamPin
from ufo.runtime.access.credentials import CredentialStore

BASE_IMAGE = "handbook_base"
CONTAINER_PREFIX = "ufo-handbook-"
CONTAINER_PORT = 8000
LOOPBACK = "127.0.0.1"
MCP_SERVERS_SLOT = "mcp_servers"
SERVER_NAME = "workplace"
PROXY_COMMAND = ("bash", "/app/scripts/start.sh", "--method", "http", "--port", str(CONTAINER_PORT))
WORKDIR = "/workdir"
# Where the sandbox serves the conversation workspace; the services mount it at the same path so an
# attachment path the agent writes resolves identically on both sides.
SANDBOX_WORKSPACE = "/workspace"
TESTS_DIR = "/tests"
RESULTS = "/tests/results.json"
READY_TIMEOUT_SECONDS = 180.0
START_ATTEMPTS = 3
ABANDON_TIMEOUT_SECONDS = 30.0
READY_POLL_SECONDS = 1.0
VERIFY_TIMEOUT_SECONDS = 900.0
DOCKER_BUILD_TIMEOUT_SECONDS = 1_800.0


class RubricResult(BaseModel):
    """One rubric's verdict, as upstream's verifier reports it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    passed: bool = Field(validation_alias="pass")
    score: float
    feedback: str = ""


class VerifierResults(BaseModel):
    """Upstream's scorecard, read back from the container verbatim."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    passed: bool
    rubrics_passed: int
    rubrics_total: int
    score: float
    rubric_results: tuple[RubricResult, ...]


class DockerError(RuntimeError):
    """A docker step failed — fail loud rather than score a case against a broken environment."""


@dataclass(frozen=True)
class TaskEnvironment:
    """One task's environment across the case: `start` brings the services up and points the
    workspace at them; `verify` scores the finished workspace with upstream's verifier and tears the
    container down.

    Container name is per task and per workspace, and the port is whatever Docker assigns: eval
    lanes run concurrently as separate stacks over separate workspaces, so a shared name would let
    one lane's teardown kill another lane's services mid-case — the finished workspace would then be
    graded against seed state, silently.

    The workspace half of the name is also how a lane reclaims itself. A case whose turn never
    reached grading leaves its container running, and only this lane can know that container is
    finished with, so `start` sweeps its own workspace's containers before raising a new one."""

    task: HandbookTask
    pin: UpstreamPin
    checkout: Path
    credentials: CredentialStore

    @property
    def image(self) -> str:
        return f"handbook_task_{self.task.task_id}"

    def container_for(self, workspace_id: UUID) -> str:
        return f"{CONTAINER_PREFIX}{workspace_id.hex[:8]}-{self.task.task_id}"

    def lane_filter(self, workspace_id: UUID) -> str:
        return f"{CONTAINER_PREFIX}{workspace_id.hex[:8]}-"

    async def start(self, workspace_id: UUID, workspace_dir: Path) -> None:
        """The case's prepare hook: bring the services up over the conversation's own workspace,
        then point the workspace at them.

        The services resolve an emailed attachment as a plain path inside their own container, so
        the conversation's workspace is mounted there at the same absolute path the sandbox serves
        it from. Without that, every attachment the agent produced was a path only the sandbox
        could see, and the mail service answered "Attachment file not found" — as a success, not an
        error. Read-only: the services have no business editing what the turn is graded on."""
        await self._ensure_base_image()
        await self._build_task_image()
        await self._reclaim_lane(workspace_id)
        container = self.container_for(workspace_id)
        try:
            endpoint = await self._serving_endpoint(container, workspace_dir)
            await self._point_workspace_at_services(workspace_id, endpoint)
        except BaseException:
            await self._abandon(container)
            raise

    async def _abandon(self, container: str) -> None:
        """Drop a container whose start failed, without letting the cleanup replace the failure.
        Shielded so a cancelled case still releases its services, bounded so a wedged daemon cannot
        hold the traceback, and silent because the caller is already raising the real error."""
        try:
            await asyncio.shield(
                self._docker(
                    "rm",
                    "--force",
                    container,
                    check=False,
                    deadline_seconds=ABANDON_TIMEOUT_SECONDS,
                )
            )
        except (DockerError, TimeoutError, asyncio.CancelledError):
            return

    async def _reclaim_lane(self, workspace_id: UUID) -> None:
        """Remove containers this lane left behind. A turn that ended uncleanly never reaches
        grading, so its container is still running with nothing left to do; the lane's own workspace
        scopes the sweep, so a concurrent lane's live services are never touched."""
        listed = await self._docker(
            "ps", "--all", "--quiet", "--filter", f"name={self.lane_filter(workspace_id)}"
        )
        stale = listed.split()
        if stale:
            await self._docker("rm", "--force", *stale, check=False)

    async def _serving_endpoint(self, container: str, workspace_dir: Path) -> str:
        """Bring the services up and return the endpoint they answer on.

        Upstream's proxy assigns each mock service a port by probing for a free one and binding it
        a moment later, so two services can be handed the same number; the loser exits and the proxy
        aborts the whole startup. The race lives in the pinned upstream code, widens whenever the
        host is busy, and is re-rolled by a fresh container — so a start that dies or never reports
        ready is retried on a new container rather than failing the case. A start that never
        succeeds is a broken environment and still raises."""
        failures: list[str] = []
        for _ in range(START_ATTEMPTS):
            await self._remove_container(container)
            await self._run_container(container, workspace_dir)
            try:
                endpoint = await self._published_endpoint(container)
                await self._await_ready(container, endpoint)
                return endpoint
            except (DockerError, TimeoutError) as error:
                failures.append(f"{type(error).__name__}: {error}")
        raise DockerError(
            f"{container} never served its tools in {START_ATTEMPTS} attempts: "
            + "; ".join(failures)
        )

    async def verify(self, workspace_id: UUID, workspace_dir: Path) -> VerifierResults:
        container = self.container_for(workspace_id)
        try:
            await self._copy_in(container, workspace_dir)
            return await self._run_verifier(container)
        finally:
            await self._remove_container(container)

    async def teardown(self, workspace_id: UUID) -> None:
        """Drop this case's services when the turn ended without reaching grading."""
        await self._remove_container(self.container_for(workspace_id))

    async def _ensure_base_image(self) -> None:
        if await self._image_exists(BASE_IMAGE):
            return
        await self._docker(
            "build",
            "-t",
            BASE_IMAGE,
            str(self.checkout / "docker"),
            deadline_seconds=DOCKER_BUILD_TIMEOUT_SECONDS,
        )

    async def _build_task_image(self) -> None:
        environment = self.task.environment_root
        await self._docker(
            "build",
            "-t",
            self.image,
            "-f",
            str(environment / "Dockerfile"),
            str(environment),
            deadline_seconds=DOCKER_BUILD_TIMEOUT_SECONDS,
        )

    async def _run_container(self, container: str, workspace_dir: Path) -> None:
        await self._docker(
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            f"{LOOPBACK}::{CONTAINER_PORT}",
            "--volume",
            f"{workspace_dir}:{SANDBOX_WORKSPACE}:ro",
            "--env",
            f"WORLDBENCH_TOOL_SETS={' '.join(self.pin.service_tool_sets)}",
            self.image,
            *PROXY_COMMAND,
        )

    async def _published_endpoint(self, container: str) -> str:
        """The loopback `host:port` Docker assigned this container. Asking Docker for the mapping it
        chose is the only race-free way to place concurrent lanes: probing for a free port and then
        binding it leaves a window another lane can take."""
        mapping = (await self._docker("port", container, str(CONTAINER_PORT))).strip()
        endpoint = mapping.splitlines()[0].strip() if mapping else ""
        if not endpoint:
            raise DockerError(f"{container} published no port for {CONTAINER_PORT}")
        return endpoint

    async def _await_ready(self, container: str, endpoint: str) -> None:
        """Poll upstream's own health endpoint until it answers. A container that has already exited
        is reported the moment it does, rather than polling a dead endpoint until the deadline — the
        proxy aborts within seconds when one of its services loses the port race, and the caller is
        waiting to retry."""
        async with AsyncClient(timeout=5.0) as client:
            async with asyncio.timeout(READY_TIMEOUT_SECONDS):
                while True:
                    try:
                        if (await client.get(f"http://{endpoint}/health")).status_code == 200:
                            return
                    except HTTPError:
                        pass
                    if not await self._container_running(container):
                        raise DockerError(f"{container} exited before serving its tools")
                    await asyncio.sleep(READY_POLL_SECONDS)

    async def _container_running(self, container: str) -> bool:
        state = await self._docker(
            "inspect", "--format", "{{.State.Running}}", container, check=False
        )
        return state.strip() == "true"

    async def _point_workspace_at_services(self, workspace_id: UUID, endpoint: str) -> None:
        """Write the `mcp_servers` slot the `mcp` extension reads, naming this case's own
        container. The slot is workspace-scoped and each concurrent lane owns its own workspace, so
        a lane never points the agent at another lane's services."""
        servers = {"servers": {SERVER_NAME: {"url": f"http://{endpoint}/mcp"}}}
        await self.credentials.put(workspace_id, MCP_SERVERS_SLOT, json.dumps(servers))

    async def _copy_in(self, container: str, workspace_dir: Path) -> None:
        """Replace the container's workspace with the conversation's, and stage the task's tests.
        Upstream's verifier reads `/workdir` and the services' `/data` snapshots, so after this the
        container holds exactly the end state the turn produced.

        Only the conversation's visible entries cross over. ufo-owned runtime files live outside
        the workspace, and upstream's rubrics resolve files by `glob('**/name')` — a runtime copy
        of a spreadsheet could otherwise be the one a rubric grades."""
        entries = await asyncio.to_thread(self._visible_entries, workspace_dir)
        if not entries:
            raise DockerError(f"conversation workspace {workspace_dir} holds no files to grade")
        await self._exec(container, "rm", "-rf", WORKDIR)
        await self._exec(container, "mkdir", "-p", WORKDIR, TESTS_DIR)
        for entry in entries:
            await self._docker("cp", str(entry), f"{container}:{WORKDIR}/{entry.name}")
        await self._docker("cp", f"{self.task.tests_root}/.", f"{container}:{TESTS_DIR}")

    def _visible_entries(self, workspace_dir: Path) -> tuple[Path, ...]:
        if not workspace_dir.is_dir():
            raise DockerError(f"conversation workspace {workspace_dir} does not exist")
        return tuple(
            sorted(path for path in workspace_dir.iterdir() if not path.name.startswith("."))
        )

    async def _run_verifier(self, container: str) -> VerifierResults:
        """Upstream's verifier exits non-zero whenever a rubric fails, so its exit status is a
        score, not an error. Only a missing scorecard means the verifier itself broke."""
        await self._exec(
            container,
            "python",
            f"{TESTS_DIR}/sop_verifier.py",
            check=False,
            deadline_seconds=VERIFY_TIMEOUT_SECONDS,
        )
        return VerifierResults.model_validate_json(await self._exec(container, "cat", RESULTS))

    async def _exec(
        self,
        container: str,
        *command: str,
        check: bool = True,
        deadline_seconds: float = VERIFY_TIMEOUT_SECONDS,
    ) -> str:
        """Run a command in the container from `/`, never from the image's own `/workdir`: grading
        replaces that directory, and a process whose working directory no longer exists cannot be
        started at all — docker fails it with an exit status and no message."""
        return await self._docker(
            "exec",
            "--workdir",
            "/",
            container,
            *command,
            check=check,
            deadline_seconds=deadline_seconds,
        )

    async def _image_exists(self, image: str) -> bool:
        listed = await self._docker("images", "--quiet", image)
        return bool(listed.strip())

    async def _remove_container(self, container: str) -> None:
        await self._docker("rm", "--force", container, check=False)

    async def _docker(
        self,
        *args: str,
        check: bool = True,
        deadline_seconds: float = VERIFY_TIMEOUT_SECONDS,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(deadline_seconds):
                stdout, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise DockerError(f"docker {args[0]} exceeded {deadline_seconds:g}s") from None
        if check and process.returncode != 0:
            reported = (
                stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            )
            raise DockerError(
                f"docker {' '.join(args)} failed ({process.returncode}): "
                f"{reported[:2000] or 'no output'}"
            )
        return stdout.decode(errors="replace")
