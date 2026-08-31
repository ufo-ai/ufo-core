"""The live workspace driver: what the operator runner hands the harness so a case runs as a real
turn against a running `ufoctl serve`. It fills the two steps the scoped ExtensionContext cannot —
opening a fresh conversation per case and awaiting an admitted turn's terminal transcript — by
reaching the workspace's own rows and blob store, and it builds the ExtensionContext bound to the
shared admission invoker (the same producer every surface and job admits through). Enqueuing needs a
running serve to drain the turn queue; the driver awaits a queued or running durable workflow, then
reads the terminal row and its exact-sequence transcript. A wait that reaches its deadline cancels
the turn — cancelled terminal committed, DBOS workflow durably cancelled — so the runner never
advances over a still-running predecessor."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
import yaml
from dbos import DBOSClient, WorkflowHandleAsync
from dbos import error as dbos_error
from httpx import AsyncClient
from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from evals.budget import EvalRunBudget
from evals.harness.capability import UndeliveredRound, WorkspaceFile
from evals.harness.timing import TurnStep
from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.models.catalog import ANTHROPIC_KEY_ENV, CORE_PRICING, OPENAI_KEY_ENV
from ufo.harness.models.interface import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from ufo.harness.models.pricing import Pricing
from ufo.host.environment import store_environment_document, store_environment_file
from ufo.onboard.onboard_control import (
    EnsuredWorkspace,
    MemberModelKey,
    SeatRequest,
)
from ufo.runtime.access.credentials import deploy_env
from ufo.runtime.engine import DispatchResult, StreamResult
from ufo.runtime.ext.context import Trajectory
from ufo.runtime.kinds.governance import prompt_digest
from ufo.runtime.object_name import validate_object_name
from ufo.runtime.seats import signup_workspace_id
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.turns.transcript import (
    Conversation,
    TranscriptDecodeError,
    decode,
    encode,
    transcript_key,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    DELIVERY_PENDING,
    PENDING,
    ReasoningEffort,
    RuntimeAttestation,
    RuntimeIdentity,
    SandboxSize,
    TerminalFrame,
    ToolIntent,
    TurnContext,
    TurnRuntimeConfig,
    Usage,
)
from ufo.sdk.models import (
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

EVAL_SURFACE = "eval"
REMOTE_SURFACE = "ufo"
CANDIDATE_AGENT_NAME = "candidate-{proposal_id}"
POLL_INTERVAL_SECONDS = 1.0
TRANSCRIPT_POLL_ATTEMPTS = 6
WORKFLOW_WAIT_SECONDS = 300.0
TERMINAL_STATUSES = frozenset({"done", "cancelled", "failed"})
WORKFLOW_STATUSES = frozenset({"queued", "running"})
FAILED_WORKFLOW_STATUSES = frozenset({"ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED", "CANCELLED"})
REMOTE_TOKEN_TTL = timedelta(days=1)
REMOTE_EVAL_DOMAIN = "eval.invalid"


class RemoteTurnTimeout(Exception):
    def __init__(self, turn_id: UUID | None) -> None:
        super().__init__("ufo remote session exceeded the workflow wait")
        self.turn_id = turn_id


@dataclass
class _StoredEnvironment:
    digest: str | None = None


async def _resolved_environment_files(
    body: bytes, base: Path, store: Callable[[bytes], Awaitable[str]]
) -> bytes:
    """The authored document with every local `files:` path uploaded through `store` and replaced
    by its digest — byte-identical when there is nothing to resolve, so a digest-only document
    stores exactly the bytes the author wrote."""
    loaded = yaml.safe_load(body)
    files = loaded.get("files") if isinstance(loaded, dict) else None
    if not isinstance(files, dict):
        return body
    rewrote = False
    for destination, source in files.items():
        if isinstance(source, str) and source.startswith("sha256:"):
            continue
        content = await asyncio.to_thread((base / str(source)).read_bytes)
        files[destination] = await store(content)
        rewrote = True
    if not rewrote:
        return body
    return json.dumps(loaded).encode()


def _identity_summary(identity: RuntimeIdentity) -> str:
    digest = sha256(identity.model_dump_json().encode()).hexdigest()[:12]
    return f"{identity.revision or 'unknown'}#{digest}"


@dataclass
class RemoteRuntimeLog:
    identity_cases: dict[RuntimeIdentity, set[UUID]] = field(default_factory=dict)
    models: set[str] = field(default_factory=set)
    reasoning: set[ReasoningEffort | None] = field(default_factory=set)
    environments: set[str | None] = field(default_factory=set)
    error: str = ""

    def record(self, conversation_id: UUID, attestation: RuntimeAttestation) -> None:
        """Count one streamed attestation's identity. Identity only: a session's stream renders
        every terminal it tails — a profile child's beside the case root's — so the model pin
        never reads it."""
        self.identity_cases.setdefault(attestation.runtime, set()).add(conversation_id)

    def pin_target(
        self, model: str, reasoning: ReasoningEffort | None, environment: str | None
    ) -> None:
        """One admitted case turn's terminal settings — what `verify` holds against the run's pin.
        The driver pins only the turn it admitted, read off that turn's durable row, so a profile
        child legitimately running the member's own model never taints the pin."""
        if model:
            self.models.add(model)
            self.reasoning.add(reasoning)
            self.environments.add(environment)

    def reject(self, reason: str) -> None:
        self.error = reason

    def identity_counts(self) -> dict[str, int]:
        """Cases per attested runtime identity (`revision#digest12`), most-covered first —
        informational: a fleet deploy rolling mid-run shows here as two identities, never as a
        refusal."""
        return {
            _identity_summary(identity): len(cases)
            for identity, cases in sorted(
                self.identity_cases.items(), key=lambda item: -len(item[1])
            )
        }

    def verify(
        self, model: str, reasoning: ReasoningEffort, environment: str | None = None
    ) -> RuntimeAttestation:
        if self.error:
            raise RuntimeError(self.error)
        if not self.identity_cases:
            raise RuntimeError("remote eval received no runtime attestation")
        if self.models and self.models != {model}:
            raise RuntimeError(
                f"remote eval expected model {model!r}, terminal frames reported "
                f"{sorted(self.models)!r}"
            )
        if self.reasoning and self.reasoning != {reasoning}:
            raise RuntimeError(
                f"remote eval expected reasoning {reasoning!r}, terminal frames reported "
                f"{sorted(value or '' for value in self.reasoning)!r}"
            )
        if self.environments and self.environments != {environment}:
            raise RuntimeError(
                f"remote eval expected environment {environment!r}, terminal frames reported "
                f"{sorted(value or '' for value in self.environments)!r}"
            )
        dominant = max(self.identity_cases, key=lambda identity: len(self.identity_cases[identity]))
        return RuntimeAttestation(
            runtime=dominant,
            model=next(iter(self.models), ""),
            reasoning=next(iter(self.reasoning), None),
            environment=next(iter(self.environments), None),
        )


