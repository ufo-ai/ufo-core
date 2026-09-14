import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import ufo_ext_rag.manifest as rag
from ufo_ext_rag.pages import PageStore
from ufo_ext_rag.prefetch import (
    PASSAGE_MAX_CHARS,
    TOTAL_MAX_CHARS,
    TRUNCATION_NOTICE,
    Grounding,
    Prefetch,
)
from ufo_ext_rag.route import route

from ufo.runtime.ext.context import ExtensionContext, PageState, ScopedStore, SourceReader
from ufo.runtime.indexing import OWNER_KIND_PAGE, Chunk, Hit, IndexScope, TextChunker
from ufo.runtime.workspace import ws
from ufo.schema.records import Agent, Turn, TurnRuntimeConfig
from ufo.sdk.context import CredentialAccess
from ufo.sdk.hub import SourceRef
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit
from ufo.sdk.search import FetchedPage, FetchRequest, SearchHit, SearchQuery, SearchResults
from ufo.sdk.surfaces import fence_member_message, mint_marker

WORKSPACE = UUID("11111111-1111-1111-1111-111111111111")
ORDER_FORM = UUID("22222222-2222-2222-2222-222222222222")
ORDER_FORM_BODY = (
    "Northwind order form, signed 6 March 2025. Northwind bills this workspace $24 per seat per "
    "month. The contract renews on 1 July 2026 and takes 30 days' notice to cancel."
)
READER = SourceReader(agent_id=uuid4(), requesting_member_id=None, subjects=frozenset({"shared"}))
PRICING_PAGE = "https://northwind.example/pricing"
PAGE_DIGEST = "sha256:order-form"


@dataclass(frozen=True)
class StubSearch:
    hits: dict[str, tuple[SearchHit, ...]]
    supports_fetch: bool = True

    async def search(self, query: SearchQuery) -> SearchResults:
        for term, hits in self.hits.items():
            if term in query.query.lower():
                return SearchResults(hits=hits)
        return SearchResults(hits=())

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        raise NotImplementedError


@dataclass
class RecordingSearch:
    queries: tuple[str, ...] = ()
    supports_fetch: bool = False

    async def search(self, query: SearchQuery) -> SearchResults:
        self.queries += (query.query,)
        return SearchResults(hits=())

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        raise NotImplementedError


@dataclass(frozen=True)
class FailingSearch:
    supports_fetch: bool = False

    async def search(self, query: SearchQuery) -> SearchResults:
        raise RuntimeError("perplexity is down")

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        raise NotImplementedError


@dataclass(frozen=True)
class StubIndex:
    """The pieces of the index seam a page search reads: a lexical leg over seeded chunks and a
    vector leg ranked the other way round, so a fused order is distinguishable from either."""

    chunks: tuple[Chunk, ...] = ()
    raises: bool = False

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        raise NotImplementedError

    async def delete(self, scope: IndexScope) -> None:
        raise NotImplementedError

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        raise NotImplementedError

    async def has_chunks(self, scope: IndexScope) -> bool:
        raise NotImplementedError

    async def restamp(self, scope: IndexScope, subject: str, keep: frozenset[str]) -> bool:
        raise NotImplementedError

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        if self.raises:
            raise RuntimeError("index is down")
        terms = {word.strip("?.,").casefold() for word in query.split()}
        scored = sorted(
            (
                (sum(1 for term in terms if term in chunk.text.casefold()), order, chunk)
                for order, chunk in enumerate(self.chunks)
                if chunk.owner_kind == owner_kind and chunk.subject in subjects
            ),
            key=lambda row: (-row[0], row[1]),
        )
        return tuple(
            _index_hit(chunk, float(score)) for score, _order, chunk in scored[:limit] if score
        )

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        eligible = [
            chunk
            for chunk in reversed(self.chunks)
            if chunk.owner_kind == owner_kind and chunk.subject in subjects
        ]
        return tuple(_index_hit(chunk, 0.5) for chunk in eligible[:limit])


@dataclass(frozen=True)
class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)


