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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import (
    Pricing,
    UsageExport,
    ack_usage_exports,
    mint_usage_exports,
    read_pending_usage_exports,
)
from ufo.blob import BlobNotFound, BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.db import workspace_tx
from ufo.ext.surface import SurfaceInstallationAccess
from ufo.governance import Governance, prompt_digest
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.interface import (
    Message,
    ModelClient,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolUseBlock,
)
from ufo.o11y import log
from ufo.scheduling import ScheduleInvoker, ScheduleStore
from ufo.schema import tables
from ufo.schema.records import AgentChange, ProposalRef, Usage
from ufo.sources.sync import PageFeed, source_row_id
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
    `ws_current().credential`. The declared set is all this holds — workspace and secret both come
    from the scope the turn or job bound, so a handler can read neither an undeclared slot nor a
    workspace it did not name."""

    declared: frozenset[str]

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

    async def rotate(self, slot: str, expected: str, plaintext: str) -> bool:
        """Compare-and-swap an existing declared slot after an external provider rotates it. This
        cannot create the initial credential: that remains the member-sealed surface handoff."""
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        return await ws_current().rotate_credential(slot, expected, plaintext)


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
    """The one blob reach a handler gets: this workspace's conversation transcripts, read only. The
    store stays module-private (`_blob`), so the only operation exposed is enumerating this
    workspace's trajectories — never an arbitrary blob get or put over another conversation or an
    artifact. The read is bounded to the `limit` most recently created conversations, so a
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
        """Run one tool-aware model turn and return its assistant message after metering it."""
        model = self._resolver.auto_model
        client = await self._resolver.client_for(model)
        parts: list[str] = []
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []
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
            content=(*((TextBlock(text=text),) if text else ()), *tool_calls),
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
    next_sync_at: datetime
    consecutive_errors: int


@dataclass(frozen=True)
class PageRecord:
    """One live synced page as `ExtensionContext.source_pages` reads it: its identity, the source
    row it belongs to, the visibility subject, the content digest, and the blob reference — the
    body stays by reference, never inlined. A value object — never leaves the process."""

    id: UUID
    source_id: UUID
    subject: str
    digest: str
    body_ref: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ExtensionContext:
    store: ScopedStore
    credentials: CredentialAccess
    installations: SurfaceInstallationAccess = field(
        default_factory=lambda: SurfaceInstallationAccess(frozenset())
    )
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    corpus: TrajectoryCorpus | None = None
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
        """Kick an internal turn for `agent_id` in `conversation_id`. Fails loud when no invoker is
        wired rather than silently dropping the invocation."""
        if self.invoker is None:
            raise RuntimeError("invoke requires a turn invoker; none is wired")
        return await self.invoker.invoke(conversation_id, agent_id, message, idempotency_key)

    async def register_source(
        self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None
    ) -> UUID:
        """Register a content-sync source for this workspace under `backend` — a `SourceBackend` an
        extension declared through its Manifest `sources` point — with `config` the backend's typed
        per-source parameters (the connected account, a folder root), `subject` the disclosure every
        page it syncs is stamped with, and `owner_member_id` the registering member (None for a
        shared feed). Idempotent on (workspace, backend, config): re-running onboarding or
        re-connecting the same account settles on the one row, never a duplicate sync — but a live
        row's disclosure is fixed at registration: re-registering it under a different `subject`
        raises rather than silently reclassifying already-synced pages. The core sync driver polls
        the row and lands its pages in memory; embedding stays a job."""
        payload = config.model_dump(mode="json")
        source_id = source_row_id(self.store.workspace_id, backend, payload)
        async with workspace_tx() as connection:
            present = (
                await connection.execute(
                    sa.select(
                        tables.source.c.id, tables.source.c.removed_at, tables.source.c.subject
                    ).where(tables.source.c.id == source_id)
                )
            ).one_or_none()
            if present is not None and present.removed_at is None:
                if present.subject != subject:
                    raise ValueError(
                        "a source with this configuration is already registered; delete it "
                        "before changing its disclosure"
                    )
                return source_id
            if present is not None:
                await connection.execute(
                    sa.update(tables.source)
                    .values(
                        removed_at=None,
                        subject=subject,
                        owner_member_id=owner_member_id,
                        cursor=None,
                        next_sync_at=datetime.now(UTC),
                        consecutive_errors=0,
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.source.c.id == source_id)
                )
                return source_id
            await connection.execute(
                sa.insert(tables.source).values(
                    id=source_id,
                    workspace_id=self.store.workspace_id,
                    backend=backend,
                    config=payload,
                    subject=subject,
                    owner_member_id=owner_member_id,
                    cursor=None,
                    next_sync_at=datetime.now(UTC),
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
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
                tables.source.c.next_sync_at,
                tables.source.c.consecutive_errors,
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
                next_sync_at=row["next_sync_at"],
                consecutive_errors=row["consecutive_errors"],
            )
            for row in rows
        )

    async def source_pages(self, subjects: frozenset[str] | None = None) -> tuple[PageRecord, ...]:
        """This workspace's live (non-tombstoned) synced pages, optionally narrowed to a set of
        visibility subjects — the sanctioned read over the core `page` table, scoped by workspace
        exactly as `sources()` is. A subject-scoped caller passes `{member_subject(audience),
        shared}` so a member never reads another member's private page; the off-turn producer that
        wants every page passes None. The body stays by reference in each record."""
        query = (
            sa.select(
                tables.page.c.id,
                tables.page.c.source_id,
                tables.page.c.subject,
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
        if subjects is not None:
            query = query.where(tables.page.c.subject.in_(subjects))
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return tuple(
            PageRecord(
                id=row["id"],
                source_id=row["source_id"],
                subject=row["subject"],
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
    invoker: TurnInvoker | None = None,
    model_resolver: ModelResolver | None = None,
    schedule_invoker: ScheduleInvoker | None = None,
    surfaces: frozenset[str] = frozenset(),
) -> ExtensionContext:
    """The scoped handle a handler receives — no workspace passed: every accessor reads the ambient
    workspace the turn or job bound (`ws_current()`), so the one context object serves whichever
    workspace is bound when a handler runs. `declared` gates credential slots and `surfaces` gates
    installation registration; a `model_resolver` wires the metered model seam, keyed and billed
    to that same workspace."""
    return ExtensionContext(
        store=ScopedStore(extension=extension),
        credentials=CredentialAccess(declared=declared),
        installations=SurfaceInstallationAccess(declared=surfaces),
        index=index,
        embed=embed,
        pages=pages,
        corpus=None if blob is None else TrajectoryCorpus(blob),
        scheduler=ScheduleStore(schedule_invoker),
        invoker=invoker,
        model=None if model_resolver is None else ModelAccess(model_resolver),
        key_slot_for=None if model_resolver is None else model_resolver.key_slot_for,
    )
