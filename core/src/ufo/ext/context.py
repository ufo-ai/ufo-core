"""The capability-scoped view a job or extension handler receives.

A handler never sees a raw DB handle, a raw blob store, or another workspace: it gets a
`ScopedStore` (its own key space under one workspace), `CredentialAccess` (only the slots its
manifest declared), manifest-scoped surface installation registration for tools, and — when
trajectory reads are wired — a `TrajectoryCorpus` (this workspace's transcripts, read only).
`context_for` builds the same shape for an extension and for a core job, so a core job rides the
exact path an extension does. The `ExtensionContext` shape is open: it carries the selected
index/embed backends, a transaction over the extension's own tables, governed proposals, and
invoke, without reshaping what handlers already hold."""

import hashlib
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import (
    UsageExport,
    ack_usage_exports,
    mint_usage_exports,
    read_pending_usage_exports,
    workspace_owns_the_key,
)
from ufo.agent_scope import agent, agent_current
from ufo.artifact_url import mint_image_preview_url
from ufo.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    parse_audience,
    readable_audiences,
)
from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.credentials import (
    CredentialSource,
    CredentialStore,
    installed_credential_requests,
    seal_installation,
    slot_secret,
)
from ufo.db import workspace_tx
from ufo.ext.surface import (
    OPERATOR_EMAIL_DOMAIN,
    TERMINAL_TURN_STATUSES,
    SurfaceInstallationAccess,
    TurnTailer,
    retitle_conversation,
    summarize_conversation_title,
)
from ufo.governance import Governance, prompt_digest
from ufo.hub import LiveFrame
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.interface import (
    Message,
    ModelClient,
    ModelRequest,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolUseBlock,
)
from ufo.models.pricing import Pricing
from ufo.o11y import BACKGROUND_PROFILE, emit_histogram, emit_metric, log
from ufo.sandbox.conversation import ConversationSandbox
from ufo.sandbox.session import ExecResult, ProbeToken, ProbeTokenCodec
from ufo.schema import tables
from ufo.schema.records import (
    MEMBER_ADMISSION,
    SUBAGENT_SURFACE,
    AgentChange,
    AgentVisibility,
    ProposalRef,
    TurnStatus,
    Usage,
)
from ufo.seats import workspace_domain
from ufo.sources.sync import PageFeed, SourceRowConfig, source_row_id
from ufo.subjects import SHARED_SUBJECT
from ufo.transcript import TranscriptDecodeError, decode, transcript_key
from ufo.workspace import ws_current

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class UndeclaredCredentialSlot(KeyError):
    """A handler asked for a credential slot its manifest never declared."""


@dataclass(frozen=True)
class ScopedStore:
    """One extension's durable key space within one workspace, reached only through workspace_tx.
    The workspace is the ambient one the turn or job bound — never passed, never another's."""

    extension: str

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def get(self, key: str) -> JsonValue | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.ext_store.c.value).where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key == key,
                    )
                )
            ).one_or_none()
        return None if row is None else row.value

    async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]:
        """The stored values for exactly `keys`, absent keys omitted — one query, so a listing
        joins its rows without reading the extension's whole key space."""
        if not keys:
            return {}
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.ext_store.c.key, tables.ext_store.c.value).where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key.in_(tuple(keys)),
                    )
                )
            ).all()
        return {row.key: row.value for row in rows}

    async def put(self, key: str, value: JsonValue) -> None:
        """Write `key`, whether or not it is already there. One upsert, not a probe and a write: two
        callers writing a key that does not exist yet each find nothing to update, and separate
        statements would leave both to insert and one to fail on the primary key."""
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.ext_store)
                .values(
                    workspace_id=self.workspace_id,
                    extension=self.extension,
                    key=key,
                    value=value,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.ext_store.c.workspace_id,
                        tables.ext_store.c.extension,
                        tables.ext_store.c.key,
                    ],
                    set_={"value": value, "updated_at": sa.func.now()},
                )
            )

    async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool:
        """Write only while the stored value is still `expected`; returns whether it was written. A
        caller holding a value it read earlier writes through this so a concurrent writer's newer
        value is never overwritten by its own stale one. The row is locked, compared in Python and
        updated in one transaction; `expected is None` means the key was absent, so it inserts and
        reports whether the insert landed."""
        async with workspace_tx() as connection:
            if expected is None:
                insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
                landed = await connection.execute(
                    insert(tables.ext_store)
                    .values(
                        workspace_id=self.workspace_id,
                        extension=self.extension,
                        key=key,
                        value=value,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            tables.ext_store.c.workspace_id,
                            tables.ext_store.c.extension,
                            tables.ext_store.c.key,
                        ]
                    )
                )
                return landed.rowcount == 1
            locked = (
                await connection.execute(
                    sa.select(tables.ext_store.c.value)
                    .where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key == key,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if locked is None or locked.value != expected:
                return False
            await connection.execute(
                sa.update(tables.ext_store)
                .values(value=value, updated_at=sa.func.now())
                .where(
                    tables.ext_store.c.workspace_id == self.workspace_id,
                    tables.ext_store.c.extension == self.extension,
                    tables.ext_store.c.key == key,
                )
            )
            return True

    async def delete(self, key: str) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.ext_store).where(
                    tables.ext_store.c.workspace_id == self.workspace_id,
                    tables.ext_store.c.extension == self.extension,
                    tables.ext_store.c.key == key,
                )
            )

    async def list(self, prefix: str = "") -> tuple[tuple[str, JsonValue], ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.ext_store.c.key, tables.ext_store.c.value)
                    .where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key.startswith(prefix, autoescape=True),
                    )
                    .order_by(tables.ext_store.c.key)
                )
            ).all()
        return tuple((row.key, row.value) for row in rows)


