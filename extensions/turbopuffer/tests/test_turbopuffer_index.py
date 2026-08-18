"""The Turbopuffer index backend end to end against a mocked Turbopuffer HTTP API.

Turbopuffer is the external dependency, stood in for with `httpx.MockTransport` (no live API, no
key on a real wire) so the real backend's request-building and response-parsing run against canned
responses — the thing asserted is the extension's code, never the mock. The pure id-encoding and
filter helpers are asserted directly. The Bearer key is read from a real `CredentialSlot` through
the same `CredentialAccess` core hands the factory, so the both-ends of the slot are exercised: the
stored secret reaches the request's Authorization header."""

import hashlib
import json
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_turbopuffer as tpuf
from cryptography.fernet import Fernet

from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess, context_for
from ufo.indexing import Chunk, IndexScope
from ufo.schema import tables
from ufo.workspace import init_workspace_credentials, ws

SHARED = "shared"
OWNER_KIND = "memory_item"
API_KEY = "tpuf-secret-key"


def _digest(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _access(workspace_id: UUID) -> CredentialAccess:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, tpuf.API_KEY_SLOT, API_KEY)
    return context_for(tpuf.NAME, frozenset({tpuf.API_KEY_SLOT})).credentials


def _api(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """The process-lifetime client the boot factory builds, over the test's stub transport."""
    return httpx.AsyncClient(
        base_url=tpuf.BASE_URL, timeout=tpuf.TIMEOUT_SECONDS, transport=transport
    )


def _recorder(
    rows: list[dict[str, object]] | None = None,
) -> tuple[httpx.MockTransport, list[tuple[httpx.Request, dict[str, object]]]]:
    """A Turbopuffer stand-in: records each request with its decoded body, answers a `/query` with
    the canned rows and any other write with an empty ok."""
    seen: list[tuple[httpx.Request, dict[str, object]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request, json.loads(request.content.decode())))
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"rows": rows or []})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle), seen


def test_turbopuffer_id_roundtrips_sha256_digests() -> None:
    digest = _digest("chunk")
    encoded = tpuf.turbopuffer_id(digest)
    assert len(encoded) == tpuf.ENCODED_ID_LEN and "=" not in encoded
    assert tpuf.chunk_digest_from_id(encoded) == digest


def test_turbopuffer_id_passes_through_a_non_sha256_id() -> None:
    assert tpuf.turbopuffer_id("d-alpha") == "d-alpha"
    assert tpuf.chunk_digest_from_id("d-alpha") == "d-alpha"


def test_bm25_query_is_empty_for_text_holding_no_term_to_match() -> None:
    """BM25 over a query whose every token is punctuation or a lone character matches the whole
    namespace: there is no term to rank by, so every chunk ties. Measured on the testing corpus, a
    punctuation string drew 200 rows and four single letters drew 200 — the index answering "these
    all match" for text that means nothing. Such a query is no lexical query, so it is dropped here
    and the search stands on its vector leg alone, where the recall floor governs."""
    assert tpuf.bm25_query('3$2`."|$') == ""
    assert tpuf.bm25_query("a b c d") == ""
    assert tpuf.bm25_query("   ") == ""
    assert tpuf.bm25_query("!!! ??? ...") == ""

    # A token carrying two or more of its own characters is a term, however odd it looks.
    assert tpuf.bm25_query("401k") == "401k"
    assert tpuf.bm25_query("G64 build") == "G64 build"
    assert tpuf.bm25_query("what is a sandbox?") == "what is sandbox?"


def test_bm25_query_bounds_a_long_query_to_the_full_text_limit() -> None:
    assert tpuf.bm25_query("  orbital widget  ") == "orbital widget"
    words = " ".join(f"term{number}" for number in range(400))
    bounded = tpuf.bm25_query(words)
    assert len(bounded.encode()) <= tpuf.MAX_FULL_TEXT_QUERY_BYTES
    assert bounded.split() == words.split()[: len(bounded.split())]
    assert len(tpuf.bm25_query("wärme " * 400).encode()) <= tpuf.MAX_FULL_TEXT_QUERY_BYTES
    assert tpuf.bm25_query("x" * 2000) == "x" * tpuf.MAX_FULL_TEXT_QUERY_BYTES


def test_query_filters_scope_owner_kind_and_subjects() -> None:
    filters = tpuf.query_filters(OWNER_KIND, frozenset({SHARED, "member:x"}))
    assert filters == [
        "And",
        [["owner_kind", "Eq", OWNER_KIND], ["subject", "In", ["member:x", SHARED]]],
    ]


async def test_upsert_posts_columnar_batches_under_the_bearer_key(db: None) -> None:
    workspace_id = await _workspace()
    transport, seen = _recorder()
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))
    d1, d2 = _digest("a"), _digest("b")
    with ws(workspace_id):
        await index.upsert(
            (
                Chunk(d1, OWNER_KIND, "m1", SHARED, 0, "orbital widget fleet", (1.0, 0.0)),
                Chunk(d2, OWNER_KIND, "m2", SHARED, 1, "second chunk", (0.0, 1.0)),
            )
        )
    assert len(seen) == 1
    request, body = seen[0]
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert f"/namespaces/{tpuf.NAMESPACE_PREFIX}{workspace_id}" in request.url.path
    columns = body["upsert_columns"]
    assert columns["id"] == [tpuf.turbopuffer_id(d1), tpuf.turbopuffer_id(d2)]
    assert columns["vector"] == [[1.0, 0.0], [0.0, 1.0]]
    assert columns["owner_kind"] == [OWNER_KIND, OWNER_KIND]
    assert columns["subject"] == [SHARED, SHARED]
    assert body["distance_metric"] == "cosine_distance"
    assert body["schema"]["text"]["full_text_search"] is True