RUNNER_KEY_ENVS = ((PROVIDER_ANTHROPIC, ANTHROPIC_KEY_ENV), (PROVIDER_OPENAI, OPENAI_KEY_ENV))


def runner_model_key() -> MemberModelKey | None:
    """The member model-key seed a fresh remote workspace gets: the first provider key the runner's
    environment holds, in the provider order the gate reads. None when it holds neither, and the
    workspace is seated without one — a coding-profile spawn then refuses exactly as it would for a
    member who never connected an account."""
    for provider, env in RUNNER_KEY_ENVS:
        key = deploy_env(env)
        if key:
            return MemberModelKey(provider=provider, key=key)
    return None


@dataclass(frozen=True)
class RemoteWorkspaceProvisioner:
    client: AsyncClient
    budget_micro_usd: int
    model_key: MemberModelKey | None = None

    async def provision(self, run_id: UUID) -> UUID:
        """Found one clean hosted workspace whose identity is derived from the eval run. The seat
        carries `model_key` so the seated member holds their own provider account and a profile
        gated on one (the coding subagent) spawns for them."""
        domain = f"{run_id.hex}.{REMOTE_EVAL_DOMAIN}"
        workspace_id = signup_workspace_id(domain)
        request = SeatRequest(
            workspace_id=workspace_id,
            domain=domain,
            email=f"swebench@{domain}",
            signup_subject=domain,
            model_key=self.model_key,
        )
        response = await self.client.post(
            "/internal/onboard/seat",
            json=request.model_dump(mode="json"),
        )
        if not response.is_success:
            detail = response.text.strip()[:1_000]
            raise RuntimeError(
                f"hosted eval workspace provisioning failed ({response.status_code}): {detail}"
            )
        try:
            provisioned = EnsuredWorkspace.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise RuntimeError(
                "hosted eval workspace provisioning returned an invalid response"
            ) from error
        if (
            provisioned.workspace_id != str(workspace_id)
            or not provisioned.admin
            or not provisioned.founding
        ):
            raise RuntimeError(
                "hosted eval workspace provisioning did not create the expected admin workspace"
            )
        await EvalRunBudget(run_id, self.budget_micro_usd).install(workspace_id)
        return workspace_id


