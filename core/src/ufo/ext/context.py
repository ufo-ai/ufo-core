"""The capability-scoped view a job or extension handler receives.

A handler never sees a raw DB handle, a raw blob store, or another workspace: it gets a
`ScopedStore` (its own key space under one workspace), `CredentialAccess` (only the slots its
manifest declared), manifest-scoped surface installation registration for tools, and — when
trajectory reads are wired — a `TrajectoryCorpus` (this workspace's transcripts, read only).
`context_for` builds the same shape for an extension and for a core job, so a core job rides the
exact path an extension does. The `ExtensionContext` shape is open: it carries the selected
index/embed backends, a transaction over the extension's own tables, governed proposals, and
invoke, without reshaping what handlers already hold."""

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import (
    UsageExport,
    ack_usage_exports,
    mint_usage_exports,
    read_pending_usage_exports,
)
from ufo.agent_scope import agent_current
from ufo.audience import SHARED_AUDIENCE, Audience
from ufo.blob import BlobNotFound, BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.credentials import (
    CredentialSource,
    CredentialStore,
    installed_credential_requests,
    seal_installation,
    slot_secret,
)
from ufo.db import workspace_tx
from ufo.ext.surface import SurfaceInstallationAccess
from ufo.governance import Governance, prompt_digest
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
from ufo.o11y import log
from ufo.sandbox.conversation import ConversationSandbox
from ufo.scheduling import ScheduleInvoker, ScheduleStore
from ufo.schema import tables
from ufo.schema.records import AgentChange, ProposalRef, Usage
from ufo.sources.sync import PageFeed, SourceRowConfig, source_row_id
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
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.ext_store)
                .values(value=value, updated_at=sa.func.now())
                .where(
                    tables.ext_store.c.workspace_id == self.workspace_id,
                    tables.ext_store.c.extension == self.extension,
                    tables.ext_store.c.key == key,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(tables.ext_store).values(
                        workspace_id=self.workspace_id,
                        extension=self.extension,
                        key=key,
                        value=value,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
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
    only. The store stays module-private (`_blob`), so the only operation exposed is enumerating
    this workspace's trajectories — never an arbitrary blob get or put over another conversation or
    an artifact. The read is bounded to the `limit` most recently created conversations, so a
    workspace with a long history hands a job a bounded corpus, never every transcript it ever
    produced. A conversation whose transcript is missing or corrupt is skipped-with-log, never
    aborting the whole corpus."""

    _blob: BlobStore
    limit: int = TRAJECTORY_CORPUS_CONVERSATIONS

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def trajectories(self) -> tuple[Trajectory, ...]:
        recent = (
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.workspace_id == self.workspace_id)
            .order_by(tables.conversation.c.created_at.desc(), tables.conversation.c.id.desc())
            .limit(self.limit)
            .scalar_subquery()
        )
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
                    .where(tables.conversation.c.id.in_(recent))
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
    (`_sandboxes`): the only operations exposed are a scoped write and a scoped prune, never an
    arbitrary exec. Every operation resolves the conversation against the ambient workspace first,
    so a handler holding another tenant's conversation id writes nothing — the scoping is in the
    predicate, not left to the RLS tier."""

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


class TurnInvoker(Protocol):
    """The internal turn seam a background handler drives. It never consumes a member's pause;
    idempotency collapses a redelivered invocation to the turn already admitted."""

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID: ...


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


@dataclass(frozen=True)
class ModelAccess:
    """The metered LLM a background handler reaches: one model turn against the deploy's default,
    keyed to and billed to the ambient workspace. It resolves its client through the same
    `client_for` a turn uses (the workspace's BYOK key, else the platform key) and books usage
    through the same `billable_event`, so the key's workspace and the billed workspace are one, by
    construction — never an unmetered direct egress. Both operations fix the request's model to the
    deploy default, so the model billed is always the model called; `turn` preserves requested tool
    calls in the existing assistant `Message` shape and `complete` returns only its text."""

    _resolver: ModelResolver

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

    async def turn(self, request: ModelRequest) -> Message:
        """Run one tool-aware model turn and return its assistant message after metering it. A
        reasoning round's message opens with the blocks the model streamed, ahead of its text and
        tool calls: a handler that feeds tool results back through `turn` sends that message again,
        and the provider requires the sequence unchanged beside the tool calls it authenticates."""
        model = self._resolver.auto_model
        client = await self._resolver.client_for(model)
        parts: list[str] = []
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []
        reasoning: list[ThinkingBlock | RedactedThinkingBlock | ReasoningItemBlock] = []
        usages: list[Usage] = []
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
            bill.usage(
                model,
                Usage(
                    input_tokens=sum(u.input_tokens for u in usages),
                    output_tokens=sum(u.output_tokens for u in usages),
                    cache_read_tokens=sum(u.cache_read_tokens for u in usages),
                    cache_write_tokens=sum(u.cache_write_tokens for u in usages),
                ),
                self._resolver.pricing,
            )
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
    scheduler: ScheduleStore | None = None
    invoker: TurnInvoker | None = None
    model: ModelAccess | None = None
    key_slot_for: Callable[[str], str | None] | None = None

    async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]:
        """This extension's settled, unacknowledged usage deltas, at most `limit`, minting new
        intents first — the billing-export read seam. Consumer-keyed by the extension's stable
        name, so two exporters never touch each other's marks; usage settling before `floor`
        never exports (the extension's backfill bound). Core owns the mint because settlement and
        delta-freezing are writer knowledge no extension can express through the SDK without
        re-declaring the ledger's private schema — as is the `byok` label, resolved per model
        through the deploy's provider registry."""
        if self.key_slot_for is None:
            raise RuntimeError(
                "usage export needs the model registry to label byok; serve wires it"
            )
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
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID:
        """Kick an internal turn in `conversation_id`, asserting the conversation is bound to
        `agent_id` — admission refuses a mismatch, so a stored binding can never fire into another
        agent's conversation. Fails loud when no invoker is wired rather than silently dropping
        the invocation."""
        if self.invoker is None:
            raise RuntimeError("invoke requires a turn invoker; none is wired")
        return await self.invoker.invoke(conversation_id, agent_id, message, idempotency_key)

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
        non_identity = (
            type(config).non_identity_fields
            if isinstance(config, SourceRowConfig)
            else frozenset[str]()
        )
        source_id = source_row_id(
            self.store.workspace_id,
            backend,
            payload,
            connection_id=connection_id,
            non_identity_keys=non_identity,
        )
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
    blob: BlobStore | None = None,
    sandboxes: ConversationSandbox | None = None,
    invoker: TurnInvoker | None = None,
    model_resolver: ModelResolver | None = None,
    schedule_invoker: ScheduleInvoker | None = None,
    surfaces: frozenset[str] = frozenset(),
    credential_sources: tuple[tuple[str, CredentialSource], ...] = (),
    credential_store: CredentialStore | None = None,
    *,
    audience: Audience = SHARED_AUDIENCE,
) -> ExtensionContext:
    """The scoped handle a handler receives — no workspace passed: every accessor reads the ambient
    workspace the turn or job bound (`ws_current()`), so the one context object serves whichever
    workspace is bound when a handler runs. `declared` gates credential slots and `surfaces` gates
    installation registration; a `model_resolver` wires the metered model seam, keyed and billed
    to that same workspace."""
    return ExtensionContext(
        store=ScopedStore(extension=extension),
        credentials=CredentialAccess(
            declared=declared,
            _sources=credential_sources,
            _store=credential_store,
        ),
        audience=audience,
        installations=SurfaceInstallationAccess(declared=surfaces),
        index=index,
        embed=embed,
        pages=pages,
        corpus=None if blob is None else TrajectoryCorpus(blob),
        files=None if sandboxes is None else ConversationFiles(sandboxes),
        scheduler=ScheduleStore(schedule_invoker),
        invoker=invoker,
        model=None if model_resolver is None else ModelAccess(model_resolver),
        key_slot_for=None if model_resolver is None else model_resolver.key_slot_for,
    )