def _index_hit(chunk: Chunk, score: float) -> Hit:
    return Hit(
        chunk_digest=chunk.chunk_digest,
        owner_kind=chunk.owner_kind,
        owner_id=chunk.owner_id,
        subject=chunk.subject,
        ordinal=chunk.ordinal,
        text=chunk.text,
        score=score,
    )


def _page_chunks(
    page_id: UUID, title: str, body: str, subject: str = "shared"
) -> tuple[Chunk, ...]:
    return TextChunker().chunk(body, OWNER_KIND_PAGE, str(page_id), subject, PAGE_DIGEST)


def _store(
    pages: dict[UUID, tuple[str, str]],
    subject: str = "shared",
    raises: bool = False,
    live_digest: str = PAGE_DIGEST,
    as_of: str = "2025-03-06",
):
    """A page store over chunks of `pages` (page id → title, body), fenced on those same pages.
    `live_digest` is what the page holds now, so a value other than the indexed one is a page
    edited since its chunks were derived."""
    chunks = tuple(
        chunk
        for page_id, (_title, body) in pages.items()
        for chunk in TextChunker().chunk(body, OWNER_KIND_PAGE, str(page_id), subject, PAGE_DIGEST)
    )

    async def readable(page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]:
        return {
            page_id: PageState(
                subject=subject,
                revision=1,
                digest=live_digest,
                body_ref="blob:seed",
                title=pages[page_id][0],
                stream="notes",
                indexed=True,
                as_of=as_of,
                backend="notion",
            )
            for page_id in page_ids
            if page_id in pages and subject in reader.subjects
        }

    return PageStore(
        index=StubIndex(chunks=chunks, raises=raises), embed=StubEmbed(), readable=readable
    )


def _hit(url: str, title: str, text: str, date: str | None = None) -> SearchHit:
    return SearchHit(url=url, title=title, text=text, published_date=date)


def _turn(
    inbound: str,
    member_id: UUID | None = None,
    admission: str = "member",
    parent_turn_id: UUID | None = None,
) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=WORKSPACE,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound=inbound,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        admission_source=admission,
        speaker_member_id=member_id,
        parent_turn_id=parent_turn_id,
    )


def _ext(search: object | None, index: object | None = None) -> ExtensionContext:
    return ExtensionContext(
        store=ScopedStore(extension=rag.NAME),
        credentials=CredentialAccess(declared=frozenset()),
        search=search,  # type: ignore[arg-type]
        index=index,  # type: ignore[arg-type]
        embed=StubEmbed(),  # type: ignore[arg-type]
    )


def _hook_ctx(
    ext: ExtensionContext,
    inbound: str,
    admission: str = "member",
    parent_turn_id: UUID | None = None,
    spoken: bool = True,
) -> HookContext:
    member_id = uuid4() if spoken and admission == "member" else None
    return HookContext(
        ext=ext,
        payload=UserPromptSubmit(text=inbound),
        turn=_turn(inbound, member_id, admission, parent_turn_id),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        speaker_member_id=member_id,
    )


def test_route_queries_every_message_whatever_its_shape() -> None:
    assert route("What does Northwind charge for the team plan?").queries == (
        "What does Northwind charge for the team plan?",
    )
    assert route("Send Dana the signed Northwind order form.").queries == (
        "Send Dana the signed Northwind order form.",
    )
    assert route("Can you open the pull request for the retry fix?").prefetches
    assert route("How do I connect the Slack account?").prefetches
    assert route("no").queries == ("no",)


def test_route_sends_a_two_word_query_as_it_stands() -> None:
    decision = route("s&p close")

    assert decision.queries == ("s&p close",)
    assert decision.reason == "message"


@pytest.mark.parametrize("inbound", ["", "   ", "\n\n"])
def test_route_refuses_a_message_with_no_content(inbound: str) -> None:
    decision = route(inbound)

    assert decision.queries == ()
    assert decision.reason == "no content"


def test_route_refuses_a_pasted_document() -> None:
    decision = route("Northwind order form, signed 6 March 2025. " * 60)

    assert decision.queries == ()
    assert decision.reason == "too long"