@dataclass(frozen=True)
class RemoteClient:
    executable: str
    workspace_url: str
    token_secret: str
    home_root: Path
    model: str | None = None
    environment_document: Path | None = None
    runtime: RemoteRuntimeLog = field(default_factory=RemoteRuntimeLog)
    _environment: _StoredEnvironment = field(default_factory=_StoredEnvironment, init=False)

    async def validate(self) -> None:
        """Fail unless the selected client implements the remote JSON transport."""
        process = await asyncio.create_subprocess_exec(
            self.executable,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        needed = (
            b"--remote",
            b"--json",
            *((b"--model",) if self.model is not None else ()),
            *((b"--environment",) if self.environment_document is not None else ()),
        )
        if process.returncode or any(option not in stdout for option in needed):
            detail = (stderr or stdout).decode("utf-8", "replace").strip()
            raise RuntimeError(f"{self.executable} does not support remote JSON sessions: {detail}")

    def stored_environment(self) -> str | None:
        """The digest this run's turns must attest to — set once the document uploaded, None when
        the run pins no environment (a turn attesting one anyway fails verification)."""
        return self._environment.digest

    async def _environment_value(self, workspace_id: UUID, email: str) -> str | None:
        """The digest every remote turn pins: the run's overrides document, stored once through
        the workspace's own document endpoint so the eval measures exactly the bytes it uploaded."""
        if self.environment_document is None:
            return None
        if self._environment.digest is None:
            token = mint_token(self.token_secret, str(workspace_id), email, REMOTE_TOKEN_TTL)
            headers = {"authorization": f"Bearer {token}"}
            body = await asyncio.to_thread(self.environment_document.read_bytes)
            async with AsyncClient(timeout=30.0) as client:

                async def store(content: bytes) -> str:
                    stored = await client.post(
                        f"{self.workspace_url}/surface/ufo/environment/file",
                        content=content,
                        headers=headers,
                    )
                    if stored.status_code != 200:
                        raise RuntimeError(
                            f"environment file refused ({stored.status_code}): {stored.text}"
                        )
                    return stored.text

                body = await _resolved_environment_files(
                    body, self.environment_document.parent, store
                )
                response = await client.post(
                    f"{self.workspace_url}/surface/ufo/environment/document",
                    content=body,
                    headers=headers,
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"environment document refused ({response.status_code}): {response.text}"
                )
            self._environment.digest = response.text
        return self._environment.digest

    async def admit(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        email: str,
        message: str,
        wait_seconds: float,
    ) -> UUID:
        """Run one member turn through `ufo --remote --json` and return its durable turn id."""
        home = self.home_root / str(conversation_id)
        token = mint_token(self.token_secret, str(workspace_id), email, REMOTE_TOKEN_TTL)
        environment = await self._environment_value(workspace_id, email)
        await asyncio.to_thread(self._write_credentials, home, token)
        env = dict(os.environ)
        env.update(
            {
                "UFO_HOME": str(home),
                "UFO_URL": self.workspace_url,
                "WORKSPACE_URL": self.workspace_url,
                "UFO_CHANNEL": str(conversation_id),
            }
        )
        process: asyncio.subprocess.Process | None = None
        admitted: list[UUID] = []
        try:
            try:
                loop = asyncio.get_running_loop()
                async with asyncio.timeout(None) as budget:

                    def executing() -> None:
                        if budget.when() is None:
                            budget.reschedule(loop.time() + wait_seconds)

                    process = await asyncio.create_subprocess_exec(
                        self.executable,
                        "--remote",
                        *(() if self.model is None else ("--model", self.model)),
                        *(() if environment is None else ("--environment", environment)),
                        "--json",
                        "--resume",
                        str(conversation_id),
                        message,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                    stdout, stderr = await self._read_session(process, admitted, executing)
            except TimeoutError as error:
                await self._stop(process)
                raise RemoteTurnTimeout(admitted[-1] if admitted else None) from error
            except BaseException:
                await self._stop(process)
                raise
        finally:
            await asyncio.to_thread(shutil.rmtree, home, True)
        try:
            events = tuple(json.loads(line) for line in stdout.splitlines())
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"ufo remote session returned non-JSON output: "
                f"{stdout.decode('utf-8', 'replace')[-1000:]}"
            ) from error
        if not events or any(not isinstance(event, dict) for event in events):
            raise RuntimeError("ufo remote session returned no JSON events")
        event_types = tuple(event.get("type") for event in events)
        if event_types[0] != "session_start" or "turn_end" not in event_types:
            raise RuntimeError(f"ufo remote session returned incomplete JSON events: {event_types}")
        runtime_events = tuple(event for event in events if event.get("type") == "runtime")
        if not runtime_events:
            self.runtime.reject("remote eval terminal reported no runtime attestation")
        for event in runtime_events:
            try:
                self.runtime.record(
                    conversation_id,
                    RuntimeAttestation.model_validate(
                        {key: value for key, value in event.items() if key != "type"}
                    ),
                )
            except ValidationError as error:
                self.runtime.reject("remote eval terminal reported an invalid runtime attestation")
                raise RuntimeError(
                    "ufo remote session returned an invalid runtime attestation"
                ) from error
        if len(admitted) != 1:
            detail = (stderr or stdout).decode("utf-8", "replace").strip()[-1000:]
            raise RuntimeError(f"ufo remote session admitted {len(admitted)} turns: {detail}")
        return admitted[0]

    @staticmethod
    async def _read_session(
        process: asyncio.subprocess.Process,
        admitted: list[UUID],
        executing: Callable[[], None],
    ) -> tuple[bytes, bytes]:
        """Read the session's events, calling `executing` at the first evidence the admitted turn
        is actually running. A turn waiting in the fleet's member-turn slot queue emits nothing
        after its `message_sent`, and a queued turn never times out — the wait budget covers
        execution, so the caller arms it here rather than at admission."""
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("ufo remote session pipes are unavailable")
        lines: list[bytes] = []
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            while line := await process.stdout.readline():
                lines.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                match event:
                    case {"type": "message_sent", "turn_id": str(turn_id)}:
                        admitted.append(UUID(turn_id))
                    case {"type": _} if admitted:
                        executing()
            await process.wait()
            return b"".join(lines), await stderr_task
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process | None) -> None:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

    @staticmethod
    def _write_credentials(home: Path, token: str) -> None:
        home.mkdir(parents=True, exist_ok=True)
        path = home / "credentials"
        path.write_text(f"{token}\n")
        path.chmod(0o600)


