"""Turbopuffer index backend as an extension: cosine ANN + BM25 lexical over one namespace.

Registered through the `indexes` Manifest point and selected by `memory.index_backend =
"turbopuffer"`. Core builds it at boot with a credential reader for the `turbopuffer_api_key` slot;
the index runs in the jobs/serve role, never in the sandbox, so every request is a direct httpx call
under a Bearer key read from the slot in-process, not injected at the egress proxy.

Chunks store as documents in a per-workspace namespace keyed by chunk_digest (base64url-shortened),
with the embedding as the vector and owner_kind/owner_id/subject/ordinal/text as attributes; queries
filter by owner_kind and the recall subject set and return `Hit`s. `delete` drops a scope by
enumerating its ids; `prune` drops only the scope's ids outside a keep-set so a re-chunked owner
leaves no orphan. This adapts metalcraft's page-based `TurbopufferIndex` to ufo's chunk-based
`IndexBackend` protocol (owner_kind/subject filter, `Chunk`/`Hit`/`IndexScope` value objects); the
base URL is the default region endpoint rather than a per-deploy override."""

import asyncio
import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ufo.sdk.context import CredentialAccess
from ufo.sdk.index import Chunk, Hit, IndexScope
from ufo.sdk.manifest import CredentialSlot, IndexBackendSpec, Manifest

NAME = "turbopuffer"
VERSION = "0.1.0"
INDEX_BACKEND = "turbopuffer"
API_KEY_SLOT = "turbopuffer_api_key"
BASE_URL = "https://api.turbopuffer.com/v2"
TIMEOUT_SECONDS = 30.0
WRITE_BATCH = 1000
EXPORT_PAGE = 1200
NAMESPACE_PREFIX = "ufo-"
ATTRIBUTES = ("owner_kind", "owner_id", "subject", "ordinal", "text")
MAX_FULL_TEXT_QUERY_BYTES = 1024
MIN_TERM_CHARS = 2
"""A term BM25 can rank by: a run of two or more letters or digits. The run is what counts, not the
tally — the index splits on everything else, so `3$2` is the two one-character terms `3` and `2`,
each of which the whole corpus holds."""
TERM_RUN = re.compile(rf"[^\W_]{{{MIN_TERM_CHARS},}}", re.UNICODE)
SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
ENCODED_ID_LEN = 43


def turbopuffer_id(chunk_digest: str) -> str:
    """A chunk_digest as a Turbopuffer document id: sha256 digests shorten to base64url (dropping
    the `=` pad) to stay well under the id length limit; any other id passes through unchanged."""
    if not SHA256_ID.match(chunk_digest):
        return chunk_digest
    raw = bytes.fromhex(chunk_digest.removeprefix("sha256:"))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def chunk_digest_from_id(chunk_id: str) -> str:
    """Invert `turbopuffer_id`: a 43-char base64url id decodes back to its `sha256:` digest,
    anything else passes through — so a Hit read back carries the same digest that was written."""
    if len(chunk_id) != ENCODED_ID_LEN:
        return chunk_id
    try:
        raw = base64.urlsafe_b64decode(chunk_id + "=")
    except (ValueError, binascii.Error):
        return chunk_id
    return "sha256:" + raw.hex()


def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]:
    """The columnar upsert payload for a batch: parallel id/vector/attribute columns, cosine
    distance, and a full-text-search schema on `text` so the BM25 lexical rank works."""
    columns: dict[str, list[Any]] = {
        "id": [turbopuffer_id(chunk.chunk_digest) for chunk in chunks],
        "vector": [list(chunk.embedding) for chunk in chunks],
        "owner_kind": [chunk.owner_kind for chunk in chunks],
        "owner_id": [chunk.owner_id for chunk in chunks],
        "subject": [chunk.subject for chunk in chunks],
        "ordinal": [chunk.ordinal for chunk in chunks],
        "text": [chunk.text for chunk in chunks],
    }
    return {
        "upsert_columns": columns,
        "distance_metric": "cosine_distance",
        "schema": {"text": {"type": "string", "full_text_search": True}},
    }