def test_route_splits_a_multi_question_inbound() -> None:
    decision = route(
        "What does Northwind charge us? When does the contract renew? "
        "Who signed it last time? Which quarter did we book it in?"
    )
    assert len(decision.queries) == 3
    assert decision.queries[0] == "What does Northwind charge us?"


async def test_both_legs_reach_the_block_with_their_provenance() -> None:
    prefetch = Prefetch(
        search=StubSearch(
            {
                "northwind": (
                    _hit(
                        PRICING_PAGE, "Northwind pricing", "Team plan is $30 a seat.", "2026-02-11"
                    ),
                )
            }
        ),
        pages=_store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}),
    )
    with ws(WORKSPACE):
        found = await prefetch.passages(("What does Northwind charge?",), READER)

    block = found.block
    assert (
        "[1] Title: Northwind pricing\n"
        f"URL: {PRICING_PAGE}\n"
        "Published: 2026-02-11\n"
        "Content:\nTeam plan is $30 a seat."
    ) in block
    assert (
        "[2] Title: Northwind order form\n"
        f"URL: page/{ORDER_FORM}\n"
        "Published: 2025-03-06\n"
        "Content:\n"
    ) in block
    assert "$24 per seat" in block
    assert "<untrusted-content" in block
    assert found.sources == (
        SourceRef(kind="web", title="Northwind pricing", url=PRICING_PAGE),
        SourceRef(
            kind="workspace",
            title="Northwind order form",
            ref=f"page/{ORDER_FORM}",
            provider="notion",
        ),
    )


async def test_the_block_numbers_every_entry_from_one() -> None:
    prefetch = Prefetch(
        search=StubSearch(
            {
                "northwind": (
                    _hit(PRICING_PAGE, "Northwind pricing", "Team plan is $30 a seat."),
                    _hit("https://northwind.example/support", "Support", "Support runs 9 to 5."),
                )
            }
        ),
        pages=_store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}),
    )
    with ws(WORKSPACE):
        found = await prefetch.passages(("What does Northwind charge?",), READER)

    block = found.block
    numbers = re.findall(r"\[(\d+)\] Title: ", block)
    assert len(found.sources) == len(numbers)
    assert numbers == [str(number) for number in range(1, len(numbers) + 1)]
    assert len(numbers) >= 3


async def test_an_entry_names_no_date_the_source_did_not_carry() -> None:
    prefetch = Prefetch(
        search=StubSearch({"northwind": (_hit(PRICING_PAGE, "Northwind pricing", "Undated."),)}),
        pages=None,
    )
    with ws(WORKSPACE):
        block = (await prefetch.passages(("What does Northwind charge?",), READER)).block

    assert f"[1] Title: Northwind pricing\nURL: {PRICING_PAGE}\nContent:\nUndated." in block
    assert "Published:" not in block


async def test_a_whole_passage_ends_with_its_content() -> None:
    prefetch = Prefetch(
        search=StubSearch(
            {"northwind": (_hit(PRICING_PAGE, "Northwind pricing", "Team plan is $30 a seat."),)}
        ),
        pages=None,
    )
    with ws(WORKSPACE):
        block = (await prefetch.passages(("What does Northwind charge?",), READER)).block

    assert "Content:\nTeam plan is $30 a seat." in block
    assert TRUNCATION_NOTICE not in block


async def test_a_cut_passage_ends_with_the_truncation_notice() -> None:
    long_text = " ".join(["Northwind-seat-pricing-detail"] * 60)
    prefetch = Prefetch(
        search=StubSearch({"northwind": (_hit(PRICING_PAGE, "Northwind pricing", long_text),)}),
        pages=None,
    )
    with ws(WORKSPACE):
        block = (await prefetch.passages(("What does Northwind charge?",), READER)).block

    assert long_text not in block
    assert block.count(TRUNCATION_NOTICE) == 1
    assert f"{long_text[:PASSAGE_MAX_CHARS]}{TRUNCATION_NOTICE}" in block