async def resolve_workspace_and_agent(
    agent_name: str, workspace_id: UUID | None = None
) -> tuple[UUID, UUID, str, str, ReasoningEffort, SandboxSize]:
    """Resolve the named target agent in an explicit workspace or the dedicated workspace."""
    if workspace_id is None:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                        tables.agent.c.sandbox_size,
                    ).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == agent_name,
                    )
                )
            ).one()
    return (
        workspace_id,
        agent.id,
        agent.prompt,
        agent.model,
        agent.reasoning,
        agent.sandbox_size,
    )


async def seed_candidate_agent(
    proposal_id: UUID, workspace_id: UUID | None = None
) -> tuple[UUID, str]:
    """Seed a disposable scratch agent carrying a pending proposal's candidate prompt and its base
    agent's model and reasoning effort, and return the workspace and the scratch agent's name — the
    arm the harness runs to measure a self-improvement proposal's cross-suite impact. The candidate
    varies only the prompt body; model, reasoning effort, workspace, and the suites stay fixed
    against the baseline run, so the before/after diff isolates the proposal. Upsert by name: a
    re-run against the same proposal reseeds one stable `candidate-<proposal>` agent rather than
    accreting rows."""
    if workspace_id is None:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    name = CANDIDATE_AGENT_NAME.format(proposal_id=proposal_id)
    validate_object_name(name)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            proposal = (
                await connection.execute(
                    sa.select(
                        tables.proposal.c.agent_id,
                        tables.proposal.c.body,
                        tables.proposal.c.status,
                    ).where(
                        tables.proposal.c.workspace_id == workspace_id,
                        tables.proposal.c.id == proposal_id,
                    )
                )
            ).one_or_none()
            if proposal is None:
                raise ValueError(f"no proposal {proposal_id} in workspace {workspace_id}")
            if proposal.status != PENDING:
                raise ValueError(f"proposal {proposal_id} is {proposal.status}, not pending")
            prompt = proposal.body.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise ValueError(f"proposal {proposal_id} carries no prompt body")
            base = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                        tables.agent.c.sandbox_size,
                    ).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.id == proposal.agent_id,
                    )
                )
            ).one()
            existing = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == name,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name=name,
                        prompt=prompt,
                        model=base.model,
                        reasoning=base.reasoning,
                        sandbox_size=base.sandbox_size,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                await connection.execute(
                    sa.update(tables.agent)
                    .values(
                        prompt=prompt,
                        model=base.model,
                        reasoning=base.reasoning,
                        sandbox_size=base.sandbox_size,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.agent.c.id == existing)
                )
    return workspace_id, name