def bm25_query(text: str) -> str:
    """The lexical query bounded to Turbopuffer's 1024-byte full-text query limit — a longer one is
    rejected as a malformed request, and the text is a member's or a model's of any length. The cut
    drops the term it lands in, so the bounded query carries whole terms.

    A token with fewer than `MIN_TERM_CHARS` of its own characters is dropped, and text left holding
    none answers the empty query — which the caller reads as no lexical leg at all. BM25 ranks by
    term, so a query of punctuation or lone letters gives it nothing to rank by and every chunk in
    the namespace ties: the index answers "all of these match" for text that means nothing, and a
    search would then carry the whole namespace past any relevance bar it applies. Dropping the
    query here leaves such a search standing on its vector leg, where the recall floor governs."""
    query = " ".join(token for token in text.split() if TERM_RUN.search(token))
    encoded = query.encode()
    if len(encoded) <= MAX_FULL_TEXT_QUERY_BYTES:
        return query
    clipped = encoded[:MAX_FULL_TEXT_QUERY_BYTES].decode(errors="ignore")
    return " ".join(clipped.split()[:-1]) or clipped


def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]:
    """The Turbopuffer filter for a scoped query: this owner kind and any of the recall subjects —
    the same subject + owner-kind scoping the dialect-native backends apply in SQL."""
    clauses = [["owner_kind", "Eq", owner_kind], ["subject", "In", sorted(subjects)]]
    return ["And", clauses]


def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]:
    clauses: list[Any] = [
        ["owner_kind", "Eq", scope.owner_kind],
        ["owner_id", "Eq", scope.owner_id],
    ]
    if after_id is not None:
        clauses.append(["id", "Gt", after_id])
    return ["And", clauses]


def hit_from_row(row: dict[str, Any], score: float) -> Hit:
    return Hit(
        chunk_digest=chunk_digest_from_id(str(row["id"])),
        owner_kind=str(row["owner_kind"]),
        owner_id=str(row["owner_id"]),
        subject=str(row["subject"]),
        ordinal=int(row["ordinal"]),
        text=str(row["text"]),
        score=score,
    )


def vector_score(row: dict[str, Any], position: int, total: int) -> float:
    """A vector hit's score: `1 - cosine_distance` when Turbopuffer returns `$dist`, else a
    descending rank fallback — either way higher is nearer, which is all recall's fusion needs."""
    distance = row.get("$dist")
    if isinstance(distance, (int, float)):
        return 1.0 - float(distance)
    return float(total - position)