async def test_a_failed_leg_leaves_the_other_grounding_the_turn() -> None:
    workspace_only = Prefetch(
        search=FailingSearch(),
        pages=_store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}),
    )
    web_only = Prefetch(
        search=StubSearch({"renewal": (_hit(PRICING_PAGE, "Renewals", "Renewals run annually."),)}),
        pages=_store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}, raises=True),
    )
    with ws(WORKSPACE):
        without_web = (await workspace_only.passages(("When is the renewal?",), READER)).block
        without_workspace = (await web_only.passages(("When is the renewal?",), READER)).block
        without_either = await Prefetch(search=None, pages=None).passages(
            ("When does it renew?",), READER
        )

    assert "renews on 1 July 2026" in without_web
    assert PRICING_PAGE not in without_web
    assert "Renewals run annually." in without_workspace
    assert without_either.block == "" and without_either.sources == ()


async def test_selection_seats_each_leg_and_holds_the_budget() -> None:
    long_text = "Northwind pricing detail. " * 200
    prefetch = Prefetch(
        search=StubSearch(
            {
                "northwind": tuple(
                    _hit(f"{PRICING_PAGE}/{index}", f"Page {index}", long_text)
                    for index in range(6)
                )
            }
        ),
        pages=_store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}),
    )
    with ws(WORKSPACE):
        block = (await prefetch.passages(("What does Northwind charge?",), READER)).block

    assert "$24 per seat" in block
    assert len(block) < TOTAL_MAX_CHARS + len(rag.PREFACE) + 1_000


async def test_a_passage_both_queries_return_outranks_one_only_a_single_query_returns() -> None:
    shared = _hit(PRICING_PAGE, "Northwind pricing", "Team plan is $30 a seat.")
    single = _hit("https://northwind.example/support", "Support hours", "Support runs 9 to 5.")
    prefetch = Prefetch(
        search=StubSearch({"charge": (shared,), "support": (single, shared)}),
        pages=None,
    )
    with ws(WORKSPACE):
        found = await prefetch.passages(
            ("What does Northwind charge?", "What are the support hours?"), READER
        )

    block = found.block
    assert block.index("Team plan is $30 a seat.") < block.index("Support runs 9 to 5.")
    assert [source.url for source in found.sources] == [shared.url, single.url]


async def test_a_two_word_query_reaches_both_legs() -> None:
    decision = route("s&p close")
    search = RecordingSearch()
    pages = _store({ORDER_FORM: ("Market note", "The S&P 500 closed at 6,120 on 12 September.")})
    with ws(WORKSPACE):
        found = await Prefetch(search=search, pages=pages).passages(decision.queries, READER)

    assert search.queries == ("s&p close",)
    assert "The S&P 500 closed at 6,120" in found.block


async def test_the_hook_injects_for_every_member_turn_and_stays_out_of_an_internal_one() -> None:
    ext = _ext(
        StubSearch({"northwind": (_hit(PRICING_PAGE, "Northwind pricing", "Team plan is $30."),)})
    )
    with ws(WORKSPACE):
        asked = await rag.prefetch_hook(_hook_ctx(ext, "What does Northwind charge per seat?"))
        instructed = await rag.prefetch_hook(
            _hook_ctx(ext, "Send Dana the Northwind order form today.")
        )
        internal = await rag.prefetch_hook(
            _hook_ctx(ext, "What does Northwind charge per seat?", admission="internal")
        )

    assert isinstance(asked, InjectContext)
    assert asked.text.startswith(rag.PREFACE)
    assert "Team plan is $30." in asked.text
    assert asked.sources == (SourceRef(kind="web", title="Northwind pricing", url=PRICING_PAGE),)
    assert isinstance(instructed, InjectContext)
    assert instructed.text.startswith(rag.PREFACE)
    assert internal is None


async def test_the_hook_sends_no_query_for_an_arrival_no_member_spoke() -> None:
    search = RecordingSearch()
    ext = _ext(search)
    with ws(WORKSPACE):
        child = await rag.prefetch_hook(
            _hook_ctx(
                ext,
                "Read the Northwind order form and report what it bills per seat.",
                admission="internal",
                parent_turn_id=uuid4(),
            )
        )
        fired = await rag.prefetch_hook(
            _hook_ctx(ext, "Post the Northwind renewal digest.", admission="scheduled")
        )
        folded = await rag.prefetch_hook(
            _hook_ctx(ext, "The Northwind subagent reports the order form is signed.", spoken=False)
        )

    assert child is None
    assert fired is None
    assert folded is None
    assert search.queries == ()