@dataclass(frozen=True)
class WorkspaceDriver:
    workspace_id: UUID
    agent_id: UUID
    agent_prompt: str
    blob: WorkspaceBlobStore
    dbos: DBOSClient
    workspace_root: Path
    agent_model: str = "eval"
    pricing: Pricing = CORE_PRICING
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS
    workflow_wait_seconds: float = WORKFLOW_WAIT_SECONDS
    remote: RemoteClient | None = None
    environment_document: Path | None = None
    _environment: _StoredEnvironment = field(default_factory=_StoredEnvironment, init=False)

    async def open(
        self,
        case_name: str,
        member_key: str | None = None,
        workspace_files: tuple[WorkspaceFile, ...] = (),
        prior_messages: tuple[str, ...] = (),
        undelivered: tuple[UndeliveredRound, ...] = (),
        shared: bool = False,
    ) -> UUID:
        """Open one isolated eval conversation, bound to the member who speaks in it. A case names
        its member by the exact workspace `member.email`; one that names none speaks as the
        workspace's founding admin, so a case reads to the runtime as the member message it is
        written as rather than as a background fire.

        `shared` leaves the conversation unowned, which is what a shared room is: the
        `conversation_audience_member` check holds `member_id` not-null exactly when the audience is
        that member's private subject, so a conversation cannot be both owned and shared. Ownership
        is not authorship — the member who speaks rides `turn.speaker_member_id`, which `admit`
        carries, so a shared-audience case still speaks as its asker.

        An absent email fails rather than degrading to
        shared-only recall. Seeded undelivered rounds land as the narration-plus-tool-call pairs
        they were, so the case message reads to the model as a member writing into a turn already
        at work."""
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            member_id = await self._speaker(connection, member_key)
            member_email = (
                None
                if member_id is None
                else (
                    await connection.execute(
                        sa.select(tables.member.c.email).where(tables.member.c.id == member_id)
                    )
                ).scalar_one()
            )
            if self.remote is not None and (shared or member_email is None):
                raise ValueError("remote eval conversations require one member audience")
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    agent_id=self.agent_id,
                    surface=REMOTE_SURFACE if self.remote is not None else EVAL_SURFACE,
                    queue_key=(
                        f"{member_email}:{conversation_id}"
                        if self.remote is not None
                        else f"{EVAL_SURFACE}:{case_name}:{conversation_id}"
                    ),
                    member_id=None if shared else member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            if prior_messages:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=self.workspace_id,
                        conversation_id=conversation_id,
                        agent_id=self.agent_id,
                        seq=1,
                        status="done",
                        inbound=prior_messages[0],
                        terminal={"status": "done", "text": "Done.", "model": self.agent_model},
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        if prior_messages:
            messages = tuple(
                Message(role="user" if index % 2 == 0 else "assistant", content=content)
                for index, content in enumerate(prior_messages)
            )
            for index, round in enumerate(undelivered):
                call_id = f"undelivered-{index}"
                messages = (
                    *messages,
                    Message(
                        role="assistant",
                        content=(
                            *((TextBlock(text=round.narration),) if round.narration else ()),
                            ToolUseBlock(id=call_id, name=round.tool, input=round.input),
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            ToolResultBlock(
                                tool_use_id=call_id,
                                content=round.result,
                                is_error=round.is_error,
                            ),
                        ),
                    ),
                )
            await self.blob.put(
                transcript_key(conversation_id), encode(Conversation(seq=1, messages=messages))
            )
        for item in workspace_files:
            target = self.workspace_path(conversation_id, item.path)
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, item.content)
        return conversation_id

    async def steps(self, turn_id: UUID) -> tuple[TurnStep, ...]:
        """The turn's durable engine steps, in the order the workflow recorded them. A tool call's
        step carries the tool-use id its result was memoized under, which is what names it.

        A round's cost is reported only as far as the record supports it, never raised over. The
        driver knows the evaluated agent's model, so a cancel frame that names no model still
        prices each completed model round from its recorded usage. The rounds are also only part of
        what a turn spends: a compaction rides its own step, the
        browser `find` ranking meters onto a dispatch, and a resumed attempt bills on top of a step
        log that starts empty, so the terminal's residual cost lands on the last round only when the
        rounds account for every token the terminal counts. A completed model or tool step also
        carries the message its memoized output rebuilds, so an eval timeout retains the trajectory
        that finished before cancellation."""
        recorded = await self.dbos.list_workflow_steps_async(str(turn_id))
        stream_usage: dict[int, Usage] = {}
        for index, step in enumerate(recorded):
            output = step.get("output")
            if not isinstance(output, StreamResult):
                continue
            stream_usage[index] = Usage(
                input_tokens=sum(item.input_tokens for item in output.usages),
                output_tokens=sum(item.output_tokens for item in output.usages),
                cache_read_tokens=sum(item.cache_read_tokens for item in output.usages),
                cache_write_5m_tokens=sum(item.cache_write_5m_tokens for item in output.usages),
                cache_write_30m_tokens=sum(item.cache_write_30m_tokens for item in output.usages),
                cache_write_1h_tokens=sum(item.cache_write_1h_tokens for item in output.usages),
            )
        resources: dict[int, tuple[int, int | None]] = {}
        if stream_usage:
            async with workspace_tx() as connection:
                terminal = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one()
            terminal_model = terminal.get("model", "") if terminal else ""
            model = terminal_model or self.agent_model
            terminal_tokens = terminal.get("tokens", 0) if terminal else 0
            terminal_cost = terminal.get("cost_micro_usd", 0) if terminal else 0
            costs: dict[int, int] = {}
            for index, usage in stream_usage.items():
                tokens = sum(
                    (
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.cache_read_tokens,
                        usage.cache_write_5m_tokens,
                        usage.cache_write_30m_tokens,
                        usage.cache_write_1h_tokens,
                    )
                )
                if model:
                    costs[index] = self.pricing.micro_usd(model, usage) if tokens else 0
                resources[index] = (tokens, costs.get(index))
            if (
                terminal_model
                and costs
                and sum(tokens for tokens, _cost in resources.values()) == terminal_tokens
            ):
                last = next(reversed(costs))
                costs[last] += terminal_cost - sum(costs.values())
                if costs[last] < 0:
                    raise RuntimeError(f"turn {turn_id} step cost exceeds its terminal")
                resources[last] = (resources[last][0], costs[last])
        steps: list[TurnStep] = []
        for index, step in enumerate(recorded):
            messages: tuple[Message, ...] = ()
            match step.get("output"):
                case DispatchResult() as dispatched:
                    call_id = dispatched.tool_use_id
                    call_ids: tuple[str, ...] = ()
                    step_tokens: int | None = None
                    step_output_tokens: int | None = None
                    step_cost_micro_usd: int | None = None
                    if dispatched.image_refs:
                        images: list[ImageBlock] = []
                        for ref in dispatched.image_refs:
                            images.append(
                                ImageBlock(
                                    source=ImageSource(
                                        media_type=ref.media_type,
                                        data=(await self.blob.get(ref.blob_key)).decode(),
                                    )
                                )
                            )
                        content: str | tuple[TextBlock | ImageBlock, ...] = (
                            *((TextBlock(text=dispatched.text),) if dispatched.text else ()),
                            *images,
                        )
                    else:
                        content = dispatched.text
                    messages = (
                        Message(
                            role="user",
                            content=(
                                ToolResultBlock(
                                    tool_use_id=dispatched.tool_use_id,
                                    content=content,
                                    is_error=dispatched.is_error,
                                    activity=dispatched.activity,
                                ),
                            ),
                        ),
                    )
                case StreamResult() as streamed:
                    step_tokens, step_cost_micro_usd = resources[index]
                    step_output_tokens = stream_usage[index].output_tokens
                    call_id = ""
                    call_ids = tuple(call.id for call in streamed.tool_calls)
                    blocks = (
                        *streamed.reasoning,
                        *((TextBlock(text=streamed.text),) if streamed.text else ()),
                        *streamed.tool_calls,
                    )
                    if blocks:
                        messages = (Message(role="assistant", content=blocks),)
                    elif streamed.partial_output:
                        messages = (Message(role="assistant", content=streamed.partial_output),)
                case _:
                    call_id = ""
                    call_ids = ()
                    step_tokens = step_output_tokens = step_cost_micro_usd = None
            steps.append(
                TurnStep(
                    function_name=step["function_name"],
                    started_at_epoch_ms=step.get("started_at_epoch_ms"),
                    completed_at_epoch_ms=step.get("completed_at_epoch_ms"),
                    call_id=call_id,
                    call_ids=call_ids,
                    tokens=step_tokens,
                    output_tokens=step_output_tokens,
                    cost_micro_usd=step_cost_micro_usd,
                    messages=messages,
                )
            )
        return tuple(steps)

    async def _speaker(self, connection: AsyncConnection, member_key: str | None) -> UUID | None:
        """The member a conversation speaks as: the one a case names — fail loud on an email this
        workspace does not carry — else the workspace's founding admin, the member `ufoctl init`
        seats.

        A workspace with no admin at all speaks unbound. Only a materialized corpus builds one:
        `memory_100` inserts exactly the members its audience bindings name, and its shared-audience
        cases carry no email on purpose, since a speaker there would union that member's private
        subject into what the case may recall and change what the grader sees. Choosing an arbitrary
        member for them would corrupt the grade; refusing would abort the run. So the corpus keeps
        the shared reading it was built for, and every ordinary workspace gets its owner."""
        selection = sa.select(tables.member.c.id).where(
            tables.member.c.workspace_id == self.workspace_id
        )
        if member_key is not None:
            found = (
                await connection.execute(selection.where(tables.member.c.email == member_key))
            ).scalar_one_or_none()
            if found is None:
                raise ValueError(
                    f"eval member_key {member_key!r} is not a member email in this workspace"
                )
            return found
        return (
            await connection.execute(
                selection.where(tables.member.c.is_admin.is_(True))
                .order_by(tables.member.c.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _owner(self, connection: AsyncConnection, conversation_id: UUID) -> UUID | None:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID:
        """Admit one case message as the member who speaks it. A remote driver uses the terminal
        surface through `ufo --remote --json`; an in-process driver uses the same `MemberAdmission`
        every surface reaches, including its seat gate and mid-turn folding.

        `speaker_key` is that member's email, carried by the case rather than read off the
        conversation: a shared room is unowned (`member_id` is null) yet still has someone talking
        in it, so authorship cannot be derived from ownership. Absent one, the conversation's own
        member speaks, which is the private-conversation case."""
        async with workspace_tx() as connection:
            speaker = await (
                self._speaker(connection, speaker_key)
                if speaker_key is not None
                else self._owner(connection, conversation_id)
            )
            sender = (
                None
                if speaker is None
                else (
                    await connection.execute(
                        sa.select(tables.member.c.email).where(tables.member.c.id == speaker)
                    )
                ).scalar_one()
            )
        if self.remote is not None:
            if sender is None:
                raise ValueError("remote eval conversations require a member speaker")
            try:
                turn_id = await self.remote.admit(
                    self.workspace_id,
                    conversation_id,
                    sender,
                    message,
                    self.workflow_wait_seconds,
                )
            except RemoteTurnTimeout as error:
                if error.turn_id is None:
                    raise RuntimeError(
                        "ufo remote session timed out before admitting a turn"
                    ) from error
                await self._cancel_overdue(conversation_id, error.turn_id)
                turn_id = error.turn_id
            await self._pin_admitted_runtime(self.remote, turn_id)
            return turn_id
        admitter = MemberAdmission(
            admission=Admission(dbos=self.dbos, durable_surfaces=frozenset()),
            workspace_id=self.workspace_id,
        )
        admitted = await admitter.admit(
            conversation_id,
            message,
            idempotency_key,
            context=None if sender is None else TurnContext(sender=sender),
            speaker_member_id=speaker,
            runtime_config=await self._local_environment(),
        )
        return admitted.turn_id

    async def _pin_admitted_runtime(self, remote: RemoteClient, turn_id: UUID) -> None:
        """Pin the admitted turn's terminal model, reasoning, and environment for runtime
        verification, read off its durable row — the session stream carries no turn attribution,
        so a profile child's terminal rendered on it must never reach the pin. The runner only
        admits case roots, and the row proves it: a spawned turn carries its lineage
        (`parent_turn_id`, `subagent_profile`) and pins nothing. A turn without a terminal —
        the wait deadline lost its race to the cancel — pins nothing either, exactly as a session
        that never rendered one."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.terminal,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.subagent_profile,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None or row.terminal is None:
            return
        if row.parent_turn_id is not None or row.subagent_profile is not None:
            return
        frame = TerminalFrame.model_validate(row.terminal)
        remote.runtime.pin_target(frame.model, frame.reasoning, frame.environment)

    async def _local_environment(self) -> TurnRuntimeConfig | None:
        """The digest every in-process turn pins: the run's environment document, stored once
        content-addressed in this workspace's blob store — the same bytes the host loads back."""
        if self.environment_document is None:
            return None
        if self._environment.digest is None:
            body = await asyncio.to_thread(self.environment_document.read_bytes)

            async def store(content: bytes) -> str:
                return await store_environment_file(self.blob, content)

            body = await _resolved_environment_files(body, self.environment_document.parent, store)
            self._environment.digest = await store_environment_document(self.blob, body)
        return TurnRuntimeConfig(environment=self._environment.digest)

    async def apply_object_intent(
        self,
        source_conversation_id: UUID,
        kind: str,
        name: str,
        spec: dict[str, JsonValue],
        idempotency_key: str,
    ) -> tuple[UUID, TerminalFrame]:
        """Apply one browser-captured object write through the member's prepared-intent lane."""

        async with workspace_tx() as connection:
            source = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.agent_id,
                        tables.conversation.c.member_id,
                        tables.member.c.email,
                    )
                    .join(tables.member, tables.member.c.id == tables.conversation.c.member_id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == source_conversation_id,
                    )
                )
            ).one()
            queue_key = f"intent/{source.agent_id}/{source.email}"
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.queue_key == queue_key,
                    )
                )
            ).scalar_one_or_none()
            if conversation_id is None:
                conversation_id = uuid4()
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
                        agent_id=source.agent_id,
                        member_id=source.member_id,
                        surface=EVAL_SURFACE,
                        queue_key=queue_key,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        manifest = json.dumps({"kind": kind, "name": name, "spec": spec})
        intent = ToolIntent(
            tool="object_apply",
            input={
                "manifest": manifest,
            },
        )
        admitted = await MemberAdmission(
            admission=Admission(dbos=self.dbos, durable_surfaces=frozenset()),
            workspace_id=self.workspace_id,
        ).admit(
            conversation_id,
            intent.model_dump_json(),
            idempotency_key,
            TurnContext(sender=source.email),
            speaker_member_id=source.member_id,
            intent=intent,
        )
        await self.settle(conversation_id, admitted.turn_id)
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.id == admitted.turn_id,
                    )
                )
            ).scalar_one_or_none()
        if terminal is None:
            raise RuntimeError("prepared object intent produced no terminal")
        return admitted.turn_id, TerminalFrame.model_validate(terminal)

    async def stage(self, conversation_id: UUID, path: str, source: Path) -> None:
        target = self.workspace_path(conversation_id, path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, source, target)

    def workspace_path(self, conversation_id: UUID, rel: str) -> Path:
        """The host location of a conversation workspace file — the directory serve's local
        carrier serves `/workspace` from, which the eval process shares a filesystem with."""
        return self.workspace_root / str(conversation_id) / rel

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.seq,
                        tables.turn.c.result_delivery,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES and row.result_delivery != DELIVERY_PENDING:
            return await self._trajectory(conversation_id, row.seq)
        if row.status not in WORKFLOW_STATUSES and row.status not in TERMINAL_STATUSES:
            return None
        try:
            async with asyncio.timeout(self.workflow_wait_seconds):
                handle: WorkflowHandleAsync[object]
                while True:
                    try:
                        handle = await self.dbos.retrieve_workflow_async(str(turn_id))
                        break
                    except dbos_error.DBOSNonExistentWorkflowError as error:
                        async with workspace_tx() as connection:
                            row = (
                                await connection.execute(
                                    sa.select(
                                        tables.turn.c.status,
                                        tables.turn.c.seq,
                                        tables.turn.c.result_delivery,
                                    ).where(tables.turn.c.id == turn_id)
                                )
                            ).one_or_none()
                        if row is None:
                            return None
                        if (
                            row.status in TERMINAL_STATUSES
                            and row.result_delivery != DELIVERY_PENDING
                        ):
                            return await self._trajectory(conversation_id, row.seq)
                        if row.status in TERMINAL_STATUSES:
                            raise RuntimeError(
                                f"terminal child {turn_id} has no workflow to settle delivery"
                            ) from error
                        if row.status not in WORKFLOW_STATUSES:
                            return None
                        if row.status == "running":
                            raise
                        await asyncio.sleep(self.poll_interval_seconds)
                try:
                    await handle.get_result(polling_interval_sec=self.poll_interval_seconds)
                except Exception:
                    workflow = await handle.get_status()
                    if workflow.status not in FAILED_WORKFLOW_STATUSES:
                        raise
        except TimeoutError:
            return await self._cancel_overdue(conversation_id, turn_id)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.seq,
                        tables.turn.c.result_delivery,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if (
            row is None
            or row.status not in TERMINAL_STATUSES
            or row.result_delivery == DELIVERY_PENDING
        ):
            return None
        return await self._trajectory(conversation_id, row.seq)

    async def cancel(self, turn_id: UUID) -> bool:
        """Terminalize a live turn through the shared `cancel_one_turn` primitive: cancel its DBOS
        workflow — dequeuing a queued run, preempting a streaming model round — then commit its
        cancelled terminal. Returns False when the turn already reached its own terminal (the
        deadline racing its own done commit), which the primitive leaves untouched. The research
        subagents a delegated turn spawned are cancelled by the serve process's cancel reconciler,
        which sweeps any turn left live under a cancelled ancestor; the primitive's
        cancel-before-commit ordering keeps a crash mid-cancel from orphaning this root, which the
        reconciler never re-examines."""
        return await cancel_one_turn(self.dbos, turn_id) is not None

    async def _cancel_overdue(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        """The wait's deadline fired: terminalize the turn before the runner advances. A turn that
        reached its own terminal in the race settles normally."""
        if await self.cancel(turn_id):
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status, tables.turn.c.seq).where(
                        tables.turn.c.id == turn_id
                    )
                )
            ).one_or_none()
        if row is None or row.status not in TERMINAL_STATUSES:
            return None
        return await self._trajectory(conversation_id, row.seq)

    async def _trajectory(self, conversation_id: UUID, turn_seq: int) -> Trajectory | None:
        for attempt in range(TRANSCRIPT_POLL_ATTEMPTS):
            try:
                body = await self.blob.get(transcript_key(conversation_id))
            except BlobNotFound:
                pass
            else:
                try:
                    conversation = decode(body)
                except TranscriptDecodeError:
                    return None
                if conversation.seq == turn_seq:
                    return Trajectory(
                        conversation_id=conversation_id,
                        agent_id=self.agent_id,
                        agent_prompt=self.agent_prompt,
                        agent_prompt_digest=prompt_digest(self.agent_prompt),
                        messages=conversation.messages,
                    )
            if attempt < TRANSCRIPT_POLL_ATTEMPTS - 1:
                await asyncio.sleep(self.poll_interval_seconds)
        return None
