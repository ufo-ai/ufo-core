"""The capability-scoped view a job or extension handler receives.

A handler never sees a raw DB handle, a raw blob store, or another workspace: it gets a
`ScopedStore` (its own key space under one workspace), `CredentialAccess` (only the slots its
manifest declared), and — when trajectory reads are wired — a `TrajectoryCorpus` (this workspace's
transcripts, read only). `context_for` builds the same shape for an extension (its manifest name +
declared slots) and for a core job (the `core` namespace, no slots) — so a core job rides the exact
path an extension does. The `ExtensionContext` shape is open: it carries the selected index/embed
backends, a transaction over the extension's own tables, governed proposals, and invoke, without
reshaping what handlers already hold."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import CORE_PRICING, Pricing, record_workspace_usage
from ufo.blob import BlobNotFound, BlobStore
from ufo.credentials import CredentialStore
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

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class UndeclaredCredentialSlot(KeyError):
    """A handler asked for a credential slot its manifest never declared."""


@dataclass(frozen=True)
class ScopedStore:
    """One extension's durable key space within one workspace, reached only through workspace_tx."""

    workspace_id: UUID
    extension: str

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
    """Reads only the slots a manifest declared; an undeclared slot never reaches the store. The
    store stays module-private (`_store`), so `get` — which checks the declaration first — is the
    only path to a secret; the raw store is never a public field an undeclared read could bypass."""

    workspace_id: UUID
    declared: frozenset[str]
    _store: CredentialStore | None

    async def get(self, slot: str) -> str:
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        if self._store is None:
            raise RuntimeError(f"credential slot {slot!r} declared but no credential key is set")
        return await self._store.get(self.workspace_id, slot)


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

    workspace_id: UUID
    _blob: BlobStore

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


class TurnInvoker(Protocol):
    """The admit-turn seam a background handler drives: place one turn for a conversation's agent on
    the durable queue and return its id. `idempotency_key` collapses a redelivered fire to the turn
    already admitted, so a refired schedule tick never spawns a second turn."""

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID: ...


@dataclass(frozen=True)
class ModelAccess:
    """The metered LLM a background handler reaches: one completion against the deploy's default
    model, its token usage priced and written to the workspace ledger under `ctx.store.workspace_id`
    (turn_id NULL, workspace-anchored) on every call — so extension model spend is visible in
    `ufoctl spend` and moves workspace-scoped spend caps, never an unmetered direct egress. The
    underlying client is built lazily through `_client_factory` on first use, so a deploy with no
    model key only fails when a handler actually calls the model, mirroring the other optional
    accessors. `complete` fixes the request's model to the deploy default (`model`), so the model
    billed is always the model called."""

    workspace_id: UUID
    model: str
    pricing: Pricing
    _client_factory: Callable[[], ModelClient]

    async def complete(self, request: ModelRequest) -> str:
        """Stream one completion, accumulate its text and token usage, meter the usage to the
        workspace ledger, and return the assembled text. Bound `request.max_tokens` and the input
        payload at the call site — this seam prices whatever the provider returns."""
        parts: list[str] = []
        usages: list[Usage] = []
        async for event in self._client_factory().complete(
            request.model_copy(update={"model": self.model})
        ):
            match event:
                case TextDelta(text=text):
                    parts.append(text)
                case Usage():
                    usages.append(event)
        usage = Usage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            cache_read_tokens=sum(u.cache_read_tokens for u in usages),
            cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        )
        async with workspace_tx() as connection:
            await record_workspace_usage(
                connection, self.workspace_id, self.model, usage, self.pricing
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
    workspace_id: UUID,
    extension: str,
    declared: frozenset[str],
    credential_store: CredentialStore | None,
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    pages: PageFeed | None = None,
    blob: BlobStore | None = None,
    invoker: TurnInvoker | None = None,
    model_client_factory: Callable[[], ModelClient] | None = None,
    model_name: str = "",
    pricing: Pricing = CORE_PRICING,
) -> ExtensionContext:
    store = ScopedStore(workspace_id=workspace_id, extension=extension)
    credentials = CredentialAccess(
        workspace_id=workspace_id, declared=declared, _store=credential_store
    )
    corpus = None if blob is None else TrajectoryCorpus(workspace_id, blob)
    model = (
        None
        if model_client_factory is None
        else ModelAccess(workspace_id, model_name, pricing, model_client_factory)
    )
    return ExtensionContext(
        store=store,
        credentials=credentials,
        index=index,
        embed=embed,
        pages=pages,
        corpus=corpus,
        scheduler=ScheduleStore(workspace_id=workspace_id),
        invoker=invoker,
        model=model,
    )
