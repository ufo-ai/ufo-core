"""The Turbopuffer index backend end to end against a mocked Turbopuffer HTTP API.

Turbopuffer is the external dependency, stood in for with `httpx.MockTransport` (no live API, no
key on a real wire) so the real backend's request-building and response-parsing run against canned
responses — the thing asserted is the extension's code, never the mock. The pure id-encoding and
filter helpers are asserted directly. The Bearer key is read from a real `CredentialSlot` through
the same `CredentialAccess` core hands the factory, so the both-ends of the slot are exercised: the
stored secret reaches the request's Authorization header."""

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable, Coroutine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_turbopuffer as tpuf
from cryptography.fernet import Fernet

from ufo.db import workspace_tx
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import CredentialAccess, context_for
from ufo.runtime.indexing import IndexScope
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

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


async def test_lexical_bounds_the_query_turbopuffer_rejects_as_malformed(db: None) -> None:
    """A query arrives as a member's message or a model's search text, of any length, and one over
    Turbopuffer's 1024-byte full-text limit makes the whole request a 400 — the leg's hits lost to a
    client error of our own making. The bound lands on the request body, so the query the provider
    rejects is never built, and a query with no text is no request at all."""
    workspace_id = await _workspace()
    transport, seen = _recorder([])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), transport=transport)
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
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), transport=transport)
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


async def test_has_chunks_is_false_for_an_empty_scope_or_missing_namespace(db: None) -> None:
    workspace_id = await _workspace()
    transport, _ = _recorder([])
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id), transport=transport)
    with ws(workspace_id):
        assert not await index.has_chunks(IndexScope(OWNER_KIND, "m1"))

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "namespace not found"})

    missing = tpuf.TurbopufferIndex(
        credentials=await _access(workspace_id),
        transport=httpx.MockTransport(handle),
    )
    with ws(workspace_id):
        assert not await missing.has_chunks(IndexScope(OWNER_KIND, "m1"))


async def test_query_on_a_missing_namespace_returns_no_hits(db: None) -> None:
    workspace_id = await _workspace()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "namespace not found"})

    index = tpuf.TurbopufferIndex(
        credentials=await _access(workspace_id),
        transport=httpx.MockTransport(handle),
    )
    with ws(workspace_id):
        assert await index.lexical("x", frozenset({SHARED}), OWNER_KIND, 5) == ()
        assert await index.vector((1.0, 0.0), frozenset({SHARED}), OWNER_KIND, 5) == ()


async def test_each_event_loop_gets_its_own_client(db: None) -> None:
    """One boot-built index serves uvicorn's loop, DBOS's, and the heartbeat thread's. A pooled TLS
    connection carries anyio primitives bound to the loop that opened it, so a client shared across
    them raises `is bound to a different event loop` on the first reuse from a second loop.

    The sequential case is the one that discriminates: a second loop on the same thread must still
    get its own client. Keying on the thread returns the first loop's client to every later loop,
    and keying on `id(loop)` does the same whenever CPython hands the freed loop's address back —
    both of which are the production fault, and neither of which the threaded case alone catches.
    The last call sweeps: the closed loops leave the registry, and the live loop keeps its client.
    """
    workspace_id = await _workspace()
    index = tpuf.TurbopufferIndex(
        credentials=await _access(workspace_id),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"rows": []})),
    )

    async def client_here() -> httpx.AsyncClient:
        return index._api()

    here = await client_here()
    elsewhere: list[httpx.AsyncClient] = []

    def successive_loops() -> None:
        for _ in range(3):
            elsewhere.append(asyncio.run(client_here()))

    thread = threading.Thread(target=successive_loops)
    thread.start()
    thread.join()

    handed = [here, *elsewhere]
    assert len({id(client) for client in handed}) == len(handed)
    assert index._api() is here
    assert index.clients.keys() == {asyncio.get_running_loop()}


async def test_the_client_registry_does_not_decide_index_equality(db: None) -> None:
    """`clients` is a cache, not identity. It carries `compare=False` so the frozen dataclass stays
    hashable — a dict field would otherwise make `hash()` raise on the index and on every frozen
    dataclass holding one, `ExtensionContext` included."""
    workspace_id = await _workspace()
    credentials = await _access(workspace_id)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"rows": []}))
    one = tpuf.TurbopufferIndex(credentials=credentials, transport=transport)
    two = tpuf.TurbopufferIndex(credentials=credentials, transport=transport)
    one._api()

    assert one.clients and not two.clients
    assert one == two
    assert hash(one) == hash(two)


class _KeepAlive(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        body = b'{"rows": []}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class _LiveLoop:
    """An event loop on its own thread that stays open, as each loop `serve` runs does."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def ask(self, question: Callable[[], Coroutine[Any, Any, bool]]) -> bool:
        return asyncio.run_coroutine_threadsafe(question(), self.loop).result(timeout=30)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


async def test_a_second_live_loop_reuses_no_connection_bound_to_the_first(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keying test pins the mechanism; this reproduces the fault it prevents. A stub transport
    holds no socket, so the `RuntimeError` needs a real keep-alive connection: the index dials on
    one loop, and a second loop takes that connection warm from the pool and awaits an `Event`
    bound to the first. Both loops staying open is what makes it fire — `asyncio.run` closes its
    loop and drops the socket, so a sequential probe redials and the fault hides."""
    workspace_id = await _workspace()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAlive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tpuf, "BASE_URL", f"http://127.0.0.1:{server.server_port}")
    index = tpuf.TurbopufferIndex(credentials=await _access(workspace_id))
    first, second = _LiveLoop(), _LiveLoop()

    async def has_chunks() -> bool:
        with ws(workspace_id):
            return await index.has_chunks(IndexScope(OWNER_KIND, "m1"))

    try:
        assert first.ask(has_chunks) is False
        assert second.ask(has_chunks) is False
    finally:
        first.close()
        second.close()
        server.shutdown()