@dataclass(frozen=True)
class TurbopufferIndex:
    """The `IndexBackend` over Turbopuffer's HTTP API. Holds the credential reader for the BYOK key
    and one registry of per-loop clients whose connection pools the operations on that loop share (a
    test builds them over a stub transport). The Bearer key rides each request, not the client — the
    ambient workspace scopes the key, and one boot-built index serves them all. Its namespace is
    derived from the workspace, so the deploy's one workspace owns one namespace."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    clients: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = field(
        default_factory=dict, compare=False
    )

    def _api(self) -> httpx.AsyncClient:
        """The running loop's client, built on first touch. A pooled TLS connection carries anyio
        primitives bound to the loop that opened it, and serve drives one boot-built index from
        three loops — uvicorn's, DBOS's and the heartbeat thread's — so a client shared across them
        raises `is bound to a different event loop` the moment a second loop reuses a pooled
        connection. Sync throughout: there is no await between the lookup and the store, so two
        tasks on one loop cannot interleave here, and two loops write different keys. The key is
        the loop itself, never its id: CPython hands a freed loop's address straight back, so an
        id key serves a dead loop's pool to the live loop that replaced it. The sweep drops
        entries whose loop is gone but cannot close them — the loop that owns those sockets is the
        only thing that could, and it is already closed — so it only keeps the registry bounded.
        It snapshots the keys because the serve, DBOS and heartbeat loops insert here from
        different threads."""
        loop = asyncio.get_running_loop()
        for stale in [held for held in list(self.clients) if held.is_closed()]:
            self.clients.pop(stale, None)
        client = self.clients.get(loop)
        if client is None:
            client = self.clients[loop] = httpx.AsyncClient(
                base_url=BASE_URL, timeout=TIMEOUT_SECONDS, transport=self.transport
            )
        return client

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        embeddable = tuple(chunk for chunk in chunks if chunk.embedding)
        if not embeddable:
            return
        headers = await self._auth()
        for start in range(0, len(embeddable), WRITE_BATCH):
            response = await self._api().post(
                self._path(),
                json=upsert_body(embeddable[start : start + WRITE_BATCH]),
                headers=headers,
            )
            response.raise_for_status()

    async def delete(self, scope: IndexScope) -> None:
        headers = await self._auth()
        chunks = await self._scope_chunks(scope, headers)
        ids = [turbopuffer_id(chunk.chunk_digest) for chunk in chunks]
        for start in range(0, len(ids), WRITE_BATCH):
            response = await self._api().post(
                self._path(), json={"deletes": ids[start : start + WRITE_BATCH]}, headers=headers
            )
            response.raise_for_status()

    async def restamp(self, scope: IndexScope, subject: str, keep: frozenset[str]) -> bool:
        headers = await self._auth()
        chunks = await self._scope_chunks(scope, headers)
        if frozenset(chunk.chunk_digest for chunk in chunks) != keep:
            return False
        if chunks:
            response = await self._api().post(
                self._path(),
                json={
                    "patch_by_filter": {
                        "filters": scope_filters(scope, None),
                        "patch": {"subject": subject},
                    }
                },
                headers=headers,
            )
            response.raise_for_status()
        return True

    async def reattribute(self, scope: IndexScope, owner_id: str) -> None:
        response = await self._api().post(
            self._path(),
            json={
                "patch_by_filter": {
                    "filters": scope_filters(scope, None),
                    "patch": {"owner_id": owner_id},
                }
            },
            headers=await self._auth(),
        )
        if response.status_code != httpx.codes.NOT_FOUND:
            response.raise_for_status()

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        headers = await self._auth()
        chunks = await self._scope_chunks(scope, headers)
        ids = [
            turbopuffer_id(chunk.chunk_digest) for chunk in chunks if chunk.chunk_digest not in keep
        ]
        for start in range(0, len(ids), WRITE_BATCH):
            response = await self._api().post(
                self._path(), json={"deletes": ids[start : start + WRITE_BATCH]}, headers=headers
            )
            response.raise_for_status()

    async def has_chunks(self, scope: IndexScope) -> bool:
        body = {"rank_by": ["id", "asc"], "top_k": 1, "filters": scope_filters(scope, None)}
        response = await self._api().post(
            self._path("/query"), json=body, headers=await self._auth()
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        response.raise_for_status()
        return bool(response.json().get("rows"))

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        text = bm25_query(query)
        if not text or not subjects:
            return ()
        rows = await self._query(["text", "BM25", text], owner_kind, subjects, limit)
        return tuple(
            hit_from_row(row, float(len(rows) - position)) for position, row in enumerate(rows)
        )

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        if not embedding or not subjects:
            return ()
        rows = await self._query(["vector", "ANN", list(embedding)], owner_kind, subjects, limit)
        scored = (
            (row, vector_score(row, position, len(rows))) for position, row in enumerate(rows)
        )
        return tuple(hit_from_row(row, score) for row, score in scored if score > 0)

    async def _query(
        self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int
    ) -> list[dict[str, Any]]:
        body = {
            "rank_by": rank_by,
            "top_k": limit,
            "include_attributes": list(ATTRIBUTES),
            "filters": query_filters(owner_kind, subjects),
        }
        response = await self._api().post(
            self._path("/query"), json=body, headers=await self._auth()
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return []
        response.raise_for_status()
        return list(response.json().get("rows") or [])

    async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]:
        chunks: list[Chunk] = []
        after_id: str | None = None
        while True:
            body = {
                "rank_by": ["id", "asc"],
                "top_k": EXPORT_PAGE,
                "include_attributes": list(ATTRIBUTES),
                "filters": scope_filters(scope, after_id),
            }
            response = await self._api().post(self._path("/query"), json=body, headers=headers)
            if response.status_code == httpx.codes.NOT_FOUND:
                return chunks
            response.raise_for_status()
            rows = list(response.json().get("rows") or [])
            chunks.extend(
                Chunk(
                    chunk_digest=chunk_digest_from_id(str(row["id"])),
                    owner_kind=str(row["owner_kind"]),
                    owner_id=str(row["owner_id"]),
                    subject=str(row["subject"]),
                    ordinal=int(row["ordinal"]),
                    text=str(row["text"]),
                )
                for row in rows
            )
            if len(rows) < EXPORT_PAGE:
                return chunks
            after_id = str(rows[-1]["id"])

    async def _auth(self) -> dict[str, str]:
        api_key = await self.credentials.get(API_KEY_SLOT)
        return {"Authorization": f"Bearer {api_key}"}

    def _path(self, suffix: str = "") -> str:
        return f"/namespaces/{NAMESPACE_PREFIX}{self.credentials.workspace_id}{suffix}"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=API_KEY_SLOT,
                description="Turbopuffer API key the index reads in-process to authorize its "
                "HTTP requests (no egress-proxy injection; the index runs outside the sandbox).",
            ),
        ),
        indexes=(
            IndexBackendSpec(
                name=INDEX_BACKEND,
                factory=lambda ctx: TurbopufferIndex(credentials=ctx.credentials),
            ),
        ),
    )