async def test_lexical_and_vector_queries_filter_and_parse_hits(db: None) -> None:
    workspace_id = await _workspace()
    d1, d2 = _digest("a"), _digest("b")
    rows = [
        {
            "id": tpuf.turbopuffer_id(d1),
            "owner_kind": OWNER_KIND,
            "owner_id": "m1",
            "subject": SHARED,
            "ordinal": 0,
            "text": "orbital widget",
            "$dist": 0.25,
        },
        {
            "id": tpuf.turbopuffer_id(d2),
            "owner_kind": OWNER_KIND,
            "owner_id": "m2",
            "subject": SHARED,
            "ordinal": 1,
            "text": "second",
            "$dist": 0.75,
        },
    ]
    transport, seen = _recorder(rows)
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))

    with ws(workspace_id):
        lexical = await index.lexical("orbital", frozenset({SHARED}), OWNER_KIND, 10)
        _, lex_body = seen[-1]
        assert lex_body["rank_by"] == ["text", "BM25", "orbital"]
        assert lex_body["top_k"] == 10
        assert lex_body["filters"] == [
            "And",
            [["owner_kind", "Eq", OWNER_KIND], ["subject", "In", [SHARED]]],
        ]
        assert [hit.chunk_digest for hit in lexical] == [d1, d2]
        assert lexical[0].text == "orbital widget" and lexical[0].score > lexical[1].score

        vector = await index.vector((1.0, 0.0), frozenset({SHARED}), OWNER_KIND, 10)
        _, vec_body = seen[-1]
        assert vec_body["rank_by"] == ["vector", "ANN", [1.0, 0.0]]
        assert [hit.chunk_digest for hit in vector] == [d1, d2]
        assert vector[0].score == pytest.approx(0.75)
        assert vector[1].score == pytest.approx(0.25)


async def test_lexical_bounds_the_query_turbopuffer_rejects_as_malformed(db: None) -> None:
    """A query arrives as a member's message or a model's search text, of any length, and one over
    Turbopuffer's 1024-byte full-text limit makes the whole request a 400 — the leg's hits lost to a
    client error of our own making. The bound lands on the request body, so the query the provider
    rejects is never built, and a query with no text is no request at all."""
    workspace_id = await _workspace()
    transport, seen = _recorder([])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))
    words = " ".join(f"term{number}" for number in range(500))
    with ws(workspace_id):
        assert await index.lexical(words, frozenset({SHARED}), OWNER_KIND, 10) == ()
        assert await index.lexical("   ", frozenset({SHARED}), OWNER_KIND, 10) == ()
    assert len(seen) == 1
    _, body = seen[0]
    rank_by = body["rank_by"]
    assert isinstance(rank_by, list)
    sent = str(rank_by[2])
    assert len(sent.encode()) <= tpuf.MAX_FULL_TEXT_QUERY_BYTES
    assert sent.split() == words.split()[: len(sent.split())]


async def test_delete_enumerates_a_scope_then_posts_id_deletes(db: None) -> None:
    workspace_id = await _workspace()
    d1 = _digest("a")
    row = {
        "id": tpuf.turbopuffer_id(d1),
        "owner_kind": OWNER_KIND,
        "owner_id": "m1",
        "subject": SHARED,
        "ordinal": 0,
        "text": "x",
    }
    transport, seen = _recorder([row])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))
    with ws(workspace_id):
        await index.delete(IndexScope(OWNER_KIND, "m1"))
    query_request, query_body = seen[0]
    assert query_request.url.path.endswith("/query")
    assert query_body["filters"] == [
        "And",
        [["owner_kind", "Eq", OWNER_KIND], ["owner_id", "Eq", "m1"]],
    ]
    delete_request, delete_body = seen[1]
    assert not delete_request.url.path.endswith("/query")
    assert delete_body == {"deletes": [tpuf.turbopuffer_id(d1)]}


async def test_has_chunks_queries_the_scope_for_a_single_row(db: None) -> None:
    workspace_id = await _workspace()
    row = {
        "id": tpuf.turbopuffer_id(_digest("a")),
        "owner_kind": OWNER_KIND,
        "owner_id": "m1",
        "subject": SHARED,
        "ordinal": 0,
        "text": "x",
    }
    transport, seen = _recorder([row])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))
    with ws(workspace_id):
        assert await index.has_chunks(IndexScope(OWNER_KIND, "m1"))
    request, body = seen[0]
    assert request.url.path.endswith("/query")
    assert body["top_k"] == 1
    assert body["filters"] == [
        "And",
        [["owner_kind", "Eq", OWNER_KIND], ["owner_id", "Eq", "m1"]],
    ]


async def test_has_chunks_is_false_for_an_empty_scope_or_missing_namespace(db: None) -> None:
    workspace_id = await _workspace()
    transport, _ = _recorder([])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), api=_api(transport))
    with ws(workspace_id):
        assert not await index.has_chunks(IndexScope(OWNER_KIND, "m1"))

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "namespace not found"})

    missing = tpuf.TurbopufferIndex(
        credentials=await _access(workspace_id),
        api=_api(httpx.MockTransport(handle)),
    )
    with ws(workspace_id):
        assert not await missing.has_chunks(IndexScope(OWNER_KIND, "m1"))


async def test_query_on_a_missing_namespace_returns_no_hits(db: None) -> None:
    workspace_id = await _workspace()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "namespace not found"})

    index = tpuf.TurbopufferIndex(
        credentials=await _access(workspace_id),
        api=_api(httpx.MockTransport(handle)),
    )
    with ws(workspace_id):
        assert await index.lexical("x", frozenset({SHARED}), OWNER_KIND, 5) == ()
        assert await index.vector((1.0, 0.0), frozenset({SHARED}), OWNER_KIND, 5) == ()
