"""The capability-scoped view a job or extension handler receives.

A handler never sees a raw DB handle, a raw blob store, or another workspace: it gets a
`ScopedStore` (its own key space under one workspace), `CredentialAccess` (only the slots its
manifest declared), and — when trajectory reads are wired — a `TrajectoryCorpus` (this workspace's
transcripts, read only). `context_for` builds the same shape for an extension (its manifest name +
declared slots) and for a core job (the `core` namespace, no slots) — so a core job rides the exact
path an extension does. The `ExtensionContext` shape is open: it carries the selected index/embed
backends, a transaction over the extension's own tables, governed proposals, and invoke, without
reshaping what handlers already hold."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import Pricing
from ufo.blob import BlobNotFound, BlobStore
from ufo.candidates import WorkspaceCandidates, owner_candidates
from ufo.db import workspace_tx
from ufo.governance import Governance, prompt_digest
from ufo.indexing import EmbedClient, IndexBackend
from ufo.models.interface import Message, ModelClient, ModelRequest, TextDelta
from ufo.o11y import log
from ufo.scheduling import ScheduleStore
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


@dataclass(frozen=True)
class TrajectoryCorpus:
    """The one blob reach a handler gets: this workspace's conversation transcripts, read only. The
    store stays module-private (`_blob`), so the only operation exposed is enumerating this
    workspace's trajectories — never an arbitrary blob get or put over another conversation or an
    artifact. A conversation whose transcript is missing or corrupt is skipped-with-log, never
    aborting the whole corpus."""

    _blob: BlobStore

    @property
    def workspace_id(self) -> UUID:
        return ws_current().workspace_id

    async def trajectories(self) -> tuple[Trajectory, ...]:
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
                    .where(tables.conversation.c.workspace_id == self.workspace_id)
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
    with at least one turn — one distinct `workspace_id` per such workspace, read for the extension
    through the one RLS-bypass path. Core owns the `conversation`/`turn` tables, so it owns this
    query and the extension declares `candidates=trajectory_workspaces()` without reaching
    `owner_tx`; the dispatcher binds each and the corpus read runs RLS-scoped, exactly as a turn
    would scope it."""
    with_a_turn = (
        sa.select(tables.conversation.c.workspace_id)
        .select_from(
            tables.conversation.join(
                tables.turn, tables.turn.c.conversation_id == tables.conversation.c.id
            )
        )
        .distinct()
    )
    return owner_candidates(with_a_turn)


class TurnInvoker(Protocol):
    """The admit-turn seam a background handler drives: place one turn for a conversation's agent on
    the durable queue and return its id. `idempotency_key` collapses a redelivered fire to the turn
    already admitted, so a refired schedule tick never spawns a second turn."""

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


@dataclass(frozen=True)
class ModelAccess:
    """The metered LLM a background handler reaches: one completion against the deploy's default
    model, keyed to and billed to the ambient workspace. It resolves its client through the same
    `client_for` a turn uses (the workspace's BYOK key, else the platform key) and books usage
    through the same `billable_event`, so the key's workspace and the billed workspace are one, by
    construction — never an unmetered direct egress. `complete` fixes the request's model to the
    deploy default, so the model billed is always the model called."""

    _resolver: ModelResolver

    @property
    def model(self) -> str:
        """The deploy default this seam bills and calls — the id fixed onto every completion."""
        return self._resolver.auto_model

    async def complete(self, request: ModelRequest) -> str:
        """Stream one completion against the deploy default, book its token usage to the bound
        workspace when the block succeeds, and return the assembled text. Bound `request.max_tokens`
        and the payload at the call site — this seam prices whatever the provider returns."""
        model = self._resolver.auto_model
        client = await self._resolver.client_for(model)
        parts: list[str] = []
        usages: list[Usage] = []
        async with ws_current().billable_event() as bill:
            async for event in client.complete(request.model_copy(update={"model": model})):
                match event:
                    case TextDelta(text=text):
                        parts.append(text)
                    case Usage():
                        usages.append(event)
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
        return "".join(parts)


@dataclass(frozen=True)
class ExtensionContext:
    store: ScopedStore
    credentials: CredentialAccess
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    pages: PageFeed | None = None
    corpus: TrajectoryCorpus | None = None
    scheduler: ScheduleStore | None = None
    invoker: TurnInvoker | None = None
    model: ModelAccess | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A transaction for the extension's own tables — those a migration the extension ships
        created. The handler builds queries against the SQLAlchemy tables it declares and scopes
        rows by `self.store.workspace_id`. This yields a RAW whole-database connection: it is not
        restricted to the extension's schema and enforces no workspace scoping — reaching only its
        own tables, scoped to its workspace, is the extension's responsibility, not a guarantee of
        this handle (the SDK import boundary is a static gate over imports, not over runtime SQL).
        Commits on exit, rolls back on error — the same one transaction the ScopedStore rides."""
        async with workspace_tx() as connection:
            yield connection

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID:
        """Kick a turn for `agent_id` in `conversation_id` through the admit-turn seam. Fails loud
        when no invoker is wired, rather than silently dropping a scheduled fire."""
        if self.invoker is None:
            raise RuntimeError("invoke requires a turn invoker; none is wired")
        return await self.invoker.invoke(conversation_id, agent_id, message, idempotency_key)

    async def register_source(self, backend: str, config: BaseModel) -> None:
        """Register a content-sync source for this workspace under `backend` — a `SourceBackend` an
        extension declared through its Manifest `sources` point — with `config` the backend's typed
        per-source parameters (the connected account, a folder root). Idempotent on (workspace,
        backend, config): re-running onboarding or re-connecting the same account settles on the one
        row, never a duplicate sync. The core sync driver polls the row and lands its pages in
        memory; embedding stays a job."""
        payload = config.model_dump(mode="json")
        source_id = source_row_id(self.store.workspace_id, backend, payload)
        async with workspace_tx() as connection:
            present = (
                await connection.execute(
                    sa.select(tables.source.c.id).where(tables.source.c.id == source_id)
                )
            ).one_or_none()
            if present is not None:
                return
            await connection.execute(
                sa.insert(tables.source).values(
                    id=source_id,
                    workspace_id=self.store.workspace_id,
                    backend=backend,
                    config=payload,
                    cursor=None,
                    next_sync_at=datetime.now(UTC),
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
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
) -> ExtensionContext:
    """The scoped handle a handler receives — no workspace passed: every accessor reads the ambient
    workspace the turn or job bound (`ws_current()`), so the one context object serves whichever
    workspace is bound when a handler runs. `declared` gates which credential slots it may read; a
    `model_resolver` (the registry) wires the metered model seam, keyed and billed to that same
    workspace."""
    return ExtensionContext(
        store=ScopedStore(extension=extension),
        credentials=CredentialAccess(declared=declared),
        index=index,
        embed=embed,
        pages=pages,
        corpus=None if blob is None else TrajectoryCorpus(blob),
        scheduler=ScheduleStore(),
        invoker=invoker,
        model=None if model_resolver is None else ModelAccess(model_resolver),
    )