async def test_the_hook_drops_its_injection_when_every_leg_fails() -> None:
    ext = _ext(FailingSearch())
    with ws(WORKSPACE):
        outcome = await rag.prefetch_hook(_hook_ctx(ext, "What does Northwind charge per seat?"))

    assert outcome is None


async def test_the_hook_carries_the_turn_connection_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: tuple[UUID, ...] | None = None

    @dataclass(frozen=True)
    class RecordingPrefetch:
        search: object
        pages: object

        async def passages(self, queries: tuple[str, ...], reader: SourceReader) -> Grounding:
            nonlocal seen
            seen = reader.connections
            return Grounding(block="", sources=())

    monkeypatch.setattr(rag, "Prefetch", RecordingPrefetch)
    connection_scope = (uuid4(),)
    ctx = _hook_ctx(_ext(RecordingSearch()), "What does Northwind charge per seat?")
    assert ctx.turn is not None
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={"runtime_config": TurnRuntimeConfig(connections=connection_scope)}
        ),
    )
    with ws(WORKSPACE):
        await rag.prefetch_hook(ctx)
    assert seen == connection_scope


def test_the_manifest_declares_one_best_effort_prompt_hook() -> None:
    hooks = rag.manifest().hooks
    assert [(hook.event, hook.best_effort) for hook in hooks] == [("user_prompt_submit", True)]


async def test_the_hook_calls_no_provider_for_a_message_with_no_content() -> None:
    search = RecordingSearch()
    with ws(WORKSPACE):
        outcome = await rag.prefetch_hook(_hook_ctx(_ext(search), "   "))

    assert outcome is None
    assert search.queries == ()


def test_the_preface_is_the_two_sentences_the_arms_kept() -> None:
    assert rag.PREFACE == (
        "Answer from these passages when they answer the question. Name, in the sentence that "
        "states it, where every figure, date and name came from: a web passage by its address, a "
        "workspace passage by the record it names.\n"
    )


async def test_the_page_leg_serves_only_pages_the_reader_may_read() -> None:
    private = UUID("33333333-3333-3333-3333-333333333333")
    store = _store({private: ("Board pack", ORDER_FORM_BODY)}, subject="member:dana")
    with ws(WORKSPACE):
        mine = await store.hits("What does Northwind bill per seat?", READER, 5)
        theirs = await store.hits(
            "What does Northwind bill per seat?",
            SourceReader(
                agent_id=uuid4(),
                requesting_member_id=None,
                subjects=frozenset({"member:dana"}),
            ),
            5,
        )

    assert mine == ()
    assert [hit.page_id for hit in theirs] == [private]
    assert theirs[0].ref == f"page/{private}"


def test_the_hook_builds_its_page_store_from_the_workspace_index() -> None:
    index = StubIndex()
    wired = rag._page_store(_hook_ctx(_ext(None, index), "What does Northwind charge?"))
    unwired = rag._page_store(_hook_ctx(_ext(None), "What does Northwind charge?"))

    assert wired is not None
    assert wired.index is index
    assert unwired is None


async def test_the_page_leg_refuses_chunks_the_page_body_no_longer_holds() -> None:
    edited = _store({ORDER_FORM: ("Northwind order form", ORDER_FORM_BODY)}, live_digest="sha256:2")
    with ws(WORKSPACE):
        hits = await edited.hits("What does Northwind bill per seat?", READER, 5)

    assert hits == ()


async def test_the_hook_routes_the_members_own_words_not_the_channel_around_them() -> None:
    marker = mint_marker()
    ambient = (
        f"<channel_context_{marker}>\n"
        "Priya: What does Northwind charge for the enterprise plan?\n"
        f"</channel_context_{marker}>\n"
    )
    inbound = fence_member_message(marker, ambient, "When does our contract renew?", "")
    search = RecordingSearch()
    with ws(WORKSPACE):
        await rag.prefetch_hook(_hook_ctx(_ext(search), inbound))

    assert search.queries == ("When does our contract renew?",)