@dataclass(frozen=True)
class CredentialAccess:
    """The declared-slot gate over the ambient workspace's secrets: a handler reads only the slots
    its manifest declared, and each resolves to the bound workspace's value through
    `ws_current().credential` or its declared source. Source and store dependencies stay private;
    workspace and secret both come from the scope the turn or job bound, so a handler can read
    neither an undeclared slot nor a workspace it did not name."""

    declared: frozenset[str]
    _sources: tuple[tuple[str, CredentialSource], ...] = ()
    _store: CredentialStore | None = None

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def get(self, slot: str) -> str:
        """Resolve a declared slot to the bound workspace's live value — its stored BYOK secret if
        set, else the platform default from env. An undeclared slot never reaches a secret; a slot
        set in neither place fails loud."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        return await ws_current().credential(slot)

    async def stored(self, slot: str) -> bool:
        """Whether the bound workspace holds its own secret for a declared slot rather than running
        on the platform default. A handler that spends money on a provider key asks this to know
        whose money it spent: the ledger meters what the platform is owed, and a call made on the
        workspace's own key is billed to it by that provider directly."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        return await ws_current().credential_is_stored(slot)

    async def resolve(self, slot: str) -> str:
        """Resolve a declared slot through its manifest source, then its stored or platform
        value."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        source = next((source for name, source in self._sources if name == slot), None)
        if source is None:
            return await ws_current().credential(slot)
        if self._store is None:
            raise RuntimeError(f"credential slot {slot!r} has a source but no credential store")
        secret = await slot_secret(slot, source, self.workspace_id, self._store)
        if secret is not None:
            return secret
        return await ws_current().credential(slot)

    async def rotate(self, slot: str, expected: str, plaintext: str) -> bool:
        """Compare-and-swap an existing declared slot after an external provider rotates it. This
        cannot create the initial credential: that remains the member-sealed handoff."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        return await ws_current().rotate_credential(slot, expected, plaintext)

    async def bind_installation(self, slot: str, installation_id: str) -> None:
        """Record a provider installation the caller has already proved this workspace's member can
        reach. What lands in the slot is a seal over `(workspace, installation)`, not the id: an id
        is a small integer, a deploy's app key mints against any installation of it, and the slot is
        also fillable through the ordinary credential prompt — so only a value sealed here opens
        when a credential is minted against it."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        await ws_current().put_credential(
            slot,
            seal_installation(
                installed_credential_requests().fernet, self.workspace_id, slot, installation_id
            ),
        )


@dataclass(frozen=True)
class Trajectory:
    """One conversation's durable transcript as the eval corpus reads it: the messages, plus the
    agent that produced them and that agent's current prompt (the baseline a proposer rewrites and
    the `from_digest` a governed change is pinned against)."""

    conversation_id: UUID
    agent_id: UUID
    agent_prompt: str
    agent_prompt_digest: str
    messages: tuple[Message, ...]


TRAJECTORY_CORPUS_CONVERSATIONS = 200


@dataclass(frozen=True)
class TrajectoryCorpus:
    """A handler's read reach into blob storage: this workspace's conversation transcripts, read
    only. The store stays module-private (`_blob`), so the only operations exposed are enumerating
    this workspace's trajectories and reading the ones a handler names — never an arbitrary blob get
    or put over another conversation or an artifact. The enumeration is bounded to the `limit` most
    recently created conversations, so a workspace with a long history hands a job a bounded corpus,
    never every transcript it ever produced. A conversation whose transcript is missing or corrupt
    is skipped-with-log, never aborting the whole corpus."""

    _blob: WorkspaceBlobStore
    limit: int = TRAJECTORY_CORPUS_CONVERSATIONS

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def trajectories(self) -> tuple[Trajectory, ...]:
        return await self._read(
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.workspace_id == self.workspace_id)
            .order_by(tables.conversation.c.created_at.desc(), tables.conversation.c.id.desc())
            .limit(self.limit)
            .scalar_subquery()
        )

    async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]:
        """The transcripts of exactly these conversations — the read a job that already knows which
        conversations it works on takes, so it decodes what it works on rather than the corpus
        around it, and reaches a conversation older than the `limit` most recent one. The workspace
        is still the boundary: an id another workspace holds answers nothing."""
        return await self._read(
            sa.select(tables.conversation.c.id)
            .where(
                tables.conversation.c.workspace_id == self.workspace_id,
                tables.conversation.c.id.in_(conversation_ids),
            )
            .scalar_subquery()
        )

    async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.id,
                        tables.turn.c.agent_id,
                        tables.agent.c.prompt,
                    )
                    .select_from(
                        tables.conversation.join(
                            tables.turn,
                            tables.turn.c.conversation_id == tables.conversation.c.id,
                        ).join(tables.agent, tables.agent.c.id == tables.turn.c.agent_id)
                    )
                    .where(tables.conversation.c.id.in_(chosen))
                    .distinct()
                    .order_by(tables.conversation.c.id)
                )
            ).all()
        seen: set[UUID] = set()
        trajectories: list[Trajectory] = []
        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)
            try:
                body = await self._blob.get(transcript_key(row.id))
            except BlobNotFound:
                continue
            try:
                conversation = decode(body)
            except TranscriptDecodeError as error:
                log("trajectory.skip_corrupt", conversation_id=str(row.id), error=str(error))
                continue
            trajectories.append(
                Trajectory(
                    conversation_id=row.id,
                    agent_id=row.agent_id,
                    agent_prompt=row.prompt,
                    agent_prompt_digest=prompt_digest(row.prompt),
                    messages=conversation.messages,
                )
            )
        return tuple(trajectories)


CONVERSATION_FILES_KEEP = 50


@dataclass(frozen=True)
class ConversationFiles:
    """Write a file into one conversation's agent-visible workspace, off-turn. The workspace lives
    in the conversation's sandbox, so a write goes through the carrier — the same `/workspace` the
    agent's file tools read on its next turn — and the sandbox seam stays module-private
    (`_sandboxes`): the only operations exposed are a scoped write and a scoped prune. Running a
    command in that sandbox is a capability of its own (`ConversationProbes`), wired into a
    different set of roles. Every operation resolves the conversation against the ambient workspace
    first, so a handler holding another tenant's conversation id writes nothing — the scoping is in
    the predicate, not left to the RLS tier."""

    _sandboxes: ConversationSandbox

    async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str:
        """Land `content` at `rel` inside the conversation's workspace and return the `/workspace`
        path the agent will see. Raises on an unknown conversation or a path that escapes."""
        return await self._sandboxes.write(conversation_id, rel, content)

    async def prune(
        self, conversation_id: UUID, rel_prefix: str, keep: int = CONVERSATION_FILES_KEEP
    ) -> None:
        """Keep only the newest `keep` files under `rel_prefix`, deleting the rest — the bound on an
        off-turn writer that appends unattended. Names sort lexically, so a timestamp-named file
        sorts chronologically."""
        await self._sandboxes.prune(conversation_id, rel_prefix, keep)


async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None:
    """The agent a conversation is permanently bound to, or None when the id names no conversation
    in this workspace. One read behind both askers: the handler resolving an opaque id to an agent
    wall, and an off-turn exec deciding whose scope it runs under."""
    async with workspace_tx() as connection:
        found = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.id == conversation_id,
                )
            )
        ).one_or_none()
    return None if found is None else found.agent_id


PROBE_TIMEOUT_SECONDS = 60
PROBE_TIMEOUT_MAX_SECONDS = 120

ProbeEnvironment = Callable[[UUID, UUID, UUID | None], Awaitable[dict[str, str]]]
"""What a probe's sandbox open exports, answered for one conversation, one probe id and the member
the probe acts as: the git proxy-auth and credential config, that member's connector CLI sentinels,
and the conversation's own id. The derivation reads the deploy's declared credential slots, so it is
wired in by the deploy that holds them rather than reached from here."""


@dataclass(frozen=True)
class ConversationProbes:
    """Run one bounded command in a conversation's sandbox, off every turn.

    This is the capability no extension can express. A turn's egress is authorized by that turn, so
    work meant to outlive its arming turn loses the network the moment the turn commits, and a
    container the carrier suspended has nothing left running to ask. A probe re-enters the
    conversation's own sandbox — the same `/workspace` the agent's files live in, resumed on touch —
    under a token this deployment signs for that one exec, and hands back what the command reported.

    Its authority is the authority a turn's own sandbox open has: the conversation's agent, that
    agent's snapshotted internet policy, the workspace's keyed credentials, the grants shared with
    the agent's audience, and — when `acting_member_id` names the member who armed the work — that
    member's private connections, exactly as their own turn would forward them. The one thing it
    deliberately lacks is the deployment's model key: no sentinel is exported and the proxy
    resolves no injection for it, so an unattended exec cannot spend the deployment's model
    budget.

    A conversation bound to a member's terminal raises `TerminalGone` when that terminal is not
    connected. That reaches the caller rather than reading as an empty result: a probe that could
    not run is not a probe that found nothing."""

    _sandboxes: ConversationSandbox
    _probe_tokens: ProbeTokenCodec
    _env: ProbeEnvironment

    async def run(
        self,
        conversation_id: UUID,
        command: str,
        timeout_s: int = PROBE_TIMEOUT_SECONDS,
        acting_member_id: UUID | None = None,
    ) -> ExecResult:
        """Run `command` under `bash -lc` in the conversation's sandbox and return its captured
        stdout, stderr, and exit code. A nonzero exit is a result, not an error — reading what a
        command reports is the whole point of running it.

        `acting_member_id` is the member this exec acts as: the one who armed the work it serves, so
        a command reaching their own connected account keeps reaching it off-turn, the way a
        scheduled fire keeps its initiator's private connectors. Passing nobody forwards only the
        connections shared with the whole workspace — safe, and the reason an unattended exec is
        never silently promoted to a member's authority.

        The exec runs bound to the conversation's own agent. A job binds a workspace and no agent —
        nothing has an agent to bind, since a probe answers to no turn — yet the environment is
        derived from that agent's connector grants, and the proxy resolves this probe's rules under
        the same agent. Binding it here is what makes those two agree, and what keeps an
        agent-scoped read anywhere under this call from failing on an unbound scope.

        The token's deadline is this timeout, minted before the sandbox is opened, so the probe's
        egress window is bounded by the exec it belongs to and closes with nothing having to revoke
        it. Raises on a conversation outside the ambient workspace, and on a timeout over
        `PROBE_TIMEOUT_MAX_SECONDS`: an unattended exec able to hold a sandbox open longer than that
        is a wait with no end rather than a probe."""
        if not 0 < timeout_s <= PROBE_TIMEOUT_MAX_SECONDS:
            raise ValueError(
                f"a probe timeout of {timeout_s}s is outside 1..{PROBE_TIMEOUT_MAX_SECONDS}s"
            )
        workspace_id = ws_current().workspace_id
        agent_id = await conversation_agent_id(workspace_id, conversation_id)
        if agent_id is None:
            raise ValueError(f"conversation {conversation_id} is not in this workspace")
        probe = ProbeToken(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            probe_id=uuid4(),
            expires_at=int(datetime.now(UTC).timestamp()) + timeout_s,
            acting_member_id=acting_member_id,
        )
        with agent(agent_id):
            session = await self._sandboxes.open(
                conversation_id,
                None,
                self._probe_tokens.encode(probe),
                await self._env(conversation_id, probe.probe_id, acting_member_id),
            )
            return await session.bash(command, timeout_s=timeout_s)


def trajectory_workspaces() -> WorkspaceCandidates:
    """The candidate seam a trajectory-reading job declares: the workspaces holding a conversation
    with at least one turn — a semi-join from `workspace` that stops each workspace at its first
    turn-bearing conversation, read for the extension through the one RLS-bypass path. Core owns
    the `conversation`/`turn` tables, so it owns this query and the extension declares
    `candidates=trajectory_workspaces()` without reaching `owner_tx`; the dispatcher binds each and
    the corpus read runs RLS-scoped, exactly as a turn would scope it."""

    def with_a_turn() -> sa.Select[tuple[UUID]]:
        return sa.select(tables.workspace.c.id).where(
            sa.exists(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == tables.workspace.c.id,
                    sa.exists(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.conversation_id == tables.conversation.c.id
                        )
                    ),
                )
            )
        )

    return owner_candidates(with_a_turn)


def seated_member_workspaces() -> WorkspaceCandidates:
    """Workspaces with at least one seated member, for first-party member jobs."""

    def with_a_seated_member() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(tables.member.c.workspace_id)
            .where(tables.member.c.seated_at.is_not(None))
            .distinct()
        )

    return owner_candidates(with_a_seated_member)


def connection_workspaces() -> WorkspaceCandidates:
    """The candidate seam a connection-driven job declares: the workspaces where the main agent
    holds a connector grant. Core owns the `connector_grant`/`agent` tables, so it owns this query —
    a workspace whose main agent has no connected account never fires the handler."""

    def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(tables.connector_grant.c.workspace_id)
            .join(tables.agent, tables.connector_grant.c.agent_id == tables.agent.c.id)
            .where(tables.agent.c.is_main.is_(True))
            .distinct()
        )

    return owner_candidates(with_a_main_agent_connection)


ANSWERED: TurnStatus = "done"


def awaiting_a_title() -> sa.ColumnElement[bool]:
    """A conversation the titling job still owes a summary: no summary has named it, and a member
    turn of it has answered. Both halves are the filter. A conversation nobody spoke in is an
    extension's errand or a subagent's run, named by the payload that opened it and read by nobody
    who needs a summary. A conversation whose member turns have all yet to answer — still running,
    or refused and cancelled at admission — has no exchange to summarize, so it is not work either:
    the transcript the summary is written from is what the run that ends a turn writes."""
    return sa.and_(
        tables.conversation.c.title_summarized.is_(False),
        sa.exists(
            sa.select(tables.turn.c.id).where(
                tables.turn.c.conversation_id == tables.conversation.c.id,
                tables.turn.c.admission_source == MEMBER_ADMISSION,
                tables.turn.c.status == ANSWERED,
            )
        ),
    )


def untitled_conversation_workspaces() -> WorkspaceCandidates:
    """The candidate seam the titling job declares: the workspaces holding a conversation a member
    spoke in that no summary has named yet. Core owns `conversation`/`turn` and the title on that
    row, so it owns this query — the job names the work without knowing which surfaces exist, and a
    workspace whose conversations are all named never fires the handler."""

    def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]:
        return sa.select(tables.conversation.c.workspace_id).where(awaiting_a_title()).distinct()

    return owner_candidates(with_an_unsummarized_title)


def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates:
    """The candidate seam a once-per-agent sweep declares: the workspaces whose agents outnumber
    the extension's `prefix` keys. The sweep writes one key per agent it settles and agent delete
    is refused, so a workspace leaves this set exactly when every agent is settled — counted
    rather than joined, because the key spells the agent id in Python's dashed form while the
    column's SQL text differs by dialect. Core owns `agent` and `ext_store`, so it owns this
    query — a workspace with nothing left to settle never fires the handler."""

    def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]:
        agents = (
            sa.select(sa.func.count())
            .where(tables.agent.c.workspace_id == tables.workspace.c.id)
            .scalar_subquery()
        )
        settled = (
            sa.select(sa.func.count())
            .where(
                tables.ext_store.c.workspace_id == tables.workspace.c.id,
                tables.ext_store.c.extension == extension,
                tables.ext_store.c.key.startswith(prefix, autoescape=True),
            )
            .scalar_subquery()
        )
        return sa.select(tables.workspace.c.id).where(agents > settled)

    return owner_candidates(with_an_unsettled_agent)


class TurnInvoker(Protocol):
    """The internal turn seam a background handler drives. It never consumes a member's pause;
    idempotency collapses a redelivered invocation to the turn already admitted."""

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        on_behalf_of_member_id: UUID | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
    ) -> UUID | None: ...


class ModelResolver(Protocol):
    """The model registry as the background model seam sees it: the deploy's default model, its
    per-workspace client (keyed through `ws_current().credential`), and the price table. Held as a
    Protocol so core's model layer stays out of the `ext.context` import cycle; read-only members so
    the frozen `ModelRegistry` dataclass satisfies it."""

    @property
    def auto_model(self) -> str: ...

    @property
    def pricing(self) -> Pricing: ...

    async def client_for(self, model: str) -> ModelClient: ...

    def key_slot_for(self, model: str) -> str | None: ...

    def provider_for(self, model: str) -> str: ...


@dataclass(frozen=True)
class ModelAccess:
    """The metered LLM a background handler reaches: one model turn against the deploy's default,
    keyed to and billed to the ambient workspace. It resolves its client through the same
    `client_for` a turn uses (the workspace's BYOK key, else the platform key) and books usage
    through the same `billable_event`, so the key's workspace and the billed workspace are one, by
    construction — never an unmetered direct egress. Both operations fix the request's model to the
    deploy default, so the model billed is always the model called; `turn` preserves requested tool
    calls in the existing assistant `Message` shape and `complete` returns only its text.

    `_job` is what its spend and latency are attributed to on the `ufo.model_*` series the turn
    engine already feeds: the job key this seam was wired for — `<extension>:<job>`, the key
    `bindings_from` registers, and the same shape for the two off-turn seams that run outside the
    job table (`core:ambient_reply`, the eval harness). The label is one bounded set, decided at
    boot by the installed extensions exactly as the `profile` dimension's set is, never a workspace,
    conversation, or payload value."""

    _resolver: ModelResolver
    _job: str

    @property
    def model(self) -> str:
        """The deploy default this seam bills and calls — the id fixed onto every completion."""
        return self._resolver.auto_model

    async def complete(self, request: ModelRequest) -> str:
        """Stream one completion against the deploy default, book its token usage to the bound
        workspace when the block succeeds, and return the assembled text. Bound `request.max_tokens`
        and the payload at the call site — this seam prices whatever the provider returns."""
        response = await self.turn(request)
        if isinstance(response.content, str):
            return response.content
        return "".join(block.text for block in response.content if isinstance(block, TextBlock))

    async def _serves_itself(self, model: str) -> bool:
        """Whether the workspace's own provider key serves this job's call, decided beside the call
        rather than at billing time — a background job is metered on the same terms a turn is, and
        the workspace already paid its provider directly for what its own key served."""
        async with workspace_tx() as connection:
            return await workspace_owns_the_key(
                connection, ws_current().workspace_id, self._resolver.key_slot_for(model)
            )

    async def turn(self, request: ModelRequest) -> Message:
        """Run one tool-aware model turn and return its assistant message after metering it. A
        reasoning round's message opens with the blocks the model streamed, ahead of its text and
        tool calls: a handler that feeds tool results back through `turn` sends that message again,
        and the provider requires the sequence unchanged beside the tool calls it authenticates.

        Metered onto the turn engine's own `model_round_ms` and `model_round_tokens_total` rather
        than a series of its own: the question an operator asks is what one model costs and how long
        it takes, and a second family would answer it twice — every dashboard, every rate, every
        price comparison would have to sum both and would silently miss whichever one it forgot. So
        the work shape rides the dimensions instead. `profile` is `background` where a turn writes
        `main`, a spawned agent `agent`, and a subagent its profile name, and `job` names which
        background one. A turn emits no `job` at all, so no turn series is split and the added cost
        is one series per (job, model, provider) — the jobs a deploy registers at boot, times the
        one background model they all run on.

        A failed call is metered too, carrying the `error_class` a round records: a job whose model
        call raises books no spend, so the ledger cannot show it, and the latency of the attempt is
        the only trace that the model was reached at all. A cancellation is one of those classes — a
        job dropped at shutdown must not read back as a round that answered in no tokens."""
        model = self._resolver.auto_model
        client = await self._resolver.client_for(model)
        byok = await self._serves_itself(model)
        dimensions = {
            "model": model,
            "provider": self._resolver.provider_for(model),
            "profile": BACKGROUND_PROFILE,
            "job": self._job,
        }
        parts: list[str] = []
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []
        reasoning: list[ThinkingBlock | RedactedThinkingBlock | ReasoningItemBlock] = []
        usages: list[Usage] = []
        failure: dict[str, str] = {}
        started = time.monotonic()
        try:
            async with ws_current().billable_event() as bill:
                async for event in client.complete(request.model_copy(update={"model": model})):
                    match event:
                        case TextDelta(text=text):
                            parts.append(text)
                        case ToolCallStart(id=call_id, name=name):
                            call_names[call_id] = name
                            call_json[call_id] = []
                            call_order.append(call_id)
                        case ToolCallDelta(id=call_id, partial_json=partial):
                            call_json[call_id].append(partial)
                        case ThinkingBlock() | RedactedThinkingBlock() | ReasoningItemBlock():
                            reasoning.append(event)
                        case Usage():
                            usages.append(event)
                            bill.usage(model, event, self._resolver.pricing, byok)
                if not usages:
                    raise RuntimeError("model stream produced no usage")
                tool_calls = tuple(
                    ToolUseBlock(
                        id=call_id,
                        name=call_names[call_id],
                        input=json.loads("".join(call_json[call_id]) or "{}"),
                    )
                    for call_id in call_order
                )
                usage = Usage(
                    input_tokens=sum(u.input_tokens for u in usages),
                    output_tokens=sum(u.output_tokens for u in usages),
                    cache_read_tokens=sum(u.cache_read_tokens for u in usages),
                    cache_write_5m_tokens=sum(u.cache_write_5m_tokens for u in usages),
                    cache_write_30m_tokens=sum(u.cache_write_30m_tokens for u in usages),
                    cache_write_1h_tokens=sum(u.cache_write_1h_tokens for u in usages),
                )
        except BaseException as error:
            failure = {"error_class": type(error).__name__}
            raise
        finally:
            emit_histogram(
                "model_round_ms",
                int((time.monotonic() - started) * 1000),
                **dimensions,
                **failure,
            )
        for kind, amount in (
            ("input", usage.input_tokens),
            ("output", usage.output_tokens),
            ("cache_read", usage.cache_read_tokens),
            (
                "cache_write",
                usage.cache_write_5m_tokens
                + usage.cache_write_30m_tokens
                + usage.cache_write_1h_tokens,
            ),
        ):
            if amount:
                emit_metric("model_round_tokens_total", amount, **dimensions, kind=kind)
        text = "".join(parts)
        if not tool_calls:
            return Message(role="assistant", content=text)
        return Message(
            role="assistant",
            content=(*reasoning, *((TextBlock(text=text),) if text else ()), *tool_calls),
        )


@dataclass(frozen=True)
class SourceRecord:
    """One live content-sync source as `ExtensionContext.sources` reads it: the row's identity,
    the backend's typed per-source parameters as stored, and the timing marks a caller renders as
    status; `subject` is the disclosure every synced page is stamped with, `owner_member_id` the
    registering member (None for a deploy- or extension-registered feed). A value object — never
    leaves the process."""

    id: UUID
    backend: str
    config: dict[str, JsonValue]
    subject: str
    owner_member_id: UUID | None
    connection_id: UUID | None
    next_sync_at: datetime
    consecutive_errors: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceReader:
    agent_id: UUID
    requesting_member_id: UUID | None
    subjects: frozenset[str]


@dataclass(frozen=True)
class PageRecord:
    """One live synced page as `ExtensionContext.source_pages` reads it: its identity, the source
    row it belongs to, browse metadata, visibility subject, content digest, and blob reference —
    the body stays by reference, never inlined. A value object — never leaves the process."""

    id: UUID
    source_id: UUID
    stream: str
    title: str
    record_created_at: str | None
    record_updated_at: str | None
    subject: str
    revision: int
    digest: str
    body_ref: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PageState:
    subject: str
    revision: int
    digest: str
    body_ref: str


def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]:
    granted = sa.exists(
        sa.select(1).where(
            tables.source_grant.c.workspace_id == workspace_id,
            tables.source_grant.c.source_id == tables.source.c.id,
            tables.source_grant.c.agent_id == reader.agent_id,
        )
    )
    main_for_member = (
        sa.false()
        if reader.requesting_member_id is None
        else sa.and_(
            tables.source.c.owner_member_id == reader.requesting_member_id,
            sa.exists(
                sa.select(1).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.id == reader.agent_id,
                    tables.agent.c.is_main.is_(True),
                )
            ),
        )
    )
    return sa.and_(
        tables.source.c.workspace_id == workspace_id,
        tables.source.c.removed_at.is_(None),
        tables.source.c.subject.in_(reader.subjects),
        sa.or_(granted, main_for_member),
    )


@dataclass(frozen=True, slots=True)
class ConversationFacts:
    """What a member-facing listing must know about a conversation its rows report into: the
    disclosure audience that decides who may see a row, and the surface's own name for where that
    conversation lives. Read live rather than snapshotted onto the rows — an audience never changes,
    but a surface label does when a channel is renamed, and a stale name in an `origin` column is a
    listing telling a member about a place that no longer goes by that name."""

    audience: Audience
    surface_label: str | None


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """How one turn ended, as a status line renders it: the status it holds, and the text of its
    terminal frame once it has committed one. `text` is None for a turn still running, and for one
    that ended without saying anything."""

    status: str
    text: str | None


class SeatedMember(BaseModel):
    id: UUID
    timezone: str = "UTC"


class WorkspaceAgent(BaseModel):
    id: UUID
    name: str
    owner_member_id: UUID | None = None
    tools: tuple[str, ...] | None = None


class SeatedMemberPage(BaseModel):
    members: tuple[SeatedMember, ...]
    next_cursor: UUID | None = None


class ScheduledMemberTurn(BaseModel):
    conversation_id: UUID
    turn_id: UUID | None


class MemberContextRecord(BaseModel):
    kind: str
    ref: str
    title: str
    text: str
    information_date: datetime
    stable_subject_key: str

    @field_validator("information_date")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


MEMBER_CONTEXT_TEXT_BYTES = 10_000


async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str:
    data = bytearray()
    stream = blob.get_stream(key)
    try:
        async for chunk in stream:
            remaining = MEMBER_CONTEXT_TEXT_BYTES + 1 - len(data)
            data.extend(chunk[:remaining])
            if len(data) > MEMBER_CONTEXT_TEXT_BYTES:
                break
    finally:
        if isinstance(stream, AsyncGenerator):
            await stream.aclose()
    bounded = data[:MEMBER_CONTEXT_TEXT_BYTES]
    try:
        return bounded.decode()
    except UnicodeDecodeError as error:
        if len(data) <= MEMBER_CONTEXT_TEXT_BYTES or error.reason != "unexpected end of data":
            raise
        return bounded[: error.start].decode()


@dataclass(frozen=True)
class ExtensionContext:
    store: ScopedStore
    credentials: CredentialAccess
    audience: Audience = SHARED_AUDIENCE
    installations: SurfaceInstallationAccess = field(
        default_factory=lambda: SurfaceInstallationAccess(frozenset())
    )
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    corpus: TrajectoryCorpus | None = None
    files: ConversationFiles | None = None
    probes: ConversationProbes | None = None
    invoker: TurnInvoker | None = None
    model: ModelAccess | None = None
    key_slot_for: Callable[[str], str | None] | None = None
    public_base_url: str | None = None
    home_surface: str | None = None
    tailer: TurnTailer | None = None
    member_context_read_allowed: bool = False
    scheduled_member_id: UUID | None = None
    member_context_blob: WorkspaceBlobStore | None = None
    artifact_token_secret: str = ""

    @property
    def workspace_id(self) -> UUID:
        return self.store.workspace_id

    def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None:
        """The signed link that renders one picture an extension's own row stores, or None when this
        deploy mints no artifact links or the key and size are not an eligible raster.

        The URL opens the same signed preview route a shared file's picture is served through, so a
        kind listing rows a member reads publishes a link the portal draws directly. Signing stays
        core's: the deploy secret and the artifact key namespace both live here, and an extension
        holds neither."""
        return mint_image_preview_url(
            self.artifact_token_secret,
            self.public_base_url,
            blob_key,
            size_bytes,
            workspace_id=self.workspace_id,
        )

    def home_url(self, fragment: str = "") -> str | None:
        """A link into the deploy's browser portal, or None when this deploy has no public base or
        installs no browser surface. A route answering a member's browser with nothing left to do
        renders one, so they land on a surface that can carry the conversation on.

        Core owns both halves, so no extension has to know another's routes: the surface mount path
        is core's own and `home_surface` is the manifest flag naming the one surface a browser
        belongs on. `fragment` is the portal's own hash route, which core does not interpret."""
        if not self.public_base_url or self.home_surface is None:
            return None
        base = self.public_base_url.rstrip("/")
        return f"{base}/surface/{self.home_surface}{fragment}"

    async def seated_members(
        self, *, cursor: UUID | None = None, limit: int = 100
    ) -> SeatedMemberPage:
        """Read one page of seated members for a first-party member job."""
        if not self.member_context_read_allowed:
            raise PermissionError("this extension cannot read seated members")
        if limit < 1 or limit > 100:
            raise ValueError("member page limit must be from 1 through 100")
        query = (
            sa.select(tables.member.c.id, tables.member.c.timezone)
            .where(
                tables.member.c.workspace_id == self.workspace_id,
                tables.member.c.seated_at.is_not(None),
            )
            .order_by(tables.member.c.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            query = query.where(tables.member.c.id > cursor)
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        page = rows[:limit]
        return SeatedMemberPage(
            members=tuple(SeatedMember(id=row.id, timezone=row.timezone or "UTC") for row in page),
            next_cursor=page[-1].id if len(rows) > limit else None,
        )

    async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]:
        """Every agent of the workspace with its owner, oldest first, for a first-party sweep
        job."""
        if not self.member_context_read_allowed:
            raise PermissionError("this extension cannot read the agent roster")
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.owner_member_id,
                        tables.agent.c.tools,
                    )
                    .where(tables.agent.c.workspace_id == self.workspace_id)
                    .order_by(tables.agent.c.created_at, tables.agent.c.id)
                )
            ).all()
        return tuple(
            WorkspaceAgent(
                id=row.id,
                name=row.name,
                owner_member_id=row.owner_member_id,
                tools=None if row.tools is None else tuple(row.tools),
            )
            for row in rows
        )

    async def agent_visibilities(self) -> dict[UUID, AgentVisibility]:
        """Every agent's portal visibility by id — the audience floor a thing attached to an
        agent (its homepage) answers on. Workspace shape, not member data, so it is not gated on
        `member_context_read`."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.visibility).where(
                        tables.agent.c.workspace_id == self.workspace_id
                    )
                )
            ).all()
        return {row.id: row.visibility for row in rows}

    async def earliest_seated_admin(self) -> UUID | None:
        """The workspace's earliest-seated admin — the deterministic member an ownerless agent's
        background work acts on behalf of — or None in a workspace no admin holds a seat in."""
        if not self.member_context_read_allowed:
            raise PermissionError("this extension cannot read seated members")
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == self.workspace_id,
                        tables.member.c.is_admin.is_(True),
                        tables.member.c.seated_at.is_not(None),
                    )
                    .order_by(tables.member.c.seated_at, tables.member.c.id)
                    .limit(1)
                )
            ).one_or_none()
        return None if row is None else row.id

    async def invoke_agent_for_member(
        self,
        *,
        agent_name: str,
        member_id: UUID,
        conversation_key: str,
        message: str,
        idempotency_key: str,
    ) -> ScheduledMemberTurn:
        """Resolve this extension's agent, open its private member conversation, and schedule
        one turn."""
        if not self.member_context_read_allowed:
            raise PermissionError("this extension cannot schedule member turns")
        surface = f"extension:{self.store.extension}"
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == self.workspace_id)
                .with_for_update()
            )
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == self.workspace_id,
                        tables.member.c.id == member_id,
                        tables.member.c.seated_at.is_not(None),
                    )
                )
            ).one_or_none()
            if member is None:
                raise PermissionError("scheduled member does not hold a seat")
            agent_row = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.provisioned_by == self.store.extension,
                        tables.agent.c.provisioned_name == agent_name,
                    )
                )
            ).one_or_none()
            if agent_row is None:
                raise ValueError(
                    f"extension agent {self.store.extension!r}/{agent_name!r} does not exist"
                )
            conversation = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.id,
                        tables.conversation.c.agent_id,
                        tables.conversation.c.member_id,
                        tables.conversation.c.audience,
                    ).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.surface == surface,
                        tables.conversation.c.queue_key == conversation_key,
                    )
                )
            ).one_or_none()
            if conversation is not None and (
                conversation.agent_id != agent_row.id
                or conversation.member_id != member_id
                or conversation.audience != str(conversation_audience(member_id))
            ):
                raise PermissionError("scheduled conversation belongs to another principal")
            if conversation is None:
                conversation_id = uuid4()
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
                        agent_id=agent_row.id,
                        surface=surface,
                        queue_key=conversation_key,
                        title="Daily brief",
                        member_id=member_id,
                        audience=str(conversation_audience(member_id)),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                conversation_id = conversation.id
        turn_id = await self.invoke(
            conversation_id,
            agent_row.id,
            message,
            idempotency_key,
            on_behalf_of_member_id=member_id,
            as_scheduled=True,
        )
        return ScheduledMemberTurn(conversation_id=conversation_id, turn_id=turn_id)

    async def member_context(
        self, *, since: datetime, limit: int = 200
    ) -> tuple[MemberContextRecord, ...]:
        """Read bounded cross-agent context visible to the scheduled member."""
        if not self.member_context_read_allowed or self.scheduled_member_id is None:
            raise PermissionError("member context is not bound to a scheduled member")
        if limit < 1 or limit > 200:
            raise ValueError("member context limit must be from 1 through 200")
        member_id = self.scheduled_member_id
        if self.member_context_blob is None:
            raise RuntimeError("member context requires workspace blob storage")
        audiences = tuple(str(value) for value in readable_audiences(member_id))
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.inbound,
                        tables.turn.c.terminal,
                        tables.turn.c.updated_at,
                        tables.conversation.c.id.label("conversation_id"),
                        tables.conversation.c.title,
                    )
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.updated_at >= since,
                        tables.conversation.c.audience.in_(audiences),
                        tables.conversation.c.surface != f"extension:{self.store.extension}",
                        tables.conversation.c.surface != SUBAGENT_SURFACE,
                    )
                    .order_by(tables.turn.c.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        records = [
            MemberContextRecord(
                kind="conversation",
                ref=f"conversation/{row.conversation_id}",
                title=row.title or "Conversation",
                text=(
                    row.inbound
                    + (
                        "\n" + str(row.terminal.get("text", ""))
                        if isinstance(row.terminal, dict) and row.terminal.get("text")
                        else ""
                    )
                )[:MEMBER_CONTEXT_TEXT_BYTES],
                information_date=row.updated_at,
                stable_subject_key=f"turn:{row.id}",
            )
            for row in rows
        ]
        async with workspace_tx() as connection:
            artifact_rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.id,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.updated_at,
                        tables.conversation.c.id.label("conversation_id"),
                    )
                    .select_from(tables.shared_artifact.join(tables.turn).join(tables.conversation))
                    .where(
                        tables.shared_artifact.c.workspace_id == self.workspace_id,
                        tables.shared_artifact.c.updated_at >= since,
                        tables.conversation.c.audience.in_(audiences),
                        tables.conversation.c.surface != f"extension:{self.store.extension}",
                        tables.conversation.c.surface != SUBAGENT_SURFACE,
                    )
                    .order_by(tables.shared_artifact.c.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        for row in artifact_rows:
            text = f"{row.subject or 'Shared file'} ({row.media_type})."
            if row.media_type.startswith("text/") or row.media_type == "application/json":
                try:
                    text = await _member_blob_text(self.member_context_blob, row.blob_key)
                except (BlobNotFound, UnicodeDecodeError):
                    pass
            records.append(
                MemberContextRecord(
                    kind="artifact",
                    ref=f"conversation/{row.conversation_id}",
                    title=row.filename,
                    text=text,
                    information_date=row.updated_at,
                    stable_subject_key=f"artifact:{row.id}:{row.updated_at.isoformat()}",
                )
            )
        async with workspace_tx() as connection:
            pages = (
                await connection.execute(
                    sa.select(
                        tables.page.c.id,
                        tables.page.c.stream,
                        tables.page.c.title,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.updated_at,
                    )
                    .select_from(
                        tables.page.join(
                            tables.source, tables.page.c.source_id == tables.source.c.id
                        )
                    )
                    .where(
                        tables.page.c.workspace_id == self.workspace_id,
                        tables.page.c.updated_at >= since,
                        tables.page.c.subject.in_(("shared", f"member:{member_id}")),
                        tables.page.c.tombstone.is_(False),
                        tables.source.c.removed_at.is_(None),
                        sa.exists(
                            sa.select(1).where(
                                tables.source_grant.c.workspace_id == self.workspace_id,
                                tables.source_grant.c.source_id == tables.source.c.id,
                            )
                        ),
                    )
                    .order_by(tables.page.c.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        for page in pages:
            try:
                body = await _member_blob_text(self.member_context_blob, page.body_ref)
            except (BlobNotFound, UnicodeDecodeError):
                continue
            records.append(
                MemberContextRecord(
                    kind="page",
                    ref=f"page/{page.id}",
                    title=page.title,
                    text=body,
                    information_date=page.updated_at,
                    stable_subject_key=f"page:{page.id}:{page.digest}",
                )
            )
        records.extend(await self._member_extension_records(member_id, audiences, since, limit))
        return tuple(sorted(records, key=lambda item: item.information_date, reverse=True)[:limit])

    async def _member_extension_records(
        self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int
    ) -> tuple[MemberContextRecord, ...]:
        memory_item = sa.table(
            "memory_item",
            sa.column("id", sa.Uuid),
            sa.column("workspace_id", sa.Uuid),
            sa.column("subject", sa.Text),
            sa.column("body", sa.Text),
            sa.column("memory_kind", sa.Text),
            sa.column("as_of", sa.DateTime(timezone=True)),
            sa.column("superseded_by", sa.Uuid),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        objective = sa.table(
            "objective",
            sa.column("id", sa.Uuid),
            sa.column("workspace_id", sa.Uuid),
            sa.column("conversation_id", sa.Uuid),
            sa.column("name", sa.Text),
            sa.column("directive", sa.Text),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        objective_step = sa.table(
            "objective_step",
            sa.column("id", sa.Uuid),
            sa.column("objective_id", sa.Uuid),
            sa.column("accepts", sa.JSON),
        )
        objective_event = sa.table(
            "objective_event",
            sa.column("step_id", sa.Uuid),
            sa.column("kind", sa.Text),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        objective_check = sa.table(
            "objective_check",
            sa.column("step_id", sa.Uuid),
            sa.column("verdicts", sa.JSON),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        async with workspace_tx() as connection:
            memories = (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.memory_kind,
                        memory_item.c.as_of,
                        memory_item.c.updated_at,
                    ).where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.subject.in_(("shared", f"member:{member_id}")),
                        memory_item.c.superseded_by.is_(None),
                        sa.or_(
                            memory_item.c.updated_at >= since,
                            memory_item.c.memory_kind == "task",
                        ),
                    )
                )
            ).all()
            objectives = (
                await connection.execute(
                    sa.select(
                        objective.c.id,
                        objective.c.name,
                        objective.c.directive,
                        objective.c.updated_at,
                    )
                    .select_from(
                        objective.join(
                            tables.conversation,
                            objective.c.conversation_id == tables.conversation.c.id,
                        )
                    )
                    .where(
                        objective.c.workspace_id == self.workspace_id,
                        tables.conversation.c.audience.in_(audiences),
                        tables.conversation.c.surface != f"extension:{self.store.extension}",
                        tables.conversation.c.surface != SUBAGENT_SURFACE,
                    )
                    .order_by(objective.c.updated_at.desc())
                    .limit(limit)
                )
            ).all()
            objective_ids = tuple(row.id for row in objectives)
            steps = (
                (
                    await connection.execute(
                        sa.select(
                            objective_step.c.id,
                            objective_step.c.objective_id,
                            objective_step.c.accepts,
                        ).where(objective_step.c.objective_id.in_(objective_ids))
                    )
                ).all()
                if objective_ids
                else ()
            )
            step_ids = tuple(row.id for row in steps)
            ranked_events = (
                sa.select(
                    objective_event.c.step_id,
                    objective_event.c.kind,
                    objective_event.c.created_at,
                    sa.func.count()
                    .filter(objective_event.c.kind == "did")
                    .over(partition_by=objective_event.c.step_id)
                    .label("did_count"),
                    sa.func.row_number()
                    .over(
                        partition_by=objective_event.c.step_id,
                        order_by=objective_event.c.created_at.desc(),
                    )
                    .label("rank"),
                ).where(objective_event.c.step_id.in_(step_ids))
            ).subquery()
            latest_events = (
                (
                    await connection.execute(
                        sa.select(ranked_events).where(ranked_events.c.rank == 1)
                    )
                ).all()
                if step_ids
                else ()
            )
            ranked_checks = (
                sa.select(
                    objective_check.c.step_id,
                    objective_check.c.verdicts,
                    objective_check.c.created_at,
                    sa.func.row_number()
                    .over(
                        partition_by=objective_check.c.step_id,
                        order_by=objective_check.c.created_at.desc(),
                    )
                    .label("rank"),
                ).where(objective_check.c.step_id.in_(step_ids))
            ).subquery()
            latest_checks = (
                (
                    await connection.execute(
                        sa.select(ranked_checks).where(ranked_checks.c.rank == 1)
                    )
                ).all()
                if step_ids
                else ()
            )
        steps_by_objective: dict[UUID, list[sa.Row[tuple[object, ...]]]] = {}
        for step in steps:
            steps_by_objective.setdefault(step.objective_id, []).append(step)
        events_by_step = {row.step_id: row for row in latest_events}
        checks_by_step = {row.step_id: row for row in latest_checks}
        objective_records: list[MemberContextRecord] = []
        for row in objectives:
            objective_steps = steps_by_objective.get(row.id, [])
            open_objective = not objective_steps
            information_date = (
                row.updated_at
                if row.updated_at.tzinfo is not None
                else row.updated_at.replace(tzinfo=UTC)
            )
            for step in objective_steps:
                latest_event = events_by_step.get(step.id)
                latest_check = checks_by_step.get(step.id)
                if latest_event is not None:
                    event_date = latest_event.created_at
                    information_date = max(
                        information_date,
                        (
                            event_date
                            if event_date.tzinfo is not None
                            else event_date.replace(tzinfo=UTC)
                        ),
                    )
                if latest_check is not None:
                    check_date = latest_check.created_at
                    information_date = max(
                        information_date,
                        (
                            check_date
                            if check_date.tzinfo is not None
                            else check_date.replace(tzinfo=UTC)
                        ),
                    )
                accepts = step.accepts if isinstance(step.accepts, list) else []
                verdicts = (
                    latest_check.verdicts
                    if latest_check is not None and isinstance(latest_check.verdicts, list)
                    else []
                )
                done = (
                    latest_event is not None
                    and latest_event.kind != "blocked"
                    and latest_event.did_count > 0
                    and (
                        not accepts
                        or (
                            len(verdicts) == len(accepts)
                            and all(
                                isinstance(verdict, dict) and verdict.get("holds") is True
                                for verdict in verdicts
                            )
                        )
                    )
                )
                open_objective = open_objective or not done
            if not open_objective:
                continue
            objective_digest = hashlib.sha256(
                f"{row.directive}\0{information_date.isoformat()}".encode()
            ).hexdigest()
            objective_records.append(
                MemberContextRecord(
                    kind="objective",
                    ref=f"objective/{row.id}",
                    title=row.name,
                    text=row.directive,
                    information_date=information_date,
                    stable_subject_key=f"objective:{row.id}:{objective_digest}",
                )
            )
        return (
            *(
                MemberContextRecord(
                    kind="task" if row.memory_kind == "task" else "memory",
                    ref=f"memory/{row.id}",
                    title="Task memory" if row.memory_kind == "task" else "Memory",
                    text=row.body,
                    information_date=row.as_of or row.updated_at,
                    stable_subject_key=(
                        f"memory:{row.id}:{hashlib.sha256(row.body.encode()).hexdigest()}"
                    ),
                )
                for row in memories
            ),
            *objective_records,
        )

    async def retitle_conversation(self, conversation_id: UUID, title: str) -> None:
        """Name a conversation of this workspace — what a job that reads a conversation and writes
        a better name for it than its opening words calls the result."""
        await retitle_conversation(self.workspace_id, conversation_id, title)

    async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]:
        """This workspace's conversations a member spoke in and an agent answered that no summary
        has named yet, newest first and at most `limit` of them — the work the titling job takes per
        tick, whatever surface holds them. Newest first because a conversation opened a minute ago
        is the one a member is looking at, and a backlog is drained behind it.

        The bound is the job's spend per tick, not a page: the handler records a summary against
        every conversation it takes, so each tick reads the next `limit` and a backlog drains rather
        than a batch of conversations nothing can name standing in front of it forever."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.conversation.c.id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        awaiting_a_title(),
                    )
                    .order_by(
                        tables.conversation.c.created_at.desc(),
                        tables.conversation.c.id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        return tuple(row.id for row in rows)

    async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None:
        """Name a conversation of this workspace what a summary of its opening exchange calls it,
        and record that the summary has run — which is what takes it out of
        `conversations_awaiting_title`. A summary the model wrote nothing usable for still records
        the attempt, so one conversation costs one summary."""
        await summarize_conversation_title(self.workspace_id, conversation_id, title)

    async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]:
        """This extension's settled, unacknowledged usage deltas, at most `limit`, minting new
        intents first — the billing-export read seam. Consumer-keyed by the extension's stable
        name, so two exporters never touch each other's marks; usage settling before `floor`
        never exports (the extension's backfill bound). Core owns the mint because settlement and
        delta-freezing are writer knowledge no extension can express through the SDK without
        re-declaring the ledger's private schema."""
        if self.key_slot_for is None:
            raise RuntimeError("model key-slot resolver is required for usage exports")
        async with workspace_tx() as connection:
            await mint_usage_exports(
                connection,
                self.store.workspace_id,
                self.store.extension,
                floor,
                self.key_slot_for,
            )
            return await read_pending_usage_exports(
                connection, self.store.workspace_id, self.store.extension, limit
            )

    async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None:
        """Acknowledge delivered exports so they leave the pending read — called only after the
        external receiver accepted them; anything unacknowledged re-reads frozen and re-delivers
        under the same dedup key."""
        if not exports:
            return
        async with workspace_tx() as connection:
            await ack_usage_exports(
                connection, self.store.workspace_id, self.store.extension, exports
            )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A transaction for the extension's own tables — those a migration the extension ships
        created — and for SDK-exported core rules that take the caller's connection
        (`ufo.sdk.seats`' `Seats`), so an extension applies core-owned state changes inside its
        own workspace scope. The handler builds queries against the SQLAlchemy tables it declares
        and scopes rows by `self.store.workspace_id`. This yields a RAW whole-database
        connection: it is not restricted to the extension's schema and enforces no workspace
        scoping — reaching only its
        own tables, scoped to its workspace, is the extension's responsibility, not a guarantee of
        this handle (the SDK import boundary is a static gate over imports, not over runtime SQL).
        Commits on exit, rolls back on error — the same one transaction the ScopedStore rides."""
        async with workspace_tx() as connection:
            yield connection

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        on_behalf_of_member_id: UUID | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
    ) -> UUID | None:
        """Kick an internal turn in `conversation_id`, asserting the conversation is bound to
        `agent_id` — admission refuses a mismatch, so a stored binding can never fire into another
        agent's conversation. `on_behalf_of_member_id` is the member the woken turn acts on behalf
        of; `holds_work_already_done` parks the turn on a spend breach instead of cancelling it,
        for work already performed and metered; `as_scheduled` stamps the turn as a scheduled
        fire — its own turn, never folded, seat-gated on the on-behalf member;
        `unless_member_since` and `unless_member_arrival_since` — a pair, refused half-set —
        refuse the admission with None when a member turn past the turn watermark exists or a
        member arrival past the arrival watermark does (each watermark compares only its own
        counter space) — None is reachable only for a caller that passed them. Fails loud when
        no invoker is wired rather than silently dropping the invocation."""
        if self.invoker is None:
            raise RuntimeError("invoke requires a turn invoker; none is wired")
        return await self.invoker.invoke(
            conversation_id,
            agent_id,
            message,
            idempotency_key,
            on_behalf_of_member_id=on_behalf_of_member_id,
            holds_work_already_done=holds_work_already_done,
            as_scheduled=as_scheduled,
            unless_member_since=unless_member_since,
            unless_member_arrival_since=unless_member_arrival_since,
        )

    def tail(
        self, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]:
        """Tail a turn's live frames off the hub until it ends — how a handler watches the turn it
        fires under from side-channel work of its own, reaching the hub only through the injected
        tailer. Leaving the scope ends the subscription and the tasks behind it, however the block
        ends. Fails loud when no tailer is wired rather than yielding a stream that never opens."""
        if self.tailer is None:
            raise RuntimeError("tail requires a turn tailer; none is wired")
        return self.tailer.tail(turn_id, since)

    async def turn_is_terminal(self, turn_id: UUID) -> bool:
        """Whether a turn has committed its terminal state, read from the row rather than the hub —
        what side-channel work asks before speaking for the turn it follows, since a tail learns the
        end by polling and the reply may already be delivered. A missing turn reads as terminal:
        there is nothing left to report on."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return row is None or row.status in TERMINAL_TURN_STATUSES

    async def conversation_agent(self, conversation_id: UUID) -> UUID | None:
        """The agent this workspace's conversation is permanently bound to, or None when the id
        names no conversation here — how a handler resolves an opaque conversation id to the agent
        wall a member-facing link addresses."""
        return await conversation_agent_id(self.workspace_id, conversation_id)

    async def conversation_facts(
        self, conversation_ids: tuple[UUID, ...]
    ) -> dict[UUID, ConversationFacts]:
        """The audience and surface label of each named conversation in this workspace — the pair a
        member-facing listing answers visibility and origin from. One query for a whole page, so a
        listing never pays a read per row, and workspace-scoped in the predicate, so a borrowed id
        from another tenant resolves to nothing rather than leaking where it reports.

        An id naming no conversation here is absent from the mapping rather than defaulted, and a
        caller deciding visibility must read that absence as "not visible": the fact that decides
        disclosure is missing, and standing in a shared audience for it would publish a row nothing
        vouched for. That absence is tenant isolation, not robustness — a row whose conversation was
        deleted is not reachable, since the rows that name one hold a foreign key to it, so the
        absence a caller actually meets is an id belonging to another workspace, satisfying its
        foreign key perfectly while resolving to nothing here."""
        if not conversation_ids:
            return {}
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.id,
                        tables.conversation.c.audience,
                        tables.conversation.c.surface_label,
                    ).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id.in_(conversation_ids),
                    )
                )
            ).all()
        return {
            row.id: ConversationFacts(
                audience=parse_audience(row.audience), surface_label=row.surface_label
            )
            for row in rows
        }

    async def conversation_arrival_seq(self, conversation_id: UUID) -> int:
        """How far this conversation's member arrivals have got: the highest `seq` a member-sourced
        inbound message holds, or 0 when no member has spoken into it yet.

        A watermark, not a count. Work that arms itself to be woken later records this at arm time,
        so whoever decides whether to wake it can tell a member message that landed *before* the arm
        — already accounted for by the turn that armed it — from one that landed after, which is the
        thing the arm was waiting on. Comparing counts could not separate those two, because the
        question is ordering rather than volume.

        Only member arrivals count: an internal arrival is work the system posted to itself, and a
        watcher woken by its own effects is the loop this watermark exists to prevent. Zero is
        therefore a real answer meaning "no member has spoken", and any later member arrival exceeds
        it."""
        async with workspace_tx() as connection:
            return int(
                (
                    await connection.execute(
                        sa.select(
                            sa.func.coalesce(sa.func.max(tables.inbound_message.c.seq), 0)
                        ).where(
                            tables.inbound_message.c.workspace_id == self.workspace_id,
                            tables.inbound_message.c.conversation_id == conversation_id,
                            tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                        )
                    )
                ).scalar_one()
            )

    async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]:
        """How each named turn in this workspace ended — the read a status line renders the last
        run's outcome from. Batched and workspace-scoped for the same two reasons
        `conversation_facts` is.

        A turn id naming no turn here is absent from the mapping rather than an error: a row may
        point at a turn since compacted away, and a status line reads that absence exactly as it
        reads a row that has never fired."""
        if not turn_ids:
            return {}
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.status,
                        tables.turn.c.terminal,
                    ).where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.id.in_(turn_ids),
                    )
                )
            ).all()
        return {
            row.id: TurnOutcome(status=row.status, text=(row.terminal or {}).get("text"))
            for row in rows
        }

    async def is_operator_workspace(self) -> bool:
        """Whether the bound workspace is the fleet operator's own, by the same domain read the
        surface seam gates on — so a rendering meant for the operator alone (a turn's spend, a
        debugger link) is withheld in a customer's workspace wherever it is built. An unidentified
        workspace is never the operator's."""
        async with workspace_tx() as connection:
            return await workspace_domain(connection, self.workspace_id) == OPERATOR_EMAIL_DOMAIN

    async def open_conversation(
        self, agent_id: UUID, key: str, member_id: UUID | None = None
    ) -> UUID:
        """Get-or-create the conversation this extension keys by `key`, held by `agent_id` — the
        conversation a trigger opens rather than a member does. The work an event starts is one
        conversation of the agent that does that work: it lists under that agent, reaches no
        member's rail (which reads the conversations a member opened, on whatever surface), holds
        its own queue partition, and takes its own sandbox, so two of them neither serialize
        against each other nor share a checkout tree.

        The key is the workflow subject — a pull request, a scheduled task, a delivery — so later
        events for that subject keep one history and a replay reopens the same conversation. The
        extension's name is the surface, so one extension's keys can never collide with another's.
        Without `member_id` the audience is the workspace's, since no member delegated it. With
        `member_id` the room is that member's own — the shape every on-behalf invocation runs in,
        so the authority the turn carries stays inside the one room its member already reads; the
        caller keys such rooms by the member as well as the subject, so a change of acting member
        opens a fresh room rather than rebinding another member's. `invoke` admits the turns; this
        only opens the room they run in, and an agent of another workspace fails loud rather than
        binding a conversation nothing can reach."""
        workspace_id = self.store.workspace_id
        async with workspace_tx() as connection:
            known = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.id == agent_id,
                    )
                )
            ).one_or_none()
            if known is None:
                raise ValueError(f"agent {agent_id} is not an agent of this workspace")
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.conversation)
                .values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface=self.store.extension,
                    queue_key=key,
                    member_id=member_id,
                    audience=str(
                        SHARED_AUDIENCE if member_id is None else conversation_audience(member_id)
                    ),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.conversation.c.workspace_id,
                        tables.conversation.c.surface,
                        tables.conversation.c.queue_key,
                    ]
                )
            )
            return (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id,
                        tables.conversation.c.surface == self.store.extension,
                        tables.conversation.c.queue_key == key,
                    )
                )
            ).scalar_one()

    async def agent_name(self) -> str:
        """The bound agent's stable name — the `agent` object kind's own object name, so an
        agent-scoped kind can link to the agent it belongs to. Reads the agent the caller bound,
        which is the turn's agent inside a turn and the agent a portal read names outside one."""
        scope = agent_current()
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(
                        tables.agent.c.id == scope.agent_id,
                        tables.agent.c.workspace_id == scope.workspace_id,
                    )
                )
            ).scalar_one()

    async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]:
        """Current subject and revision for this workspace's live named pages."""
        if not page_ids:
            return {}
        query = sa.select(
            tables.page.c.id,
            tables.page.c.subject,
            tables.page.c.revision,
            tables.page.c.digest,
            tables.page.c.body_ref,
        ).where(
            tables.page.c.workspace_id == self.store.workspace_id,
            tables.page.c.id.in_(page_ids),
            tables.page.c.tombstone.is_(False),
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return {
            row.id: PageState(
                subject=row.subject,
                revision=row.revision,
                digest=row.digest,
                body_ref=row.body_ref,
            )
            for row in rows
        }

    async def readable_page_states(
        self, page_ids: tuple[UUID, ...], reader: SourceReader
    ) -> dict[UUID, PageState]:
        if not page_ids:
            return {}
        query = (
            sa.select(
                tables.page.c.id,
                tables.page.c.subject,
                tables.page.c.revision,
                tables.page.c.digest,
                tables.page.c.body_ref,
            )
            .select_from(
                tables.page.join(tables.source, tables.page.c.source_id == tables.source.c.id)
            )
            .where(
                tables.page.c.workspace_id == self.store.workspace_id,
                tables.page.c.id.in_(page_ids),
                tables.page.c.tombstone.is_(False),
                tables.page.c.subject.in_(reader.subjects),
                _source_readable(self.store.workspace_id, reader),
            )
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return {
            row.id: PageState(
                subject=row.subject,
                revision=row.revision,
                digest=row.digest,
                body_ref=row.body_ref,
            )
            for row in rows
        }

    async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]:
        query = sa.select(tables.source.c.id).where(
            _source_readable(self.store.workspace_id, reader),
        )
        async with workspace_tx() as connection:
            return frozenset((await connection.execute(query)).scalars())

    async def register_source(
        self,
        backend: str,
        config: BaseModel,
        *,
        subject: str,
        owner_member_id: UUID | None,
        connection_id: UUID | None = None,
        agent_id: UUID | None = None,
    ) -> UUID:
        """Register a content-sync source for this workspace under `backend` — a `SourceBackend` an
        extension declared through its Manifest `sources` point — with `config` the backend's typed
        per-source parameters (the connected account, a folder root), `subject` the disclosure every
        page it syncs is stamped with, `owner_member_id` the registering member, and `connection_id`
        the exact member-owned connection generation behind a broker source (None for direct or
        extension-owned feeds). Brokered row identity includes that connection generation; direct
        row identity is (workspace, backend, config) minus the config model's
        `SourceRowConfig.non_identity_fields` (a backfill window). Re-registering the same authority
        settles on one row and leaves its stored config alone, while changing its owner, disclosure,
        or `requested_fields()` fails loud under the same `for update` lock — so a caller is never
        told a window was applied that the row does not hold. What is compared is the request, not
        the instant it resolved to, since each caller resolves the same day count against its own
        `now`; the loser settles on the winner's row and reads the winner's pin back.

        That keeps one binding's streams on one window while the racing callers submit the same
        stream set — both register in sorted order, so the loser is refused before creating any
        other. Differing stream sets can still split a binding, as they already could: each
        `register_source` is its own transaction.

        Reviving a removed row instead takes the re-registering config, its identity keys being the
        id's own inputs. `agent_id` — the main agent when unnamed — is granted the source, so a feed
        a member adds for a second agent grants that agent while still syncing once under one row.
        The core sync driver polls the row and lands its pages in memory; embedding stays a job."""
        payload = config.model_dump(mode="json")
        source_id = self.source_id(backend, config, connection_id=connection_id)
        registered_at = datetime.now(UTC)
        async with workspace_tx() as connection:
            target_agent_id = agent_id
            if target_agent_id is None:
                target_agent_id = (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == self.store.workspace_id,
                            tables.agent.c.is_main.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if target_agent_id is None:
                    raise RuntimeError("registering a source requires a main agent")
            target = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.store.workspace_id,
                        tables.agent.c.id == target_agent_id,
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                raise ValueError("the source target agent is outside this workspace")
            if connection_id is not None:
                account = payload.get("account")
                if owner_member_id is None or not isinstance(account, str):
                    raise ValueError(
                        "a connection-bound source requires its member owner and account"
                    )
                authorized = (
                    await connection.execute(
                        sa.select(tables.connection.c.id)
                        .where(
                            tables.connection.c.id == connection_id,
                            tables.connection.c.workspace_id == self.store.workspace_id,
                            tables.connection.c.owner_member_id == owner_member_id,
                            tables.connection.c.provider == backend,
                            tables.connection.c.account_id == account,
                        )
                        .with_for_update(read=True)
                    )
                ).scalar_one_or_none()
                if authorized is None:
                    raise ValueError(
                        "the source connection is not active for its workspace, provider, "
                        "account, and member owner"
                    )
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.source)
                .values(
                    id=source_id,
                    workspace_id=self.store.workspace_id,
                    backend=backend,
                    config=payload,
                    subject=subject,
                    owner_member_id=owner_member_id,
                    connection_id=connection_id,
                    cursor=None,
                    next_sync_at=registered_at,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=registered_at,
                    updated_at=registered_at,
                )
                .on_conflict_do_nothing(index_elements=[tables.source.c.id])
            )
            present = (
                await connection.execute(
                    sa.select(
                        tables.source.c.id,
                        tables.source.c.removed_at,
                        tables.source.c.config,
                        tables.source.c.subject,
                        tables.source.c.owner_member_id,
                        tables.source.c.connection_id,
                    )
                    .where(tables.source.c.id == source_id)
                    .with_for_update()
                )
            ).one()
            revived = present.removed_at is not None
            if not revived:
                if (
                    present.subject,
                    present.owner_member_id,
                    present.connection_id,
                ) != (
                    subject,
                    owner_member_id,
                    connection_id,
                ):
                    raise ValueError(
                        "a source with this configuration is already registered under a different "
                        "owner, connection, or disclosure; delete it before changing its authority"
                    )
                requested = (
                    type(config).requested_fields()
                    if isinstance(config, SourceRowConfig)
                    else frozenset[str]()
                )
                differing = sorted(
                    field for field in requested if present.config.get(field) != payload.get(field)
                )
                if differing:
                    raise ValueError(
                        "a source with this configuration is already registered asking for a "
                        f"different {', '.join(differing)}; delete it before changing what it "
                        "reaches"
                    )
            else:
                await connection.execute(
                    sa.update(tables.source)
                    .values(
                        removed_at=None,
                        config=payload,
                        subject=subject,
                        owner_member_id=owner_member_id,
                        connection_id=connection_id,
                        cursor=None,
                        next_sync_at=registered_at,
                        consecutive_errors=0,
                        claimed_by=None,
                        claim_expires_at=None,
                        created_at=registered_at,
                        updated_at=registered_at,
                    )
                    .where(tables.source.c.id == source_id)
                )
            await connection.execute(
                insert(tables.source_grant)
                .values(
                    workspace_id=self.store.workspace_id,
                    source_id=source_id,
                    agent_id=target_agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.source_grant.c.workspace_id,
                        tables.source_grant.c.source_id,
                        tables.source_grant.c.agent_id,
                    ]
                )
            )
        return source_id

    async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None:
        """Grant an agent a source this workspace already holds, so a second agent reads a feed
        without a second row syncing the same account twice.

        `register_source` grants as it registers, and it is the only path that did. A source the
        workspace already holds is settled there — same authority, same window — so registering it
        again grants nothing new and the asking agent is left with no feed and no error.

        The actor must own the source or the source must be workspace-shared, the rule
        `GrantStore.attach` holds for connections. What a grant decides is which agent may reach a
        source; whether its pages may be read at all stays with the subject each page carries, so
        this widens no disclosure on its own — the refusal is about the authority behind the feed,
        which belongs to the member whose connection serves it."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.source.c.owner_member_id, tables.source.c.subject)
                    .where(
                        tables.source.c.workspace_id == self.store.workspace_id,
                        tables.source.c.id == source_id,
                        tables.source.c.removed_at.is_(None),
                    )
                    .with_for_update(read=True)
                )
            ).one_or_none()
            if row is None:
                raise ValueError("no such source in this workspace")
            if row.owner_member_id != actor_member_id and row.subject != SHARED_SUBJECT:
                raise ValueError("member cannot grant a source another member holds privately")
            target = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == self.store.workspace_id,
                        tables.agent.c.id == agent_id,
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                raise ValueError("the source target agent is outside this workspace")
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.source_grant)
                .values(
                    workspace_id=self.store.workspace_id,
                    source_id=source_id,
                    agent_id=agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.source_grant.c.workspace_id,
                        tables.source_grant.c.source_id,
                        tables.source_grant.c.agent_id,
                    ]
                )
            )

    def source_id(
        self, backend: str, config: BaseModel, *, connection_id: UUID | None = None
    ) -> UUID:
        """The row `register_source` settles this authority on. It is derived, never read, so a
        caller may name a row that does not exist and be naming the exact row registering would
        create. Paired with `removed_source_ids` it is how a caller that registers on its own
        initiative tells a feed nobody has yet from one the member deleted — which `register_source`
        would otherwise revive."""
        return source_row_id(
            self.store.workspace_id,
            backend,
            config.model_dump(mode="json"),
            connection_id=connection_id,
            non_identity_keys=(
                type(config).non_identity_fields
                if isinstance(config, SourceRowConfig)
                else frozenset[str]()
            ),
        )

    async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        """Which of these sources this workspace has removed. Deleting a source stamps
        `removed_at` rather than dropping the row, so nothing an extension keyed on a source ever
        hears about it: the row it holds outlives the feed it names, invisible to every listing and
        armed if that source is registered again.

        The answer is positive evidence — a row that exists and is removed — never an id this read
        failed to return. An extension asking "is this gone?" cannot be told yes by a query that
        narrowed, went stale, or lost a row, so a caller deleting what this names deletes too
        little when something is wrong rather than everything. Absence is not removal here, and a
        caller that wants live sources reads `sources`."""
        if not source_ids:
            return frozenset()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.source.c.id).where(
                        tables.source.c.workspace_id == self.store.workspace_id,
                        tables.source.c.id.in_(source_ids),
                        tables.source.c.removed_at.is_not(None),
                    )
                )
            ).scalars()
        return frozenset(rows)

    async def sources(self, backend: str | None = None) -> tuple[SourceRecord, ...]:
        """This workspace's live registered sources, optionally narrowed to one backend — the read
        half of `register_source`, scoped exactly as it is. Removed sources never appear."""
        query = (
            sa.select(
                tables.source.c.id,
                tables.source.c.backend,
                tables.source.c.config,
                tables.source.c.subject,
                tables.source.c.owner_member_id,
                tables.source.c.connection_id,
                tables.source.c.next_sync_at,
                tables.source.c.consecutive_errors,
                tables.source.c.created_at,
                tables.source.c.updated_at,
            )
            .where(
                tables.source.c.workspace_id == self.store.workspace_id,
                tables.source.c.removed_at.is_(None),
            )
            .order_by(tables.source.c.backend, tables.source.c.id)
        )
        if backend is not None:
            query = query.where(tables.source.c.backend == backend)
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return tuple(
            SourceRecord(
                id=row["id"],
                backend=row["backend"],
                config=row["config"],
                subject=row["subject"],
                owner_member_id=row["owner_member_id"],
                connection_id=row["connection_id"],
                next_sync_at=row["next_sync_at"],
                consecutive_errors=row["consecutive_errors"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]:
        """This agent's readable live synced pages, scoped by workspace, audience, and source
        authority."""
        query = (
            sa.select(
                tables.page.c.id,
                tables.page.c.source_id,
                tables.page.c.stream,
                tables.page.c.title,
                tables.page.c.record_created_at,
                tables.page.c.record_updated_at,
                tables.page.c.subject,
                tables.page.c.revision,
                tables.page.c.digest,
                tables.page.c.body_ref,
                tables.page.c.created_at,
                tables.page.c.updated_at,
            )
            .where(
                tables.page.c.workspace_id == self.store.workspace_id,
                tables.page.c.tombstone.is_(False),
            )
            .order_by(tables.page.c.id)
        )
        query = query.select_from(
            tables.page.join(tables.source, tables.page.c.source_id == tables.source.c.id)
        ).where(
            tables.page.c.subject.in_(reader.subjects),
            _source_readable(self.store.workspace_id, reader),
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return tuple(
            PageRecord(
                id=row["id"],
                source_id=row["source_id"],
                stream=row["stream"],
                title=row["title"],
                record_created_at=row["record_created_at"],
                record_updated_at=row["record_updated_at"],
                subject=row["subject"],
                revision=row["revision"],
                digest=row["digest"],
                body_ref=row["body_ref"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def forget_page(self, page_id: UUID) -> None:
        """Tombstone one live page so the page-change pipeline reaps its derived index state,
        exactly as removing its source does — the read-and-forget half of `source_pages`. Fails
        loud on an unknown or already-tombstoned page in this workspace."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            forgotten = await connection.execute(
                sa.update(tables.page)
                .values(tombstone=True, updated_at=now)
                .where(
                    tables.page.c.id == page_id,
                    tables.page.c.workspace_id == self.store.workspace_id,
                    tables.page.c.tombstone.is_(False),
                )
            )
        if forgotten.rowcount == 0:
            raise ValueError(f"no live page {page_id} in this workspace")

    async def remove_source(self, source_id: UUID) -> None:
        """Remove one registered source: mark the row removed so the sync driver never claims it
        again, and tombstone its live pages in the same transaction — the existing page-change
        delivery then clears derived index state, exactly as a snapshot shrink does. The row
        persists as the pages' referent (they carry its foreign key); re-registering the identical
        config revives it fresh. Fails loud on an unknown or already-removed id."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            removed = await connection.execute(
                sa.update(tables.source)
                .values(
                    removed_at=now,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.id == source_id,
                    tables.source.c.workspace_id == self.store.workspace_id,
                    tables.source.c.removed_at.is_(None),
                )
            )
            if removed.rowcount == 0:
                raise ValueError(f"no live source {source_id} in this workspace")
            await connection.execute(
                sa.delete(tables.source_grant).where(
                    tables.source_grant.c.workspace_id == self.store.workspace_id,
                    tables.source_grant.c.source_id == source_id,
                )
            )
            await connection.execute(
                sa.update(tables.page)
                .values(tombstone=True, updated_at=now)
                .where(
                    tables.page.c.source_id == source_id,
                    tables.page.c.tombstone.is_(False),
                )
            )

    async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None:
        """Flip live sources' disclosure and restamp their live pages in one transaction, each page
        with a fresh microsecond `updated_at` so the page-change replay re-indexes every one under
        the new subject — exactly as an edit does. Passing a binding's several stream rows settles
        their new subject atomically, never in torn per-stream commits. Tombstoned pages stay put;
        their chunks are already gone. Fails loud when no live source matched."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.source)
                .values(subject=subject, updated_at=sa.func.now())
                .where(
                    tables.source.c.id.in_(source_ids),
                    tables.source.c.workspace_id == self.store.workspace_id,
                    tables.source.c.removed_at.is_(None),
                )
            )
            if updated.rowcount == 0:
                raise ValueError(f"no live sources {source_ids} in this workspace")
            await connection.execute(
                sa.update(tables.page)
                .values(subject=subject, updated_at=now)
                .where(
                    tables.page.c.source_id.in_(source_ids),
                    tables.page.c.tombstone.is_(False),
                )
            )

    async def rewindow_sources(
        self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID] = frozenset()
    ) -> None:
        """Rewrite live rows' non-identity config in one transaction, clearing the cursor of each
        row in `refetch` so its next run re-walks from the new parameter. A binding's several
        stream rows settle together, never in torn per-stream commits.

        Every new config is re-hashed against the row it is written to and must still land on it,
        so this can only move a parameter OF the dataset a row syncs, never which dataset it is —
        a row whose id stopped deriving from its own config would be unfindable by every later
        registration. Fails loud on that, and when a passed row is not live in this workspace.

        A refetched row's claim is dropped with its cursor: a sync already in flight completes by
        writing `cursor=next_cursor` under `claimed_by == its claim`, putting the row straight back
        on the delta path it was just taken off. Dropping the claim makes that write match no row.
        Its pages still land, harmlessly — digest-skipped on the re-walk, as after any expired
        lease. `next_sync_at` needs no equivalent; `_rescheduled` already guards it."""
        if not configs:
            return
        if not refetch <= configs.keys():
            raise ValueError("every refetched source must be one of the rewindowed rows")
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.source.c.id,
                        tables.source.c.backend,
                        tables.source.c.connection_id,
                    )
                    .where(
                        tables.source.c.id.in_(tuple(configs)),
                        tables.source.c.workspace_id == self.store.workspace_id,
                        tables.source.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
            if len(rows) != len(configs):
                found = {row.id for row in rows}
                raise ValueError(
                    f"no live sources {sorted(set(configs) - found)} in this workspace"
                )
            for row in rows:
                config = configs[row.id]
                payload = config.model_dump(mode="json")
                non_identity = (
                    type(config).non_identity_fields
                    if isinstance(config, SourceRowConfig)
                    else frozenset[str]()
                )
                landed = source_row_id(
                    self.store.workspace_id,
                    row.backend,
                    payload,
                    connection_id=row.connection_id,
                    non_identity_keys=non_identity,
                )
                if landed != row.id:
                    raise ValueError(
                        f"rewindowing source {row.id} would move it to {landed}: only a config "
                        "field the model declares non-identity may be rewritten in place"
                    )
                values: dict[str, Any] = {"config": payload, "updated_at": now}
                if row.id in refetch:
                    values |= {
                        "cursor": None,
                        "next_sync_at": now,
                        "claimed_by": None,
                        "claim_expires_at": None,
                    }
                await connection.execute(
                    sa.update(tables.source).values(**values).where(tables.source.c.id == row.id)
                )

    async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None:
        """Pull live sources' next sync to now, so the sync driver claims them on its next pass —
        the one sanctioned way to resync on demand. The claim lease serializes concurrent syncs of
        one source, and a request landing while a sync holds that lease survives it: the completing
        writer reschedules only the sync it actually ran (`sources.sync._rescheduled`). Fails loud
        when no live source matched."""
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.source)
                .values(next_sync_at=datetime.now(UTC), updated_at=sa.func.now())
                .where(
                    tables.source.c.id.in_(source_ids),
                    tables.source.c.workspace_id == self.store.workspace_id,
                    tables.source.c.removed_at.is_(None),
                )
            )
            if updated.rowcount == 0:
                raise ValueError(f"no live sources {source_ids} in this workspace")

    async def propose_change(self, change: AgentChange) -> ProposalRef:
        """Open a governed proposal against an agent's prompt, stamped with this extension as the
        proposer — never a direct write to agent config; approval re-checks the digest and applies
        the compare-and-swap."""
        return await Governance(
            workspace_id=self.store.workspace_id, extension=self.store.extension
        ).propose_change(change)

    async def trajectories(self) -> tuple[Trajectory, ...]:
        """This workspace's conversation transcripts as the eval corpus. Fails loud when no corpus
        is wired, rather than reporting an empty corpus."""
        if self.corpus is None:
            raise RuntimeError("trajectories requires a trajectory corpus; none is wired")
        return await self.corpus.trajectories()


def context_for(
    extension: str,
    declared: frozenset[str],
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    pages: PageFeed | None = None,
    blob: WorkspaceBlobStore | None = None,
    sandboxes: ConversationSandbox | None = None,
    invoker: TurnInvoker | None = None,
    model_resolver: ModelResolver | None = None,
    model_job: str | None = None,
    surfaces: frozenset[str] = frozenset(),
    addressed_surfaces: frozenset[str] = frozenset(),
    credential_sources: tuple[tuple[str, CredentialSource], ...] = (),
    credential_store: CredentialStore | None = None,
    tailer: TurnTailer | None = None,
    probes: ConversationProbes | None = None,
    member_context_read: bool = False,
    scheduled_member_id: UUID | None = None,
    member_context_blob: WorkspaceBlobStore | None = None,
    *,
    audience: Audience = SHARED_AUDIENCE,
    public_base_url: str | None = None,
    artifact_token_secret: str = "",
    home_surface: str | None = None,
) -> ExtensionContext:
    """The scoped handle a handler receives — no workspace passed: every accessor reads the ambient
    workspace the turn or job bound (`ws_current()`), so the one context object serves whichever
    workspace is bound when a handler runs. `declared` gates credential slots and `surfaces` gates
    installation registration; a `model_resolver` wires the metered model seam, keyed and billed
    to that same workspace, and `model_job` is the job key that seam's spend and latency are
    attributed to — required wherever a resolver is wired, so a metered call can never reach the
    `ufo.model_*` series unattributed.
    `public_base_url` is the deploy's externally reachable base, which a
    kind listing rows a member opens needs and cannot reach any other way, and
    `artifact_token_secret` is what a link into the artifact namespace is signed with — a kind whose
    row carries a picture mints its preview link over the two. A `tailer` lets a handler
    firing inside a turn watch that turn's frames — the one seam a hook's own side-channel work
    reads the loop through. `probes` is the off-turn sandbox exec, wired only where a handler runs
    outside every turn: a tool or in-turn hook already holds the turn's own sandbox."""
    if model_resolver is not None and model_job is None:
        raise ValueError("a wired model_resolver needs the model_job its spend is attributed to")
    return ExtensionContext(
        store=ScopedStore(extension=extension),
        credentials=CredentialAccess(
            declared=declared,
            _sources=credential_sources,
            _store=credential_store,
        ),
        audience=audience,
        installations=SurfaceInstallationAccess(declared=surfaces, addressed=addressed_surfaces),
        index=index,
        embed=embed,
        pages=pages,
        corpus=None if blob is None else TrajectoryCorpus(blob),
        files=None if sandboxes is None else ConversationFiles(sandboxes),
        probes=probes,
        invoker=invoker,
        model=(
            None
            if model_resolver is None or model_job is None
            else ModelAccess(model_resolver, model_job)
        ),
        key_slot_for=None if model_resolver is None else model_resolver.key_slot_for,
        public_base_url=public_base_url,
        home_surface=home_surface,
        tailer=tailer,
        member_context_read_allowed=member_context_read,
        scheduled_member_id=scheduled_member_id,
        member_context_blob=member_context_blob if member_context_read else None,
        artifact_token_secret=artifact_token_secret,
    )
